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
    """Merge a tuned change into the bot's settings (paused and flat, audited like any settings change) and record it.
    Only the tuned change from the bot's latest assessment that passed the final check can be applied."""
    if not change:
        raise ValueError("this suggestion has no settings change to apply")
    t = latest_test(db, bot)
    tuned = ((t or {}).get("result") or {}).get("tuned") if t and t["status"] == "DONE" else None
    if not (tuned and tuned["verdict"] == "passed" and suggestion_id == f"test:tuned:{t['id']}" and tuned["change"] == change):
        raise ValueError("only a tuned change that passed the Assessor's final check (more profit, no deeper worst drawdown, "
                         "on history it never tuned on) can be applied; press Assess first")
    if t["stale"]:
        raise ValueError("this bot's settings changed after the assessment ran; assess again before applying")
    params = {**current_params(db, bot), **change}
    row = coord.update_params(bot, params, operator)
    db.x("INSERT INTO applied_suggestions(bot_id,suggestion_id,change_json,review_id,source,config_version,operator,applied_at) "
         "VALUES(?,?,?,?,?,?,?,?)", (bot, suggestion_id, _change_key(change), review_id, source, row["config_version"], operator, now_iso()))
    db.event("SUGGESTION_APPLIED", bot, {"suggestion": suggestion_id, "change": change, "config_version": row["config_version"]})
    return row


def _cleared_at(db) -> str:
    return db.get_control("assessor:suggestions_cleared_at") or ""


def latest_test(db, bot: str) -> Optional[dict]:
    """The newest assessment of a bot (optimise.py), with ``stale`` when the bot's settings changed since it ran."""
    r = db.one("SELECT * FROM optimisations WHERE bot_id=? AND created_at>? ORDER BY id DESC LIMIT 1", (bot, _cleared_at(db)))
    if not r:
        return None
    cur = db.one("SELECT config_version FROM bots WHERE bot_id=?", (bot,))
    out = {k: r[k] for k in ("id", "bot_id", "created_at", "finished_at", "status", "progress", "trigger", "dataset", "start", "end",
                             "config_version", "error")}
    out["result"] = json.loads(r["result_json"]) if r["result_json"] else None
    if out["result"]:
        sent = {x["title"] for x in db.q("SELECT title FROM change_requests WHERE bot_id=?", (bot,))}
        for f in out["result"].get("failures") or []:
            f["rule"] = FAILURE_RULES.get(f["code"], "find and fix the cause in the strategy's rules")
            f["sent"] = f["title"] in sent
    out["stale"] = bool(cur and r["config_version"] is not None and cur["config_version"] != r["config_version"])
    return out


def _money(v: float) -> str:
    return f"{v:+,.2f}"


def tuned_suggestion(t: dict) -> dict:
    """The tuned change of an assessment as one suggestion card, with the numbers for all three periods."""
    res = t["result"]
    tu, per = res["tuned"], res["periods"]
    o, n = tu["original"], tu["tuned"]
    steps = "; ".join(f"round {x['round']}: {x['title']}" for x in tu["steps"])
    ev = (f"Final check on {per['final'][0]} to {per['final'][1]} (never used for tuning): net {_money(o['final']['net'])} -> "
          f"{_money(n['final']['net'])}, worst drawdown {o['final']['dd']:.1%} -> {n['final']['dd']:.1%}. Steps: {steps}. "
          f"{res['variants_tried']} variants tried, so expect less than this.")
    return {"id": f"test:tuned:{t['id']}", "title": "Tuned settings: " + ", ".join(x["title"] for x in tu["steps"]),
            "evidence": ev, "kind": "parameter", "change": tu["change"], "strength": None, "confidence": None,
            "rule_change": None, "gain": tu["gain"], "verdict": tu["verdict"],
            "tested": {"test_id": t["id"], "periods": per, "original": o, "tuned": n, "steps": tu["steps"],
                       "verdict": tu["verdict"], "stale": t["stale"]}}


def suggestions_for(db, b: str) -> list[dict]:
    """One bot's suggestions: the tuned change if it passed the final check, then rule ideas (need code).
    Settings ideas from the trade review are tuned automatically, so on their own they are listed as untested."""
    t = latest_test(db, b)
    done = t and t["status"] == "DONE" and t["result"]
    rows = []
    if done and (t["result"].get("tuned") or {}).get("verdict") == "passed":
        rows.append({**tuned_suggestion(t), "bot_id": b, "source": f"assessed on {t['dataset']} history", "review_id": None,
                     "reviewed_at": t["finished_at"]})
    cleared = _cleared_at(db)
    for prefix in ("demo", "assessment"):
        rv = latest(db, b, prefix)
        if not rv or rv["created_at"] <= cleared:
            continue
        for s in rv.get("suggestions", []):
            if s.get("rule_change"):
                rows.append({**s, "bot_id": b, "source": rv["source"], "review_id": rv["id"], "reviewed_at": rv["created_at"]})
            elif s.get("change") and not done:
                rows.append({**s, "bot_id": b, "source": rv["source"], "review_id": rv["id"], "reviewed_at": rv["created_at"],
                             "untested": True})
    return rows


