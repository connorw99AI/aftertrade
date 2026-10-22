"""Demo-only enforcement and account identity binding.

The account identity is reduced to a salted SHA-256 fingerprint of
(login, server, company) so that no account number is ever written to logs,
the database, API responses or reports.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .gateway import ReadOnlyGateway

_FP_SALT = b"metals-lab/account-fingerprint/v1"


class AccountState(str, Enum):
    DEMO = "DEMO"
    LIVE = "LIVE"
    CONTEST = "CONTEST"
    UNKNOWN = "UNKNOWN"
    DISCONNECTED = "DISCONNECTED"


def fingerprint(login: object, server: object, company: object) -> str:
    h = hashlib.sha256(_FP_SALT + f"{login}|{server}|{company}".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class AccountCheck:
    state: AccountState
    fingerprint: Optional[str]
    server: Optional[str]
    currency: Optional[str]
    margin_mode: Optional[int]
    hedging: bool
    balance: Optional[float]
    equity: Optional[float]
    margin_free: Optional[float]
    trade_allowed: Optional[bool]
    reason: str

    @property
    def is_demo(self) -> bool:
        return self.state == AccountState.DEMO

    def public(self) -> dict:
        """Safe for API/logs: no login number."""
        return {
            "state": self.state.value,
            "fingerprint": (self.fingerprint or "")[:12],
            "server": self.server,
            "currency": self.currency,
            "margin_mode": {0: "NETTING", 1: "EXCHANGE", 2: "HEDGING"}.get(self.margin_mode, "UNKNOWN"),
            "hedging": self.hedging,
            "trade_allowed": self.trade_allowed,
            "reason": self.reason,
        }


def check_account(gw: ReadOnlyGateway) -> AccountCheck:
    try:
        info = gw.account_info()
        term = gw.terminal_info()
    except Exception as e:  # pragma: no cover - backend failure
        return AccountCheck(AccountState.DISCONNECTED, None, None, None, None, False, None, None, None, None, f"error: {e}")
    if info is None or term is None or not term.get("connected", False):
        return AccountCheck(AccountState.DISCONNECTED, None, None, None, None, False, None, None, None, None,
                            "terminal not connected or account_info() unavailable")
    mode = info.get("trade_mode")
    state = {
        gw.const("ACCOUNT_TRADE_MODE_DEMO"): AccountState.DEMO,
        gw.const("ACCOUNT_TRADE_MODE_REAL"): AccountState.LIVE,
        gw.const("ACCOUNT_TRADE_MODE_CONTEST"): AccountState.CONTEST,
    }.get(mode, AccountState.UNKNOWN)
    reason = "verified demo account" if state == AccountState.DEMO else f"trade_mode={mode} rejected (demo only)"
    mm = info.get("margin_mode")
    return AccountCheck(
        state=state,
        fingerprint=fingerprint(info.get("login"), info.get("server"), info.get("company")),
        server=info.get("server"),
        currency=info.get("currency"),
        margin_mode=mm,
        hedging=mm == gw.const("ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"),
        balance=info.get("balance"),
        equity=info.get("equity"),
        margin_free=info.get("margin_free"),
        trade_allowed=bool(info.get("trade_allowed")) and bool(term.get("trade_allowed", False)),
        reason=reason,
    )


def execution_blockers(check: AccountCheck, activation_fp: Optional[str]) -> list[str]:
    """Reasons automatic execution must not run. Empty list == allowed."""
    out = []
    if check.state != AccountState.DEMO:
        out.append(f"ACCOUNT_NOT_DEMO:{check.state.value}")
    if not check.hedging:
        out.append("ACCOUNT_NOT_HEDGING: independent per-bot positions cannot be guaranteed on netting/exchange accounts")
    if not check.trade_allowed:
        out.append("TRADING_DISABLED_IN_TERMINAL (enable Algo Trading in MT5)")
    if activation_fp is None:
        out.append("NOT_ARMED")
    elif check.fingerprint != activation_fp:
        out.append("ACCOUNT_CHANGED_SINCE_ACTIVATION")
    return out
