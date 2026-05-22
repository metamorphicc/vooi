from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

from .allocator import FundingRotationAllocator
from .config import BotConfig, load_config
from .executor import OrderIntent, PaperExecutor, plan_summary
from .mcp_client import McpHttpClient, McpError
from .provider import MarketDataProvider, SnapshotProvider, VooiMcpProvider
from .risk import RiskEngine, RiskReject
from .state import StateStore
from .strategy import FundingRegimeRotation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vooi-bot")
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default="config.example.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser(
        "scan", parents=[parent], help="rank live or snapshot funding carry candidates"
    )
    scan.add_argument("--snapshot")
    scan.add_argument("--json", action="store_true")

    paper = sub.add_parser("paper", parents=[parent], help="open the best candidate in paper state")
    paper.add_argument("--snapshot")
    paper.add_argument("--once", action="store_true")

    propose = sub.add_parser("propose", parents=[parent], help="show the next allocator action")
    propose.add_argument("--snapshot")
    propose.add_argument("--mode", choices=["paper", "live"], default="paper")

    live = sub.add_parser("live-once", parents=[parent], help="execute one guarded live allocator step")
    live.add_argument("--confirm")

    live_run = sub.add_parser("live-run", parents=[parent], help="run guarded live allocator cycles")
    live_run.add_argument("--confirm")
    live_run.add_argument("--interval-sec", type=int, default=900)
    live_run.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted")
    live_run.add_argument("--stop-file", default="data/STOP_LIVE")

    accounts = sub.add_parser("accounts", parents=[parent], help="show VOOI accounts")
    accounts.add_argument("--json", action="store_true")

    positions = sub.add_parser("positions", parents=[parent], help="show VOOI open positions")
    positions.add_argument("--json", action="store_true")

    trades = sub.add_parser("trades", parents=[parent], help="show VOOI trade history")
    trades.add_argument("--exchange", choices=["aster", "hyperliquid", "lighter"], action="append")
    trades.add_argument("--limit", type=int, default=20)
    trades.add_argument("--cursor")
    trades.add_argument("--json", action="store_true")

    report = sub.add_parser("report", parents=[parent], help="show closed live-position performance")
    report.add_argument("--json", action="store_true")
    report.add_argument("--scale-usd", nargs="*", default=["500", "1000"])

    flatten = sub.add_parser("flatten-position", parents=[parent], help="close one detected live position")
    flatten.add_argument("--exchange", required=True)
    flatten.add_argument("--asset", required=True)
    flatten.add_argument("--confirm")

    reduce_order = sub.add_parser(
        "reduce-order",
        parents=[parent],
        help="submit one explicit reduce-only order without position discovery",
    )
    reduce_order.add_argument("--exchange", required=True)
    reduce_order.add_argument("--asset", required=True)
    reduce_order.add_argument("--side", required=True, choices=["buy", "sell"])
    reduce_order.add_argument("--size", required=True)
    reduce_order.add_argument("--confirm")

    status = sub.add_parser("status", parents=[parent], help="show local paper allocator state")
    status.add_argument("--json", action="store_true")

    run = sub.add_parser("run", parents=[parent], help="run repeated paper allocator cycles")
    run.add_argument("--snapshot")
    run.add_argument("--interval-sec", type=int, default=900)
    run.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted")

    reset = sub.add_parser("reset-paper", parents=[parent], help="close all open paper positions")
    reset.add_argument("--reason", default="manual_reset")

    reset_live = sub.add_parser("reset-live", parents=[parent], help="mark local managed live positions closed")
    reset_live.add_argument("--reason", default="manual_reset")

    tools = sub.add_parser("tools", parents=[parent], help="list raw MCP tools")
    tools.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.command == "tools":
            return cmd_tools(cfg, json_output=args.json)
        if args.command == "scan":
            provider = build_provider(cfg, args.snapshot)
            return cmd_scan(cfg, provider, json_output=args.json)
        if args.command == "paper":
            provider = build_provider(cfg, args.snapshot)
            return cmd_paper(cfg, provider)
        if args.command == "propose":
            provider = build_provider(cfg, args.snapshot)
            return cmd_propose(cfg, provider, args.mode)
        if args.command == "live-once":
            provider = build_provider(cfg)
            return cmd_live_once(cfg, provider, args.confirm)
        if args.command == "live-run":
            provider = build_provider(cfg)
            return cmd_live_run(
                cfg,
                provider,
                args.confirm,
                args.interval_sec,
                args.iterations,
                Path(args.stop_file),
            )
        if args.command == "accounts":
            provider = build_provider(cfg)
            return cmd_accounts(provider, args.json)
        if args.command == "positions":
            provider = build_provider(cfg)
            return cmd_positions(provider, args.json)
        if args.command == "trades":
            provider = build_provider(cfg)
            return cmd_trades(provider, args.exchange, args.limit, args.cursor, args.json)
        if args.command == "report":
            return cmd_report(cfg, args.json, [Decimal(str(item)) for item in args.scale_usd])
        if args.command == "flatten-position":
            provider = build_provider(cfg)
            return cmd_flatten_position(cfg, provider, args.exchange, args.asset, args.confirm)
        if args.command == "reduce-order":
            provider = build_provider(cfg)
            return cmd_reduce_order(
                provider,
                args.exchange,
                args.asset,
                args.side,
                Decimal(str(args.size)),
                args.confirm,
            )
        if args.command == "status":
            return cmd_status(cfg, json_output=args.json)
        if args.command == "run":
            provider = build_provider(cfg, args.snapshot)
            return cmd_run(cfg, provider, args.interval_sec, args.iterations)
        if args.command == "reset-paper":
            return cmd_reset_paper(cfg, args.reason)
        if args.command == "reset-live":
            return cmd_reset_live(cfg, args.reason)
    except (McpError, RiskReject, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


def build_provider(cfg: BotConfig, snapshot: str | None = None) -> MarketDataProvider:
    if snapshot:
        return SnapshotProvider(snapshot)
    if cfg.runtime.backend == "snapshot":
        return SnapshotProvider(Path("data/snapshot.json"))
    if cfg.runtime.backend != "mcp":
        raise ValueError(f"unknown backend: {cfg.runtime.backend}")
    return VooiMcpProvider(McpHttpClient(cfg.runtime.mcp_url, cfg.runtime.token_env))


def cmd_tools(cfg: BotConfig, json_output: bool) -> int:
    client = McpHttpClient(cfg.runtime.mcp_url, cfg.runtime.token_env)
    tools = client.list_tools()
    if json_output:
        print(json.dumps(tools, indent=2, ensure_ascii=True))
    else:
        for item in tools:
            print(item.get("name", "<unnamed>"))
    return 0


def cmd_scan(cfg: BotConfig, provider: MarketDataProvider, json_output: bool) -> int:
    strategy = FundingRegimeRotation(cfg)
    raw = provider.funding_strategies(limit=max(cfg.strategy.max_candidates * 3, 20))
    candidates = strategy.rank(raw)[: cfg.strategy.max_candidates]

    rows = []
    for candidate in candidates:
        item = candidate.strategy
        rows.append(
            {
                "asset": item.asset,
                "long": f"{item.long_leg.exchange}:{item.long_leg.base_symbol}",
                "short": f"{item.short_leg.exchange}:{item.short_leg.base_symbol}",
                "volume24h": str(item.volume24h),
                "netApr": str(item.net_apr),
                "expectedNetApr": str(candidate.expected_net_apr),
                "stability": str(candidate.stability_ratio),
                "score": str(candidate.score),
            }
        )

    if json_output:
        print(json.dumps(rows, indent=2, ensure_ascii=True))
    else:
        if not rows:
            print("No candidates passed the configured filters.")
            return 0
        for i, row in enumerate(rows, 1):
            print(
                f"{i:02d} {row['asset']:>14} "
                f"long={row['long']:<28} short={row['short']:<28} "
                f"netAPR={_pct(row['netApr'])} expected={_pct(row['expectedNetApr'])} "
                f"vol={row['volume24h']} stability={row['stability']}"
            )
    return 0


def cmd_paper(cfg: BotConfig, provider: MarketDataProvider) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    strategy = FundingRegimeRotation(cfg)
    allocator = FundingRotationAllocator(cfg, strategy)
    risk = RiskEngine(cfg)
    paper_executor = PaperExecutor()

    raw = provider.funding_strategies(limit=max(cfg.strategy.max_candidates * 3, 20))
    candidates = strategy.rank(raw)
    open_positions = store.get_open_paper_positions()
    open_position = open_positions[0] if open_positions else None
    decision = allocator.decide(candidates, open_position)

    if decision.action == "idle":
        print("No paper action: no candidates passed filters.")
        return 0

    if decision.action == "hold":
        output = {
            "action": "hold",
            "reason": decision.reason,
            "paper_position_id": open_position.id if open_position else None,
            "current_rank": decision.current_rank,
            "current": _candidate_row(decision.current) if decision.current else None,
        }
        store.log_event("paper_hold", output)
        print(json.dumps(output, indent=2))
        return 0

    if decision.action == "exit":
        if open_position is None:
            print("No paper action: exit requested but no open position exists.")
            return 0
        store.close_paper_position(open_position.id, _slug(decision.reason))
        payload = {
            "action": "exit",
            "reason": decision.reason,
            "paper_position_id": open_position.id,
            "current_rank": decision.current_rank,
        }
        store.log_event("paper_exit", payload)
        print(json.dumps(payload, indent=2))
        return 0

    if decision.action == "rotate":
        if open_position is not None:
            store.close_paper_position(open_position.id, "rotated")
            store.log_event(
                "paper_rotate_close",
                {"paper_position_id": open_position.id, "reason": decision.reason},
            )
        candidate = decision.selected
    else:
        candidate = decision.selected

    if candidate is None:
        print("No paper action: no selected candidate.")
        return 0
    item = candidate.strategy
    long_slippage = provider.estimate_slippage(
        item.long_leg.exchange, item.long_leg.base_symbol, "buy", cfg.risk.quote_size_usd
    )
    short_slippage = provider.estimate_slippage(
        item.short_leg.exchange, item.short_leg.base_symbol, "sell", cfg.risk.quote_size_usd
    )
    risk.assert_can_enter(
        candidate,
        open_position_count=store.count_open_paper_positions(),
        long_slippage=long_slippage,
        short_slippage=short_slippage,
    )

    plan = strategy.plan(candidate)
    markets = provider.markets(item.asset)
    intents = paper_executor.build_intents(item, cfg.risk.quote_size_usd, markets)
    payload = {
        "candidate": candidate,
        "long_slippage": long_slippage,
        "short_slippage": short_slippage,
        "summary": plan_summary(plan, intents),
    }
    position_id = store.open_paper_position(plan, payload)
    event_kind = "paper_rotate_open" if decision.action == "rotate" else "paper_open"
    store.log_event(event_kind, payload)
    print(
        json.dumps(
            {
                "action": decision.action,
                "decision_reason": decision.reason,
                "paper_position_id": position_id,
                **plan_summary(plan, intents),
            },
            indent=2,
        )
    )
    return 0


def cmd_propose(cfg: BotConfig, provider: MarketDataProvider, mode: str) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    strategy = FundingRegimeRotation(cfg)
    allocator = FundingRotationAllocator(cfg, strategy)
    open_position = None
    if mode == "live":
        live_positions = store.get_open_live_positions()
        open_position = live_positions[0] if live_positions else None
    else:
        paper_positions = store.get_open_paper_positions()
        open_position = paper_positions[0] if paper_positions else None

    raw = provider.funding_strategies(limit=max(cfg.strategy.max_candidates * 3, 20))
    candidates = strategy.rank(raw)
    decision = allocator.decide(candidates, open_position)
    output: dict[str, object] = {
        "mode": mode,
        "action": decision.action,
        "reason": decision.reason,
        "current_rank": decision.current_rank,
        "current": _candidate_row(decision.current) if decision.current else None,
        "selected": _candidate_row(decision.selected) if decision.selected else None,
    }
    selected = decision.selected
    if selected is not None:
        output["entry"] = _entry_preview(cfg, provider, selected)
    print(json.dumps(output, indent=2))
    return 0


def cmd_live_once(cfg: BotConfig, provider: MarketDataProvider, confirmation: str | None) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    strategy = FundingRegimeRotation(cfg)
    allocator = FundingRotationAllocator(cfg, strategy)
    risk = RiskEngine(cfg)

    risk.assert_live_allowed()
    risk.assert_confirmation(confirmation)

    live_positions = store.get_open_live_positions()
    open_position = live_positions[0] if live_positions else None
    raw = provider.funding_strategies(limit=max(cfg.strategy.max_candidates * 3, 20))
    candidates = strategy.rank(raw)
    decision = allocator.decide(candidates, open_position)

    if open_position is not None:
        live_exchange_positions = provider.positions()
        health = _managed_position_health(open_position, live_exchange_positions)
        if not health["healthy"]:
            close_intents = _close_intents_for_targets(open_position, live_exchange_positions)
            close_result = provider.create_orders(close_intents) if close_intents else None
            stats = _build_live_position_stats(
                open_position,
                str(health["reason"]),
                live_exchange_positions,
                close_intents,
                close_result,
            )
            store.close_live_position(open_position.id, _slug(str(health["reason"])), close_result)
            store.record_live_position_stats(open_position.id, stats)
            payload = {
                "action": "emergency_exit",
                "reason": health["reason"],
                "live_position_id": open_position.id,
                "health": health,
                "intents": close_intents,
                "close_result": close_result,
                "stats": stats,
            }
            store.log_event("live_emergency_exit", payload)
            if close_result is not None and _order_result_has_errors(close_result):
                raise RiskReject(f"emergency close failed: {_order_errors(close_result)}")
            print(json.dumps(payload, indent=2, default=str))
            return 0

    if decision.action in ("idle", "hold"):
        payload = {
            "action": decision.action,
            "reason": decision.reason,
            "live_position_id": open_position.id if open_position else None,
            "current_rank": decision.current_rank,
            "current": _candidate_row(decision.current) if decision.current else None,
        }
        store.log_event(f"live_{decision.action}", payload)
        print(json.dumps(payload, indent=2))
        return 0

    if decision.action in ("exit", "rotate") and open_position is not None:
        live_exchange_positions = provider.positions()
        close_intents = _close_intents_for_position(open_position, live_exchange_positions)
        close_result = provider.create_orders(close_intents)
        stats = _build_live_position_stats(
            open_position,
            decision.reason,
            live_exchange_positions,
            close_intents,
            close_result,
        )
        store.close_live_position(open_position.id, _slug(decision.reason), close_result)
        store.record_live_position_stats(open_position.id, stats)
        store.log_event(
            "live_close",
            {
                "live_position_id": open_position.id,
                "reason": decision.reason,
                "intents": close_intents,
                "result": close_result,
                "stats": stats,
            },
        )
        if decision.action == "exit":
            print(
                json.dumps(
                    {
                        "action": "exit",
                        "reason": decision.reason,
                        "live_position_id": open_position.id,
                        "close_result": close_result,
                    },
                    indent=2,
                )
            )
            return 0

    selected = decision.selected
    if selected is None:
        print(json.dumps({"action": decision.action, "reason": "no selected candidate"}, indent=2))
        return 0

    payload, intents = _build_entry_payload(cfg, provider, selected, open_count=store.count_open_live_positions())
    accounts = provider.accounts()
    risk.assert_accounts_funded(accounts)
    risk.assert_accounts_cover_exchanges(accounts, {intent.exchange for intent in intents})
    leverage_result = _set_entry_leverage(provider, intents, cfg.risk.max_leverage)
    payload["leverage_result"] = leverage_result
    try:
        open_result = provider.create_orders(intents)
    except Exception as exc:
        try:
            live_positions = provider.positions()
        except Exception:
            live_positions = []
        health = _entry_intents_health(intents, live_positions)
        if health["healthy"]:
            open_result = {
                "status": "reconciled_after_exception",
                "error": str(exc),
                "health": health,
            }
            payload["live_open_result"] = open_result
            position_id = store.open_live_position(strategy.plan(selected), payload)
            store.log_event(
                "live_open_reconciled",
                {"live_position_id": position_id, "decision": decision.reason, "payload": payload},
            )
            print(
                json.dumps(
                    {
                        "action": decision.action,
                        "decision_reason": decision.reason,
                        "live_position_id": position_id,
                        **payload["summary"],
                        "open_result": open_result,
                    },
                    indent=2,
                    default=str,
                )
            )
            return 0
        cleanup = _cleanup_failed_open(provider, intents)
        failure_payload = {
            "action": decision.action,
            "decision_reason": decision.reason,
            "summary": payload["summary"],
            "error": str(exc),
            "health": health,
            "cleanup": cleanup,
        }
        store.log_event("live_open_exception", failure_payload)
        message = f"live open order raised before confirmation: {exc}"
        if cleanup["status"] == "closed":
            message += "; matching live positions were closed"
        elif cleanup["status"] == "close_failed":
            message += f"; cleanup failed: {_order_errors(cleanup.get('close_result'))}"
        elif cleanup["status"] in ("positions_error", "close_exception"):
            message += f"; cleanup status={cleanup['status']}: {cleanup.get('error')}"
        else:
            message += f"; cleanup status={cleanup['status']}"
        raise RiskReject(message) from exc
    if _order_result_has_errors(open_result):
        cleanup = _cleanup_failed_open(provider, intents)
        failure_payload = {
            "action": decision.action,
            "decision_reason": decision.reason,
            "summary": payload["summary"],
            "open_result": open_result,
            "cleanup": cleanup,
        }
        store.log_event("live_open_failed", failure_payload)
        message = f"live open order failed: {_order_errors(open_result)}"
        if cleanup["status"] == "closed":
            message += "; partial fill cleanup order sent"
        elif cleanup["status"] == "close_failed":
            message += f"; partial fill cleanup failed: {_order_errors(cleanup.get('close_result'))}"
        elif cleanup["status"] in ("positions_error", "close_exception"):
            message += f"; cleanup status={cleanup['status']}: {cleanup.get('error')}"
        raise RiskReject(message)
    payload["live_open_result"] = open_result
    position_id = store.open_live_position(strategy.plan(selected), payload)
    store.log_event(
        "live_open",
        {"live_position_id": position_id, "decision": decision.reason, "payload": payload},
    )
    print(
        json.dumps(
            {
                "action": decision.action,
                "decision_reason": decision.reason,
                "live_position_id": position_id,
                **payload["summary"],
                "open_result": open_result,
            },
            indent=2,
        )
    )
    return 0


def cmd_live_run(
    cfg: BotConfig,
    provider: MarketDataProvider,
    confirmation: str | None,
    interval_sec: int,
    iterations: int,
    stop_file: Path,
) -> int:
    if interval_sec < 60:
        raise ValueError("interval-sec must be at least 60 for live-run")
    risk = RiskEngine(cfg)
    risk.assert_live_allowed()
    risk.assert_confirmation(confirmation)
    count = 0
    while True:
        if stop_file.exists():
            print(json.dumps({"action": "stop", "reason": f"stop file exists: {stop_file}"}, indent=2))
            return 0
        count += 1
        print(f"--- live allocator cycle {count} ---")
        result = cmd_live_once(cfg, provider, confirmation)
        if result != 0:
            return result
        if iterations and count >= iterations:
            return 0
        time.sleep(interval_sec)


def cmd_accounts(provider: MarketDataProvider, json_output: bool) -> int:
    accounts = provider.accounts()
    rows = [
        {
            "exchange": account.exchange,
            "available_usd": str(account.available_usd) if account.available_usd is not None else None,
            "raw": account.raw,
        }
        for account in accounts
    ]
    if json_output:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("No accounts returned by VOOI MCP.")
    else:
        for row in rows:
            print(f"{row['exchange']}: available_usd={row['available_usd']}")
    return 0


def cmd_positions(provider: MarketDataProvider, json_output: bool) -> int:
    positions = provider.positions()
    rows = [
        {
            "exchange": position.exchange,
            "asset": position.asset,
            "size": str(position.size) if position.size is not None else None,
            "raw": position.raw,
        }
        for position in positions
    ]
    if json_output:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("No open positions returned by VOOI MCP.")
    else:
        for row in rows:
            print(f"{row['exchange']} {row['asset']}: size={row['size']}")
    return 0



def cmd_trades(
    provider: MarketDataProvider,
    exchanges: list[str] | None,
    limit: int,
    cursor: str | None,
    json_output: bool,
) -> int:
    if limit < 1 or limit > 100:
        raise ValueError("--limit must be between 1 and 100")
    selected: str | list[str] | None = None
    if exchanges:
        selected = exchanges[0] if len(exchanges) == 1 else exchanges

    raw = provider.trades(exchanges=selected, limit=limit, cursor=cursor)
    output = raw if isinstance(raw, dict) else {"items": raw}

    if json_output:
        print(json.dumps(output, indent=2))
        return 0

    items = output.get("items", []) if isinstance(output, dict) else []
    next_cursor = output.get("cursor") if isinstance(output, dict) else None
    if not items:
        print("No trades returned by VOOI MCP.")
    else:
        print(f"Trades returned: {len(items)}")
        for item in items:
            exchange = item.get("exchange", "?") if isinstance(item, dict) else "?"
            asset = item.get("asset") or item.get("symbol") or item.get("market") if isinstance(item, dict) else "?"
            side = item.get("side", "?") if isinstance(item, dict) else "?"
            size = item.get("size") or item.get("qty") or item.get("quantity") if isinstance(item, dict) else None
            price = item.get("price") if isinstance(item, dict) else None
            ts = item.get("timestamp") or item.get("time") or item.get("createdAt") if isinstance(item, dict) else None
            print(f"{exchange} {asset} side={side} size={size} price={price} time={ts}")
    if next_cursor:
        print(f"next_cursor={next_cursor}")
    return 0

def cmd_report(cfg: BotConfig, json_output: bool, scale_usd: list[Decimal]) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    stats = store.get_live_position_stats()
    summary = _performance_summary(stats, scale_usd)
    output = {"summary": summary, "positions": stats}
    if json_output:
        print(json.dumps(output, indent=2))
        return 0
    if not stats:
        print("No closed live-position stats recorded yet.")
        return 0
    print(
        "Closed live positions: "
        f"{summary['closed_positions']} | "
        f"net={summary['net_pnl_usd']} USDC | "
        f"funding={summary['funding_pnl_usd']} USDC | "
        f"price={summary['price_pnl_usd']} USDC"
    )
    print(f"Return on entry notional: {summary['pnl_pct_on_entry_notional']}")
    for scale, scaled in summary["scaled"].items():
        print(
            f"If quote_size_usd={scale}: "
            f"net={scaled['estimated_net_pnl_usd']} USDC, "
            f"funding={scaled['estimated_funding_pnl_usd']} USDC"
        )
    for item in stats[-10:]:
        print(
            f"#{item['live_position_id']} {item['asset']} "
            f"{item['long_exchange']}->{item['short_exchange']} "
            f"net={item['net_pnl_usd']} funding={item['funding_pnl_usd']} "
            f"hold={item['hold_hours']}h reason={item['close_reason']}"
        )
    return 0


def cmd_flatten_position(
    cfg: BotConfig,
    provider: MarketDataProvider,
    exchange: str,
    asset: str,
    confirmation: str | None,
) -> int:
    phrase = "FLATTEN_OPEN_POSITION"
    if confirmation != phrase:
        raise RiskReject(f"missing flatten confirmation phrase; pass --confirm {phrase}")

    exchange = exchange.lower()
    positions = provider.positions()
    target = None
    for position in positions:
        if position.exchange != exchange:
            continue
        if _asset_matches(position.asset, asset):
            target = position
            break
    if target is None:
        print(json.dumps({"action": "flatten", "status": "no_position_found"}, indent=2))
        return 0

    side = target.close_side
    size = target.abs_size
    if side is None or size is None or size <= 0:
        raise RiskReject(f"cannot derive close side/size for {exchange}:{asset}")

    intent = OrderIntent(
        exchange=exchange,
        asset=asset,
        side=side,
        size=size,
        reduce_only=True,
    )
    result = provider.create_orders([intent])
    if _order_result_has_errors(result):
        raise RiskReject(f"flatten order failed: {_order_errors(result)}")
    print(
        json.dumps(
            {
                "action": "flatten",
                "exchange": exchange,
                "asset": asset,
                "side": side,
                "size": str(size),
                "result": result,
            },
            indent=2,
        )
    )
    return 0


def cmd_reduce_order(
    provider: MarketDataProvider,
    exchange: str,
    asset: str,
    side: str,
    size: Decimal,
    confirmation: str | None,
) -> int:
    phrase = "CREATE_REDUCE_ONLY_ORDER"
    if confirmation != phrase:
        raise RiskReject(f"missing reduce-order confirmation phrase; pass --confirm {phrase}")
    if size <= 0:
        raise RiskReject("reduce-order size must be positive")

    intent = OrderIntent(
        exchange=exchange.lower(),
        asset=asset.upper(),
        side=side,
        size=size,
        reduce_only=True,
    )
    result = provider.create_orders([intent])
    if _order_result_has_errors(result):
        raise RiskReject(f"reduce-order failed: {_order_errors(result)}")
    print(
        json.dumps(
            {
                "action": "reduce_order",
                "exchange": intent.exchange,
                "asset": intent.asset,
                "side": intent.side,
                "size": str(intent.size),
                "reduce_only": True,
                "result": result,
            },
            indent=2,
        )
    )
    return 0


def cmd_status(cfg: BotConfig, json_output: bool) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    positions = store.get_open_paper_positions()
    rows = [
        {
            "id": item.id,
            "created_at": item.created_at.isoformat(),
            "age_hours": str(_age_hours(item.created_at)),
            "asset": item.asset,
            "long_exchange": item.long_exchange,
            "short_exchange": item.short_exchange,
            "expected_net_apr": item.payload.get("candidate", {}).get("expected_net_apr"),
        }
        for item in positions
    ]
    if json_output:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("No open paper positions.")
    else:
        for row in rows:
            print(
                f"#{row['id']} {row['asset']} long={row['long_exchange']} "
                f"short={row['short_exchange']} age={row['age_hours']}h "
                f"expectedAPR={_pct(str(row['expected_net_apr']))}"
            )
    return 0


def cmd_run(
    cfg: BotConfig,
    provider: MarketDataProvider,
    interval_sec: int,
    iterations: int,
) -> int:
    if interval_sec < 30:
        raise ValueError("interval-sec must be at least 30")
    count = 0
    while True:
        count += 1
        print(f"--- allocator cycle {count} ---")
        result = cmd_paper(cfg, provider)
        if result != 0:
            return result
        if iterations and count >= iterations:
            return 0
        time.sleep(interval_sec)


def cmd_reset_paper(cfg: BotConfig, reason: str) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    positions = store.get_open_paper_positions()
    for position in positions:
        store.close_paper_position(position.id, _slug(reason))
        store.log_event(
            "paper_reset",
            {"paper_position_id": position.id, "reason": reason},
        )
    print(json.dumps({"closed": len(positions), "reason": reason}, indent=2))
    return 0


def cmd_reset_live(cfg: BotConfig, reason: str) -> int:
    store = StateStore(cfg.runtime.database, cfg.runtime.log_file)
    positions = store.get_open_live_positions()
    for position in positions:
        store.close_live_position(position.id, _slug(reason))
        store.log_event(
            "live_reset",
            {"live_position_id": position.id, "reason": reason},
        )
    print(json.dumps({"closed": len(positions), "reason": reason}, indent=2))
    return 0


def _pct(value: str) -> str:
    return f"{Decimal(value) * Decimal('100'):.2f}%"


def _candidate_row(candidate: object) -> dict[str, str] | None:
    if candidate is None:
        return None
    item = candidate.strategy
    return {
        "asset": item.asset,
        "long": f"{item.long_leg.exchange}:{item.long_leg.base_symbol}",
        "short": f"{item.short_leg.exchange}:{item.short_leg.base_symbol}",
        "score": str(candidate.score),
        "expected_net_apr": str(candidate.expected_net_apr),
        "stability": str(candidate.stability_ratio),
    }


def _entry_preview(cfg: BotConfig, provider: MarketDataProvider, candidate: object) -> dict[str, object]:
    payload, _ = _build_entry_payload(cfg, provider, candidate, open_count=0, enforce_risk=False)
    return payload["summary"]


def _build_entry_payload(
    cfg: BotConfig,
    provider: MarketDataProvider,
    candidate: object,
    open_count: int,
    enforce_risk: bool = True,
) -> tuple[dict[str, object], list[OrderIntent]]:
    strategy = FundingRegimeRotation(cfg)
    risk = RiskEngine(cfg)
    paper_executor = PaperExecutor()
    item = candidate.strategy
    long_slippage = provider.estimate_slippage(
        item.long_leg.exchange, item.long_leg.base_symbol, "buy", cfg.risk.quote_size_usd
    )
    short_slippage = provider.estimate_slippage(
        item.short_leg.exchange, item.short_leg.base_symbol, "sell", cfg.risk.quote_size_usd
    )
    if enforce_risk:
        risk.assert_can_enter(candidate, open_count, long_slippage, short_slippage)
    markets = provider.markets(item.asset)
    plan = strategy.plan(candidate)
    intents = paper_executor.build_intents(item, cfg.risk.quote_size_usd, markets)
    notionals = paper_executor.notional_values(intents, markets)
    if enforce_risk:
        required_notional = cfg.risk.min_order_value_usd * Decimal("1.05")
        low = {key: str(value) for key, value in notionals.items() if value < required_notional}
        if low:
            raise RiskReject(
                f"computed order notional below buffered minimum {required_notional}: {low}"
            )
    payload = {
        "candidate": candidate,
        "long_slippage": long_slippage,
        "short_slippage": short_slippage,
        "summary": plan_summary(plan, intents),
        "notionals": notionals,
    }
    return payload, intents


def _close_intents_from_payload(payload: dict[str, object]) -> list[OrderIntent]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("managed live position is missing order summary")
    raw_intents = summary.get("intents")
    if not isinstance(raw_intents, list):
        raise ValueError("managed live position is missing entry intents")
    close_intents: list[OrderIntent] = []
    for raw in raw_intents:
        if not isinstance(raw, dict):
            continue
        side = str(raw["side"])
        close_intents.append(
            OrderIntent(
                exchange=str(raw["exchange"]),
                asset=str(raw["asset"]),
                side="sell" if side == "buy" else "buy",
                size=Decimal(str(raw["size"])),
                reduce_only=True,
            )
        )
    if not close_intents:
        raise ValueError("no close intents could be built from managed live position")
    return close_intents


def _set_entry_leverage(
    provider: MarketDataProvider, intents: list[OrderIntent], leverage: int
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for intent in intents:
        if intent.reduce_only:
            continue
        key = (intent.exchange, intent.asset.upper())
        if key in seen:
            continue
        seen.add(key)
        result = provider.set_leverage(intent.exchange, intent.asset, leverage)
        results.append(
            {
                "exchange": intent.exchange,
                "asset": intent.asset,
                "leverage": leverage,
                "result": result,
            }
        )
    return results


def _build_live_position_stats(
    open_position: object,
    close_reason: str,
    live_positions: list[object],
    close_intents: list[OrderIntent],
    close_result: object,
) -> dict[str, object]:
    strategy = open_position.strategy
    summary = open_position.payload.get("summary", {})
    quote_size = Decimal(str(summary.get("quote_size_usd", "0")))
    notionals = open_position.payload.get("notionals", {})
    if isinstance(notionals, dict) and notionals:
        entry_notional = sum((Decimal(str(value)) for value in notionals.values()), Decimal("0"))
    else:
        entry_notional = quote_size * Decimal("2")

    legs = []
    funding_pnl = Decimal("0")
    price_pnl = Decimal("0")
    for exchange, asset in _managed_targets(open_position):
        position = _find_position(live_positions, exchange, asset)
        if position is None:
            legs.append({"exchange": exchange, "asset": asset, "missing": True})
            continue
        raw = position.raw
        funding = _raw_decimal(raw, ("fundingFee",))
        if funding is None:
            funding = _raw_decimal(raw, ("totalFundingPaidOut",))
        funding = funding or Decimal("0")
        unrealized = _raw_decimal(raw, ("unrealizedPnl",)) or Decimal("0")
        realized = _raw_decimal(raw, ("realizedPnl",)) or Decimal("0")
        leg_price_pnl = unrealized + realized
        funding_pnl += funding
        price_pnl += leg_price_pnl
        legs.append(
            {
                "exchange": exchange,
                "asset": asset,
                "side": position.raw.get("side"),
                "size": str(position.size) if position.size is not None else None,
                "entry_price": position.raw.get("entryPrice"),
                "funding_pnl_usd": str(funding),
                "price_pnl_usd": str(leg_price_pnl),
                "unrealized_pnl_usd": str(unrealized),
                "realized_pnl_usd": str(realized),
            }
        )

    net_pnl = funding_pnl + price_pnl
    hold_hours = _age_hours(open_position.created_at)
    return {
        "live_position_id": open_position.id,
        "asset": strategy.asset,
        "long_exchange": strategy.long_leg.exchange,
        "short_exchange": strategy.short_leg.exchange,
        "opened_at": open_position.created_at.isoformat(),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "hold_hours": str(hold_hours),
        "close_reason": close_reason,
        "entry_quote_size_usd": str(quote_size),
        "entry_notional_usd": str(entry_notional),
        "funding_pnl_usd": str(funding_pnl),
        "price_pnl_usd": str(price_pnl),
        "net_pnl_usd": str(net_pnl),
        "pnl_pct_on_entry_notional": str(_ratio(net_pnl, entry_notional)),
        "pnl_pct_on_quote_size": str(_ratio(net_pnl, quote_size)),
        "legs": legs,
        "close_intents": close_intents,
        "close_result": close_result,
        "note": "Stats are captured from the live position snapshot immediately before close; close execution fees may settle separately.",
    }


def _performance_summary(stats: list[dict[str, object]], scale_usd: list[Decimal]) -> dict[str, object]:
    funding = _sum_stats(stats, "funding_pnl_usd")
    price = _sum_stats(stats, "price_pnl_usd")
    net = _sum_stats(stats, "net_pnl_usd")
    entry_notional = _sum_stats(stats, "entry_notional_usd")
    quote = _sum_stats(stats, "entry_quote_size_usd")
    scaled: dict[str, object] = {}
    for target in scale_usd:
        multiplier = _ratio(target, quote)
        scaled[str(target)] = {
            "estimated_net_pnl_usd": str((net * multiplier).quantize(Decimal("0.000001"))),
            "estimated_funding_pnl_usd": str((funding * multiplier).quantize(Decimal("0.000001"))),
            "estimated_price_pnl_usd": str((price * multiplier).quantize(Decimal("0.000001"))),
        }
    return {
        "closed_positions": len(stats),
        "funding_pnl_usd": str(funding),
        "price_pnl_usd": str(price),
        "net_pnl_usd": str(net),
        "entry_quote_size_usd": str(quote),
        "entry_notional_usd": str(entry_notional),
        "pnl_pct_on_entry_notional": str(_ratio(net, entry_notional)),
        "pnl_pct_on_quote_size": str(_ratio(net, quote)),
        "scaled": scaled,
    }


def _sum_stats(stats: list[dict[str, object]], key: str) -> Decimal:
    total = Decimal("0")
    for item in stats:
        value = item.get(key)
        if value not in (None, ""):
            total += Decimal(str(value))
    return total


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (numerator / denominator).quantize(Decimal("0.000001"))


def _close_intents_for_position(open_position: object, live_positions: list[object]) -> list[OrderIntent]:
    strategy = open_position.strategy
    targets = _managed_targets(open_position)
    intents: list[OrderIntent] = []
    for exchange, asset in targets:
        for position in live_positions:
            if position.exchange != exchange:
                continue
            if not _asset_matches(position.asset, asset):
                continue
            side = position.close_side
            size = position.abs_size
            if side is None or size is None or size <= 0:
                continue
            intents.append(
                OrderIntent(
                    exchange=exchange,
                    asset=asset,
                    side=side,
                    size=size,
                    reduce_only=True,
                )
            )
            break
    if intents:
        return intents
    return _close_intents_from_payload(open_position.payload)


def _find_position(live_positions: list[object], exchange: str, asset: str) -> object | None:
    for position in live_positions:
        if position.exchange == exchange and _asset_matches(position.asset, asset):
            return position
    return None


def _managed_targets(open_position: object) -> list[tuple[str, str]]:
    strategy = open_position.strategy
    return [
        (strategy.long_leg.exchange, strategy.long_leg.base_symbol),
        (strategy.short_leg.exchange, strategy.short_leg.base_symbol),
    ]


def _managed_position_health(open_position: object, live_positions: list[object]) -> dict[str, object]:
    matched: list[dict[str, object]] = []
    missing: list[str] = []
    for exchange, asset in _managed_targets(open_position):
        found = None
        for position in live_positions:
            if position.exchange == exchange and _asset_matches(position.asset, asset):
                found = position
                break
        if found is None or found.abs_size is None or found.abs_size <= 0:
            missing.append(f"{exchange}:{asset}")
            continue
        matched.append(
            {
                "exchange": exchange,
                "asset": asset,
                "side": found.close_side,
                "size": found.abs_size,
            }
        )

    if missing:
        return {
            "healthy": False,
            "reason": "managed position is missing one or more live legs",
            "missing": missing,
            "matched": matched,
        }

    sizes = [item["size"] for item in matched if isinstance(item["size"], Decimal)]
    if len(sizes) == 2:
        larger = max(sizes)
        smaller = min(sizes)
        if larger > 0 and (larger - smaller) / larger > Decimal("0.02"):
            return {
                "healthy": False,
                "reason": "managed position leg sizes are imbalanced",
                "matched": matched,
                "imbalance_ratio": str((larger - smaller) / larger),
            }

    return {"healthy": True, "reason": "managed position legs are present and balanced", "matched": matched}


def _entry_intents_health(intended_intents: list[OrderIntent], live_positions: list[object]) -> dict[str, object]:
    matched: list[dict[str, object]] = []
    missing: list[str] = []
    wrong_side: list[str] = []
    for intent in intended_intents:
        found = _find_position(live_positions, intent.exchange, intent.asset)
        if found is None or found.abs_size is None or found.abs_size <= 0:
            missing.append(f"{intent.exchange}:{intent.asset}")
            continue
        actual_side = _position_entry_side(found)
        if actual_side is not None and actual_side != intent.side:
            wrong_side.append(f"{intent.exchange}:{intent.asset} expected={intent.side} actual={actual_side}")
        matched.append(
            {
                "exchange": intent.exchange,
                "asset": intent.asset,
                "expected_side": intent.side,
                "actual_side": actual_side,
                "expected_size": intent.size,
                "actual_size": found.abs_size,
            }
        )

    if missing or wrong_side:
        return {
            "healthy": False,
            "reason": "entry intents are not fully present after order exception",
            "missing": missing,
            "wrong_side": wrong_side,
            "matched": matched,
        }

    sizes = [item["actual_size"] for item in matched if isinstance(item["actual_size"], Decimal)]
    if len(sizes) == len(intended_intents) and sizes:
        larger = max(sizes)
        smaller = min(sizes)
        if larger > 0 and (larger - smaller) / larger > Decimal("0.02"):
            return {
                "healthy": False,
                "reason": "entry legs are imbalanced after order exception",
                "matched": matched,
                "imbalance_ratio": str((larger - smaller) / larger),
            }

    return {"healthy": True, "reason": "entry legs are present and balanced", "matched": matched}


def _position_entry_side(position: object) -> str | None:
    explicit = str(
        position.raw.get("side", position.raw.get("direction", position.raw.get("positionSide", "")))
    ).lower()
    if explicit in ("long", "buy"):
        return "buy"
    if explicit in ("short", "sell"):
        return "sell"
    if position.size is None:
        return None
    return "buy" if position.size > 0 else "sell"


def _close_intents_for_targets(open_position: object, live_positions: list[object]) -> list[OrderIntent]:
    target_intents = [
        OrderIntent(exchange=exchange, asset=asset, side="buy", size=Decimal("0"))
        for exchange, asset in _managed_targets(open_position)
    ]
    return _close_intents_for_intents(target_intents, live_positions)


def _cleanup_failed_open(provider: MarketDataProvider, intended_intents: list[OrderIntent]) -> dict[str, object]:
    try:
        live_positions = provider.positions()
    except Exception as exc:
        return {"status": "positions_error", "error": str(exc)}

    close_intents = _close_intents_for_intents(intended_intents, live_positions)
    payload: dict[str, object] = {
        "status": "no_matching_positions",
        "live_positions": live_positions,
        "close_intents": close_intents,
        "close_result": None,
    }
    if not close_intents:
        return payload

    try:
        close_result = provider.create_orders(close_intents)
    except Exception as exc:
        payload["status"] = "close_exception"
        payload["error"] = str(exc)
        return payload

    payload["close_result"] = close_result
    payload["status"] = "close_failed" if _order_result_has_errors(close_result) else "closed"
    return payload


def _close_intents_for_intents(
    intended_intents: list[OrderIntent], live_positions: list[object]
) -> list[OrderIntent]:
    close_intents: list[OrderIntent] = []
    seen: set[tuple[str, str]] = set()
    for intent in intended_intents:
        key = (intent.exchange, intent.asset.upper())
        if key in seen:
            continue
        seen.add(key)
        for position in live_positions:
            if position.exchange != intent.exchange:
                continue
            if not _asset_matches(position.asset, intent.asset):
                continue
            side = position.close_side
            size = position.abs_size
            if side is None or size is None or size <= 0:
                continue
            close_intents.append(
                OrderIntent(
                    exchange=intent.exchange,
                    asset=intent.asset,
                    side=side,
                    size=size,
                    reduce_only=True,
                )
            )
            break
    return close_intents


def _asset_matches(position_asset: str, target_asset: str) -> bool:
    left = position_asset.upper().replace("USDC", "").replace("USDT", "")
    right = target_asset.upper().replace("USDC", "").replace("USDT", "")
    return left == right or left.endswith(right) or right.endswith(left)


def _raw_decimal(row: dict[str, object], keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return Decimal(str(value))
            except Exception:
                return None
    for value in row.values():
        if isinstance(value, dict):
            found = _raw_decimal(value, keys)
            if found is not None:
                return found
    return None


def _order_result_has_errors(result: object) -> bool:
    if isinstance(result, dict):
        if str(result.get("status", "")).lower() == "error" or "error" in result:
            return True
        nested = result.get("results")
        if isinstance(nested, list):
            return any(_order_result_has_errors(item) for item in nested)
    if isinstance(result, list):
        return any(_order_result_has_errors(item) for item in result)
    return False


def _order_errors(result: object) -> str:
    errors: list[str] = []
    if isinstance(result, dict):
        if "error" in result:
            errors.append(str(result["error"]))
        nested = result.get("results")
        if isinstance(nested, list):
            for item in nested:
                text = _order_errors(item)
                if text:
                    errors.append(text)
    elif isinstance(result, list):
        for item in result:
            text = _order_errors(item)
            if text:
                errors.append(text)
    return "; ".join(errors)


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")[:60]


def _age_hours(created_at: datetime) -> Decimal:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created_at
    return (Decimal(str(delta.total_seconds())) / Decimal("3600")).quantize(Decimal("0.01"))


if __name__ == "__main__":
    raise SystemExit(main())
