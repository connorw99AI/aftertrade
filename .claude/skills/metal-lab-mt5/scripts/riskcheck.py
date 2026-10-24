#!/usr/bin/env python3
"""Preflight for a Metal Lab risk config.

Checks the limits for contradictions and works out what they allow in the
worst realistic cases, so you see the consequences before a bot is live. Can
also emit an MT5 .set preset whose names match assets/mql5/ExampleEA.mq5.

  python3 riskcheck.py --config metal_lab.json [--emit-set Guard.set]

Exit code 1 if there are errors. Standard library only.
"""
import argparse
import json
import math
import sys

SET_MAP = [
    ("InpRiskPerTradePct", "risk_per_trade_pct"),
    ("InpMaxDailyLossPct", "max_daily_loss_pct"),
    ("InpMaxWeeklyLossPct", "max_weekly_loss_pct"),
    ("InpMaxDrawdownPct", "max_drawdown_pct"),
    ("InpMaxOpenPositions", "max_open_positions"),
    ("InpMaxLotsPerTrade", "max_lots_per_trade"),
    ("InpMaxTotalLots", "max_total_lots"),
    ("InpMaxEntriesPerDay", "max_entries_per_day"),
    ("InpMaxConsecLosses", "max_consecutive_losses"),
    ("InpCooldownMinutes", "cooldown_minutes_after_loss"),
    ("InpMaxSpreadPoints", "max_spread_points"),
    ("InpMinMarginLevelPct", "min_margin_level_pct"),
    ("InpSessionStartHour", "session_start_hour"),
    ("InpSessionEndHour", "session_end_hour"),
    ("InpFridayCutoffHour", "friday_cutoff_hour"),
    ("InpCloseOnBreach", "close_on_breach"),
]

REQUIRED = ["risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_drawdown_pct",
            "max_open_positions", "max_lots_per_trade", "max_total_lots"]


def check(lim):
    errors, warns, facts = [], [], []
    for k in REQUIRED:
        if k not in lim:
            errors.append("missing limit: %s" % k)
    if errors:
        return errors, warns, facts

    r, d, w, dd = (lim["risk_per_trade_pct"], lim["max_daily_loss_pct"],
                   lim["max_weekly_loss_pct"], lim["max_drawdown_pct"])
    n = lim["max_open_positions"]

    for k in ("risk_per_trade_pct", "max_daily_loss_pct", "max_weekly_loss_pct", "max_drawdown_pct"):
        if lim[k] <= 0:
            errors.append("%s must be above 0 (a zero limit switches the protection off)" % k)
    if errors:
        return errors, warns, facts

    if not (r <= d <= w <= dd):
        errors.append("expected risk/trade <= daily <= weekly <= drawdown, got %s / %s / %s / %s" % (r, d, w, dd))

    # What the limits actually allow.
    facts.append("A full stop-out costs %.2f%% of equity." % r)
    to_daily = math.ceil(d / r)
    facts.append("%d straight stop-outs reach the daily limit (%.2f%%)." % (to_daily, d))
    facts.append("About %d stop-outs at full risk take you from peak to the drawdown limit (%.2f%%), "
                 "if the daily and weekly halts were ignored." % (math.ceil(dd / r), dd))
    simultaneous = n * r
    facts.append("All %d allowed positions stopping out together costs %.2f%% (daily limit %.2f%%)."
                 % (n, simultaneous, d))
    if simultaneous > d:
        warns.append("%d positions x %.2f%% = %.2f%%, above the %.2f%% daily limit: one bad minute on "
                     "correlated metals could breach it before the guard acts." % (n, r, simultaneous, d))
    # Gold and silver are highly correlated; treat open positions as one bet.
    gap = 3
    facts.append("A stop that fills %dx worse than planned (gap, news, thin liquidity) on one trade costs %.2f%%; "
                 "on all %d positions %.2f%%." % (gap, gap * r, n, gap * simultaneous))
    if gap * simultaneous > dd:
        warns.append("a 3x-slippage event across all open positions (%.2f%%) would exceed the drawdown limit "
                     "(%.2f%%) in one go. Lower max_open_positions or risk per trade." % (gap * simultaneous, dd))

    mcl = lim.get("max_consecutive_losses", 0)
    if mcl:
        facts.append("The loss-streak cap (%d) alone costs about %.2f%% before trading pauses." % (mcl, mcl * r))
        if mcl * r > d:
            warns.append("max_consecutive_losses x risk (%.2f%%) exceeds the daily limit (%.2f%%), "
                         "so the daily limit trips first; the streak cap only matters across days." % (mcl * r, d))
    else:
        warns.append("max_consecutive_losses is off: a broken bot can keep losing until the daily limit.")

    if r > 1.0:
        warns.append("risk per trade above 1%% (%.2f%%) is aggressive for leveraged metals." % r)
    if d > 3.0:
        warns.append("daily loss limit above 3%% (%.2f%%); the limit is meant to stop a bad day, not allow one." % d)
    if dd > 15.0:
        warns.append("drawdown limit above 15%% (%.2f%%): recovering a 15%% loss needs about 18%% gain." % dd)
    lots_cap = lim["max_total_lots"]
    if lim["max_lots_per_trade"] > lots_cap:
        errors.append("max_lots_per_trade (%s) is above max_total_lots (%s)" % (lim["max_lots_per_trade"], lots_cap))
    if not lim.get("close_on_breach", True):
        warns.append("close_on_breach is off: after a loss limit trips, open positions stay open and keep "
                     "running against you.")
    if lim.get("friday_cutoff_hour", -1) < 0:
        warns.append("no Friday cutoff: stops do not protect across a weekend gap.")
    if lim.get("max_spread_points", 0) <= 0:
        warns.append("spread filter is off: metals spreads widen at rollover and around news.")
    if lim.get("min_margin_level_pct", 0) <= 0:
        warns.append("margin-level floor is off: leverage can force a stop-out before your stops.")
    if lim.get("session_start_hour", -1) >= 0:
        facts.append("Session hours are SERVER time, which is not GMT and may shift with daylight saving. "
                     "Check your broker's server offset.")
    return errors, warns, facts


def emit_set(lim, path):
    lines = []
    for name, key in SET_MAP:
        if key not in lim:
            continue
        v = lim[key]
        if isinstance(v, bool):
            v = "true" if v else "false"
        lines.append("%s=%s" % (name, v))
    with open(path, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--emit-set", help="write an MT5 .set preset matching ExampleEA.mq5 inputs")
    a = ap.parse_args()
    with open(a.config, encoding="utf-8-sig") as fh:
        cfg = json.load(fh)
    lim = cfg.get("limits", {})
    errors, warns, facts = check(lim)

    for f in facts:
        print("  " + f)
    for w in warns:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)
    if not errors and a.emit_set:
        emit_set(lim, a.emit_set)
        print("wrote %s (load it from the EA's Inputs tab; check the values appear before going live)" % a.emit_set)
    print("RESULT: " + ("FAIL" if errors else "OK with %d warning(s)" % len(warns)))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
