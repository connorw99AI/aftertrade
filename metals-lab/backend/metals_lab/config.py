"""Configuration loading. No credentials are ever read from config: the system
attaches to the terminal that is already logged in."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULTS: dict[str, Any] = {
    "mt5": {
        "terminal_path": None,          # optional path to terminal64.exe; None attaches to the running terminal
        "timeout_ms": 60000,
        "server_timezone": "auto",      # IANA name (e.g. "Europe/Athens") or "auto" (measure offset from fresh ticks)
        "sessions_export": None,        # path to metals_lab_sessions.json written by mql5/ExportSessions.mq5
    },
    "instruments": {"gold": None, "silver": None, "reference_symbols": []},
    "sessions": {},                     # optional explicit session windows per role (server time)
    "capital": {"bot_fraction": 0.05,      # 20 bots (10 strategies x long/short halves), 5% each
                "apply_assessor_retirements": False},  # true: a fresh lab starts with the assessor's flagged halves retired
    "risk": {
        "risk_per_trade": 0.03,             # default; a bot may override it with strategies.<id>.risk_per_trade
        "max_risk_per_trade_cap": 0.03,     # hard ceiling for any per-bot override
        "min_lot": {"GOLD": 0.1, "SILVER": 0.05},  # floor per trade: a number or per metal; 0 = risk-sized, skip if below broker min
        "per_trade_limit_enabled": False,   # True rejects entries whose planned stop loss exceeds risk_per_trade x bot equity
        "pre_trade_limits": False,          # True re-enables the per-trade risk, bot margin share, account margin share and
                                            # exposure caps below; False = losses are handled by the daily stops and drawdown pause
        "max_entries_per_bot_per_day": 5,   # new trades (a two-leg group counts once) per bot per UTC day; 0 = unlimited
        "max_trade_risk": 0.30,             # skip a trade when even the minimum lot would risk more than this share of the bot
        "daily_loss_pause": 0.30,           # no new entries for the rest of the UTC day after losing this much
        "daily_lock_from": 0.05,            # profit lock: once up this much on the day...
        "daily_giveback": 0.5,              # ...stop for the day after giving back this share of the day's best gain (0 = off)
        "daily_profit_stop": 0.0,           # stop after gaining this much (0 = off); open trades stay managed
        "max_drawdown_pause": 0.50,         # >0 pauses a bot (operator resumes) after this fall from its peak; 0 = off
        "weekend_flat_from": "20:00",       # Friday UTC: close every position, no entries until Sunday 22:00 UTC; null = hold
        "max_positions_per_bot": 2,
        "max_bot_margin_fraction": 0.5,     # of the bot's own equity
        "min_account_margin_level": 300.0,  # percent; block entries below
        "max_account_margin_used": 0.5,     # fraction of account equity
        "exposure_scope": "per_bot",        # "per_bot": exposure caps use the bot's own positions and equity, as if it
                                            # had its own account; "account": all bots' positions vs account equity
        "max_metals_gross_exposure": 200.0, # gross notional / equity (account currency); 0.1 lot gold is ~250x a 150 bot
        "max_same_direction_exposure": 200.0,# long-gold+long-silver (or short) notional / equity
        "max_spread_points": {"GOLD": 80, "SILVER": 80},
        "stop_buffer_points": 5,
        "require_stop": True,
    },
    "auditor": {
        "ruleset_version": "metals-audit-1.0",
        "max_quote_age_seconds": 30,
        "spread_baseline_mult_delay": 3.0,
        "atr_move_mult": 3.0,
        "gap_atr_mult": 2.0,
        "rv_spike_ratio": 3.0,
        "tick_activity_z": 4.0,
        "reversal_atr": 2.0,
        "correlated_shock_atr": 2.5,
        "delay_seconds": 300,
        "reference_move_atr": 3.0,
        "max_stop_atr": 15.0,               # trade-setup check: a stop further than this many ATRs is treated as a bug
    },
    "news": {
        "enabled": False,                   # operator choice: no news checks at all (True re-enables the calendar rules)
        "provider": "csv",                  # "csv" | "none"
        "path": "config/news_calendar.csv",
        "max_age_hours": 192,
        "pre_minutes": 30,
        "post_minutes": 30,
        "impacts": ["HIGH"],
        "unknown_policy": "manual_review",  # "manual_review" | "delay" | "allow_with_flag"
        "filter_enabled": True,             # non-safety filter (can be compared on/off in research)
    },
    "execution": {
        "deviation_points": 30,
        "approval_ttl_seconds": 20,
        "loop_interval_seconds": 10,
        "intent_timeout_seconds": 120,
        "settle_seconds": 300,
        "history_lookback_days": 30,
    },
    "api": {"host": "127.0.0.1", "port": 8765, "token_env": "METALS_LAB_API_TOKEN"},
    "storage": {"db_path": "data/state.sqlite", "cache_dir": "data/cache"},
    "selection": {"min_trades": 50},        # consistency ranking for choosing a live candidate
    "strategies": {},                       # per strategy_id: {"enabled": bool, "params": {...}}
}


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Optional[str | Path] = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path or os.environ.get("METALS_LAB_CONFIG", "config/config.yaml"))
    if p.exists():
        cfg = _merge(cfg, yaml.safe_load(p.read_text()) or {})
        cfg["_path"] = str(p)
        base = p.parent.parent if p.parent.name == "config" else p.parent
        for sect, key in (("storage", "db_path"), ("storage", "cache_dir"), ("news", "path")):
            v = cfg[sect][key]
            if v and not Path(v).is_absolute():
                cfg[sect][key] = str(base / v)
    _forbid_secrets(cfg)
    return cfg


_SECRET_KEYS = {"password", "login", "account", "investor_password", "api_key", "secret"}


def _forbid_secrets(d: Any, path: str = "") -> None:
    if isinstance(d, dict):
        for k, v in d.items():
            if str(k).lower() in _SECRET_KEYS and v not in (None, "", []):
                raise ValueError(f"config key '{path}{k}' looks like a credential; credentials must stay in the MT5 terminal")
            _forbid_secrets(v, f"{path}{k}.")
