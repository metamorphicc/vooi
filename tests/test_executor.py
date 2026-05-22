import unittest
from decimal import Decimal

from vooi_bot.config import load_config
from vooi_bot.executor import PaperExecutor
from vooi_bot.provider import SnapshotProvider
from vooi_bot.strategy import FundingRegimeRotation


class ExecutorTests(unittest.TestCase):
    def test_paper_executor_sizes_from_market_prices(self) -> None:
        cfg = load_config("config.example.toml")
        provider = SnapshotProvider("data/snapshot.example.json")
        candidate = FundingRegimeRotation(cfg).rank(provider.funding_strategies(limit=10))[0]
        markets = provider.markets(candidate.strategy.asset)

        intents = PaperExecutor().build_intents(
            candidate.strategy, cfg.risk.quote_size_usd, markets
        )

        self.assertEqual(intents[0].asset, "PENDLE")
        self.assertEqual(intents[0].size, Decimal("59.7"))
        self.assertEqual(intents[1].size, Decimal("59.7"))
