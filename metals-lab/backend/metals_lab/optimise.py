"""The Assessor's tuner: looks for settings that make a bot more money without a deeper worst drawdown, and checks
the result on history it never tuned on.

The bot's history is split by time into three parts:

- search (first 60%): every candidate change is backtested here against the bot as it currently stands;
- choose (next 20%): the changes that beat it on search must beat it here too; the best one is kept;
- final (last 20%): looked at once, at the end, to check the whole tuned bot against the original.

"Beats" means more net profit and a worst drawdown no deeper. Tuning runs in up to three rounds: each round searches
again on top of the changes kept so far. Re-using the choose period across rounds makes it less of a fair test, which
is why the final period exists: if the tuned bot does not beat the original there, it is reported as probably overfit
and not offered.

Candidates come from the trade review (cut a losing session or metal) and from trying the main settings in both
directions: wider and tighter stops, nearer and further targets, shorter and longer holds, the breakeven and
trailing-stop exits on and off, and adding back hours or a metal that were cut. Trying many variants still favours
luck; the number tried is reported with the result.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Callable, Optional

import pandas as pd

from .review import STOP_PARAMS, TARGET_PARAMS, metal_keys
from .strategies import bot_class, build
from .strategies.filters import ALL_DAY, METAL_PARAMS, parse_windows

SPLITS = (0.6, 0.8)       # search | choose | final, by time
ROUNDS = 3
MIN_CONFIRM_TRADES = 10   # fewer trades than this in a checking period: too few to call it confirmed
DD_TOLERANCE = 0.001      # worst drawdown may be this much deeper (0.1 percentage point) and still count as "no deeper"
MAX_CANDIDATES = 20

Metrics = dict  # {"net_pnl", "max_drawdown", "n_trades"}
RunFn = Callable[[dict, str, str], Metrics]  # (params, start YYYY-MM-DD, end YYYY-MM-DD inclusive) -> metrics
RunManyFn = Callable[[list[tuple[dict, str, str]]], list[Metrics]]


def split_periods(start: str, end: str) -> dict:
    """The three periods as {"search": (a, b), "choose": (a, b), "final": (a, b)}, inclusive days."""
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    c1, c2 = a + (b - a) * SPLITS[0], a + (b - a) * SPLITS[1]
    d = lambda t: str(t.date())
    nxt = lambda t: d(t + timedelta(days=1))
    return {"search": (d(a), d(c1)), "choose": (nxt(c1), d(c2)), "final": (nxt(c2), d(b))}


def _round(v: float) -> float:
    return round(float(v), 2)


def candidates(bot: str, params: dict, review_suggestions: list[dict]) -> list[dict]:
    """Changes worth testing, each {"id", "title", "change", "origin"}; the review's own suggestions first."""
    sid = bot[:3]
    cls = bot_class(bot)
    out: list[dict] = []

    def add(cid: str, title: str, change: dict, origin: str = "tried"):
        change = {k: v for k, v in change.items() if params.get(k) != v}
        if not change or any(c["change"] == change for c in out):
            return
        try:
            build(bot, {**params, **change})
        except Exception:
            return
        out.append({"id": cid, "title": title, "change": change, "origin": origin})

    for s in review_suggestions:
        if s.get("change"):
            add(s["id"], s["title"], s["change"], "review")
    who = lambda k: f"{k.split('_')[0].capitalize()}: " if k.startswith(("gold_", "silver_")) else ""
    sp = STOP_PARAMS.get(sid)
    for k in metal_keys(params, sp) if sp else []:  # each metal is tuned on its own
        v = float(params[k])
        add(f"{k}:wider", f"{who(k)}wider stop ({k} {v:g} -> {v * 1.25:.2f})", {k: round(v * 1.25, 2)})
        add(f"{k}:tighter", f"{who(k)}tighter stop ({k} {v:g} -> {v * 0.75:.2f})", {k: round(v * 0.75, 2)})
    tp = TARGET_PARAMS.get(sid)
    for k in metal_keys(params, tp) if tp else []:
        v = float(params[k])
        add(f"{k}:further", f"{who(k)}further target ({k} {v:g} -> {v + 0.5:g})", {k: round(v + 0.5, 2)})
        add(f"{k}:further2", f"{who(k)}much further target ({k} {v:g} -> {v + 1.0:g})", {k: round(v + 1.0, 2)})
        if v - 0.5 >= 1.0:
            add(f"{k}:nearer", f"{who(k)}nearer target ({k} {v:g} -> {v - 0.5:g})", {k: round(v - 0.5, 2)})
    mh = params.get("max_hold_bars")
    if mh:
        add("hold:longer", f"Hold longer (max_hold_bars {mh} -> {int(round(int(mh) * 1.5))})", {"max_hold_bars": int(round(int(mh) * 1.5))})
        add("hold:shorter", f"Hold shorter (max_hold_bars {mh} -> {max(2, int(round(int(mh) * 0.67)))})",
            {"max_hold_bars": max(2, int(round(int(mh) * 0.67)))})
    if "breakeven_r" in params:
        if float(params["breakeven_r"] or 0) == 0:
            add("exit:breakeven", "Move the stop to breakeven after +1R", {"breakeven_r": 1.0})
        else:
            add("exit:no_breakeven", "Stop moving the stop to breakeven", {"breakeven_r": 0.0})
    if "trail_atr" in params:
        if float(params["trail_atr"] or 0) == 0:
            add("exit:trail3", "Trail the stop 3 ATR behind the best price (no fixed target)", {"trail_atr": 3.0})
            add("exit:trail2", "Trail the stop 2 ATR behind the best price (no fixed target)", {"trail_atr": 2.0})
        else:
            add("exit:no_trail", "Stop trailing; use the fixed target again", {"trail_atr": 0.0})
    if not cls.pair_trades:  # add back what was cut
        for role, (on, win) in METAL_PARAMS.items():
            if params.get(on) == "off":
                add(f"metal:{role}:on", f"Trade {role.lower()} again", {on: "on"})
            elif parse_windows(params.get(win)) != parse_windows(ALL_DAY):
                add(f"windows:{role}:all", f"{role.capitalize()}: trade all day again", {win: ALL_DAY})
    if sid == "S11":
        mm = float(params.get("min_move") or 0)
        add("min_move:up", f"Only trade clearer first half hours (min_move {mm:g} -> {mm + 0.001:g})", {"min_move": round(mm + 0.001, 4)})
    return out[:MAX_CANDIDATES]


