"""Consistency ranking used to choose a bot for live trading.

A bot is eligible once it has at least ``min_trades`` closed trades and is profitable after costs.
Eligible bots are ranked by the smallest maximum drawdown (closed-trade equity), then by net profit.
This is a screening rule, not proof of edge: ten bots compared on one period still invites luck.
"""
from __future__ import annotations


def bot_stats(nets: list[float], allocation: float) -> dict:
    eq, peak, mdd = allocation, allocation, 0.0
    for n in nets:
        eq += n
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, (eq - peak) / peak)
    wins = sum(n for n in nets if n > 0)
    losses = -sum(n for n in nets if n < 0)
    return {
        "closed_trades": len(nets),
        "net": round(sum(nets), 2),
        "return_pct": round(sum(nets) / allocation * 100, 2) if allocation else None,
        "max_drawdown_pct": round(mdd * 100, 2),
        "win_rate_pct": round(100 * sum(1 for n in nets if n > 0) / len(nets), 1) if nets else None,
        "profit_factor": round(wins / losses, 2) if losses > 0 else (None if not wins else float("inf")),
    }


def rank(per_bot: dict[str, dict], min_trades: int = 50) -> list[dict]:
    """per_bot: {bot_id: {"nets": [...], "allocation": float, ...extra}} -> rows sorted best first."""
    rows = []
    for bot, d in per_bot.items():
        s = bot_stats(d["nets"], d["allocation"])
        why = []
        if s["closed_trades"] < min_trades:
            why.append(f"{s['closed_trades']}/{min_trades} trades")
        if s["net"] <= 0:
            why.append("not profitable after costs")
        rows.append({"bot_id": bot, **{k: v for k, v in d.items() if k != "nets"}, **s, "eligible": not why,
                     "not_eligible_because": why})
    elig = sorted((r for r in rows if r["eligible"]), key=lambda r: (-r["max_drawdown_pct"], -r["net"]))
    rest = sorted((r for r in rows if not r["eligible"]), key=lambda r: (-r["closed_trades"], -r["net"]))
    for i, r in enumerate(elig, 1):
        r["rank"] = i
    for r in rest:
        r["rank"] = None
    return elig + rest
