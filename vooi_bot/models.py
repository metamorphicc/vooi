from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


EXCHANGES = {"aster", "hyperliquid", "lighter"}


@dataclass(frozen=True)
class Leg:
    exchange: str
    base_symbol: str
    quote_symbol: str
    funding_rate: Decimal
    max_leverage: int

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "Leg":
        return cls(
            exchange=str(row["exchange"]),
            base_symbol=str(row.get("baseSymbol", row.get("base_symbol"))),
            quote_symbol=str(row.get("quoteSymbol", row.get("quote_symbol", ""))),
            funding_rate=Decimal(str(row.get("fundingRate", row.get("funding_rate", "0")))),
            max_leverage=int(row.get("maxLeverage", row.get("max_leverage", 1))),
        )


@dataclass(frozen=True)
class FundingStrategy:
    asset: str
    volume24h: Decimal
    apr1h: Decimal
    apr24h: Decimal
    apr7d: Decimal
    net_apr: Decimal
    gross_spread_hourly: Decimal
    long_leg: Leg
    short_leg: Leg

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "FundingStrategy":
        return cls(
            asset=str(row["asset"]),
            volume24h=Decimal(str(row.get("volume24h", "0"))),
            apr1h=Decimal(str(row.get("apr1h", "0"))),
            apr24h=Decimal(str(row.get("apr24h", "0"))),
            apr7d=Decimal(str(row.get("apr7d", "0"))),
            net_apr=Decimal(str(row.get("netApr", row.get("net_apr", "0")))),
            gross_spread_hourly=Decimal(
                str(row.get("grossSpreadHourly", row.get("gross_spread_hourly", "0")))
            ),
            long_leg=Leg.from_api(row.get("longMarketData", row.get("long_leg"))),
            short_leg=Leg.from_api(row.get("shortMarketData", row.get("short_leg"))),
        )


@dataclass(frozen=True)
class Candidate:
    strategy: FundingStrategy
    score: Decimal
    expected_net_apr: Decimal
    stability_ratio: Decimal
    reason: str


@dataclass(frozen=True)
class SlippageQuote:
    exchange: str
    asset: str
    side: str
    notional_usd: Decimal
    slippage_bps: Decimal
    fill_pct: Decimal
    average_price: Decimal | None = None


@dataclass(frozen=True)
class Market:
    exchange: str
    base_symbol: str
    quote_symbol: str
    price: Decimal
    base_decimals: int
    price_decimals: int
    max_leverage: int

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "Market":
        return cls(
            exchange=str(row["exchange"]),
            base_symbol=str(row["baseSymbol"]),
            quote_symbol=str(row.get("quoteSymbol", "")),
            price=Decimal(str(row.get("price", "0"))),
            base_decimals=int(row.get("baseDecimals", 6)),
            price_decimals=int(row.get("priceDecimals", 6)),
            max_leverage=int(row.get("maxLeverage", 1)),
        )


@dataclass(frozen=True)
class AccountSnapshot:
    exchange: str
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "AccountSnapshot":
        return cls(
            exchange=str(
                row.get("exchange", row.get("venue", row.get("exchangeName", row.get("name", ""))))
            ).lower(),
            raw=row,
        )

    @property
    def available_usd(self) -> Decimal | None:
        return _find_decimal(
            self.raw,
            (
                "availableBalance",
                "available_balance",
                "availableMargin",
                "available_margin",
                "withdrawable",
                "freeCollateral",
                "free_collateral",
                "equity",
                "accountValue",
            ),
        )

    @property
    def token(self) -> str:
        return str(self.raw.get("token", "")).upper()

    @property
    def account_type(self) -> str:
        return str(self.raw.get("type", "")).lower()

    @property
    def is_perps_collateral(self) -> bool:
        if self.exchange == "hyperliquid" and self.token == "USDC" and self.account_type == "spot":
            return True
        return self.account_type.startswith("perps") and self.token in {"USDC", "USDT", "USD1"}


@dataclass(frozen=True)
class PositionSnapshot:
    exchange: str
    asset: str
    size: Decimal | None
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "PositionSnapshot":
        return cls(
            exchange=str(row.get("exchange", row.get("venue", row.get("exchangeName", "")))).lower(),
            asset=str(row.get("asset", row.get("baseSymbol", row.get("symbol", "")))),
            size=_find_decimal(row, ("size", "szi", "positionSize", "position_size", "amount")),
            raw=row,
        )

    @property
    def close_side(self) -> str | None:
        explicit = str(
            self.raw.get("side", self.raw.get("direction", self.raw.get("positionSide", "")))
        ).lower()
        if explicit in ("long", "buy"):
            return "sell"
        if explicit in ("short", "sell"):
            return "buy"
        if self.size is None:
            return None
        return "sell" if self.size > 0 else "buy"

    @property
    def abs_size(self) -> Decimal | None:
        return abs(self.size) if self.size is not None else None


@dataclass(frozen=True)
class PositionPlan:
    asset: str
    long_exchange: str
    short_exchange: str
    quote_size_usd: Decimal
    expected_net_apr: Decimal
    reason: str


def _find_decimal(row: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            try:
                return Decimal(str(row[key]))
            except Exception:
                return None
    for value in row.values():
        if isinstance(value, dict):
            found = _find_decimal(value, keys)
            if found is not None:
                return found
    return None
