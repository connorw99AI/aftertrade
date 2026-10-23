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
    "capital": {"bot_fraction": 0.10},
    "risk": {
        "risk_per_trade": 0.005,            # default; a bot may override it with strategies.<id>.risk_per_trade
        "max_risk_per_trade_cap": 0.03,     # hard ceiling for any per-bot override
        "min_lot": 0.1,                     # every entry trades at least this many lots (0 = skip trades the budget can't afford)
        "per_trade_limit_enabled": False,   # True rejects entries whose planned stop loss exceeds risk_per_trade x bot equity
        "max_entries_per_bot_per_day": 5,   # new trades (a two-leg group counts once) per bot per UTC day; 0 = unlimited
        "daily_loss_pause": 0.02,
        "max_drawdown_pause": 0.10,
        "max_positions_per_bot": 2,
        "max_bot_margin_fraction": 0.5,     # of the bot's own equity
        "min_account_margin_level": 300.0,  # percent; block entries below
        "max_account_margin_used": 0.5,     # fraction of account equity
        "max_metals_gross_exposure": 3.0,   # gross notional / account equity, across all bots
        "max_same_direction_exposure": 2.0, # long-gold+long-silver (or short) notional / equity
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
    },
    "news": {
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
