"""The assessor: reviews how each bot's trades turned out and keeps the latest review per bot and source.

Runs automatically when a demo trade closes (source "demo") and when a backtest finishes ("backtest:<id>").
It only reports and suggests; it never changes a bot.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

from . import review as R
from .store import now_iso
from .strategies import BOT_IDS, bot_class, build

STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2, None: 3}


def current_params(db, bot: str) -> dict:
    row = db.one("SELECT params_json FROM bots WHERE bot_id=?", (bot,))
    return build(bot, json.loads((row or {}).get("params_json") or "{}")).params


def review_demo(db, bot: str, coord=None) -> dict:
    trades = db.q("SELECT * FROM trades WHERE bot_id=? ORDER BY entry_time", (bot,))
    positions = {r["position_key"]: r for r in db.q("SELECT * FROM positions WHERE bot_id=?", (bot,))}
    df = R.from_demo(trades, positions)
    bars = {}
    if coord is not None and not df.empty:
        from .data.history import fetch_bars
        tf = bot_class(bot).primary_timeframe
        for role in set(df["role"]):
            if role in coord.specs:
                try:
                    with coord._lock:
                        bars[role] = fetch_bars(coord.ro, coord.clock, coord.specs[role], tf,
                                                df["entry_time"].min().to_pydatetime() - timedelta(hours=1),
                                                df["exit_time"].max().to_pydatetime() + timedelta(hours=1))
                except Exception:
                    pass
    out = R.review(bot, df, current_params(db, bot), bars or None)
    out["source"] = "forward demo trades"
    return out


def review_backtest(db, bot: str, backtest_id: int, datasets=None) -> dict:
    row = db.one("SELECT strategy_id, dataset_key, trades_json FROM backtests WHERE id=?", (backtest_id,))
    if not row or row["strategy_id"] != bot:
        raise KeyError("no such backtest for this bot")
    df = R.from_backtest(json.loads(row["trades_json"] or "[]"))
    bars = {}
    if datasets is not None and row["dataset_key"] != "synthetic":
        cls = bot_class(bot)
        for role in cls.roles:
            v = datasets.latest("MT5_TERMINAL", role, cls.primary_timeframe)
            if v:
                bars[role], _ = datasets.load(v)
    out = R.review(bot, df, current_params(db, bot), bars or None)
    out["source"] = f"backtest #{backtest_id} ({'synthetic data' if row['dataset_key'] == 'synthetic' else 'MT5 history'})"
    return out


def store_review(db, bot: str, source: str, trigger: str, rv: dict) -> int:
    rv = {k: v for k, v in rv.items() if k != "trades"}
    cur = db.x("INSERT INTO reviews(created_at,bot_id,source,trigger,review_json) VALUES(?,?,?,?,?)",
               (now_iso(), bot, source, trigger, json.dumps(rv, default=str)))
    db.event("ASSESSOR_REVIEW", bot, {"source": source, "trigger": trigger,
                                      "suggestions": sum(1 for s in rv.get("suggestions", []) if s.get("change") or s.get("rule_change"))})
    return cur.lastrowid


def latest(db, bot: str, source_prefix: str) -> Optional[dict]:
    r = db.one("SELECT * FROM reviews WHERE bot_id=? AND source LIKE ? ORDER BY id DESC LIMIT 1", (bot, source_prefix + "%"))
    if not r:
        return None
    return {"id": r["id"], "created_at": r["created_at"], "source": r["source"], "trigger": r["trigger"], **json.loads(r["review_json"])}


def grade(rv: Optional[dict]) -> dict:
    """keep / watch / retire / too early, from a review's stats."""
    st = (rv or {}).get("stats") or {}
    n = st.get("n", 0)
    if n < R.MIN_N:
        return {"grade": "too early", "why": f"{n} of {R.MIN_N} trades needed"}
    pf, exp_r = st.get("profit_factor"), st.get("expectancy_r")
    if exp_r is not None and exp_r < -0.1 and n >= 30:
        return {"grade": "retire", "why": f"{exp_r}R per trade over {n} trades"}
    if pf is not None and pf >= 1.1 and (exp_r or 0) > 0:
        return {"grade": "keep", "why": f"profit factor {pf}, {exp_r}R per trade"}
    return {"grade": "watch", "why": f"profit factor {pf}, {exp_r}R per trade over {n} trades"}


def suggestions_ranked(db) -> list[dict]:
    rows = []
    for b in BOT_IDS:
        for prefix in ("demo", "backtest"):
            rv = latest(db, b, prefix)
            if not rv:
                continue
            for s in rv.get("suggestions", []):
                if s.get("change") or s.get("rule_change"):
                    rows.append({**s, "bot_id": b, "source": rv["source"], "review_id": rv["id"], "reviewed_at": rv["created_at"]})
    return sorted(rows, key=lambda s: (STRENGTH_ORDER.get(s.get("strength"), 3), s["bot_id"]))
