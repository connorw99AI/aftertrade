"""Trade review: assess the outcomes of a bot's closed trades and suggest specific, testable changes.

Works on forward-demo trades or on the trades of a backtest. It never changes a strategy by itself:
a suggestion is either a parameter change the operator can backtest and then apply (paused and flat,
audited like any parameter change), or a described rule change that needs code.

Honesty rules built in:
- every finding states its sample size; fewer than ``MIN_N`` trades in a bucket is not used for a suggestion;
- suggestions from the same trades they are tested on are optimistic, so the test runs on downloaded
  MT5 history and the UI says which period it used;
- many small tweaks found this way will be noise (multiple testing); the review says so.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

MIN_N = 10

STOP_PARAMS = {"S01": "stop_atr", "S02": "stop_atr", "S05": "stop_atr", "S06": "stop_atr", "S07": "stop_atr",
               "S08": "stop_n", "S09": "stop_atr", "S10": "leg_stop_atr"}
TARGET_PARAMS = {"S01": "reward_risk", "S03": "reward_risk", "S05": "reward_risk", "S06": "reward_risk", "S04": "tp_range_mult"}
HOUR_PARAMS = {"S01": ("start_hour", "end_hour"), "S02": ("entry_start_hour", "entry_end_hour")}

SESSIONS = (("Asia 00-07", 0, 7), ("London 07-12", 7, 12), ("London/NY 12-16", 12, 16), ("New York 16-21", 16, 21), ("Late 21-24", 21, 24))


# ----------------------------------------------------------------- normalise
def classify_live_exit(rationale: str, side: str, exit_price: float, sl: Optional[float], tp: Optional[float]) -> str:
    r = (rationale or "").lower()
    if "manual close" in r or "kill switch" in r:
        return "MANUAL"
    if "weekend" in r:
        return "WEEKEND"
    if "max holding" in r:
        return "TIME"
    if "server-side stop/target" in r or "[sl" in r or "[tp" in r:
        if sl is not None and tp is not None and exit_price is not None:
            return "STOP_LOSS" if abs(exit_price - sl) <= abs(exit_price - tp) else "TAKE_PROFIT"
        return "STOP_LOSS" if sl is not None else "TAKE_PROFIT"
    return "SIGNAL_EXIT"


def from_demo(trades: list[dict], positions: dict[str, dict]) -> pd.DataFrame:
    """Live trades table rows + their positions (for stop/target/planned risk) -> common frame."""
    import json
    rows = []
    for t in trades:
        keys = json.loads(t.get("position_keys_json") or "[]")
        p = positions.get(keys[0], {}) if keys else {}
        risk = sum(float(positions.get(k, {}).get("planned_risk") or 0) for k in keys)
        rows.append({"trade_id": t["trade_id"], "role": p.get("role") or ("SILVER" if "XAG" in (t.get("symbol") or "") else "GOLD"),
                     "side": t["side"], "entry_time": t["entry_time"], "exit_time": t["exit_time"],
                     "entry_price": t["entry_price"], "exit_price": t["exit_price"], "stop": p.get("sl"), "target": p.get("tp"),
                     "net": t["net"], "gross": t["gross"], "costs": (t.get("commission") or 0) + (t.get("swap") or 0) + (t.get("fee") or 0),
                     "planned_risk": risk or None,
                     "exit_reason": classify_live_exit(t.get("exit_rationale"), t["side"], t["exit_price"], p.get("sl"), p.get("tp"))})
    return _finish(pd.DataFrame(rows))


def from_backtest(trades: list[dict]) -> pd.DataFrame:
    rows = [{"trade_id": t["trade_id"], "role": t["role"], "side": t["side"], "entry_time": t["entry_time"], "exit_time": t["exit_time"],
             "entry_price": t["entry_price"], "exit_price": t["exit_price"], "stop": t.get("stop"), "target": t.get("target"),
             "net": t["net_pnl"], "gross": t["gross_pnl"],
             "costs": -abs(t.get("spread_slip_cost") or 0) + (t.get("commission") or 0) + (t.get("swap") or 0),
             "planned_risk": t.get("planned_risk") or None,
             "exit_reason": {"SIGNAL_EXIT": "SIGNAL_EXIT", "STOP_LOSS": "STOP_LOSS", "TAKE_PROFIT": "TAKE_PROFIT"}.get(
                 t.get("exit_reason"), "TIME" if "holding" in str(t.get("exit_rationale", "")) else
                 ("WEEKEND" if "weekend" in str(t.get("exit_rationale", "")) else str(t.get("exit_reason"))))}
            for t in trades]
    return _finish(pd.DataFrame(rows))


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, format="mixed")
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True, format="mixed")
    df["r"] = np.where(df["planned_risk"].fillna(0) > 0, df["net"] / df["planned_risk"].replace(0, np.nan), np.nan)
    df["hour"] = df["entry_time"].dt.hour
    df["session"] = df["hour"].map(lambda h: next(n for n, a, b in SESSIONS if a <= h < b))
    df["weekday"] = df["entry_time"].dt.day_name()
    df["minutes_held"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 60
    return df.sort_values("entry_time").reset_index(drop=True)


def add_excursions(df: pd.DataFrame, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Maximum favourable / adverse excursion of each trade, in price and in R (multiples of the stop distance),
    from the bot's own-timeframe bars (high/low). Missing bars leave NaN."""
    if df.empty:
        return df
    mfe, mae = [], []
    for t in df.itertuples():
        b = bars.get(t.role)
        if b is None or b.empty:
            mfe.append(np.nan)
            mae.append(np.nan)
            continue
        w = b[(b["time"] >= t.entry_time.floor("min") - pd.Timedelta(minutes=1)) & (b["time"] < t.exit_time)]
        if w.empty:
            mfe.append(np.nan)
            mae.append(np.nan)
            continue
        hi, lo = float(w["high"].max()), float(w["low"].min())
        if t.side == "LONG":
            mfe.append(max(0.0, hi - t.entry_price))
            mae.append(max(0.0, t.entry_price - lo))
        else:
            mfe.append(max(0.0, t.entry_price - lo))
            mae.append(max(0.0, hi - t.entry_price))
    df = df.copy()
    df["mfe"], df["mae"] = mfe, mae
    dist = (df["entry_price"] - df["stop"]).abs().where(df["stop"].notna())
    df["mfe_r"] = df["mfe"] / dist
    df["mae_r"] = df["mae"] / dist
    return df