def _m(m: Metrics) -> dict:
    return {"net": _round(m.get("net_pnl") or 0.0), "dd": round(float(m.get("max_drawdown") or 0.0), 4),
            "n": int(m.get("n_trades") or 0)}


def beats(c: dict, b: dict) -> bool:
    """More net profit and a worst drawdown no deeper (drawdowns are negative fractions)."""
    return c["net"] > b["net"] and c["dd"] >= b["dd"] - DD_TOLERANCE


def _diff(base: dict, new: dict) -> dict:
    return {k: v for k, v in new.items() if base.get(k) != v}


def evaluate(bot: str, params: dict, review_suggestions: list[dict], start: str, end: str, run: Optional[RunFn] = None,
             progress: Optional[Callable[[str], None]] = None, run_many: Optional[RunManyFn] = None,
             rounds: int = ROUNDS) -> dict:
    """Tune the bot in up to ``rounds`` rounds as described above. ``run_many`` runs several backtests (in parallel);
    with only ``run`` given they run one after another."""
    many = run_many or (lambda jobs: [run(*j) for j in jobs])
    per = split_periods(start, end)
    say = progress or (lambda _m: None)
    current = dict(params)
    out_rounds, kept, tried = [], [], 0
    first_base = None
    for r in range(1, rounds + 1):
        cands = [c for c in candidates(bot, current, review_suggestions if r == 1 else []) if c["id"] not in {k["id"] for k in kept}]
        if not cands:
            break
        tried += len(cands)
        say(f"round {r} of {rounds}: testing {len(cands)} changes on the search period")
        res = many([(current, *per["search"])] + [({**current, **c["change"]}, *per["search"]) for c in cands])
        base_s, cand_s = _m(res[0]), [_m(x) for x in res[1:]]
        rows = [{**c, "search": m, "choose": None} for c, m in zip(cands, cand_s)]
        good = [x for x in rows if beats(x["search"], base_s)]
        base_c = None
        if good:
            say(f"round {r} of {rounds}: checking {len(good)} on the choose period")
            res = many([(current, *per["choose"])] + [({**current, **x["change"]}, *per["choose"]) for x in good])
            base_c = _m(res[0])
            for x, m in zip(good, res[1:]):
                x["choose"] = _m(m)
        if first_base is None:
            first_base = {"search": base_s, "choose": base_c}
        for x in rows:
            if x["choose"] is None:
                x["verdict"], x["gain"] = "no_help", None
            elif not beats(x["choose"], base_c):
                x["verdict"], x["gain"] = "noise", _round(x["choose"]["net"] - base_c["net"])
            elif min(x["choose"]["n"], base_c["n"]) < MIN_CONFIRM_TRADES:
                x["verdict"], x["gain"] = "unconfirmed", _round(x["choose"]["net"] - base_c["net"])
            else:
                x["verdict"], x["gain"] = "kept_candidate", _round(x["choose"]["net"] - base_c["net"])
        order = {"kept_candidate": 0, "unconfirmed": 1, "noise": 2, "no_help": 3}
        rows.sort(key=lambda x: (order[x["verdict"]], -(x["gain"] or 0)))
        best = rows[0] if rows and rows[0]["verdict"] == "kept_candidate" else None
        if best:
            best["verdict"] = "kept"
            current = {**current, **best["change"]}
            kept.append({"round": r, "id": best["id"], "title": best["title"], "change": best["change"],
                         "search": best["search"], "choose": best["choose"], "gain": best["gain"]})
        for x in rows:
            if x["verdict"] == "kept_candidate":
                x["verdict"] = "passed_not_best"
        out_rounds.append({"round": r, "baseline": {"search": base_s, "choose": base_c}, "candidates": rows,
                           "kept": best["id"] if best else None})
        if not best:
            break
    tuned = None
    if kept:
        say("final check on history the tuning never saw")
        res = many([(params, *per["final"]), (current, *per["final"])])
        of, tf = _m(res[0]), _m(res[1])
        if not beats(tf, of):
            verdict = "overfit"
        elif min(tf["n"], of["n"]) < MIN_CONFIRM_TRADES:
            verdict = "unconfirmed"
        else:
            verdict = "passed"
        last = kept[-1]
        tuned = {"change": _diff(params, current), "steps": kept, "verdict": verdict, "gain": _round(tf["net"] - of["net"]),
                 "original": {"search": first_base["search"], "choose": first_base["choose"], "final": of},
                 "tuned": {"search": last["search"], "choose": last["choose"], "final": tf}}
    return {"periods": per, "rounds": out_rounds, "tuned": tuned, "variants_tried": tried,
            "rule": "more net profit and a worst drawdown no deeper than the bot as it stands; search on the first 60% of "
                    "the history, keep the best on the next 20%, up to three rounds, then check the tuned bot once on the "
                    "last 20%"}


def period_of_backtest(row: dict) -> Optional[tuple[str, str]]:
    """First and last day of a finished backtest's equity curve."""
    try:
        eq = json.loads(row.get("equity_json") or "[]")
    except ValueError:
        return None
    if len(eq) < 2:
        return None
    return str(eq[0][0])[:10], str(eq[-1][0])[:10]
