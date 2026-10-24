#!/usr/bin/env python3
"""Assessor review of live or demo bot performance.

Reads the deal CSV written by assets/mql5/ExportDeals.mq5, groups closed
positions by bot (magic number), and flags what needs a human look. Optionally
summarises the guard log written by MetalLabRiskGuard.mqh.

  python3 review.py --deals MetalLab_deals.csv --config metal_lab.json \
      --balance 5000 [--since 2026.10.01] [--guard-log MetalLab_guard_log.csv] [--json out.json]

Verdicts are KEEP / WATCH / PAUSE. KEEP means "no flags raised", not "this bot
has an edge". Standard library only; a fixed seed makes the bootstrap repeatable.
"""
import argparse
import csv
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

TIME_FORMATS = ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d", "%Y-%m-%d")


def parse_time(text):
    text = text.strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("unrecognised time %r" % text)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- loading

def load_positions(path, since=None):
    """Closed positions from a deal CSV. Net = profit + commission + swap + fee over ALL deals of the position."""
    by_pos = defaultdict(list)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if (row.get("type") or "").strip() not in ("0", "1"):
                continue                      # skip balance/credit rows
            by_pos[(row.get("position_id") or "").strip()].append(row)

    out = []
    skipped_open = 0
    for pid, rows in by_pos.items():
        ins = [r for r in rows if r["entry"].strip() == "0"]
        outs = [r for r in rows if r["entry"].strip() in ("1", "2", "3")]
        if not ins or not outs:
            skipped_open += 1
            continue
        vol_in = sum(num(r["volume"]) for r in ins)
        vol_out = sum(num(r["volume"]) for r in outs)
        if vol_out < vol_in - 1e-9:
            skipped_open += 1                 # partly open
            continue
        opened = min(parse_time(r["time"]) for r in ins)
        closed = max(parse_time(r["time"]) for r in outs)
        if since and closed < since:
            continue
        gross = sum(num(r["profit"]) for r in rows)
        costs = sum(num(r["commission"]) + num(r["swap"]) + num(r["fee"]) for r in rows)
        out.append({
            "position_id": pid,
            "symbol": ins[0]["symbol"],
            "magic": (ins[0].get("magic") or "0").strip(),
            "opened": opened, "closed": closed,
            "volume": vol_in,
            "gross": gross, "costs": costs, "net": gross + costs,
        })
    out.sort(key=lambda p: p["closed"])
    return out, skipped_open


# ---------------------------------------------------------------- metrics

def bootstrap_ci(values, seed, reps=2000):
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(reps))
    return means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def drawdown(nets, balance):
    cum = peak = 0.0
    max_abs = max_pct = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        dd = peak - cum
        max_abs = max(max_abs, dd)
        if balance:
            max_pct = max(max_pct, dd / (balance + peak) * 100.0)
    return max_abs, (max_pct if balance else None)


def streaks(nets):
    longest = cur = 0
    for x in nets:
        cur = cur + 1 if x < 0 else 0
        longest = max(longest, cur)
    return longest, cur          # longest, and the losing run the bot is in right now


def dd_breach_probability(nets, balance, dd_limit_pct, horizon=100, reps=2000, seed=1):
    """Share of resampled futures (next `horizon` trades) whose drawdown reaches the limit.
    Assumes trades are independent draws from the past: a rough guide, not a forecast."""
    if len(nets) < 30 or not balance:
        return None
    rng = random.Random(seed)
    limit = balance * dd_limit_pct / 100.0
    hits = 0
    for _ in range(reps):
        cum = peak = 0.0
        for _ in range(horizon):
            cum += rng.choice(nets)
            peak = max(peak, cum)
            if peak - cum >= limit:
                hits += 1
                break
    return hits / reps


