"""Bar frame conventions and resampling.

Canonical bar frame (one row per *completed or forming* bar, sorted, unique):
  time         tz-aware UTC bar open time
  open/high/low/close   BID prices (MT5 rates are bid-based)
  tick_volume  number of price changes in the bar. This is TICK VOLUME, not
               traded/consolidated volume - OTC metals have no consolidated tape.
  spread       spread in points as reported for the bar by the terminal
  real_volume  exchange volume when the broker supplies it (usually 0 for CFDs)
"""
from __future__ import annotations

import pandas as pd

from ..domain import TIMEFRAME_MINUTES

BAR_COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]

_RULE = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Aggregate a finer bar frame into ``tf`` bars (UTC-anchored).

    Note: real broker H4/D1 bars are anchored to *server* midnight, so bars
    resampled here in UTC will not match terminal D1 bars exactly. Use terminal
    bars for live trading; resampling is used for synthetic data and tick builds.
    """
    g = df.set_index("time").resample(_RULE[tf], label="left", closed="left")
    out = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "tick_volume": g["tick_volume"].sum(),
        "spread": g["spread"].mean().round(),
        "real_volume": g["real_volume"].sum() if "real_volume" in df else 0,
    }).dropna(subset=["open"])
    out = out.reset_index()
    out["spread"] = out["spread"].astype(int)
    return out


def bars_from_ticks(ticks: pd.DataFrame, tf: str, point: float) -> pd.DataFrame:
    """Build bid bars with measured spreads from MT5 ticks (time UTC, bid, ask)."""
    t = ticks.dropna(subset=["bid", "ask"]).copy()
    t["spr"] = ((t["ask"] - t["bid"]) / point).round()
    g = t.set_index("time").resample(_RULE[tf], label="left", closed="left")
    out = pd.DataFrame({
        "open": g["bid"].first(), "high": g["bid"].max(), "low": g["bid"].min(), "close": g["bid"].last(),
        "tick_volume": g["bid"].count(), "spread": g["spr"].median(), "real_volume": 0,
    }).dropna(subset=["open"]).reset_index()
    out["spread"] = out["spread"].astype(int)
    return out


def close_times(df: pd.DataFrame, tf: str) -> pd.Series:
    return df["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])


def completed_only(df: pd.DataFrame, tf: str, now_utc: pd.Timestamp) -> pd.DataFrame:
    """Drop the forming bar: keep rows whose close time <= now."""
    return df[close_times(df, tf) <= now_utc]
