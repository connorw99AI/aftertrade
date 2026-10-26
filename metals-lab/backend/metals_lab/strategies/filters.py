"""Trade filters every bot shares: which side, which metals, and each metal's permitted trading windows.

Windows are UK time (Europe/London, so they follow BST/GMT), written like ``06:00-11:00, 13:00-17:00``.
``00:00-24:00`` is all day; a window may cross midnight (``22:00-02:00``). Hours the market is shut are
blocked anyway, so an all-day window just means "whenever the market is open".
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from ..domain import Intent

UK = ZoneInfo("Europe/London")
ALL_DAY = "00:00-24:00"
DAY = 24 * 60
METAL_PARAMS = {"GOLD": ("trade_gold", "gold_windows"), "SILVER": ("trade_silver", "silver_windows")}
FILTER_PARAMS = ("sides", "trade_gold", "trade_silver", "gold_windows", "silver_windows")
LEGACY_PARAMS = ("metals", "hours_utc")  # replaced by the per-metal switches and windows; dropped when still saved

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def _minute(t: str, part: str) -> int:
    m = _HHMM.match(t.strip())
    if not m:
        raise ValueError(f"bad time '{t.strip()}' in '{part}' (use HH:MM, e.g. 06:00-11:00)")
    h, mi = int(m.group(1)), int(m.group(2))
    if mi > 59 or h > 24 or (h == 24 and mi):
        raise ValueError(f"bad time '{t.strip()}' in '{part}'")
    return h * 60 + mi


def parse_windows(spec: Optional[str]) -> list[tuple[int, int]]:
    """'06:00-11:00, 22:00-02:00' -> merged minute intervals within one day [(0, 120), (360, 660), (1320, 1440)].
    '' / 'all' / None -> all day. A window that crosses midnight is split at midnight."""
    s = (spec or "").strip().lower()
    if s in ("", "all", "any"):
        return [(0, DAY)]
    raw = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if part.count("-") != 1:
            raise ValueError(f"bad window '{part}' (use e.g. 06:00-11:00)")
        a, b = (_minute(x, part) for x in part.split("-"))
        if a == b:
            raise ValueError(f"window '{part}' is empty")
        raw += [(a, b)] if a < b else [(a, DAY), (0, b)]
    raw = [(a, b) for a, b in raw if a < b]
    if not raw:
        raise ValueError("no trading window given (use 00:00-24:00 for all day)")
    return merge(raw)


def merge(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def hhmm(minute: int) -> str:
    return "24:00" if minute >= DAY else f"{minute // 60:02d}:{minute % 60:02d}"


def format_windows(iv: list[tuple[int, int]]) -> str:
    iv = merge(iv)
    if iv == [(0, DAY)]:
        return ALL_DAY
    # a window running to midnight and one from midnight read as one window across midnight
    if len(iv) > 1 and iv[0][0] == 0 and iv[-1][1] == DAY:
        iv = iv[1:-1] + [(iv[-1][0], iv[0][1])]
    return ", ".join(f"{hhmm(a)}-{hhmm(b)}" for a, b in iv)


def uk_minute(now: datetime) -> int:
    t = now.astimezone(UK)
    return t.hour * 60 + t.minute


def in_windows(now: datetime, spec: Optional[str]) -> bool:
    m = uk_minute(now)
    return any(a <= m < b for a, b in parse_windows(spec))


def windows_minus(spec: Optional[str], a: int, b: int) -> Optional[str]:
    """Remove UK minutes [a, b) from a windows spec; None if nothing changes or nothing would be left."""
    have = parse_windows(spec)
    left = []
    for lo, hi in have:
        if hi <= a or lo >= b:
            left.append((lo, hi))
            continue
        if lo < a:
            left.append((lo, a))
        if hi > b:
            left.append((b, hi))
    if not left or left == have:
        return None
    return format_windows(left)


def side_allowed(intent: Intent, sides: str) -> bool:
    return sides in ("both", "") or (sides == "long" and intent == Intent.OPEN_LONG) or (sides == "short" and intent == Intent.OPEN_SHORT)


def metal_on(params: dict, role: str) -> bool:
    on, _ = METAL_PARAMS[role]
    return params.get(on, "on") == "on"


def metal_open(params: dict, role: str, now: datetime) -> bool:
    """This metal is switched on and ``now`` is inside its windows."""
    return metal_on(params, role) and in_windows(now, params.get(METAL_PARAMS[role][1]))


def metals_text(params: dict) -> str:
    """'both', 'gold', 'silver' or 'none', for display and for picking which metals a bot watches."""
    g, s = metal_on(params, "GOLD"), metal_on(params, "SILVER")
    return "both" if g and s else ("gold" if g else ("silver" if s else "none"))


def validate(params: dict) -> None:
    if params.get("sides") not in ("both", "long", "short"):
        raise ValueError("sides must be both, long or short")
    for on, win in METAL_PARAMS.values():
        if params.get(on) not in ("on", "off"):
            raise ValueError(f"{on} must be on or off")
        try:
            parse_windows(params.get(win))
        except ValueError as e:
            raise ValueError(f"{win}: {e}") from None
