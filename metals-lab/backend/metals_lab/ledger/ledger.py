"""Per-bot virtual accounting inside one MT5 demo account.

Rules enforced here:
* At first activation for an account fingerprint the starting balance is
  snapshotted once and each bot receives exactly ``bot_fraction`` of it.
  The snapshot is never refreshed - allocations do not drift with the shared
  balance.
* A bot's equity = its allocations + its own realized P&L/commission/swap/fees
  (from MT5 deals attributed to its positions) + unrealized P&L of its own open
  positions. Nothing flows between bots automatically.
* Short sales never create spendable cash: only P&L is booked, never notional.
* Deposits/withdrawals/credits land in an *unallocated* pool and are visible;
  only an explicit operator action (with audit record) can allocate them.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Optional

from ..store import Store, now_iso

REALIZED_KINDS = ("REALIZED_PNL", "COMMISSION", "SWAP", "FEE", "ADJUSTMENT")


@dataclass
class BotBook:
    bot_id: str
    allocation: float
    realized: float
    unrealized: float
    reserved_margin: float
    pending_reserved: float
    shadow: bool = False  # True before the first arming: allocation is what the bot WOULD get

    @property
    def equity(self) -> float:
        return self.allocation + self.realized + self.unrealized

    @property
    def free_capital(self) -> float:
        return self.equity - self.reserved_margin - self.pending_reserved

    def public(self) -> dict:
        return {"allocation": round(self.allocation, 2), "realized": round(self.realized, 2),
                "unrealized": round(self.unrealized, 2), "equity": round(self.equity, 2),
                "reserved_margin": round(self.reserved_margin, 2), "pending_reserved": round(self.pending_reserved, 2),
                "free_capital": round(self.free_capital, 2), "shadow": self.shadow}


class Ledger:
    def __init__(self, store: Store):
        self.db = store

    # ------------------------------------------------------------- allocation
    def snapshot(self, fingerprint: str) -> Optional[dict]:
        return self.db.one("SELECT * FROM capital_snapshot WHERE fingerprint=?", (fingerprint,))

    def initialize(self, fingerprint: str, balance: float, currency: str, fraction: float, bot_ids: list[str]) -> dict:
        """Idempotent. Creates the snapshot + initial allocations only once per account."""
        snap = self.snapshot(fingerprint)
        if snap:
            return snap
        if fraction * len(bot_ids) > 1.0 + 1e-9:
            raise ValueError("allocations would exceed 100% of the starting balance")
        each = round(balance * fraction, 2)
        with self.db.tx():
            self.db.conn.execute(
                "INSERT INTO capital_snapshot(fingerprint,starting_balance,currency,bot_fraction,created_at) VALUES(?,?,?,?,?)",
                (fingerprint, balance, currency, fraction, now_iso()))
            for b in bot_ids:
                self.db.conn.execute(
                    "INSERT INTO ledger(bot_id,ts,kind,amount,ref,note) VALUES(?,?,?,?,?,?)",
                    (b, now_iso(), "ALLOCATION", each, f"init:{fingerprint[:16]}:{b}",
                     f"initial {fraction:.0%} of starting balance {balance:.2f} {currency}"))
                self.db.conn.execute(
                    "INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                    (b, "INITIAL_ALLOCATION", None, json.dumps({"amount": each}), "system@activation", now_iso()))
        return self.snapshot(fingerprint)

    def unallocated_pool(self, fingerprint: str) -> float:
        snap = self.snapshot(fingerprint)
        if not snap:
            return 0.0
        alloc = self.db.one("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE kind='ALLOCATION'")["s"]
        acked = self.db.one("SELECT COALESCE(SUM(amount),0) s FROM unallocated_events WHERE acknowledged=1")["s"]
        return round(snap["starting_balance"] + acked - alloc, 2)

    def change_allocation(self, fingerprint: str, bot_id: str, delta: float, reason: str, operator: str) -> dict:
        """Explicit operator action only. Increases must be funded from the unallocated pool."""
        if not reason or not operator:
            raise ValueError("allocation changes require a reason and an operator name")
        if delta > 0 and delta > self.unallocated_pool(fingerprint) + 1e-9:
            raise ValueError("insufficient unallocated funds; reduce another bot explicitly or acknowledge a deposit first")
        book_alloc = self.allocation(bot_id)
        if delta < 0 and book_alloc + delta < 0:
            raise ValueError("allocation cannot go negative")
        ref = f"op:{uuid.uuid4().hex}"
        with self.db.tx():
            self.db.conn.execute("INSERT INTO ledger(bot_id,ts,kind,amount,ref,note) VALUES(?,?,?,?,?,?)",
                                 (bot_id, now_iso(), "ALLOCATION", delta, ref, reason))
            self.db.conn.execute(
                "INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                (bot_id, "ALLOCATION_CHANGE", json.dumps({"allocation": book_alloc}),
                 json.dumps({"allocation": book_alloc + delta, "reason": reason}), operator, now_iso()))
        return {"bot_id": bot_id, "allocation": self.allocation(bot_id), "ref": ref}

    # ---------------------------------------------------------------- reading
    def allocation(self, bot_id: str) -> float:
        return self.db.one("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind='ALLOCATION'", (bot_id,))["s"]

    def realized(self, bot_id: str) -> float:
        return self.db.one(
            f"SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind IN ({','.join('?' * len(REALIZED_KINDS))})",
            (bot_id, *REALIZED_KINDS))["s"]

    def book(self, bot_id: str, unrealized: float = 0.0, reserved_margin: float = 0.0, pending_reserved: float = 0.0) -> BotBook:
        return BotBook(bot_id, self.allocation(bot_id), self.realized(bot_id), unrealized, reserved_margin, pending_reserved)

    def entries(self, bot_id: str, limit: int = 200) -> list[dict]:
        return self.db.q("SELECT * FROM ledger WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot_id, limit))

    # ---------------------------------------------------------------- posting
    def post_deal(self, bot_id: str, deal: dict) -> int:
        """Book one MT5 deal's money fields to its owning bot. Idempotent per deal ticket."""
        n = 0
        t = deal["ticket"]
        for kind, field in (("REALIZED_PNL", "profit"), ("COMMISSION", "commission"), ("SWAP", "swap"), ("FEE", "fee")):
            amt = float(deal.get(field) or 0.0)
            if amt == 0.0:
                continue
            cur = self.db.x("INSERT OR IGNORE INTO ledger(bot_id,ts,kind,amount,ref,note) VALUES(?,?,?,?,?,?)",
                            (bot_id, now_iso(), kind, amt, f"deal:{t}:{field}", f"{deal.get('symbol')} deal {t}"))
            n += cur.rowcount
        return n
