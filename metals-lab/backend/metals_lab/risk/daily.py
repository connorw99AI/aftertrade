"""The daily stops, in one place so live trading, the backtester and the dashboard agree.

- Loss stop: no new entries for the rest of the UTC day once the bot is down ``daily_loss_pause`` on the day.
- Profit lock: once the bot has been up at least ``daily_lock_from`` on the day, it stops for the day if it gives back
  ``daily_giveback`` of the day's best gain (0.5 = half). Three wins then losses: it stops with about half the gain kept.
- Profit stop: optional, stops after gaining ``daily_profit_stop`` (0 = off).
Open trades always stay managed; these only block new entries.
"""
from __future__ import annotations

from typing import Optional

TEXT = {"DAILY_LOSS_LIMIT": "daily loss stop", "DAILY_PROFIT_LOCK": "profit lock",
        "DAILY_PROFIT_TARGET_REACHED": "daily profit stop"}


def daily_stop(day_start: float, day_high: Optional[float], equity: float, risk: dict) -> Optional[str]:
    """Which daily stop, if any, applies now. ``risk`` is the live risk config (or anything with the same keys)."""
    if not day_start or day_start <= 0:
        return None
    day = (equity - day_start) / day_start
    if day <= -float(risk.get("daily_loss_pause") or 0) and risk.get("daily_loss_pause"):
        return "DAILY_LOSS_LIMIT"
    profit_stop = float(risk.get("daily_profit_stop") or 0)
    if profit_stop and day >= profit_stop:
        return "DAILY_PROFIT_TARGET_REACHED"
    give = float(risk.get("daily_giveback") or 0)
    best = ((day_high if day_high is not None else equity) - day_start) / day_start
    if give and best >= float(risk.get("daily_lock_from") or 0) and best > 0 and day <= best * (1 - give):
        return "DAILY_PROFIT_LOCK"
    return None
