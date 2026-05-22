from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from .config import BotConfig
from .models import Candidate, FundingStrategy
from .strategy import FundingRegimeRotation


ActionKind = Literal["enter", "hold", "rotate", "exit", "idle"]


@dataclass(frozen=True)
class AllocationDecision:
    action: ActionKind
    reason: str
    selected: Candidate | None = None
    current: Candidate | None = None
    current_rank: int | None = None


@dataclass(frozen=True)
class OpenPaperPosition:
    id: int
    created_at: datetime
    asset: str
    long_exchange: str
    short_exchange: str
    payload: dict

    @property
    def strategy(self) -> FundingStrategy:
        return FundingStrategy.from_api(self.payload["candidate"]["strategy"])


class FundingRotationAllocator:
    def __init__(self, config: BotConfig, strategy: FundingRegimeRotation) -> None:
        self.config = config
        self.strategy = strategy

    def decide(
        self,
        candidates: list[Candidate],
        open_position: OpenPaperPosition | None,
        now: datetime | None = None,
    ) -> AllocationDecision:
        now = now or datetime.now(timezone.utc)
        best = candidates[0] if candidates else None

        if open_position is None:
            if best is None:
                return AllocationDecision("idle", "no candidate passed filters")
            return AllocationDecision("enter", "empty slot; entering top ranked regime", best)

        current_lookup = self.strategy.find_current_candidate(open_position.strategy, candidates)
        held_hours = _hours_between(open_position.created_at, now)
        min_hold = self.config.risk.min_hold_hours
        max_hold = self.config.risk.max_hold_hours

        if current_lookup is None:
            if held_hours < min_hold:
                return AllocationDecision(
                    "hold",
                    "current regime is not ranked now, but minimum hold has not elapsed",
                )
            return AllocationDecision("exit", "current regime fell out of ranked candidates")

        rank, current = current_lookup
        if held_hours >= max_hold:
            return AllocationDecision("exit", "maximum hold time reached", current=current, current_rank=rank)

        if current.expected_net_apr < self.config.strategy.exit_net_apr:
            if held_hours < min_hold:
                return AllocationDecision(
                    "hold",
                    "APR is below exit floor, but minimum hold has not elapsed",
                    current=current,
                    current_rank=rank,
                )
            return AllocationDecision("exit", "expected APR fell below exit floor", current=current, current_rank=rank)

        if rank > self.config.strategy.ranking_band_exit and held_hours >= min_hold:
            return AllocationDecision("exit", "current regime fell outside ranking band", current=current, current_rank=rank)

        if best and _same_candidate(best, current):
            return AllocationDecision("hold", "current regime is still top ranked", current=current, current_rank=rank)

        if best and held_hours >= min_hold:
            required_score = current.score * self.config.strategy.rotation_score_improvement
            if best.score > required_score:
                return AllocationDecision(
                    "rotate",
                    "new top regime is materially better after minimum hold",
                    selected=best,
                    current=current,
                    current_rank=rank,
                )

        return AllocationDecision("hold", "no materially better regime after rotation costs", current=current, current_rank=rank)


def _same_candidate(left: Candidate, right: Candidate) -> bool:
    return (
        left.strategy.asset == right.strategy.asset
        and left.strategy.long_leg.exchange == right.strategy.long_leg.exchange
        and left.strategy.short_leg.exchange == right.strategy.short_leg.exchange
        and left.strategy.long_leg.base_symbol == right.strategy.long_leg.base_symbol
        and left.strategy.short_leg.base_symbol == right.strategy.short_leg.base_symbol
    )


def _hours_between(start: datetime, end: datetime) -> Decimal:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return Decimal(str((end - start).total_seconds())) / Decimal("3600")