def clear_suggestions(db, operator: str) -> dict:
    """Hide every open suggestion and finished assessment so new tests start from a clean list. Applied records stay."""
    n = db.x("DELETE FROM optimisations WHERE status IN ('DONE','FAILED')").rowcount
    db.set_control("assessor:suggestions_cleared_at", now_iso())
    db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
         (None, "ASSESSOR_CLEAR_SUGGESTIONS", None, json.dumps({"assessments_removed": n}), operator, now_iso()))
    return {"assessments_removed": n}


def clear_history(db, operator: str) -> dict:
    """Remove everything the Assessor stored: reviews, assessments, applied-suggestion records, trade notes and change
    requests. Backtests, trades, bot settings and the settings audit log are kept. Running assessments are left alone."""
    out = {"reviews": db.x("DELETE FROM reviews").rowcount,
           "assessments": db.x("DELETE FROM optimisations WHERE status IN ('DONE','FAILED')").rowcount,
           "applied_records": db.x("DELETE FROM applied_suggestions").rowcount,
           "trade_notes": db.x("DELETE FROM trade_assessments").rowcount,
           "change_requests": db.x("DELETE FROM change_requests").rowcount}
    db.x("DELETE FROM control WHERE key='assessor:suggestions_cleared_at'")
    db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
         (None, "ASSESSOR_CLEAR_HISTORY", None, json.dumps(out), operator, now_iso()))
    return out


# ------------------------------------------------------------- the Engineer
def _request_spec(bot: str, title: str, rule: str, evidence: str, oid: Optional[int]) -> str:
    from .strategies import bot_class
    cls = bot_class(bot)
    return "\n".join([
        f"# Metal Lab change request: {bot} {cls.name}",
        "",
        f"**Problem:** {title}",
        f"**Evidence:** {evidence}",
        f"**Proposed rule:** {rule}",
        "",
        "## How to build it",
        f"- Strategy file: `backend/metals_lab/strategies/` class `{cls.__name__}` (strategy id {cls.strategy_id}).",
        "- Add it as a new setting that is OFF by default, so the bot behaves exactly as now until it is switched on.",
        "- Same code path for live and backtests; no live orders in tests.",
        "",
        "## How to test it",
        "- Unit test: the rule fires on a constructed case and does not fire otherwise.",
        "- Full test suite passes.",
        f"- Then press Assess for {bot}: the Assessor tunes the new setting with the others and only offers it if it adds "
        "net profit with no deeper worst drawdown, including on the final 20% of history it never tuned on.",
        "" if oid is None else f"\n(Raised by assessment #{oid}.)",
    ])


FAILURE_RULES = {"no_edge": "redesign the entry idea, or retire this bot",
                 "costs": "aim for larger moves: a slower chart, a wider target, or skip the widest-spread hours",
                 "one_year": "add a regime filter so the bot only trades in the conditions of its good year"}


def send_to_engineer(db, bot: str, title: str, rule: str, evidence: str, operator: str, oid: Optional[int] = None) -> int:
    """The operator sends one failure or rule idea to the Engineer. An open request with the same title is reused."""
    row = db.one("SELECT id FROM change_requests WHERE bot_id=? AND title=? AND status='OPEN'", (bot, title))
    if row:
        return row["id"]
    cur = db.x("INSERT INTO change_requests(bot_id,created_at,status,title,rule,evidence,spec_md,assessment_id,operator) "
               "VALUES(?,?,?,?,?,?,?,?,?)", (bot, now_iso(), "OPEN", title, rule, evidence,
                                             _request_spec(bot, title, rule, evidence, oid), oid, operator))
    return cur.lastrowid


def change_requests(db, status: Optional[str] = None) -> list[dict]:
    q = "SELECT * FROM change_requests" + (" WHERE status=?" if status else "") + " ORDER BY id DESC"
    return db.q(q, (status,) if status else ())


def suggestions_ranked(db) -> list[dict]:
    rows = []
    for b in BOT_IDS:
        rows += suggestions_for(db, b)
    rows = [x for b in BOT_IDS for x in mark_applied(db, b, [r for r in rows if r["bot_id"] == b])]
    # the tuned change first (largest final-check gain first), then rule ideas and untested settings ideas; applied last
    return sorted(rows, key=lambda s: (bool(s.get("applied")), not s.get("tested"), -(s.get("gain") or 0),
                                       STRENGTH_ORDER.get(s.get("strength"), 3), s["bot_id"]))


# --------------------------------------------------------- one note per trade
VERDICTS = {"good": "Good trade", "unlucky": "Unlucky", "poor_setup": "Poor setup", "execution": "Execution problem",
            "scratch": "Scratch"}


