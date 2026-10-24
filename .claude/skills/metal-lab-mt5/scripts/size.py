#!/usr/bin/env python3
"""Lot sizer for MT5 metals trades.

Computes the lot size whose stop-out costs a chosen % of equity, from contract
specs dumped by assets/mql5/DumpSymbolSpecs.mq5. Rounds DOWN to the volume
step. If even the minimum lot risks more than allowed it refuses; it never
rounds up.

  python3 size.py --specs MetalLab_specs.json --symbol XAUUSD --side buy \
      --entry 2650.00 --sl 2642.00 --equity 5000 --risk-pct 0.5 \
      --extra-points 30 [--config metal_lab.json] [--json]

Standard library only. Offline: it never talks to a broker.
"""
import argparse
import json
import math
import sys


def load_json(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def floor_to_step(x, step):
    return math.floor(x / step + 1e-9) * step


def step_decimals(step):
    text = ("%.10f" % step).rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def size_trade(spec, account, side, entry, sl, equity, risk_pct,
               extra_points=0.0, max_lots=None):
    point = spec["point"]
    tick_size = spec["tick_size"]
    tick_value = spec.get("tick_value_loss") or spec["tick_value"]
    vmin, vmax, vstep = spec["volume_min"], spec["volume_max"], spec["volume_step"]

    if side == "buy" and not sl < entry:
        return {"ok": False, "reason": "for a buy the stop must be below the entry"}
    if side == "sell" and not sl > entry:
        return {"ok": False, "reason": "for a sell the stop must be above the entry"}

    stop_dist = abs(entry - sl)
    dist = stop_dist + extra_points * point            # budget for spread + slippage
    loss_per_lot = dist / tick_size * tick_value
    risk_money = equity * risk_pct / 100.0
    if loss_per_lot <= 0:
        return {"ok": False, "reason": "loss per lot is zero; check the specs"}

    lots = floor_to_step(risk_money / loss_per_lot, vstep)
    notes = []
    if lots < vmin - 1e-9:
        min_risk = vmin * loss_per_lot
        return {
            "ok": False,
            "reason": ("minimum lot %.2f would risk %.2f (%.2f%% of equity), more than the %.2f%% allowed. "
                       "Use a tighter stop, a bigger account, or skip the trade."
                       % (vmin, min_risk, min_risk / equity * 100.0, risk_pct)),
        }
    cap = min(vmax, max_lots) if max_lots else vmax
    if lots > cap:
        lots = floor_to_step(cap, vstep)
        notes.append("capped at %.2f lots" % lots)
    lots = round(lots, step_decimals(vstep))

    risk_actual = lots * loss_per_lot
    margin = None
    if spec.get("margin_initial"):
        margin = lots * spec["margin_initial"]
    elif account.get("leverage") and spec.get("contract_size"):
        margin = lots * spec["contract_size"] * entry / account["leverage"]
        notes.append("margin is an estimate (contract x price / leverage); CFD margin rules vary by broker")

    return {
        "ok": True,
        "lots": lots,
        "stop_points": round(stop_dist / point, 1),
        "sized_on_points": round(dist / point, 1),
        "loss_per_lot": round(loss_per_lot, 2),
        "risk_money": round(risk_actual, 2),
        "risk_pct_actual": round(risk_actual / equity * 100.0, 3),
        "margin_estimate": None if margin is None else round(margin, 2),
        "notes": notes,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--specs", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=["buy", "sell"])
    ap.add_argument("--entry", required=True, type=float)
    ap.add_argument("--sl", required=True, type=float)
    ap.add_argument("--equity", required=True, type=float)
    ap.add_argument("--risk-pct", type=float, help="defaults to limits.risk_per_trade_pct from --config")
    ap.add_argument("--extra-points", type=float, default=0.0,
                    help="spread + slippage allowance added to the stop distance when sizing")
    ap.add_argument("--config", help="metal_lab.json; applies max_lots_per_trade and the risk cap")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    specs = load_json(a.specs)
    if a.symbol not in specs.get("symbols", {}):
        sys.exit("symbol %s not in %s (have: %s)" % (a.symbol, a.specs, ", ".join(specs.get("symbols", {}))))
    cfg = load_json(a.config)["limits"] if a.config else {}
    risk_pct = a.risk_pct if a.risk_pct is not None else cfg.get("risk_per_trade_pct")
    if risk_pct is None:
        sys.exit("give --risk-pct or --config")
    if a.equity <= 0 or risk_pct <= 0:
        sys.exit("equity and risk % must be positive")
    if cfg.get("risk_per_trade_pct") and risk_pct > cfg["risk_per_trade_pct"] * 1.05:
        sys.exit("refused: %.2f%% is above your configured per-trade risk of %.2f%%"
                 % (risk_pct, cfg["risk_per_trade_pct"]))

    r = size_trade(specs["symbols"][a.symbol], specs.get("account", {}), a.side, a.entry, a.sl,
                   a.equity, risk_pct, a.extra_points, cfg.get("max_lots_per_trade"))
    if a.json:
        print(json.dumps(r, indent=2))
    elif not r["ok"]:
        print("REFUSED: " + r["reason"])
    else:
        print("%s %s  lots %.2f" % (a.side.upper(), a.symbol, r["lots"]))
        print("  stop %s points (sized on %s incl. spread/slippage allowance)" % (r["stop_points"], r["sized_on_points"]))
        print("  risk at the stop about %.2f (%.3f%% of equity); target was %.2f%%"
              % (r["risk_money"], r["risk_pct_actual"], risk_pct))
        if r["margin_estimate"] is not None:
            print("  margin about %.2f" % r["margin_estimate"])
        for n in r["notes"]:
            print("  note: " + n)
        print("  A stop is a plan, not a guarantee: a gap or fast market can fill it worse.")
    sys.exit(0 if r["ok"] else 2)


if __name__ == "__main__":
    main()
