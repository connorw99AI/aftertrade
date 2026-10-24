"""SQLite persistence. All writes go through short transactions under a lock so
the API thread and the coordinator thread can share one database file."""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return _tnow().isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA.read_text())
        self._add_missing_columns()

    # columns added after the first release; CREATE TABLE IF NOT EXISTS does not add them to older databases
    _LATER_COLUMNS = (("positions", "config_version", "INTEGER"), ("trades", "config_version", "INTEGER"),
                      ("bot_day_start", "high", "REAL"),
                      ("bot_day_start", "stopped", "TEXT"))

    def _add_missing_columns(self) -> None:
        for table, col, kind in self._LATER_COLUMNS:
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")

    # ------------------------------------------------------------- primitives
    def x(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def q(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store._lock.acquire()
                store.conn.execute("BEGIN IMMEDIATE")
                return store

            def __exit__(self, et, ev, tb):
                try:
                    store.conn.execute("ROLLBACK" if et else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _Tx()

    # ----------------------------------------------------------------- control
    def get_control(self, key: str, default: Optional[str] = None) -> Optional[str]:
        r = self.one("SELECT value FROM control WHERE key=?", (key,))
        return r["value"] if r else default

    def set_control(self, key: str, value: str) -> None:
        self.x("INSERT INTO control(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
               (key, value, now_iso()))

    # -------------------------------------------------------------- activation
    def active_activation(self) -> Optional[dict]:
        return self.one("SELECT * FROM activation WHERE active=1 ORDER BY id DESC LIMIT 1")

    def arm(self, fingerprint: str, server: Optional[str]) -> int:
        with self.tx():
            self.conn.execute("UPDATE activation SET active=0, disarmed_at=?, disarm_reason='superseded' WHERE active=1", (now_iso(),))
            cur = self.conn.execute("INSERT INTO activation(fingerprint,server,armed_at,active) VALUES(?,?,?,1)",
                                    (fingerprint, server, now_iso()))
            return cur.lastrowid

    def disarm(self, reason: str) -> None:
        with self.tx():
            self.conn.execute("UPDATE activation SET active=0, disarmed_at=?, disarm_reason=? WHERE active=1", (now_iso(), reason))
            # Outstanding approvals die with the activation.
            self.conn.execute("UPDATE approvals SET revoked=1 WHERE used_at IS NULL AND revoked=0")

    # ----------------------------------------------------------------- alerts
    def alert(self, severity: str, code: str, message: str, blocks_entries: bool = False) -> None:
        existing = self.one("SELECT id FROM alerts WHERE code=? AND resolved_at IS NULL", (code,))
        if existing:
            return
        self.x("INSERT INTO alerts(ts,severity,code,message,blocks_entries) VALUES(?,?,?,?,?)",
               (now_iso(), severity, code, message, int(blocks_entries)))
        self.event("ALERT", None, {"severity": severity, "code": code, "message": message})

    def resolve_alert(self, code: str) -> None:
        self.x("UPDATE alerts SET resolved_at=? WHERE code=? AND resolved_at IS NULL", (now_iso(), code))

    def blocking_alerts(self) -> list[dict]:
        return self.q("SELECT * FROM alerts WHERE resolved_at IS NULL AND blocks_entries=1")

    def event(self, kind: str, bot_id: Optional[str], payload: dict) -> int:
        cur = self.x("INSERT INTO events(ts,kind,bot_id,payload_json) VALUES(?,?,?,?)",
                     (now_iso(), kind, bot_id, json.dumps(payload, default=str)))
        return cur.lastrowid

    def events_after(self, after_id: int, limit: int = 100) -> list[dict]:
        rows = self.q("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        return rows
