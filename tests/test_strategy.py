from decimal import Decimal
import unittest

from vooi_bot.config import load_config
from vooi_bot.provider import SnapshotProvider
from vooi_bot.strategy import FundingRegimeRotation


class StrategyTests(unittest.TestCase):
    def test_strategy_ranks_liquid_stable_candidates(self) -> None:
        cfg = load_config("config.example.toml")
        provider = SnapshotProvider("data/snapshot.example.json")
        strategies = provider.funding_strategies(limit=10)

        candidates = FundingRegimeRotation(cfg).rank(strategies)

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].strategy.asset, "PENDLE")
        self.assertGreater(candidates[0].expected_net_apr, Decimal("0.15"))
        self.assertTrue(all(c.strategy.long_leg.exchange != "aster" for c in candidates))
        self.assertTrue(all(c.strategy.short_leg.exchange != "aster" for c in candidates))
