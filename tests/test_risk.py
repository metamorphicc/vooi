import unittest
from dataclasses import replace
from decimal import Decimal

from vooi_bot.config import load_config
from vooi_bot.models import AccountSnapshot
from vooi_bot.risk import RiskEngine, RiskReject


class RiskTests(unittest.TestCase):
    def test_live_disabled_by_default(self) -> None:
        cfg = load_config("config.example.toml")
        cfg = replace(cfg, risk=replace(cfg.risk, allow_live=False))

        with self.assertRaises(RiskReject):
            RiskEngine(cfg).assert_live_allowed()

    def test_live_confirmation_phrase_required(self) -> None:
        cfg = load_config("config.example.toml")

        with self.assertRaises(RiskReject):
            RiskEngine(cfg).assert_confirmation("WRONG")

        RiskEngine(cfg).assert_confirmation("EXECUTE_LIVE_ORDERS")

    def test_accounts_must_cover_selected_exchanges(self) -> None:
        cfg = load_config("config.example.toml")
        accounts = [
            AccountSnapshot.from_api({"exchange": "aster", "availableBalance": "25", "type": "perps", "token": "USDT"}),
            AccountSnapshot.from_api({"exchange": "hyperliquid", "availableBalance": "25", "type": "perps", "token": "USDC"}),
        ]

        RiskEngine(cfg).assert_accounts_cover_exchanges(accounts, {"aster", "hyperliquid"})

        with self.assertRaises(RiskReject):
            RiskEngine(cfg).assert_accounts_cover_exchanges(accounts, {"aster", "lighter"})

    def test_non_hyperliquid_spot_balance_does_not_count_for_perps(self) -> None:
        cfg = load_config("config.example.toml")
        accounts = [
            AccountSnapshot.from_api({"exchange": "aster", "availableBalance": "25", "type": "spot", "token": "USDC"}),
            AccountSnapshot.from_api({"exchange": "lighter", "availableBalance": "25", "type": "perps", "token": "USDC"}),
        ]

        with self.assertRaises(RiskReject):
            RiskEngine(cfg).assert_accounts_cover_exchanges(accounts, {"aster", "lighter"})

    def test_hyperliquid_unified_spot_usdc_counts_for_perps(self) -> None:
        cfg = load_config("config.example.toml")
        accounts = [
            AccountSnapshot.from_api({"exchange": "hyperliquid", "availableBalance": "25", "type": "spot", "token": "USDC"}),
            AccountSnapshot.from_api({"exchange": "lighter", "availableBalance": "25", "type": "perps", "token": "USDC"}),
        ]

        RiskEngine(cfg).assert_accounts_cover_exchanges(accounts, {"hyperliquid", "lighter"})

    def test_quote_size_uses_leverage_adjusted_margin_requirement(self) -> None:
        cfg = load_config("config.example.toml")
        risk = RiskEngine(cfg)
        self.assertEqual(risk.required_margin_per_exchange(), Decimal("14.67"))

        accounts = [
            AccountSnapshot.from_api({"exchange": "hyperliquid", "availableBalance": "14", "type": "spot", "token": "USDC"}),
            AccountSnapshot.from_api({"exchange": "lighter", "availableBalance": "25", "type": "perps", "token": "USDC"}),
        ]
        with self.assertRaises(RiskReject):
            risk.assert_accounts_cover_exchanges(accounts, {"hyperliquid", "lighter"})
