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

import yaml

# Settings the operator asked for in conversation, newest last. Values are YAML text written as-is.
UPDATES: list[tuple[str, str, dict[str, str]]] = [
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
]

MARK = "# metal-lab settings applied: "


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
        new = _set_keys(text, section, values) + f"{MARK}{uid}\n"
        loaded = (yaml.safe_load(new) or {}).get(section, {})
        expected = {k: yaml.safe_load(v) for k, v in values.items()}
        if any(loaded.get(k) != v for k, v in expected.items()):
            shutil.copyfile(backup, path)
            raise RuntimeError(f"settings update {uid} did not load back correctly; config.yaml left unchanged")
        path.write_text(new)
        text = new
        done.append(uid)
    return done
