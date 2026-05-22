import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from vooi_bot.allocator import OpenPaperPosition
from vooi_bot.cli import (
    _build_live_position_stats,
    _cleanup_failed_open,
    _entry_intents_health,
    _order_result_has_errors,
    _set_entry_leverage,
)
from vooi_bot.executor import OrderIntent
from vooi_bot.models import FundingStrategy, PositionSnapshot
from vooi_bot.provider import MarketDataProvider


class FakeProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.created_orders = []

    def funding_strategies(self, limit: int, offset: int = 0):
        return []

    def estimate_slippage(self, exchange: str, asset: str, side: str, notional_usd: Decimal):
        return None

    def markets(self, asset: str):
        return []

    def positions(self):
        return [
            PositionSnapshot.from_api(
                {"exchange": "lighter", "asset": "LIT", "side": "short", "size": "-11"}
            )
        ]

    def create_orders(self, intents):
        self.created_orders.append(intents)
        return {"results": [{"status": "ok"}]}

    def set_leverage(self, exchange, asset, leverage):
        return {"exchange": exchange, "asset": asset, "leverage": leverage, "status": "ok"}


class EmptyProvider(FakeProvider):
    def positions(self):
        return []


class CliHelpersTests(unittest.TestCase):
    def test_order_result_error_detection_checks_nested_results(self) -> None:
        self.assertTrue(
            _order_result_has_errors({"results": [{"status": "ok"}, {"status": "error"}]})
        )
        self.assertFalse(_order_result_has_errors({"results": [{"status": "ok"}]}))

    def test_failed_open_cleanup_closes_matching_partial_position(self) -> None:
        provider = FakeProvider()
        cleanup = _cleanup_failed_open(
            provider,
            [
                OrderIntent("lighter", "LIT", "sell", Decimal("11")),
                OrderIntent("hyperliquid", "LIT", "buy", Decimal("11")),
            ],
        )

        self.assertEqual(cleanup["status"], "closed")
        self.assertEqual(len(provider.created_orders), 1)
        close_intent = provider.created_orders[0][0]
        self.assertEqual(close_intent.exchange, "lighter")
        self.assertEqual(close_intent.asset, "LIT")
        self.assertEqual(close_intent.side, "buy")
        self.assertEqual(close_intent.size, Decimal("11"))
        self.assertTrue(close_intent.reduce_only)

    def test_failed_open_cleanup_reports_no_matching_positions(self) -> None:
        cleanup = _cleanup_failed_open(
            EmptyProvider(), [OrderIntent("hyperliquid", "CHIP", "buy", Decimal("690"))]
        )

        self.assertEqual(cleanup["status"], "no_matching_positions")

    def test_entry_intents_health_accepts_balanced_filled_legs(self) -> None:
        health = _entry_intents_health(
            [
                OrderIntent("hyperliquid", "CHIP", "buy", Decimal("690")),
                OrderIntent("lighter", "CHIP", "sell", Decimal("690")),
            ],
            [
                PositionSnapshot.from_api(
                    {"exchange": "hyperliquid", "asset": "CHIP", "side": "buy", "size": "690"}
                ),
                PositionSnapshot.from_api(
                    {"exchange": "lighter", "asset": "CHIP", "side": "sell", "size": "-690"}
                ),
            ],
        )

        self.assertTrue(health["healthy"])

    def test_entry_intents_health_rejects_missing_leg(self) -> None:
        health = _entry_intents_health(
            [
                OrderIntent("hyperliquid", "CHIP", "buy", Decimal("690")),
                OrderIntent("lighter", "CHIP", "sell", Decimal("690")),
            ],
            [
                PositionSnapshot.from_api(
                    {"exchange": "hyperliquid", "asset": "CHIP", "side": "buy", "size": "690"}
                )
            ],
        )

        self.assertFalse(health["healthy"])
        self.assertEqual(health["missing"], ["lighter:CHIP"])

    def test_set_entry_leverage_sets_each_non_reduce_only_market_once(self) -> None:
        provider = FakeProvider()
        results = _set_entry_leverage(
            provider,
            [
                OrderIntent("lighter", "HYPE", "buy", Decimal("0.96")),
                OrderIntent("lighter", "HYPE", "buy", Decimal("0.96")),
                OrderIntent("hyperliquid", "HYPE", "sell", Decimal("0.96")),
                OrderIntent("hyperliquid", "HYPE", "buy", Decimal("0.96"), reduce_only=True),
            ],
            3,
        )

        self.assertEqual(
            [(item["exchange"], item["asset"], item["leverage"]) for item in results],
            [("lighter", "HYPE", 3), ("hyperliquid", "HYPE", 3)],
        )

    def test_live_position_stats_sums_price_and_funding_pnl(self) -> None:
        strategy = FundingStrategy.from_api(
            {
                "asset": "VVV",
                "volume24h": "1000000",
                "apr1h": "0.1",
                "apr24h": "0.1",
                "apr7d": "0.1",
                "netApr": "1.0",
                "grossSpreadHourly": "0.01",
                "longMarketData": {
                    "exchange": "hyperliquid",
                    "baseSymbol": "VVV",
                    "quoteSymbol": "USDC",
                    "fundingRate": "0.01",
                    "maxLeverage": 3,
                },
                "shortMarketData": {
                    "exchange": "lighter",
                    "baseSymbol": "VVV",
                    "quoteSymbol": "USDC",
                    "fundingRate": "0.02",
                    "maxLeverage": 3,
                },
            }
        )
        open_position = OpenPaperPosition(
            id=7,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
            asset="VVV",
            long_exchange="hyperliquid",
            short_exchange="lighter",
            payload={
                "candidate": {"strategy": asdict(strategy)},
                "summary": {"quote_size_usd": "13"},
                "notionals": {"hyperliquid:VVV:buy": "12.9", "lighter:VVV:sell": "13.1"},
            },
        )
        stats = _build_live_position_stats(
            open_position,
            "test close",
            [
                PositionSnapshot.from_api(
                    {
                        "exchange": "hyperliquid",
                        "asset": "VVV",
                        "side": "buy",
                        "size": "0.89",
                        "unrealizedPnl": "-0.10",
                        "fundingFee": "-0.01",
                    }
                ),
                PositionSnapshot.from_api(
                    {
                        "exchange": "lighter",
                        "asset": "VVV",
                        "side": "sell",
                        "size": "-0.89",
                        "unrealizedPnl": "0.14",
                        "fundingFee": "0.03",
                    }
                ),
            ],
            [],
            {"results": [{"status": "ok"}]},
        )

        self.assertEqual(stats["funding_pnl_usd"], "0.02")
        self.assertEqual(stats["price_pnl_usd"], "0.04")
        self.assertEqual(stats["net_pnl_usd"], "0.06")
        self.assertEqual(stats["entry_notional_usd"], "26.0")


if __name__ == "__main__":
    unittest.main()
