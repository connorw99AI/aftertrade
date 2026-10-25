"""One plain sentence per bot for the Lab cards, written from the checklist the bot already computes.

Never decorative: every phrase comes from a real check, status or open trade. Trading windows are UK time; trade times are UTC.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ..strategies.filters import uk_minute

METAL = {"GOLD": "gold", "SILVER": "silver"}
# checks about the clock rather than the market; when one fails the bot is simply off duty
TIME_CHECK = re.compile(r"session|trading window|trading hours|liquid hours|^before |cut-off|range complete|friday|evaluation bar", re.I)


def is_time_check(label: str) -> bool:
    return bool(TIME_CHECK.search(label or ""))


def allowed_roles(roles: dict, metals: str) -> list[str]:
    return [r for r in roles if metals in (None, "", "both") or r.lower() == metals]


def readiness(role_view: dict, side: str) -> Optional[dict]:
    """How many of this bot's own entry conditions are met on one metal: every filter plus its side's setup checks."""
    if not role_view or "waiting" in role_view or not role_view.get("filters"):
        return None
    own = role_view.get("long" if side == "L" else "short") or []
    # the "trades allowed" line is always true for the bot's own side; it carries no information
    own = [c for c in own if "trades allowed" not in c.get("label", "").lower()]
    checks = [c for c in role_view["filters"] if not c.get("label", "").lower().startswith("metal allowed")] + own
    met = sum(1 for c in checks if c.get("ok"))
    return {"met": met, "total": len(checks), "setup_met": sum(1 for c in own if c.get("ok")), "setup_total": len(own)}


def _hhmm(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def next_open(intervals: list, now: datetime) -> Optional[str]:
    """Start (UK time) of the next trading window today, or None when there is none left today."""
    m = uk_minute(now)
    later = [a for a, b in intervals if a > m]
    return _hhmm(min(later)) if later else None


def in_window(intervals: list, now: datetime) -> bool:
    m = uk_minute(now)
    return any(a <= m < b for a, b in intervals)


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def thought(*, side: str, status: str, kill: bool, stopped_for_day: Optional[str], opens: list[dict],
            th: Optional[dict], metals: str, window: Optional[dict], now: datetime,
            pause_reason: Optional[str] = None, cooldown_until: Optional[datetime] = None) -> str:
    long = side == "L"
    if kill:
        return "The kill switch is on, so I can't open anything."
    if opens:
        p = opens[0]
        role = METAL.get(p.get("role"), p.get("symbol", ""))
        since = str(p.get("opened_at") or "")[11:16]
        extra = f" and {len(opens) - 1} more" if len(opens) > 1 else ""
        stop = f" Stop {p['sl']:.{3 if role == 'silver' else 2}f}" if p.get("sl") else " No stop set"
        tgt = f", target {p['tp']:.{3 if role == 'silver' else 2}f}." if p.get("tp") else "."
        return f"In a {role} {p.get('side', '').lower()} since {since} UTC{extra}.{stop}{tgt}"
    if status == "RETIRED":
        return "Retired. I keep my balance until you bring me back."
    if status == "STOPPED":
        return "Stopped. Press Start and I'll begin watching."
    if status == "PAUSED_RISK":
        if pause_reason and "in a row" in pause_reason:
            return f"Paused after {pause_reason}. Have a look at what went wrong, then press Start."
        return "Paused after falling to the drawdown limit. Press Start to restart me."
    lead = "Paused by you. " if status == "PAUSED" else ""
    if stopped_for_day:
        why = {"profit lock": "the profit lock kept the rest of today's gain"}.get(stopped_for_day, f"{stopped_for_day} reached")
        return f"{lead}{'Done for the week' if stopped_for_day == 'weekly loss stop' else 'Done for today'}: {why}."
    if cooldown_until and now < cooldown_until:
        return f"{lead}Cooling down after a loss until {cooldown_until:%H:%M} UTC."
    if metals == "none":
        return f"{lead}Gold and silver are both switched off in my settings."
    roles = (th or {}).get("roles") or {}
    views = {r: roles[r] for r in allowed_roles(roles, metals) if roles[r] and "waiting" not in roles[r]}
    if not views:
        return f"{lead}Waiting for my first {(th or {}).get('timeframe') or ''} bar to close.".replace("  ", " ")
    intervals = (window or {}).get("intervals")
    if intervals is not None and not in_window(intervals, now):
        nxt = next_open(intervals, now)
        return f"{lead}Off duty. " + (f"My window opens at {nxt} UK time." if nxt else "No more trading windows today.")
    # the metal closest to an entry speaks for the bot
    best = max(views, key=lambda r: ((readiness(views[r], side) or {}).get("met", 0) / max(1, (readiness(views[r], side) or {}).get("total", 1))))
    v, metal = views[best], METAL[best]
    if str(v.get("verdict", "")).startswith("ENTRY"):
        return f"{lead}Entry signal on {metal}. It goes through sizing, the auditor and risk next."
    time_fail = next((c for c in v["filters"] if not c.get("ok") and is_time_check(c.get("label", ""))), None)
    if time_fail:
        nxt = next_open(((window or {}).get(best.lower()) or {}).get("intervals") or [], now)
        until = f" until {nxt} UK time" if nxt and "trading window" in time_fail["label"] else ""
        return f"{lead}Off duty on {metal}{until}. Failing check: {_lower_first(time_fail['label'])}."
    fail = next((c for c in v["filters"] if not c.get("ok") and not c.get("label", "").lower().startswith("metal allowed")), None)
    if fail:
        detail = f", {fail['detail']}" if fail.get("detail") else ""
        return f"{lead}Holding off on {metal}. Failing check: {_lower_first(fail['label'])}{detail}."
    rd = readiness(v, side) or {}
    own = [c for c in (v.get("long" if long else "short") or []) if "trades allowed" not in c.get("label", "").lower()]
    missing = [c for c in own if not c.get("ok")]
    word = "long" if long else "short"
    if not missing:
        return f"{lead}Everything lines up for a {metal} {word}; waiting for the bar to close."
    return (f"{lead}{rd.get('setup_met', 0)} of {rd.get('setup_total', 0)} conditions for a {metal} {word}; "
            f"missing: {_lower_first(missing[0]['label'])}.")


def next_up(*, side: str, status: str, kill: bool, stopped_for_day: Optional[str], opens: list[dict], th: Optional[dict],
            metals: str, window: Optional[dict], now: datetime, cooldown_until: Optional[datetime] = None) -> Optional[dict]:
    """The metal this bot is closest to entering on, if it is close: every filter passes (so it is inside that metal's
    window) and at most one of its own entry conditions is missing, or it has an entry signal on the latest bar."""
    if kill or status != "RUNNING" or opens or stopped_for_day or (cooldown_until and now < cooldown_until):
        return None
    if window and window.get("intervals") is not None and not in_window(window["intervals"], now):
        return None
    roles = (th or {}).get("roles") or {}
    best = None
    for r in allowed_roles(roles, metals):
        v = roles[r]
        if not v or "waiting" in v:
            continue
        signal = str(v.get("verdict", "")).startswith("ENTRY")
        filters = [c for c in v.get("filters") or [] if not c.get("label", "").lower().startswith("metal allowed")]
        own = [c for c in (v.get("long" if side == "L" else "short") or []) if "trades allowed" not in c.get("label", "").lower()]
        missing = [c["label"] for c in own if not c.get("ok")]
        if not signal and (any(not c.get("ok") for c in filters) or len(missing) > 1):
            continue
        cand = {"metal": METAL[r], "role": r, "signal": signal, "met": len(own) - len(missing), "total": len(own),
                "missing": missing, "as_of": (th or {}).get("bar_time")}
        if best is None or (cand["signal"], -len(cand["missing"])) > (best["signal"], -len(best["missing"])):
            best = cand
    return best
