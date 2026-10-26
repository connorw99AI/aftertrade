"""Replay synthetic bars through the SimTerminal so the dashboard can be exercised
locally without MT5. Everything produced this way is SIMULATED and labelled so."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ..data import bars as B
from ..data import synthetic
from . import constants as C
from .sim import SimTerminal

_TF = {"M1": C.TIMEFRAME_M1, "M5": C.TIMEFRAME_M5, "M15": C.TIMEFRAME_M15, "H1": C.TIMEFRAME_H1, "H4": C.TIMEFRAME_H4, "D1": C.TIMEFRAME_D1}
_DT = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])


def _to_rates(df: pd.DataFrame, offset_h: int) -> np.ndarray:
    t = (pd.to_datetime(df["time"], utc=True).dt.as_unit("s").astype("int64") + offset_h * 3600).to_numpy()
    out = np.zeros(len(df), dtype=_DT)
    out["time"] = t
    for c in ("open", "high", "low", "close"):
        out[c] = df[c].to_numpy()
    out["tick_volume"] = df["tick_volume"].to_numpy()
    out["spread"] = df["spread"].to_numpy()
    return out


class SimReplay:
    def __init__(self, sim: SimTerminal, days: int = 400, start_fraction: float = 0.6, seed: int = 11):
        self.sim = sim
        raw = synthetic.generate(days=days, seed=seed, tf_minutes=1)
        self.m5 = {}
        names = {"GOLD": "XAUUSD", "SILVER": "XAGUSD"}
        for role, df in raw.items():
            sym = names[role]
            frames = {"M1": df} | {tf: B.resample(df, tf) for tf in ("M5", "M15", "H1", "H4", "D1")}
            for tf, f in frames.items():
                sim.rates[(sym, _TF[tf])] = _to_rates(f, sim.server_utc_offset_hours)
            self.m5[sym] = df.reset_index(drop=True)
        self.i = int(len(next(iter(self.m5.values()))) * start_fraction)
        self._apply()

    def _apply(self):
        for df in self.m5.values():
            row = df.iloc[self.i]
            self.sim.set_time((row["time"] + timedelta(minutes=1)).to_pydatetime())
        for sym, df in self.m5.items():
            row = df.iloc[self.i]
            sp = self.sim.symbols[sym]
            self.sim.set_quote(sym, float(row["close"]), float(row["close"]) + int(row["spread"]) * sp["point"])

    def step(self, bars: int = 5):
        """Advance the replay by `bars` one-minute bars (default: 5 minutes of market time)."""
        n = len(next(iter(self.m5.values())))
        self.i = min(n - 1, self.i + bars)
        self._apply()
