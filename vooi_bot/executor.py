from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from .mcp_client import McpHttpClient
from .models import FundingStrategy, Market, PositionPlan
from .risk import RiskEngine


@dataclass(frozen=True)
class OrderIntent:
    exchange: str
    asset: str
    side: str
    size: Decimal
    reduce_only: bool = False


class PaperExecutor:
    def build_intents(
        self,
        strategy: FundingStrategy,
        quote_size_usd: Decimal,
        markets: list[Market],
    ) -> list[OrderIntent]:
        long_market = _find_market(markets, strategy.long_leg.exchange, strategy.long_leg.base_symbol)
        short_market = _find_market(markets, strategy.short_leg.exchange, strategy.short_leg.base_symbol)
        base_size = _common_base_size(quote_size_usd, long_market, short_market)
        return [
            OrderIntent(
                strategy.long_leg.exchange,
                strategy.long_leg.base_symbol,
                "buy",
                base_size,
            ),
            OrderIntent(
                strategy.short_leg.exchange,
                strategy.short_leg.base_symbol,
                "sell",
                base_size,
            ),
        ]

    def notional_values(self, intents: list[OrderIntent], markets: list[Market]) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for intent in intents:
            market = _find_market(markets, intent.exchange, intent.asset)
            values[f"{intent.exchange}:{intent.asset}:{intent.side}"] = intent.size * market.price
        return values

    def build_close_intents(self, intents: list[OrderIntent]) -> list[OrderIntent]:
        return [
            OrderIntent(
                exchange=intent.exchange,
                asset=intent.asset,
                side="sell" if intent.side == "buy" else "buy",
                size=intent.size,
                reduce_only=True,
            )
            for intent in intents
        ]


class LiveExecutor:
    def __init__(self, client: McpHttpClient, risk: RiskEngine) -> None:
        self.client = client
        self.risk = risk

    def execute(self, intents: list[OrderIntent]) -> object:
        self.risk.assert_live_allowed()
        orders = [
            {
                "exchange": intent.exchange,
                "asset": intent.asset,
                "side": intent.side,
                "size": str(intent.size),
                "reduceOnly": intent.reduce_only,
            }
            for intent in intents
        ]
        return self.client.call_tool("batch_create_orders", {"orders": orders})


def plan_summary(plan: PositionPlan, intents: list[OrderIntent]) -> dict[str, object]:
    return {
        "asset": plan.asset,
        "long_exchange": plan.long_exchange,
        "short_exchange": plan.short_exchange,
        "quote_size_usd": str(plan.quote_size_usd),
        "expected_net_apr": str(plan.expected_net_apr),
        "reason": plan.reason,
        "intents": [
            {
                "exchange": intent.exchange,
                "asset": intent.asset,
                "side": intent.side,
                "size": str(intent.size),
                "reduce_only": intent.reduce_only,
            }
            for intent in intents
        ],
    }


def _find_market(markets: list[Market], exchange: str, base_symbol: str) -> Market:
    for market in markets:
        if market.exchange == exchange and market.base_symbol == base_symbol:
            if market.price <= 0:
                raise ValueError(f"{exchange}:{base_symbol} has non-positive price")
            return market
    raise ValueError(f"missing market details for {exchange}:{base_symbol}")


def _base_size(quote_size_usd: Decimal, price: Decimal, base_decimals: int) -> Decimal:
    quantum = Decimal("1") if base_decimals <= 0 else Decimal("1").scaleb(-base_decimals)
    size = (quote_size_usd / price).quantize(quantum, rounding=ROUND_DOWN)
    if size <= 0:
        raise ValueError("computed order size is zero; increase quote_size_usd")
    return size


def _common_base_size(quote_size_usd: Decimal, long_market: Market, short_market: Market) -> Decimal:
    raw = min(quote_size_usd / long_market.price, quote_size_usd / short_market.price)
    long_quantum = Decimal("1") if long_market.base_decimals <= 0 else Decimal("1").scaleb(-long_market.base_decimals)
    short_quantum = Decimal("1") if short_market.base_decimals <= 0 else Decimal("1").scaleb(-short_market.base_decimals)
    quantum = max(long_quantum, short_quantum)
    size = raw.quantize(quantum, rounding=ROUND_DOWN)
    if size <= 0:
        raise ValueError("computed common order size is zero; increase quote_size_usd")
    return size
