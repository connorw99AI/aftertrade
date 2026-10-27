"""One-time updates to the operator's own config/config.yaml, applied by the desktop launcher.

config.yaml is machine-specific and not in git, so settings agreed after setup would otherwise never reach it.
Each update has an id; it is applied once (a marker line records it), only touches the keys it names, keeps every
other line and comment, and saves a backup first. If the edited file does not load back to the intended values,
the backup is restored and nothing changes.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

import yaml

# Settings the operator asked for in conversation, newest last. Values are YAML text written as-is;
# None instead of a dict removes the whole section.
UPDATES: list[tuple[str, str, Optional[dict[str, str]]]] = [
    ("2026-10-25-risk", "risk", {
        "risk_per_trade": "0.03",
        "min_lot": "{GOLD: 0.05, SILVER: 0.03}",
        "big_lot": "{GOLD: 0.1, SILVER: 0.1}",
        "big_lot_max_risk": "0.30",
        "max_trade_risk": "0",
        "daily_loss_pause": "0.30",
        "daily_lock_from": "0.05",
        "daily_giveback": "0.5",
        "daily_profit_stop": "0",
    }),
    ("2026-10-25-silver", "risk", {"big_lot": "{GOLD: 0.1, SILVER: 0.05}"}),
    ("2026-10-25-silver-006", "risk", {"big_lot": "{GOLD: 0.1, SILVER: 0.06}"}),
    # the all-metals / all-hours switches are replaced by each bot's own gold/silver switches and trading windows
    ("2026-10-25-windows", "trading", None),
    # S11 (session momentum) makes 22 bots: each gets 1/22 of the starting balance on the next new-lab
    ("2026-10-25-22-bots", "capital", {"bot_fraction": "0.045454"}),
    # small trades for small bots: 2% risk, broker-minimum lots, never more than 5% of a bot at the stop
    ("2026-10-26-small-trades", "risk", {"risk_per_trade": "0.02", "min_lot": "{GOLD: 0.01, SILVER: 0.01}", "big_lot": "0",
                                          "max_trade_risk": "0.05"}),
    # wide-stop bots (S07-S10, both halves) may lose up to 10% at the stop, so 0.01 lot fits their stops
    ("2026-10-26-wide-stop-cap", "risk", {"max_trade_risk_by_strategy": "{S07: 0.10, S08: 0.10, S09: 0.10, S10: 0.10}"}),
    # S12 (VWAP trend cross) makes 24 bots at 1/24 each; on a 10,000 demo every bot then fits the 5% cap, so no exceptions
    ("2026-10-27-24-bots", "capital", {"bot_fraction": "0.041666"}),
    ("2026-10-27-one-trade-cap", "risk", {"max_trade_risk_by_strategy": "{}"}),
]

MARK = "# metal-lab settings applied: "


def _section(lines: list[str], section: str) -> tuple[Optional[int], int]:
    start = next((i for i, l in enumerate(lines) if re.match(rf"^{section}:\s*(#.*)?$", l)), None)
    if start is None:
        return None, len(lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "#"))), len(lines))
    return start, end


def _drop_section(text: str, section: str) -> str:
    lines = text.splitlines()
    start, end = _section(lines, section)
    if start is not None:
        del lines[start:end]
    return "\n".join(lines) + "\n"


def _set_keys(text: str, section: str, values: dict[str, str]) -> str:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(rf"^{section}:\s*(#.*)?$", l)), None)
    if start is None:
        lines += ["", f"{section}:"] + [f"  {k}: {v}" for k, v in values.items()]
        return "\n".join(lines) + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "\t", "#"))), len(lines))
    todo = dict(values)
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)([A-Za-z_]+):", lines[i])
        if m and m.group(2) in todo:
            lines[i] = f"{m.group(1)}{m.group(2)}: {todo.pop(m.group(2))}"
    insert = [f"  {k}: {v}" for k, v in todo.items()]
    lines[start + 1:start + 1] = insert
    return "\n".join(lines) + "\n"


def apply(path: Path) -> list[str]:
    """Apply every pending update to ``path``. Returns the ids applied (empty when up to date or no file)."""
    if not path.exists():
        return []
    text = path.read_text()
    done: list[str] = []
    for uid, section, values in UPDATES:
        if MARK + uid in text:
            continue
        backup = path.with_name(f"{path.name}.bak-{uid}")
        shutil.copyfile(path, backup)
        if values is None:
            new = _drop_section(text, section) + f"{MARK}{uid}\n"
            bad = section in (yaml.safe_load(new) or {})
        else:
            new = _set_keys(text, section, values) + f"{MARK}{uid}\n"
            loaded = (yaml.safe_load(new) or {}).get(section, {})
            bad = any(loaded.get(k) != yaml.safe_load(v) for k, v in values.items())
        if bad:
            shutil.copyfile(backup, path)
            raise RuntimeError(f"settings update {uid} did not load back correctly; config.yaml left unchanged")
        path.write_text(new)
        text = new
        done.append(uid)
    return done