def metrics(pos, balance, seed):
    nets = [p["net"] for p in pos]
    n = len(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gp, gl = sum(wins), -sum(losses)
    total = sum(nets)
    lo, hi = bootstrap_ci(nets, seed)
    dd_abs, dd_pct = drawdown(nets, balance)
    longest, current = streaks(nets)
    top3 = sum(sorted(nets, reverse=True)[:3])
    costs = -sum(p["costs"] for p in pos)          # positive = money paid away
    holds = [(p["closed"] - p["opened"]).total_seconds() / 60.0 for p in pos]
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else (math.inf if gp > 0 else 0.0),
        "net": total,
        "expectancy": total / n if n else 0.0,
        "ci_low": lo, "ci_high": hi,
        "avg_win": gp / len(wins) if wins else 0.0,
        "avg_loss": -gl / len(losses) if losses else 0.0,
        "max_dd_abs": dd_abs, "max_dd_pct": dd_pct,
        "longest_loss_streak": longest, "current_loss_streak": current,
        "costs_paid": costs,
        "cost_share_of_gross_profit": (costs / gp) if gp > 0 else None,
        "top3_share_of_net": (top3 / total) if total > 0 else None,
        "avg_hold_minutes": sum(holds) / n if n else 0.0,
        "first": pos[0]["opened"] if pos else None,
        "last": pos[-1]["closed"] if pos else None,
    }


def verdict(m, a, bot_cfg, max_consec):
    """(KEEP|WATCH|PAUSE, [reasons])."""
    pause, watch = [], []
    n = m["trades"]
    if n < a["min_trades"]:
        watch.append("only %d closed trades (need %d before the numbers mean much)" % (n, a["min_trades"]))
    else:
        if m["ci_high"] is not None and m["ci_high"] < 0:
            pause.append("expectancy is negative with confidence: 95%% CI %.2f to %.2f per trade"
                         % (m["ci_low"], m["ci_high"]))
        elif m["ci_low"] is not None and m["ci_low"] <= 0:
            watch.append("no demonstrated edge yet: 95%% CI for expectancy %.2f to %.2f spans zero"
                         % (m["ci_low"], m["ci_high"]))
    if max_consec and m["current_loss_streak"] >= max_consec:
        pause.append("currently on %d straight losses (cap %d)" % (m["current_loss_streak"], max_consec))
    if m["max_dd_pct"] is not None and m["max_dd_pct"] >= a["bot_drawdown_pause_pct"]:
        pause.append("bot drawdown %.1f%% reached the %.1f%% pause line" % (m["max_dd_pct"], a["bot_drawdown_pause_pct"]))
    cs = m["cost_share_of_gross_profit"]
    if cs is not None and cs > a["max_cost_share_of_gross_profit"]:
        watch.append("costs take %.0f%% of gross profit (spread/commission/swap eat the edge)" % (cs * 100))
    t3 = m["top3_share_of_net"]
    if n >= a["min_trades"] and t3 is not None and t3 > a["top3_concentration"]:
        watch.append("top 3 trades are %.0f%% of net profit: results hang on a few outliers" % (t3 * 100))
    if n >= a["min_trades"] and bot_cfg:
        epf = bot_cfg.get("expected_pf")
        if epf and m["profit_factor"] < epf * a["pf_drift_ratio"]:
            watch.append("profit factor %.2f is far below the expected %.2f" % (m["profit_factor"], epf))
        ewr = bot_cfg.get("expected_win_rate")
        if ewr is not None and abs(m["win_rate"] - ewr) > a["win_rate_drift_points"]:
            watch.append("win rate %.0f%% vs expected %.0f%%" % (m["win_rate"] * 100, ewr * 100))
    if pause:
        return "PAUSE", pause + watch
    if watch:
        return "WATCH", watch
    return "KEEP", ["no flags raised (this is not evidence of a durable edge)"]


# ---------------------------------------------------------------- guard log

def summarise_guard_log(path):
    events = Counter()
    rejects = Counter()
    halts = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ev = (row.get("event") or "").strip()
            events[ev] += 1
            detail = (row.get("detail") or "").strip()
            if ev == "REJECT":
                rejects[re.sub(r"\d+(\.\d+)?", "#", detail.split(" (")[0])[:60]] += 1   # group "spread 55" and "spread 60" together
            if ev == "HALT":
                halts.append("%s  %s" % (row.get("time"), detail))
    return events, rejects, halts


# ---------------------------------------------------------------- output

def fmt_pf(x):
    return "inf" if x == math.inf else "%.2f" % x


