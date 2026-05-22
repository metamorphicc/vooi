from __future__ import annotations

from decimal import Decimal

from .config import BotConfig
from .models import Candidate, FundingStrategy, PositionPlan


BPS = Decimal("10000")


class FundingRegimeRotation:
    """Quality-ranked allocator for cross-venue funding regimes."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def rank(self, strategies: list[FundingStrategy]) -> list[Candidate]:
        candidates: list[Candidate] = []
        for item in strategies:
            accepted, reason, stability = self._passes_static_filters(item)
            if not accepted:
                continue
            expected_net_apr = self._expected_after_buffers(item)
            if expected_net_apr < self.config.strategy.min_net_apr:
                continue
            volume_score = min(item.volume24h / self.config.strategy.target_volume_24h, Decimal("1.5"))
            score = expected_net_apr * (Decimal("0.7") + stability) * volume_score
            candidates.append(
                Candidate(
                    strategy=item,
                    score=score,
                    expected_net_apr=expected_net_apr,
                    stability_ratio=stability,
                    reason=reason,
                )
            )
        return sorted(candidates, key=lambda x: x.score, reverse=True)

    def find_current_candidate(
        self, current: FundingStrategy, candidates: list[Candidate]
    ) -> tuple[int, Candidate] | None:
        for index, candidate in enumerate(candidates, 1):
            item = candidate.strategy
            if (
                item.asset == current.asset
                and item.long_leg.exchange == current.long_leg.exchange
                and item.short_leg.exchange == current.short_leg.exchange
                and item.long_leg.base_symbol == current.long_leg.base_symbol
                and item.short_leg.base_symbol == current.short_leg.base_symbol
            ):
                return index, candidate
        return None

    def plan(self, candidate: Candidate) -> PositionPlan:
        strategy = candidate.strategy
        return PositionPlan(
            asset=strategy.asset,
            long_exchange=strategy.long_leg.exchange,
            short_exchange=strategy.short_leg.exchange,
            quote_size_usd=self.config.risk.quote_size_usd,
            expected_net_apr=candidate.expected_net_apr,
            reason=candidate.reason,
        )

    def _passes_static_filters(self, item: FundingStrategy) -> tuple[bool, str, Decimal]:
        cfg = self.config.strategy
        if item.asset.startswith("alias:") and not cfg.allow_alias_assets:
            return False, "alias assets disabled", Decimal("0")
        if item.asset in cfg.blocked_assets:
            return False, "blocked asset", Decimal("0")
        if item.long_leg.exchange not in cfg.allowed_exchanges:
            return False, "long exchange blocked", Decimal("0")
        if item.short_leg.exchange not in cfg.allowed_exchanges:
            return False, "short exchange blocked", Decimal("0")
        if cfg.allowed_quote_symbols:
            if item.long_leg.quote_symbol not in cfg.allowed_quote_symbols:
                return False, "long quote symbol blocked", Decimal("0")
            if item.short_leg.quote_symbol not in cfg.allowed_quote_symbols:
                return False, "short quote symbol blocked", Decimal("0")
        if item.volume24h < cfg.min_volume_24h:
            return False, "volume below minimum", Decimal("0")
        if item.net_apr < cfg.min_net_apr:
            return False, "net apr below minimum", Decimal("0")
        if item.long_leg.max_leverage < self.config.risk.max_leverage:
            return False, "long venue max leverage too low", Decimal("0")
        if item.short_leg.max_leverage < self.config.risk.max_leverage:
            return False, "short venue max leverage too low", Decimal("0")

        stability = _stability_ratio(item)
        if stability < cfg.min_stability_ratio:
            return False, "funding spread too spike-like", stability

        apr1h_ann = abs(item.apr1h) * Decimal("8760")
        apr24h_ann = abs(item.apr24h) * Decimal("365")
        if apr24h_ann > 0 and apr1h_ann / apr24h_ann > cfg.max_apr_spike_ratio:
            return False, "1h apr too high versus 24h regime", stability

        return True, "funding regime passes liquidity and stability gates", stability

    def _expected_after_buffers(self, item: FundingStrategy) -> Decimal:
        # Convert one round trip cost buffer in bps into an annualized haircut.
        # This keeps the strategy conservative for short holds while still using
        # the APR metric returned by VOOI for ranking.
        round_trip_cost = self.config.strategy.fee_buffer_bps_round_trip / BPS
        hold_fraction_year = self.config.risk.max_hold_hours / Decimal("8760")
        annualized_cost = round_trip_cost / hold_fraction_year
        return item.net_apr - annualized_cost


def _stability_ratio(item: FundingStrategy) -> Decimal:
    values = [
        abs(item.apr1h) * Decimal("8760"),
        abs(item.apr24h) * Decimal("365"),
        abs(item.apr7d) * Decimal("52"),
    ]
    high = max(values)
    low = min(v for v in values if v >= 0)
    if high == 0:
        return Decimal("0")
    return low / high


# Backwards-compatible alias for older imports/tests.
RegimeFundingCarry = FundingRegimeRotation
