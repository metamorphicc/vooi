from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any

from .allocator import OpenPaperPosition


class StateStore:
    def __init__(self, path: Path, event_log: Path | None = None) -> None:
        self.path = path
        self.event_log = event_log
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.event_log:
            self.event_log.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                asset TEXT NOT NULL,
                long_exchange TEXT NOT NULL,
                short_exchange TEXT NOT NULL,
                quote_size_usd TEXT NOT NULL,
                expected_net_apr TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                asset TEXT NOT NULL,
                long_exchange TEXT NOT NULL,
                short_exchange TEXT NOT NULL,
                quote_size_usd TEXT NOT NULL,
                expected_net_apr TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS live_position_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                live_position_id INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def log_event(self, kind: str, payload: Any) -> None:
        encoded = json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True)
        self.conn.execute(
            "INSERT INTO events(kind, payload) VALUES (?, ?)",
            (kind, encoded),
        )
        self.conn.commit()
        if self.event_log:
            with self.event_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": kind, "payload": json.loads(encoded)}) + "\n")

    def open_paper_position(self, plan: Any, payload: Any) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO paper_positions(
                asset, long_exchange, short_exchange, quote_size_usd,
                expected_net_apr, status, payload
            )
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                plan.asset,
                plan.long_exchange,
                plan.short_exchange,
                str(plan.quote_size_usd),
                str(plan.expected_net_apr),
                json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_open_paper_positions(self) -> list[OpenPaperPosition]:
        cur = self.conn.execute(
            """
            SELECT id, created_at, asset, long_exchange, short_exchange, payload
            FROM paper_positions
            WHERE status = 'open'
            ORDER BY created_at ASC
            """
        )
        positions: list[OpenPaperPosition] = []
        for row in cur.fetchall():
            positions.append(
                OpenPaperPosition(
                    id=int(row[0]),
                    created_at=_parse_sqlite_timestamp(str(row[1])),
                    asset=str(row[2]),
                    long_exchange=str(row[3]),
                    short_exchange=str(row[4]),
                    payload=json.loads(str(row[5])),
                )
            )
        return positions

    def count_open_paper_positions(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM paper_positions WHERE status = 'open'")
        return int(cur.fetchone()[0])

    def close_paper_position(self, position_id: int, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE paper_positions
            SET status = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'open'
            """,
            (f"closed_{reason}", position_id),
        )
        self.conn.commit()

    def open_live_position(self, plan: Any, payload: Any) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO live_positions(
                asset, long_exchange, short_exchange, quote_size_usd,
                expected_net_apr, status, payload
            )
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                plan.asset,
                plan.long_exchange,
                plan.short_exchange,
                str(plan.quote_size_usd),
                str(plan.expected_net_apr),
                json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_open_live_positions(self) -> list[OpenPaperPosition]:
        cur = self.conn.execute(
            """
            SELECT id, created_at, asset, long_exchange, short_exchange, payload
            FROM live_positions
            WHERE status = 'open'
            ORDER BY created_at ASC
            """
        )
        positions: list[OpenPaperPosition] = []
        for row in cur.fetchall():
            positions.append(
                OpenPaperPosition(
                    id=int(row[0]),
                    created_at=_parse_sqlite_timestamp(str(row[1])),
                    asset=str(row[2]),
                    long_exchange=str(row[3]),
                    short_exchange=str(row[4]),
                    payload=json.loads(str(row[5])),
                )
            )
        return positions

    def count_open_live_positions(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM live_positions WHERE status = 'open'")
        return int(cur.fetchone()[0])

    def close_live_position(self, position_id: int, reason: str, close_result: Any | None = None) -> None:
        if close_result is not None:
            self.log_event(
                "live_close_result",
                {"live_position_id": position_id, "close_result": close_result},
            )
        self.conn.execute(
            """
            UPDATE live_positions
            SET status = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'open'
            """,
            (f"closed_{reason}", position_id),
        )
        self.conn.commit()

    def record_live_position_stats(self, position_id: int, payload: Any) -> None:
        encoded = json.dumps(_jsonable(payload), ensure_ascii=True, sort_keys=True)
        self.conn.execute(
            "INSERT INTO live_position_stats(live_position_id, payload) VALUES (?, ?)",
            (position_id, encoded),
        )
        self.conn.commit()
        self.log_event("live_position_stats", {"live_position_id": position_id, "stats": payload})

    def get_live_position_stats(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT created_at, live_position_id, payload
            FROM live_position_stats
            ORDER BY id ASC
            """
        )
        rows: list[dict[str, Any]] = []
        for created_at, position_id, payload in cur.fetchall():
            item = json.loads(str(payload))
            item["recorded_at"] = str(created_at)
            item["live_position_id"] = int(position_id)
            rows.append(item)
        return rows


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__str__") and value.__class__.__module__ == "decimal":
        return str(value)
    return value


def _parse_sqlite_timestamp(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc)
