"""Per-bot guard rules ported from the Metal Lab MT5 risk guard (MetalLabRiskGuard.mqh), applied to every bot.

The MQL5 guard protects one account from inside an expert advisor. Here each bot is guarded on its own
balance by the risk engine (live) and the backtester, with the same functions so both agree:

- weekly loss limit (from the bot's balance at the start of the UTC week; holds for the rest of the week)
- losing-streak pause (after N losses in a row the bot pauses until you press Start)
- cooldown after a loss (no new entry for M minutes after a losing trade)
- lot caps per trade and for all of a bot's open trades together
- optional daily headroom: today's loss plus this trade's risk at its stop must fit inside the daily loss stop
- close on breach: when a loss limit trips, the bot's own open trades are closed (other bots are not touched)

Rules the guard has that the lab already enforced are not repeated here: required stop, open-position and
entries-per-day caps, spread and session limits, Friday close, daily stops (risk/daily.py), drawdown pause, margin
floor, kill switch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

LOSS_LIMITS = ("DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "MAX_DRAWDOWN_LIMIT", "LOSS_STREAK_LIMIT")


def week_start(t: datetime) -> str:
    """Monday (UTC) of the week ``t`` falls in, as YYYY-MM-DD."""
    t = t.astimezone(timezone.utc)
    return (t.date() - timedelta(days=t.weekday())).isoformat()


def weekly_stop(week_start_equity: float, equity: float, risk: dict) -> Optional[str]:
    lim = float(risk.get("weekly_loss_pause") or 0)
    if not lim or not week_start_equity or week_start_equity <= 0:
        return None
    return "WEEKLY_LOSS_LIMIT" if (equity - week_start_equity) / week_start_equity <= -lim else None


def loss_streak(nets_newest_first: Iterable[float]) -> int:
    """Losing trades in a row, counting back from the most recent closed trade."""
    n = 0
    for net in nets_newest_first:
        if net is None or net >= 0:
            break
        n += 1
    return n


def in_cooldown(last_loss_at: Optional[datetime], now: datetime, risk: dict) -> bool:
    mins = float(risk.get("cooldown_minutes_after_loss") or 0)
    return bool(mins and last_loss_at and now < last_loss_at + timedelta(minutes=mins))


def lot_cap(role: str, risk: dict) -> float:
    """Largest single trade allowed on this metal (0 = no cap)."""
    c = risk.get("max_lots_per_trade") or 0
    return float(c.get(role, 0) if isinstance(c, dict) else c or 0)


def entry_blocks(*, risk: dict, now: datetime, role: str, volume: float, bot_open_lots: float, streak: int,
                 last_loss_at: Optional[datetime], week_stopped: Optional[str], day_loss_pct: float,
                 planned_risk_pct: Optional[float]) -> list[str]:
    """Every guard rule that blocks this new entry, as reason codes (empty = allowed)."""
    out = []
    if week_stopped:
        out.append(week_stopped)
    mx = int(risk.get("max_consecutive_losses") or 0)
    if mx and streak >= mx:
        out.append("LOSS_STREAK_LIMIT")
    elif streak > 0 and in_cooldown(last_loss_at, now, risk):
        out.append("LOSS_COOLDOWN")
    cap = lot_cap(role, risk)
    if cap and volume > cap + 1e-9:
        out.append("LOTS_ABOVE_TRADE_CAP")
    total = float(risk.get("max_lots_per_bot") or 0)
    if total and bot_open_lots + volume > total + 1e-9:
        out.append("LOTS_ABOVE_BOT_CAP")
    daily = float(risk.get("daily_loss_pause") or 0)
    if risk.get("daily_headroom_check") and daily and planned_risk_pct is not None and day_loss_pct + planned_risk_pct > daily:
        out.append("DAILY_HEADROOM")
    return out
