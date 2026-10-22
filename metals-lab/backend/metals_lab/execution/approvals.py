"""Proposals and bound, expiring, single-use approvals.

Pipeline: strategy signal -> Proposal -> Auditor (AUDIT approval) ->
RiskEngine (verifies the AUDIT approval, issues RISK approval) -> Executor
(verifies both, consumes both, re-checks demo at the order boundary).

Each authority signs approvals with its own process-local HMAC key. An approval
is bound to (proposal hash, account fingerprint, expiry) and recorded in SQLite
so it can be used once and revoked on account switch/disarm. This makes
bypass a structural error, not a convention; it is not a defence against
hostile code running inside the same Python process.
"""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..domain import Intent, Signal
from ..store import Store


@dataclass
class Proposal:
    bot_id: str
    role: str
    symbol: str
    intent: Intent
    volume: float
    signal: Signal
    magic: int
    sl: Optional[float] = None
    tp: Optional[float] = None
    position_key: Optional[str] = None
    position_ticket: Optional[int] = None
    planned_risk: float = 0.0
    risk_budget: float = 0.0
    est_margin: Optional[float] = None
    created_at: datetime = field(default_factory=lambda: _tnow())
    kill: bool = False

    @property
    def proposal_hash(self) -> str:
        payload = {"bot": self.bot_id, "sig": self.signal.signal_id, "sym": self.symbol, "intent": self.intent.value,
                   "vol": round(self.volume, 8), "sl": self.sl, "tp": self.tp, "pos": self.position_ticket,
                   "created": self.created_at.isoformat()}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def public(self) -> dict:
        d = asdict(self)
        d["intent"] = self.intent.value
        d["signal"] = self.signal.to_dict()
        d["created_at"] = self.created_at.isoformat()
        d["proposal_hash"] = self.proposal_hash
        return d


@dataclass(frozen=True)
class Approval:
    approval_id: str
    kind: str
    proposal_hash: str
    decision: str
    fingerprint: str
    expires_at: str
    signature: str


class ApprovalError(RuntimeError):
    pass


class ApprovalAuthority:
    def __init__(self, kind: str, store: Store, ttl_seconds: int = 20, ruleset_version: str = ""):
        self.kind = kind
        self._key = os.urandom(32)
        self.db = store
        self.ttl = ttl_seconds
        self.ruleset_version = ruleset_version

    def _sign(self, approval_id: str, proposal_hash: str, decision: str, fp: str, exp: str) -> str:
        msg = f"{self.kind}|{approval_id}|{proposal_hash}|{decision}|{fp}|{exp}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def issue(self, proposal_hash: str, decision: str, fingerprint: str, now: Optional[datetime] = None) -> Approval:
        now = now or _tnow()
        aid = uuid.uuid4().hex
        exp = (now + timedelta(seconds=self.ttl)).isoformat()
        sig = self._sign(aid, proposal_hash, decision, fingerprint, exp)
        self.db.x("INSERT INTO approvals(approval_id,kind,proposal_hash,decision,fingerprint,ruleset_version,created_at,expires_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (aid, self.kind, proposal_hash, decision, fingerprint, self.ruleset_version,
                                              now.isoformat(), exp))
        return Approval(aid, self.kind, proposal_hash, decision, fingerprint, exp, sig)

    def verify(self, a: Optional[Approval], proposal_hash: str, fingerprint: str, now: Optional[datetime] = None) -> None:
        now = now or _tnow()
        if a is None:
            raise ApprovalError(f"missing {self.kind} approval")
        if a.kind != self.kind:
            raise ApprovalError(f"wrong approval kind {a.kind}")
        if not hmac.compare_digest(a.signature, self._sign(a.approval_id, a.proposal_hash, a.decision, a.fingerprint, a.expires_at)):
            raise ApprovalError(f"{self.kind} approval signature invalid (forged or from another process)")
        if a.decision != "APPROVED":
            raise ApprovalError(f"{self.kind} decision is {a.decision}")
        if a.proposal_hash != proposal_hash:
            raise ApprovalError(f"{self.kind} approval bound to a different proposal")
        if a.fingerprint != fingerprint:
            raise ApprovalError(f"{self.kind} approval bound to a different account")
        if datetime.fromisoformat(a.expires_at) < now:
            raise ApprovalError(f"{self.kind} approval expired")
        row = self.db.one("SELECT used_at, revoked FROM approvals WHERE approval_id=?", (a.approval_id,))
        if row is None:
            raise ApprovalError(f"{self.kind} approval unknown")
        if row["revoked"]:
            raise ApprovalError(f"{self.kind} approval revoked")
        if row["used_at"]:
            raise ApprovalError(f"{self.kind} approval already used")

    def consume(self, a: Approval) -> None:
        cur = self.db.x("UPDATE approvals SET used_at=? WHERE approval_id=? AND used_at IS NULL AND revoked=0",
                        (_tnow().isoformat(), a.approval_id))
        if cur.rowcount != 1:
            raise ApprovalError("approval could not be consumed (already used or revoked)")