# ------------------------------------------------------------------- stats
def _group(df: pd.DataFrame, col: str) -> list[dict]:
    out = []
    for k, g in df.groupby(col, sort=False):
        out.append({"key": str(k), "n": int(len(g)), "net": round(float(g["net"].sum()), 2),
                    "avg_net": round(float(g["net"].mean()), 2), "win_rate": round(float((g["net"] > 0).mean() * 100), 1),
                    "avg_r": _r(g["r"].mean())})
    return sorted(out, key=lambda x: -x["n"])


def _r(v) -> Optional[float]:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 2)


def summarise(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    wins, losses = df[df["net"] > 0], df[df["net"] < 0]
    gw, gl = float(wins["net"].sum()), float(-losses["net"].sum())
    gross_abs = float(df["gross"].abs().sum()) or 1.0
    out = {
        "n": int(len(df)), "net": round(float(df["net"].sum()), 2), "win_rate": round(float((df["net"] > 0).mean() * 100), 1),
        "avg_win": round(float(wins["net"].mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses["net"].mean()), 2) if len(losses) else None,
        "expectancy": round(float(df["net"].mean()), 2), "expectancy_r": _r(df["r"].mean()),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "cost_share_pct": round(abs(float(df["costs"].sum())) / gross_abs * 100, 1),
        "avg_minutes_win": round(float(wins["minutes_held"].mean()), 1) if len(wins) else None,
        "avg_minutes_loss": round(float(losses["minutes_held"].mean()), 1) if len(losses) else None,
        "by_exit": _group(df, "exit_reason"), "by_side": _group(df, "side"), "by_metal": _group(df, "role"),
        "by_session": _group(df, "session"), "by_weekday": _group(df, "weekday"),
    }
    if "mfe_r" in df and df["mfe_r"].notna().any():
        stopped = df[df["exit_reason"] == "STOP_LOSS"]
        out["excursions"] = {
            "avg_mfe_r_losers": _r(losses["mfe_r"].mean()), "avg_mae_r_winners": _r(wins["mae_r"].mean()),
            "stopped_after_reaching_1r_pct": round(float((stopped["mfe_r"] >= 1.0).mean() * 100), 1) if len(stopped) else None,
            "winners_near_stop_pct": round(float((wins["mae_r"] >= 0.8).mean() * 100), 1) if len(wins) else None,
        }
    return out


# ------------------------------------------------------------- suggestions
def suggest(bot: str, df: pd.DataFrame, stats: dict, params: dict) -> list[dict]:
    out: list[dict] = []
    n = stats.get("n", 0)
    if n < MIN_N:
        return [{"id": "sample", "title": f"Only {n} closed trades: too few to judge", "kind": "info",
                 "evidence": f"Suggestions start at {MIN_N} trades and are only reliable well beyond 30.", "change": None}]
    conf = "low" if n < 30 else ("medium" if n < 100 else "higher")

    def add(sid, title, evidence, change=None, rule=None):
        out.append({"id": sid, "title": title, "evidence": evidence, "confidence": conf,
                     "kind": "parameter" if change else ("rule" if rule else "info"), "change": change, "rule_change": rule})

    total = stats["expectancy"]
    # one side, metal or session losing while the rest wins
    for col, label, rule_fmt in (("by_side", "side", "trade {other} only"), ("by_metal", "metal", "stop trading {key}"),
                                 ("by_session", "session", "skip the {key} session")):
        groups = [g for g in stats[col] if g["n"] >= MIN_N]
        bad = [g for g in groups if g["net"] < 0]
        good = [g for g in groups if g["net"] > 0]
        for g in bad:
            if good:
                other = ", ".join(x["key"] for x in good)
                change = None
                if col == "by_session" and bot in HOUR_PARAMS:
                    a, b = next((s, e) for name, s, e in SESSIONS if name == g["key"])
                    hp = HOUR_PARAMS[bot]
                    cur = (params.get(hp[0]), params.get(hp[1]))
                    if cur[0] is not None and cur[1] is not None and cur[0] < b and a < cur[1]:
                        if a <= cur[0] < b:
                            change = {hp[0]: b}
                        elif a < cur[1] <= b:
                            change = {hp[1]: a}
                add(f"{col}:{g['key']}", f"{g['key']} loses while {other} wins",
                    f"{g['key']}: {g['n']} trades, net {g['net']:+.2f}, win rate {g['win_rate']}%. Rest: net "
                    f"{sum(x['net'] for x in good):+.2f}.", change=change,
                    rule=None if change else rule_fmt.format(key=g["key"], other=other))
    ex = stats.get("excursions") or {}
    sp = STOP_PARAMS.get(bot)
    # stops too tight: winners often came close to the stop, or many stopped trades first went 1R our way
    if ex.get("winners_near_stop_pct") is not None and ex["winners_near_stop_pct"] >= 40 and sp and params.get(sp):
        add("stop_tight", "Stop looks tight: many winners nearly hit it first",
            f"{ex['winners_near_stop_pct']}% of winning trades went at least 0.8R against you before winning.",
            change={sp: round(float(params[sp]) * 1.25, 2)})
    if ex.get("stopped_after_reaching_1r_pct") is not None and ex["stopped_after_reaching_1r_pct"] >= 30:
        tp = TARGET_PARAMS.get(bot)
        losers_mfe = ex.get("avg_mfe_r_losers")
        if tp and params.get(tp) and float(params[tp]) > 1.0:
            add("give_back_target", "Trades often reach 1R and then get stopped: target may be too far",
                f"{ex['stopped_after_reaching_1r_pct']}% of stopped trades were at least 1R in profit first "
                f"(losers' average best point {losers_mfe}R).", change={tp: max(1.0, round(float(params[tp]) - 0.5, 2))})
        else:
            add("give_back_rule", "Trades often reach 1R and then get stopped",
                f"{ex['stopped_after_reaching_1r_pct']}% of stopped trades were at least 1R in profit first.",
                rule="move the stop to breakeven after +1R, or take partial profit at +1R")
    # time exits
    tex = next((g for g in stats["by_exit"] if g["key"] == "TIME"), None)
    mh = params.get("max_hold_bars")
    if tex and tex["n"] >= MIN_N and mh:
        if tex["net"] < 0:
            add("time_exit_losing", "Trades closed by the time limit lose money",
                f"{tex['n']} time exits, net {tex['net']:+.2f}: trades that have not worked by then rarely recover.",
                change={"max_hold_bars": max(2, int(round(int(mh) * 0.67)))})
        elif tex["avg_r"] is not None and tex["avg_r"] > 0.3:
            add("time_exit_winning", "Trades closed by the time limit were still winning",
                f"{tex['n']} time exits averaged {tex['avg_r']}R: the limit may cut winners short.",
                change={"max_hold_bars": int(round(int(mh) * 1.5))})
    # costs
    if stats["cost_share_pct"] >= 30:
        add("costs", "Costs eat a large share of the moves",
            f"Spread, commission and swap equal {stats['cost_share_pct']}% of gross P&L.",
            rule="trade a slower timeframe, aim for larger targets, or avoid the widest-spread hours")
    if total < 0 and not out:
        add("no_edge", "Losing overall with no single obvious cause",
            f"Expectancy {total:+.2f} per trade over {n} trades, spread across sides, metals and sessions.",
            rule="the entry idea itself may have no edge here; consider retiring or redesigning this bot rather than tuning")
    out.append({"id": "caveat", "kind": "info", "title": "How to use these", "change": None, "confidence": None,
                "evidence": "Each suggestion is a hypothesis. Test it on downloaded history before applying; most small tweaks "
                            "found by looking at past trades will not hold up (multiple testing)."})
    return out


def review(bot: str, df: pd.DataFrame, params: dict, bars: Optional[dict] = None) -> dict:
    if bars:
        df = add_excursions(df, bars)
    stats = summarise(df)
    return {"bot_id": bot, "stats": stats, "suggestions": suggest(bot, df, stats, params) if stats.get("n") else
            [{"id": "sample", "title": "No closed trades yet", "kind": "info", "evidence": "", "change": None}],
            "trades": [] if df.empty else df.assign(entry_time=df["entry_time"].astype(str), exit_time=df["exit_time"].astype(str))
            .replace({np.nan: None}).to_dict("records")}