def print_block(title, m, v, reasons, dd_prob=None):
    print("=" * 72)
    print("%s   ->   %s" % (title, v))
    print("-" * 72)
    ci = "n/a" if m["ci_low"] is None else "%.2f to %.2f" % (m["ci_low"], m["ci_high"])
    print("trades %d   win rate %.0f%%   profit factor %s   net %.2f"
          % (m["trades"], m["win_rate"] * 100, fmt_pf(m["profit_factor"]), m["net"]))
    print("expectancy/trade %.2f   95%% CI %s   avg win %.2f   avg loss %.2f"
          % (m["expectancy"], ci, m["avg_win"], m["avg_loss"]))
    dd = "%.2f" % m["max_dd_abs"] + ("  (%.1f%%)" % m["max_dd_pct"] if m["max_dd_pct"] is not None else "")
    print("max drawdown %s   longest loss streak %d (now %d)   avg hold %.0f min"
          % (dd, m["longest_loss_streak"], m["current_loss_streak"], m["avg_hold_minutes"]))
    print("costs paid %.2f%s" % (m["costs_paid"],
          "  (%.0f%% of gross profit)" % (m["cost_share_of_gross_profit"] * 100)
          if m["cost_share_of_gross_profit"] is not None else ""))
    if dd_prob is not None:
        print("resampled next-100-trades chance of hitting the drawdown limit: %.0f%% (rough, assumes independent trades)"
              % (dd_prob * 100))
    for r in reasons:
        print("  - " + r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deals", required=True)
    ap.add_argument("--config", help="metal_lab.json (bot names, assessor thresholds, limits)")
    ap.add_argument("--balance", type=float, help="starting balance for drawdown %")
    ap.add_argument("--since", help="only positions closed on/after this date, e.g. 2026.10.01")
    ap.add_argument("--guard-log")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", help="write the results here")
    a = ap.parse_args()

    cfg = {}
    if a.config:
        with open(a.config, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    limits = cfg.get("limits", {})
    thr = {"min_trades": 30, "bot_drawdown_pause_pct": 6.0, "max_cost_share_of_gross_profit": 0.3,
           "top3_concentration": 0.5, "pf_drift_ratio": 0.6, "win_rate_drift_points": 0.15}
    thr.update(cfg.get("assessor", {}))
    bots = cfg.get("bots", {})

    since = parse_time(a.since) if a.since else None
    positions, skipped = load_positions(a.deals, since)
    if not positions:
        sys.exit("no closed positions found" + (" since %s" % a.since if a.since else ""))

    groups = defaultdict(list)
    for p in positions:
        groups[p["magic"]].append(p)

    results = {}
    order = sorted(groups, key=lambda k: (-len(groups[k]), k))
    for magic in order + ["ALL"]:
        pos = positions if magic == "ALL" else groups[magic]
        m = metrics(pos, a.balance, a.seed)
        bot_cfg = bots.get(magic, {})
        v, reasons = verdict(m, thr, bot_cfg, limits.get("max_consecutive_losses"))
        if magic == "ALL":
            title = "ALL BOTS (%s to %s)" % (m["first"].strftime("%Y-%m-%d"), m["last"].strftime("%Y-%m-%d"))
        else:
            title = "%s (magic %s)" % (bot_cfg.get("name", "unnamed bot"), magic)
        dd_prob = None
        if limits.get("max_drawdown_pct") and a.balance:
            dd_prob = dd_breach_probability([p["net"] for p in pos], a.balance, limits["max_drawdown_pct"], seed=a.seed)
        print_block(title, m, v, reasons, dd_prob)
        results[magic] = {"metrics": {k: (str(x) if isinstance(x, datetime) else x) for k, x in m.items()},
                          "verdict": v, "reasons": reasons, "dd_breach_probability": dd_prob}

    if skipped:
        print("\n(%d position(s) skipped: still open or partly closed)" % skipped)
    print("\nCaveats: the CI treats trades as independent draws, which real trades are not, so it is a guide, not proof.")
    print("Demo fills are kinder than live ones; compare demo against live spreads and slippage before trusting demo results.")

    if a.guard_log:
        events, rejects, halts = summarise_guard_log(a.guard_log)
        print("\nGUARD LOG: " + ", ".join("%s %d" % kv for kv in sorted(events.items())))
        if rejects:
            print("  most common rejections:")
            for reason, c in rejects.most_common(5):
                print("   %4d  %s" % (c, reason))
        if halts:
            print("  HALTS (each one needs a human look):")
            for h in halts[-10:]:
                print("   " + h)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, default=str)


if __name__ == "__main__":
    main()
