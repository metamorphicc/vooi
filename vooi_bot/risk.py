from __future__ import annotations

from decimal import Decimal

from .config import BotConfig
from .models import AccountSnapshot, Candidate, SlippageQuote


class RiskReject(Exception):
    pass


class RiskEngine:
    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def assert_can_enter(
        self,
        candidate: Candidate,
        open_position_count: int,
        long_slippage: SlippageQuote | None,
        short_slippage: SlippageQuote | None,
    ) -> None:
        if self.config.risk.kill_switch:
            raise RiskReject("kill switch is enabled")
        if open_position_count >= self.config.risk.max_open_positions:
            raise RiskReject("max open positions reached")
        for quote in (long_slippage, short_slippage):
            if quote is None:
                continue
            if quote.slippage_bps > self.config.strategy.max_slippage_bps_per_leg:
                raise RiskReject(
                    f"{quote.exchange} {quote.side} slippage {quote.slippage_bps}bps exceeds limit"
                )
            if quote.fill_pct < Decimal("99"):
                raise RiskReject(f"{quote.exchange} {quote.side} fill estimate below 99%")
        if candidate.expected_net_apr < self.config.strategy.min_net_apr:
            raise RiskReject("expected net APR below configured target")
        if self.config.risk.quote_size_usd < self.config.risk.min_order_value_usd:
            raise RiskReject(
                f"quote_size_usd {self.config.risk.quote_size_usd} is below "
                f"min_order_value_usd {self.config.risk.min_order_value_usd}"
            )

    def assert_live_allowed(self) -> None:
        if not self.config.risk.allow_live:
            raise RiskReject("live trading is disabled; set risk.allow_live = true")

    def assert_confirmation(self, confirmation: str | None) -> None:
        if confirmation != self.config.risk.live_confirmation_phrase:
            raise RiskReject(
                "missing live confirmation phrase; pass "
                f"--confirm {self.config.risk.live_confirmation_phrase}"
            )

    def assert_accounts_funded(self, accounts: list[AccountSnapshot]) -> None:
        if not accounts:
            raise RiskReject("no VOOI accounts returned; deposit/connect venues before live trading")
        total = Decimal("0")
        usable = 0
        for account in accounts:
            available = account.available_usd
            if account.is_perps_collateral and available is not None and available > 0:
                usable += 1
                total += available
        if usable == 0:
            raise RiskReject("accounts returned, but no positive available USD balance was detected")
        if total < self.config.risk.min_total_available_usd:
            raise RiskReject(
                f"available USD {total} is below required {self.config.risk.min_total_available_usd}"
            )

    def assert_accounts_cover_exchanges(
        self, accounts: list[AccountSnapshot], exchanges: set[str]
    ) -> None:
        totals: dict[str, Decimal] = {}
        seen: set[str] = set()
        for account in accounts:
            if not account.exchange:
                continue
            seen.add(account.exchange)
            if not account.is_perps_collateral:
                continue
            available = account.available_usd
            if available is not None and available > 0:
                totals[account.exchange] = totals.get(account.exchange, Decimal("0")) + available
        missing: list[str] = []
        unfunded: list[str] = []
        required_margin = self.required_margin_per_exchange()
        for exchange in exchanges:
            if exchange not in seen:
                missing.append(exchange)
                continue
            if totals.get(exchange, Decimal("0")) <= 0:
                unfunded.append(exchange)
            elif totals.get(exchange, Decimal("0")) < required_margin:
                unfunded.append(
                    f"{exchange} (< required_margin {required_margin})"
                )
        if missing:
            raise RiskReject(f"missing funded account rows for exchanges: {', '.join(sorted(missing))}")
        if unfunded:
            raise RiskReject(f"no positive available USD on exchanges: {', '.join(sorted(unfunded))}")

    def required_margin_per_exchange(self) -> Decimal:
        leverage = Decimal(str(max(self.config.risk.max_leverage, 1)))
        return (
            self.config.risk.quote_size_usd
            / leverage
            * self.config.risk.min_margin_buffer_ratio
        ).quantize(Decimal("0.01"))
