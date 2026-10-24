"""Trade filters every bot shares: which side, which metal and which UTC hours it may open trades in.

Defaults come from the assessor's review of the one-year MT5 backtest (2025-10-23 to 2026-10-23). They
were found on that same data, so they are in-sample: check them on a different period before trusting them.
"""
from __future__ import annotations

from typing import Optional

from ..domain import Intent


def parse_hours(spec: Optional[str]) -> list[tuple[int, int]]:
    """'0-7,21-24' -> [(0, 7), (21, 24)]; '' / 'all' / None -> every hour."""
    s = (spec or "").strip().lower()
    if s in ("", "all", "any"):
        return [(0, 24)]
    out = []
    for part in s.split(","):
        a, b = (int(x) for x in part.strip().split("-"))
        if not (0 <= a < b <= 24):
            raise ValueError(f"bad hour range '{part}' (use e.g. 0-7,21-24 in UTC)")
        out.append((a, b))
    return out


def hour_allowed(hour: int, spec: Optional[str]) -> bool:
    return any(a <= hour < b for a, b in parse_hours(spec))


def side_allowed(intent: Intent, sides: str) -> bool:
    return sides in ("both", "") or (sides == "long" and intent == Intent.OPEN_LONG) or (sides == "short" and intent == Intent.OPEN_SHORT)


def metal_allowed(role: str, metals: str) -> bool:
    return metals in ("both", "") or metals.upper() == role


def validate(params: dict) -> None:
    if params.get("sides") not in ("both", "long", "short"):
        raise ValueError("sides must be both, long or short")
    if params.get("metals") not in ("both", "gold", "silver"):
        raise ValueError("metals must be both, gold or silver")
    parse_hours(params.get("hours_utc"))
