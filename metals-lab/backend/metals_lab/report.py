"""Shareable backtest review: the latest run per bot (MT5 history preferred over synthetic), its metrics,
reliability warnings and the trade review, as Markdown plus JSON.

Contains no account identifiers: dataset keys are price-data versions, and provenance (server, account
fingerprint) is deliberately left out so the file can be shared for analysis.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import review as R
from .strategies import BOT_IDS, bot_name, build
from .store import now_iso



def _range(dataset_key: str) -> Optional[str]:
    dates = re.findall(r"-(\d{8})-(\d{8})-", dataset_key or "")
    if not dates:
        return None
    a = min(d[0] for d in dates)
    b = max(d[1] for d in dates)
    return f"{a[:4]}-{a[4:6]}-{a[6:]} to {b[:4]}-{b[4:6]}-{b[6:]}"


def latest_runs(db) -> dict[str, dict]:
    out = {}
    for b in BOT_IDS:
        rows = db.q("SELECT * FROM backtests WHERE strategy_id=? AND metrics_json IS NOT NULL ORDER BY id DESC", (b,))
        real = [r for r in rows if r["dataset_key"] not in ("synthetic", "mt5")]
        pick = (real or rows or [None])[0]
        if pick:
            out[b] = pick
    return out


def build_report(db, cfg: dict) -> tuple[str, dict]:
    runs = latest_runs(db)
    data: dict = {"generated_at": now_iso(), "risk_settings": {k: v for k, v in cfg.get("risk", {}).items()}, "bots": {}}
    lines = ["# Backtest review export", "",
             f"Generated {data['generated_at']}. Latest completed run per bot; MT5 history preferred over synthetic.", ""]
    summary = []
    for b in BOT_IDS:
        run = runs.get(b)
        if not run:
            data["bots"][b] = None
            summary.append(f"| {b} | {bot_name(b)} | no run | | | | | | |")
            continue
        m = json.loads(run["metrics_json"])
        rel = json.loads(run["reliability_json"] or "[]")
        trades = json.loads(run["trades_json"] or "[]")
        params = json.loads(run["params_json"] or "{}")
        df = R.from_backtest(trades)
        rv = R.review(b, df, build(b, params).params)
        rv.pop("trades", None)
        synthetic = run["dataset_key"] == "synthetic"
        data["bots"][b] = {"run_id": run["id"], "data": "synthetic" if synthetic else "MT5 history", "period": _range(run["dataset_key"]),
                           "params": params, "metrics": m, "reliability": rel, "review": rv}
        summary.append(f"| {b} | {bot_name(b)} | {'synthetic' if synthetic else (_range(run['dataset_key']) or 'MT5')} | "
                       f"{m.get('n_trades')} | {m.get('net_pnl', 0):+.2f} | {m.get('total_return', 0) * 100:.1f}% | "
                       f"{m.get('max_drawdown', 0) * 100:.1f}% | {m.get('profit_factor')} | {m.get('win_rate', 0) * 100:.0f}% |")
    lines += ["| Bot | Strategy | Data | Trades | Net | Return | Max DD | PF | Win |", "|---|---|---|---|---|---|---|---|---|", *summary, ""]
    for b in BOT_IDS:
        d = data["bots"][b]
        if not d:
            continue
        st = d["review"]["stats"]
        lines += [f"## {b} {bot_name(b)}", "",
                  f"Run #{d['run_id']}, {d['data']}{', ' + d['period'] if d['period'] else ''}. "
                  f"Costs: {d['metrics'].get('total_spread_slippage_cost')} spread/slippage, {d['metrics'].get('total_commission')} commission, "
                  f"{d['metrics'].get('total_swap')} swap. Stopped by drawdown: {d['metrics'].get('stopped_by_drawdown_at') or 'no'}.", ""]
        for w in d["reliability"]:
            lines.append(f"- warning: {w}")
        if st.get("n"):
            lines.append(f"- expectancy {st['expectancy']:+.2f} per trade ({st.get('expectancy_r')}R), win rate {st['win_rate']}%, "
                         f"avg win {st['avg_win']}, avg loss {st['avg_loss']}, cost share {st['cost_share_pct']}% of gross")
            for key, title in (("by_exit", "exit"), ("by_side", "side"), ("by_metal", "metal"), ("by_session", "session")):
                parts = [f"{g['key']}: {g['n']} trades, net {g['net']:+.2f}" for g in st[key]]
                lines.append(f"- by {title}: " + "; ".join(parts))
        for s in d["review"]["suggestions"]:
            if s["id"] != "caveat":
                lines.append(f"- suggestion: {s['title']} ({s['evidence']})" + (f" -> {s['change']}" if s.get("change") else "")
                             + (f" -> rule: {s['rule_change']}" if s.get("rule_change") else ""))
        lines.append("")
    return "\n".join(lines), data
