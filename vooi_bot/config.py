from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class RuntimeConfig:
    backend: str
    mcp_url: str
    token_env: str
    database: Path
    log_file: Path


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    min_net_apr: Decimal
    min_volume_24h: Decimal
    target_volume_24h: Decimal
    max_slippage_bps_per_leg: Decimal
    fee_buffer_bps_round_trip: Decimal
    min_stability_ratio: Decimal
    max_apr_spike_ratio: Decimal
    max_candidates: int
    rotation_score_improvement: Decimal
    exit_net_apr: Decimal
    ranking_band_exit: int
    allowed_exchanges: tuple[str, ...]
    allowed_quote_symbols: tuple[str, ...]
    allow_alias_assets: bool
    blocked_assets: tuple[str, ...]


@dataclass(frozen=True)
class RiskConfig:
    quote_size_usd: Decimal
    min_order_value_usd: Decimal
    min_margin_buffer_ratio: Decimal
    max_open_positions: int
    max_leverage: int
    max_hold_hours: Decimal
    min_hold_hours: Decimal
    min_total_available_usd: Decimal
    kill_switch: bool
    allow_live: bool
    live_confirmation_phrase: str


@dataclass(frozen=True)
class PaperConfig:
    close_when_profit_buffer_bps: Decimal


@dataclass(frozen=True)
class BotConfig:
    runtime: RuntimeConfig
    strategy: StrategyConfig
    risk: RiskConfig
    paper: PaperConfig


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def load_config(path: str | Path) -> BotConfig:
    cfg_path = Path(path)
    raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent

    runtime = raw["runtime"]
    strategy = raw["strategy"]
    risk = raw["risk"]
    paper = raw["paper"]

    return BotConfig(
        runtime=RuntimeConfig(
            backend=str(runtime["backend"]),
            mcp_url=str(runtime["mcp_url"]),
            token_env=str(runtime["token_env"]),
            database=(base / str(runtime["database"])).resolve(),
            log_file=(base / str(runtime["log_file"])).resolve(),
        ),
        strategy=StrategyConfig(
            name=str(strategy["name"]),
            min_net_apr=_decimal(strategy["min_net_apr"]),
            min_volume_24h=_decimal(strategy["min_volume_24h"]),
            target_volume_24h=_decimal(strategy["target_volume_24h"]),
            max_slippage_bps_per_leg=_decimal(strategy["max_slippage_bps_per_leg"]),
            fee_buffer_bps_round_trip=_decimal(strategy["fee_buffer_bps_round_trip"]),
            min_stability_ratio=_decimal(strategy["min_stability_ratio"]),
            max_apr_spike_ratio=_decimal(strategy["max_apr_spike_ratio"]),
            max_candidates=int(strategy["max_candidates"]),
            rotation_score_improvement=_decimal(strategy["rotation_score_improvement"]),
            exit_net_apr=_decimal(strategy["exit_net_apr"]),
            ranking_band_exit=int(strategy["ranking_band_exit"]),
            allowed_exchanges=tuple(strategy["allowed_exchanges"]),
            allowed_quote_symbols=tuple(strategy.get("allowed_quote_symbols", [])),
            allow_alias_assets=bool(strategy.get("allow_alias_assets", True)),
            blocked_assets=tuple(strategy["blocked_assets"]),
        ),
        risk=RiskConfig(
            quote_size_usd=_decimal(risk["quote_size_usd"]),
            min_order_value_usd=_decimal(risk.get("min_order_value_usd", "10")),
            min_margin_buffer_ratio=_decimal(risk.get("min_margin_buffer_ratio", "1.10")),
            max_open_positions=int(risk["max_open_positions"]),
            max_leverage=int(risk["max_leverage"]),
            max_hold_hours=_decimal(risk["max_hold_hours"]),
            min_hold_hours=_decimal(risk["min_hold_hours"]),
            min_total_available_usd=_decimal(risk["min_total_available_usd"]),
            kill_switch=bool(risk["kill_switch"]),
            allow_live=bool(risk["allow_live"]),
            live_confirmation_phrase=str(risk["live_confirmation_phrase"]),
        ),
        paper=PaperConfig(
            close_when_profit_buffer_bps=_decimal(paper["close_when_profit_buffer_bps"]),
        ),
    )
