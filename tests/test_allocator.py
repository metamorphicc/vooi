from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import unittest

from vooi_bot.allocator import FundingRotationAllocator, OpenPaperPosition
from vooi_bot.config import load_config
from vooi_bot.provider import SnapshotProvider
from vooi_bot.strategy import FundingRegimeRotation


class AllocatorTests(unittest.TestCase):
    def test_empty_slot_enters_best_candidate(self) -> None:
        cfg = load_config("config.example.toml")
        strategy = FundingRegimeRotation(cfg)
        candidates = strategy.rank(SnapshotProvider("data/snapshot.example.json").funding_strategies(10))

        decision = FundingRotationAllocator(cfg, strategy).decide(candidates, None)

        self.assertEqual(decision.action, "enter")
        self.assertEqual(decision.selected.strategy.asset, "PENDLE")

    def test_existing_top_candidate_holds(self) -> None:
        cfg = load_config("config.example.toml")
        provider = SnapshotProvider("data/snapshot.example.json")
        strategy = FundingRegimeRotation(cfg)
        candidates = strategy.rank(provider.funding_strategies(10))
        open_position = OpenPaperPosition(
            id=1,
            created_at=datetime.now(timezone.utc) - timedelta(hours=8),
            asset="PENDLE",
            long_exchange="lighter",
            short_exchange="hyperliquid",
            payload={"candidate": {"strategy": asdict(candidates[0].strategy)}},
        )

        decision = FundingRotationAllocator(cfg, strategy).decide(candidates, open_position)

        self.assertEqual(decision.action, "hold")
        self.assertEqual(decision.current_rank, 1)
