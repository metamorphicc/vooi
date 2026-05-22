from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from .mcp_client import McpHttpClient
from .executor import OrderIntent
from .models import AccountSnapshot, FundingStrategy, Market, PositionSnapshot, SlippageQuote


class MarketDataProvider(ABC):
    @abstractmethod
    def funding_strategies(self, limit: int, offset: int = 0) -> list[FundingStrategy]:
        raise NotImplementedError

    @abstractmethod
    def estimate_slippage(
        self, exchange: str, asset: str, side: str, notional_usd: Decimal
    ) -> SlippageQuote | None:
        raise NotImplementedError

    @abstractmethod
    def markets(self, asset: str) -> list[Market]:
        raise NotImplementedError

    def accounts(self) -> list[AccountSnapshot]:
        return []

    def positions(self) -> list[PositionSnapshot]:
        return []

    def create_orders(self, intents: list[OrderIntent]) -> Any:
        raise NotImplementedError("order creation is not supported by this provider")

    def set_leverage(self, exchange: str, asset: str, leverage: int) -> Any:
        raise NotImplementedError("leverage setting is not supported by this provider")


class VooiMcpProvider(MarketDataProvider):
    def __init__(self, client: McpHttpClient) -> None:
        self.client = client

    def funding_strategies(self, limit: int, offset: int = 0) -> list[FundingStrategy]:
        raw = self.client.call_tool(
            "get_funding_strategies", {"limit": limit, "offset": offset}
        )
        items = _items_from_api(raw)
        return [FundingStrategy.from_api(item) for item in items]

    def estimate_slippage(
        self, exchange: str, asset: str, side: str, notional_usd: Decimal
    ) -> SlippageQuote | None:
        raw = self.client.call_tool(
            "estimate_slippage",
            {
                "exchange": exchange,
                "asset": asset,
                "side": side,
                "notionalUsd": str(notional_usd),
            },
        )
        if not isinstance(raw, dict):
            return None
        return _slippage_from_api(exchange, asset, side, notional_usd, raw)

    def markets(self, asset: str) -> list[Market]:
        raw = self.client.call_tool("get_markets", {"assets": [asset], "limit": 20})
        items = _items_from_api(raw)
        return [Market.from_api(item) for item in items]

    def accounts(self) -> list[AccountSnapshot]:
        raw = self.client.call_tool(
            "get_accounts", {"exchanges": ["aster", "hyperliquid", "lighter"]}
        )
        items = _items_from_api(raw)
        return [AccountSnapshot.from_api(item) for item in items]

    def positions(self) -> list[PositionSnapshot]:
        raw = self.client.call_tool(
            "get_positions", {"exchanges": ["aster", "hyperliquid", "lighter"]}
        )
        items = _items_from_api(raw)
        return [PositionSnapshot.from_api(item) for item in items]

    def create_orders(self, intents: list[OrderIntent]) -> Any:
        sorted_intents = sorted(intents, key=_order_priority)
        orders = [
            {
                "exchange": intent.exchange,
                "asset": intent.asset,
                "side": intent.side,
                "size": str(intent.size),
                "reduceOnly": intent.reduce_only,
            }
            for intent in sorted_intents
        ]
        return self.client.call_tool("batch_create_orders", {"orders": orders})

    def set_leverage(self, exchange: str, asset: str, leverage: int) -> Any:
        return self.client.call_tool(
            "set_leverage",
            {"exchange": exchange, "asset": asset, "leverage": leverage},
        )


class SnapshotProvider(MarketDataProvider):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.raw = json.loads(self.path.read_text(encoding="utf-8"))

    def funding_strategies(self, limit: int, offset: int = 0) -> list[FundingStrategy]:
        raw_items = self.raw.get("funding_strategies", [])
        return [FundingStrategy.from_api(item) for item in raw_items[offset : offset + limit]]

    def estimate_slippage(
        self, exchange: str, asset: str, side: str, notional_usd: Decimal
    ) -> SlippageQuote | None:
        key = f"{exchange}:{asset}:{side}"
        raw = self.raw.get("slippage", {}).get(key)
        if raw is None:
            return None
        return _slippage_from_api(exchange, asset, side, notional_usd, raw)

    def markets(self, asset: str) -> list[Market]:
        return [Market.from_api(item) for item in self.raw.get("markets", {}).get(asset, [])]

    def accounts(self) -> list[AccountSnapshot]:
        return [AccountSnapshot.from_api(item) for item in self.raw.get("accounts", [])]

    def positions(self) -> list[PositionSnapshot]:
        return [PositionSnapshot.from_api(item) for item in self.raw.get("positions", [])]


def _slippage_from_api(
    exchange: str, asset: str, side: str, notional_usd: Decimal, raw: dict[str, Any]
) -> SlippageQuote:
    slippage = (
        raw.get("slippageBps")
        or raw.get("slippage_bps")
        or raw.get("slippage")
        or raw.get("slippageBasisPoints")
        or "0"
    )
    fill_pct = raw.get("fillPct") or raw.get("fillPercentage") or raw.get("fill_pct") or "100"
    avg_price = raw.get("averagePrice") or raw.get("avgPrice") or raw.get("average_price")
    return SlippageQuote(
        exchange=exchange,
        asset=asset,
        side=side,
        notional_usd=notional_usd,
        slippage_bps=Decimal(str(slippage)),
        fill_pct=Decimal(str(fill_pct)),
        average_price=Decimal(str(avg_price)) if avg_price is not None else None,
    )


def _order_priority(intent: OrderIntent) -> int:
    priorities = {"lighter": 0, "hyperliquid": 1, "aster": 2}
    return priorities.get(intent.exchange, 99)


def _items_from_api(raw: Any) -> list[dict[str, Any]]:
    raw = _decode_jsonish(raw)
    if isinstance(raw, dict) and "items" in raw:
        raw = _decode_jsonish(raw["items"])
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        raise ValueError(f"expected list/dict API response, got {type(raw).__name__}")

    items: list[dict[str, Any]] = []
    for item in raw:
        decoded = _decode_jsonish(item)
        if isinstance(decoded, list):
            for nested in decoded:
                nested_decoded = _decode_jsonish(nested)
                if not isinstance(nested_decoded, dict):
                    raise ValueError(
                        f"expected dict item in nested API response, got {type(nested_decoded).__name__}"
                    )
                items.append(nested_decoded)
            continue
        if not isinstance(decoded, dict):
            raise ValueError(f"expected dict item in API response, got {type(decoded).__name__}")
        items.append(decoded)
    return items


def _decode_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{\"":
        return value
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return value
    if isinstance(decoded, str) and decoded != value:
        return _decode_jsonish(decoded)
    return decoded
