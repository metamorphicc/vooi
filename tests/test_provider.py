import unittest
from decimal import Decimal

from vooi_bot.executor import OrderIntent
from vooi_bot.provider import _items_from_api, _order_priority


class ProviderTests(unittest.TestCase):
    def test_order_priority_sends_lighter_before_hyperliquid(self) -> None:
        intents = [
            OrderIntent("hyperliquid", "CHIP", "buy", Decimal("1")),
            OrderIntent("lighter", "CHIP", "sell", Decimal("1")),
        ]

        ordered = sorted(intents, key=_order_priority)

        self.assertEqual([item.exchange for item in ordered], ["lighter", "hyperliquid"])

    def test_items_from_api_decodes_json_string_items(self) -> None:
        rows = _items_from_api({"items": ['{"exchange": "hyperliquid", "asset": "CHIP"}']})

        self.assertEqual(rows, [{"exchange": "hyperliquid", "asset": "CHIP"}])

    def test_items_from_api_decodes_whole_json_string_response(self) -> None:
        rows = _items_from_api('[{"exchange": "lighter", "asset": "CHIP"}]')

        self.assertEqual(rows, [{"exchange": "lighter", "asset": "CHIP"}])


if __name__ == "__main__":
    unittest.main()