def assess_trade(trade: dict, positions: list[dict], bars: Optional[dict] = None) -> dict:
    """The Assessor's note on one closed demo/live trade: why it opened and closed, the result in R, how far it went for
    and against, slippage against the planned price, whether the rules were followed, and a verdict."""
    p = positions[0] if positions else {}
    try:
        sig = json.loads(p.get("signal_json") or "{}")
    except ValueError:
        sig = {}
    side = trade.get("side") or p.get("side")
    long = side == "LONG"
    entry, exit_ = trade.get("entry_price"), trade.get("exit_price")
    stop0 = sig.get("stop_price") if sig.get("stop_price") is not None else p.get("sl")
    dist = abs(entry - stop0) if entry is not None and stop0 is not None else None
    risk = sum(float(x.get("planned_risk") or 0) for x in positions) or None
    net = float(trade.get("net") or 0.0)
    r = round(net / risk, 2) if risk else None
    best = worst = None
    b = (bars or {}).get(p.get("role"))
    if b is not None and not b.empty and dist and trade.get("entry_time") and trade.get("exit_time"):
        import pandas as pd
        t0, t1 = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
        w = b[(b["time"] >= t0.floor("min") - pd.Timedelta(minutes=1)) & (b["time"] < t1)]
        if not w.empty:
            hi, lo = float(w["high"].max()), float(w["low"].min())
            best = round(((hi - entry) if long else (entry - lo)) / dist, 2)
            worst = round(((entry - lo) if long else (hi - entry)) / dist, 2)
    ref = sig.get("ref_price")
    slip = slip_r = None
    if ref is not None and entry is not None:
        slip = (entry - ref) if long else (ref - entry)            # positive = filled worse than planned
        slip_r = round(slip / dist, 2) if dist else None
    exit_kind = R.classify_live_exit(trade.get("exit_rationale"), side, exit_, p.get("sl"), p.get("tp"))
    rules = []
    if not p.get("sl"):
        rules.append("traded without a stop")
    if exit_kind == "MANUAL":
        rules.append("closed by hand, not by the bot's rules")
    if (slip_r or 0) > 0.25:
        rules.append(f"filled {slip_r}R worse than planned")
    if rules and ("traded without a stop" in rules or (slip_r or 0) > 0.25):
        verdict = "execution"
    elif r is not None and r >= 0.3:
        verdict = "good"
    elif r is not None and r <= -0.3 and best is not None and best >= 1.0:
        verdict = "unlucky"
    elif r is not None and r <= -0.3 and (best is None or best < 0.3):
        verdict = "poor_setup"
    else:
        verdict = "scratch"
    why = {"good": "the setup worked and the exit kept the gain",
           "unlucky": f"it went {best}R its way first, then reversed to the stop; breakeven or trailing exits are tested for this",
           "poor_setup": "it never went its way: the entry signal was wrong this time",
           "execution": "the fill or the protection was not what the plan assumed",
           "scratch": "a small result either way"}[verdict]
    return {"verdict": verdict, "label": VERDICTS[verdict], "summary": f"{VERDICTS[verdict]}: {why}.",
            "entry_reason": sig.get("rationale") or trade.get("entry_rationale"), "exit": exit_kind,
            "exit_reason": trade.get("exit_rationale"), "r": r, "best_r": best, "worst_r": worst,
            "slippage": round(slip, 5) if slip is not None else None, "slippage_r": slip_r, "rule_issues": rules}


def assess_trades(db, trade_ids: list[str], coord=None) -> int:
    """Write a note for each closed trade (replacing an older one when the trade's figures were finalised)."""
    n = 0
    for tid in trade_ids:
        t = db.one("SELECT * FROM trades WHERE trade_id=?", (tid,))
        if not t:
            continue
        keys = json.loads(t.get("position_keys_json") or "[]")
        pos = [x for k in keys for x in db.q("SELECT * FROM positions WHERE position_key=?", (k,))]
        bars = {}
        if coord is not None and pos and t.get("entry_time") and t.get("exit_time"):
            from datetime import datetime
            from .data.history import fetch_bars
            role = pos[0]["role"]
            if role in getattr(coord, "specs", {}):
                try:
                    with coord._lock:
                        bars[role] = fetch_bars(coord.ro, coord.clock, coord.specs[role], bot_class(t["bot_id"]).primary_timeframe,
                                                datetime.fromisoformat(t["entry_time"]) - timedelta(minutes=15),
                                                datetime.fromisoformat(t["exit_time"]) + timedelta(minutes=15))
                except Exception:
                    pass
        note = assess_trade(t, pos, bars)
        db.x("INSERT OR REPLACE INTO trade_assessments(trade_id,bot_id,created_at,verdict,note_json) VALUES(?,?,?,?,?)",
             (tid, t["bot_id"], now_iso(), note["verdict"], json.dumps(note, default=str)))
        n += 1
    return n


def trade_notes(db, trade_ids: list[str]) -> dict:
    if not trade_ids:
        return {}
    marks = ",".join("?" * len(trade_ids))
    return {r["trade_id"]: json.loads(r["note_json"]) for r in
            db.q(f"SELECT trade_id, note_json FROM trade_assessments WHERE trade_id IN ({marks})", tuple(trade_ids))}
