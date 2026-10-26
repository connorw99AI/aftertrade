"""Historical bar/tick retrieval from the connected MT5 terminal (read-only)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..instruments import InstrumentSpec
from ..mt5.gateway import ReadOnlyGateway
from ..sessions import ServerClock
from .bars import BAR_COLUMNS, bars_from_ticks
from .store import DatasetStore, Provenance, quality_report

TF_CONST = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
            "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}


def _normalise(raw: pd.DataFrame, clock: ServerClock) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    df = raw.copy()
    df["time"] = clock.to_utc(df.pop("server_time")).to_numpy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.dropna(subset=["time"])  # ambiguous DST hour bars are dropped, and counted by the quality report
    for c in BAR_COLUMNS:
        if c not in df:
            df[c] = 0
    return df[BAR_COLUMNS].sort_values("time").reset_index(drop=True)


def fetch_bars(gw: ReadOnlyGateway, clock: ServerClock, spec: InstrumentSpec, tf: str,
               start: datetime, end: datetime, chunk_days: int = 60) -> pd.DataFrame:
    """Chunked copy_rates_range. MT5 terminals limit history by 'Max bars in chart'."""
    parts = []
    cur = start
    tfc = gw.const(TF_CONST[tf])
    while cur < end:
        nxt = min(end, cur + timedelta(days=chunk_days))
        parts.append(_normalise(gw.copy_rates_range(spec.symbol, tfc, cur, nxt), clock))
        cur = nxt
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=BAR_COLUMNS)
    return pd.concat(parts).drop_duplicates("time").sort_values("time").reset_index(drop=True)


def fetch_recent(gw: ReadOnlyGateway, clock: ServerClock, spec: InstrumentSpec, tf: str, count: int) -> pd.DataFrame:
    return _normalise(gw.copy_rates_from_pos(spec.symbol, gw.const(TF_CONST[tf]), 0, count), clock)


def fetch_ticks(gw: ReadOnlyGateway, clock: ServerClock, spec: InstrumentSpec, start: datetime, end: datetime) -> pd.DataFrame:
    raw = gw.copy_ticks_range(spec.symbol, start, end)
    if raw.empty:
        return raw
    raw["time"] = clock.to_utc(raw.pop("server_time")).to_numpy()
    raw["time"] = pd.to_datetime(raw["time"], utc=True)
    return raw.dropna(subset=["time"])


def download_and_cache(gw: ReadOnlyGateway, clock: ServerClock, store: DatasetStore, spec: InstrumentSpec, tf: str,
                       start: datetime, end: datetime, server: str | None, fp_short: str | None,
                       from_ticks: bool = False) -> str:
    if from_ticks:
        ticks = fetch_ticks(gw, clock, spec, start, end)
        df = bars_from_ticks(ticks, tf, spec.point) if not ticks.empty else pd.DataFrame(columns=BAR_COLUMNS)
        source = "MT5_TICKS"
    else:
        df = fetch_bars(gw, clock, spec, tf, start, end)
        source = "MT5_TERMINAL"
    if df.empty:
        raise RuntimeError(f"terminal returned no {tf} data for {spec.symbol} in range")
    prov = Provenance(source=source, symbol=spec.symbol, role=spec.role, timeframe=tf, terminal_server=server,
                      account_fingerprint=fp_short, clock=clock.source, spec_snapshot=spec.public(),
                      notes=["prices are BID; ask = bid + spread*point",
                             "spread column is the terminal's per-bar spread in points" if not from_ticks else
                             "spread column is the median tick spread in the bar",
                             "tick_volume is tick count, not traded volume"])
    if clock.tz is None:
        prov.notes.append("server->UTC conversion used a single measured offset; DST shifts in history are NOT corrected")
    return store.save(df, prov, quality_report(df, tf))
