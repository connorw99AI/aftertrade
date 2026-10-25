"""The Assessor's tester: a change is only suggested if it makes the bot more money without a deeper worst drawdown,
both on the period it was found on and on a later period it was not found on.

For each bot it backtests the bot as it is (the baseline) and with each candidate change, on the same history:

- the history is split by time: the first 70% is the "found on" period, the last 30% the "confirm on" period;
- a candidate passes the found-on period when net profit is higher and the worst drawdown is no deeper than the baseline's;
- only those are run on the confirm-on period, where they must pass the same test again;
- passed changes are ranked by how much net profit they added on the confirm-on period.

Candidates come from the trade review (cutting losing sessions or a losing metal) and from trying the main settings in
both directions: wider and tighter stops, nearer and further targets, shorter and longer holds, the breakeven and
trailing-stop exits on and off, and adding back hours or a metal that were cut. Trying many variants and keeping the
best still favours luck; the number of variants tried is shown with the result.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Callable, Optional

import pandas as pd

from .review import STOP_PARAMS, TARGET_PARAMS
from .strategies import bot_class, build
from .strategies.filters import ALL_DAY, METAL_PARAMS, parse_windows

SPLIT = 0.7               # share of the history the changes are found on; the rest confirms them
MIN_CONFIRM_TRADES = 10   # fewer trades than this in the confirm-on period: too few to call it confirmed
DD_TOLERANCE = 0.001      # worst drawdown may be this much deeper (0.1 percentage point) and still count as "no deeper"
MAX_CANDIDATES = 14

Metrics = dict  # {"net_pnl", "max_drawdown", "n_trades"}
RunFn = Callable[[dict, str, str], Metrics]  # (params, start YYYY-MM-DD, end YYYY-MM-DD inclusive) -> metrics


def split_period(start: str, end: str) -> tuple[str, str, str]:
    """(start, last day found-on, end) with the found-on period ending at SPLIT of the range."""
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    cut = a + (b - a) * SPLIT
    return str(a.date()), str(cut.date()), str(b.date())


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
    sp = STOP_PARAMS.get(sid)
    if sp and params.get(sp):
        v = float(params[sp])
        add(f"{sp}:wider", f"Wider stop ({sp} {v:g} -> {v * 1.25:.2f})", {sp: round(v * 1.25, 2)})
        add(f"{sp}:tighter", f"Tighter stop ({sp} {v:g} -> {v * 0.75:.2f})", {sp: round(v * 0.75, 2)})
    tp = TARGET_PARAMS.get(sid)
    if tp and params.get(tp):
        v = float(params[tp])
        add(f"{tp}:further", f"Further target ({tp} {v:g} -> {v + 0.5:g})", {tp: round(v + 0.5, 2)})
        add(f"{tp}:further2", f"Much further target ({tp} {v:g} -> {v + 1.0:g})", {tp: round(v + 1.0, 2)})
        if v - 0.5 >= 1.0:
            add(f"{tp}:nearer", f"Nearer target ({tp} {v:g} -> {v - 0.5:g})", {tp: round(v - 0.5, 2)})
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


def evaluate(bot: str, params: dict, review_suggestions: list[dict], start: str, end: str, run: RunFn,
             progress: Optional[Callable[[str], None]] = None) -> dict:
    """Backtest the baseline and every candidate as described above. ``run`` performs one backtest."""
    a, cut, b = split_period(start, end)
    after = str((pd.Timestamp(cut) + timedelta(days=1)).date())
    cands = candidates(bot, params, review_suggestions)
    say = progress or (lambda _m: None)
    total = 2 + len(cands)
    say(f"baseline (1 of {total})")
    base_in, base_out = _m(run(params, a, cut)), None
    results = []
    for i, c in enumerate(cands):
        say(f"{c['title']} ({i + 2} of {total})")
        r_in = _m(run({**params, **c["change"]}, a, cut))
        res = {**c, "in": r_in, "out": None}
        if beats(r_in, base_in):
            if base_out is None:
                base_out = _m(run(params, after, b))
            r_out = _m(run({**params, **c["change"]}, after, b))
            res["out"] = r_out
            if not beats(r_out, base_out):
                res["verdict"] = "noise"        # helped where it was found, not on the later period
            elif min(r_out["n"], base_out["n"]) < MIN_CONFIRM_TRADES:
                res["verdict"] = "unconfirmed"  # helped on both, but too few later trades to trust it
            else:
                res["verdict"] = "passed"
            res["gain"] = _round(r_out["net"] - base_out["net"])
        else:
            res["verdict"] = "no_help"
            res["gain"] = None
        results.append(res)
    order = {"passed": 0, "unconfirmed": 1, "noise": 2, "no_help": 3}
    results.sort(key=lambda r: (order[r["verdict"]], -(r["gain"] or 0), -(r["in"]["net"] - base_in["net"])))
    return {"period": {"start": a, "found_end": cut, "confirm_start": after, "end": b},
            "baseline": {"in": base_in, "out": base_out}, "candidates": results, "variants_tried": len(cands),
            "rule": "more net profit and a worst drawdown no deeper than now, on the found-on period and again on the "
                    f"later confirm-on period ({int(SPLIT * 100)}% / {100 - int(SPLIT * 100)}% of the history)"}


def period_of_backtest(row: dict) -> Optional[tuple[str, str]]:
    """First and last day of a finished backtest's equity curve."""
    try:
        eq = json.loads(row.get("equity_json") or "[]")
    except ValueError:
        return None
    if len(eq) < 2:
        return None
    return str(eq[0][0])[:10], str(eq[-1][0])[:10]
