"""SYNTHETIC gold/silver bars for tests and pipeline demonstrations ONLY.

Every dataset produced here carries provenance ``source="SYNTHETIC"``. Results
on it say nothing about real markets and must never be presented as research
evidence. The generator includes features that exercise the engine: sessions
and weekend gaps, intraday volatility seasonality, correlated metals, regime
changes, jump days around a fixed 'release' time, and time-varying spreads.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate(start: str = "2024-01-01", days: int = 260, seed: int = 7, tf_minutes: int = 5,
             gold0: float = 2050.0, silver0: float = 23.5) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=days * 24 * 60 // tf_minutes, freq=f"{tf_minutes}min", tz="UTC")
    # trading hours: Sun 22:00 UTC to Fri 21:00 UTC, daily break 21:00-22:00 UTC
    wd, hr = idx.day_of_week, idx.hour
    open_mask = ~((wd == 5) | ((wd == 6) & (hr < 22)) | ((wd == 4) & (hr >= 21)) | (hr == 21))
    idx = idx[open_mask]
    n = len(idx)
    h = idx.hour.to_numpy()
    season = np.where((h >= 7) & (h < 17), 1.6, np.where((h >= 17) & (h < 21), 1.0, 0.6))
    base_vol = 0.00045 * np.sqrt(tf_minutes / 5)
    # regime: slow drift switching every ~20 trading days
    day_id = (idx.normalize().as_unit("ns").astype("int64") // 86_400_000_000_000).to_numpy()
    uniq = np.unique(day_id)
    regime_drift = np.repeat(rng.normal(0, 0.0000025 * tf_minutes / 5, size=len(uniq) // 20 + 2), 20)[: len(uniq)]
    drift = regime_drift[np.searchsorted(uniq, day_id)]
    z1 = rng.standard_normal(n)
    z2 = 0.8 * z1 + 0.6 * rng.standard_normal(n)
    # jump days: ~1 in 10 days at 13:30 UTC
    jump_days = set(uniq[rng.random(len(uniq)) < 0.1])
    jump = np.array([(d in jump_days) and (t.hour == 13 and t.minute == 30) for d, t in zip(day_id, idx)])
    j = np.where(jump, rng.normal(0, 0.006, n), 0.0)
    rg = drift + base_vol * season * z1 + j
    rs = 1.3 * drift + 1.5 * base_vol * season * z2 + 1.6 * j
    out = {}
    for name, r, p0, spr_base in (("GOLD", rg, gold0, 22), ("SILVER", rs, silver0, 25)):
        # weekend/daily-break gaps: the gap lands between the previous close and this bar's open
        gap = np.zeros(n)
        ns = idx.as_unit("ns").asi8
        tgap = np.diff(ns, prepend=ns[0]) > tf_minutes * 60 * 1e9 * 1.5
        gap[tgap] = rng.normal(0, 0.002, tgap.sum())
        logc = np.log(p0) + np.cumsum(r + gap)
        close = np.exp(logc)
        openp = np.exp(logc - r)  # = previous close x exp(gap)
        wig = np.abs(rng.normal(0, base_vol * season, n)) * close
        high = np.maximum(openp, close) + wig
        low = np.minimum(openp, close) - np.abs(rng.normal(0, base_vol * season, n)) * close
        spread = (spr_base * np.where(season < 1, 1.6, 1.0) * np.where(jump, 4, 1) * rng.uniform(0.8, 1.3, n)).round().astype(int)
        tv = (rng.poisson(60 * season) + np.where(jump, 300, 0)).astype(int)
        out[name] = pd.DataFrame({"time": idx, "open": openp.round(5), "high": high.round(5), "low": low.round(5),
                                  "close": close.round(5), "tick_volume": tv, "spread": spread, "real_volume": 0})
    return out


_BASE_MINUTES = {"M1": 1, "M5": 5, "M15": 5, "M30": 5, "H1": 60, "H4": 60, "D1": 60}


def frames_for(timeframes, days: int = 365, seed: int = 7) -> dict[tuple[str, str], pd.DataFrame]:
    """SYNTHETIC frames for every requested timeframe, resampled from the finest base needed."""
    from .bars import resample  # local import avoids a cycle
    base_min = min(_BASE_MINUTES[tf] for tf in timeframes)
    base_tf = {1: "M1", 5: "M5", 60: "H1"}[base_min]
    raw = generate(days=days, seed=seed, tf_minutes=base_min)
    out = {}
    for role, df in raw.items():
        for tf in set(timeframes) | {base_tf}:
            out[(role, tf)] = df if tf == base_tf else resample(df, tf)
    return out
