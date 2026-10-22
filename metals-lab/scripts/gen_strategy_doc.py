"""Regenerate docs/STRATEGY_REFERENCE.md from the strategy classes."""
from pathlib import Path

from metals_lab.strategies import STRATEGY_CLASSES, magic_for

out = ["# Strategy reference (generated from code)\n",
       "Generated from `Strategy.describe()`; regenerate with `python scripts/gen_strategy_doc.py` after changing rules.\n"]
for sid, c in STRATEGY_CLASSES.items():
    d = c().describe()
    out.append(f"\n## {sid} {d['name']}\n")
    out.append(f"- Style: {d['style'].lower()} | instruments: {', '.join(d['roles'])} | timeframes: {', '.join(d['timeframes'])} "
               f"(decisions on completed {d['primary_timeframe']} bars) | min history: {d['min_history_bars']} bars | magic: {magic_for(sid)}\n")
    out.append(f"\n{d['description']}\n")
    for k, t in (("long_rules", "Long (OPEN_LONG / CLOSE_LONG)"), ("short_rules", "Short (OPEN_SHORT / COVER_SHORT)"),
                 ("exit_rules", "Exits, stops, targets, max holding"), ("invalidation_rules", "Invalidation"),
                 ("filters", "Spread, liquidity and volatility filters"), ("limitations", "Limitations")):
        out.append(f"\n**{t}.** {d[k]}\n")
    out.append("\n| Parameter | Default | Research grid | Rationale |\n|---|---|---|---|\n")
    for k, p in d["params"].items():
        out.append(f"| `{k}` | {p['default']} | {', '.join(map(str, p['grid'])) or '–'} | {p['rationale']} |\n")
    out.append("\n**Sources.**\n\n" + "".join(f"- {x}\n" for x in d["citations"]))
Path(__file__).resolve().parents[1].joinpath("docs", "STRATEGY_REFERENCE.md").write_text("".join(out))
