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


def _change_key(change: dict) -> str:
    return json.dumps(change, sort_keys=True, default=str)


def mark_applied(db, bot: str, suggestions: list[dict]) -> list[dict]:
    """Add ``applied`` ({config_version, applied_at, operator}) to each suggestion the operator already applied.
    A suggestion counts as the same one when its bot, id and exact change match; a different change shows again."""
    done = {(r["suggestion_id"], r["change_json"]): r for r in
            db.q("SELECT * FROM applied_suggestions WHERE bot_id=? ORDER BY id", (bot,))}
    out = []
    for s in suggestions:
        r = done.get((s.get("id"), _change_key(s["change"]))) if s.get("change") else None
        out.append({**s, "applied": {"config_version": r["config_version"], "applied_at": r["applied_at"],
                                     "operator": r["operator"]} if r else None})
    return out


def apply_suggestion(db, coord, bot: str, suggestion_id: str, change: dict, operator: str,
                     review_id: Optional[int] = None, source: Optional[str] = None) -> dict:
    """Merge a suggestion's change into the bot's settings (paused and flat, audited like any settings change) and
    record it as applied."""
    if not change:
        raise ValueError("this suggestion has no settings change to apply")
    t = latest_test(db, bot)
    ok = (t and t["status"] == "DONE" and t["result"] and suggestion_id.startswith("test:") and
          any(c["verdict"] == "passed" and c["change"] == change and f"test:{c['id']}" == suggestion_id for c in t["result"]["candidates"]))
    if not ok:
        raise ValueError("only changes that passed the Assessor's test (more profit, no deeper worst drawdown, confirmed on later "
                         "history) can be applied; press Test now first")
    if t["stale"]:
        raise ValueError("this bot's settings changed after the test ran; test again before applying")
    params = {**current_params(db, bot), **change}
    row = coord.update_params(bot, params, operator)
    db.x("INSERT INTO applied_suggestions(bot_id,suggestion_id,change_json,review_id,source,config_version,operator,applied_at) "
         "VALUES(?,?,?,?,?,?,?,?)", (bot, suggestion_id, _change_key(change), review_id, source, row["config_version"], operator, now_iso()))
    db.event("SUGGESTION_APPLIED", bot, {"suggestion": suggestion_id, "change": change, "config_version": row["config_version"]})
    return row


def latest_test(db, bot: str) -> Optional[dict]:
    """The newest change test for a bot (optimise.py), with ``stale`` when the bot's settings changed since it ran."""
    r = db.one("SELECT * FROM optimisations WHERE bot_id=? ORDER BY id DESC LIMIT 1", (bot,))
    if not r:
        return None
    cur = db.one("SELECT config_version FROM bots WHERE bot_id=?", (bot,))
    out = {k: r[k] for k in ("id", "bot_id", "created_at", "finished_at", "status", "progress", "trigger", "dataset", "start", "end",
                             "config_version", "error")}
    out["result"] = json.loads(r["result_json"]) if r["result_json"] else None
    out["stale"] = bool(cur and r["config_version"] is not None and cur["config_version"] != r["config_version"])
    return out


def _money(v: float) -> str:
    return f"{v:+,.2f}"


def tested_suggestion(t: dict, c: dict) -> dict:
    """A passed change from a test, as a suggestion card with its numbers."""
    res = t["result"]
    bi, bo, ci, co = res["baseline"]["in"], res["baseline"]["out"], c["in"], c["out"]
    p = res["period"]
    ev = (f"Found on {p['start']} to {p['found_end']}: net {_money(bi['net'])} -> {_money(ci['net'])}, worst drawdown "
          f"{bi['dd']:.1%} -> {ci['dd']:.1%} ({bi['n']} -> {ci['n']} trades). Confirmed on {p['confirm_start']} to {p['end']}: "
          f"net {_money(bo['net'])} -> {_money(co['net'])}, worst drawdown {bo['dd']:.1%} -> {co['dd']:.1%} "
          f"({bo['n']} -> {co['n']} trades). Best of {res['variants_tried']} variants tried, so expect less than this.")
    return {"id": f"test:{c['id']}", "title": c["title"], "evidence": ev, "kind": "parameter", "change": c["change"],
            "strength": None, "confidence": None, "rule_change": None, "gain": c.get("gain"), "verdict": c["verdict"],
            "tested": {"test_id": t["id"], "baseline": res["baseline"], "candidate": {"in": ci, "out": co}, "period": p,
                       "stale": t["stale"]}}


def suggestions_for(db, b: str) -> list[dict]:
    """One bot's suggestions: changes that passed the profit-and-drawdown test, then rule ideas (untested, need code).
    Review settings changes that have not been through a test yet are listed as untested and cannot be applied."""
    t = latest_test(db, b)
    tested = t and t["status"] == "DONE" and t["result"]
    rows = []
    if tested:
        rows += [{**tested_suggestion(t, c), "bot_id": b, "source": f"tested on {t['dataset']} history", "review_id": None,
                  "reviewed_at": t["finished_at"]} for c in t["result"]["candidates"] if c["verdict"] == "passed"]
    for prefix in ("demo", "backtest"):
        rv = latest(db, b, prefix)
        if not rv:
            continue
        for s in rv.get("suggestions", []):
            if s.get("rule_change"):
                rows.append({**s, "bot_id": b, "source": rv["source"], "review_id": rv["id"], "reviewed_at": rv["created_at"]})
            elif s.get("change") and not tested:
                rows.append({**s, "bot_id": b, "source": rv["source"], "review_id": rv["id"], "reviewed_at": rv["created_at"],
                             "untested": True})
    return rows


def suggestions_ranked(db) -> list[dict]:
    rows = []
    for b in BOT_IDS:
        rows += suggestions_for(db, b)
    rows = [x for b in BOT_IDS for x in mark_applied(db, b, [r for r in rows if r["bot_id"] == b])]
    # open tested changes first (largest confirmed gain first), then untested ideas; applied ones last
    return sorted(rows, key=lambda s: (bool(s.get("applied")), not s.get("tested"), -(s.get("gain") or 0),
                                       STRENGTH_ORDER.get(s.get("strength"), 3), s["bot_id"]))
