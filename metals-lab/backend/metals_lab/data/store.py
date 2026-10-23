"""Versioned Parquet dataset cache with provenance manifests and quality reports."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from ..domain import TIMEFRAME_MINUTES
from ..sessions import SessionCalendar, ServerClock


@dataclass
class Provenance:
    source: str                      # "MT5_TERMINAL", "MT5_TICKS", "SYNTHETIC", "CSV_IMPORT"
    symbol: str
    role: str
    timeframe: str
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    terminal_server: Optional[str] = None
    account_fingerprint: Optional[str] = None   # short hash only
    clock: Optional[str] = None                 # how server time was converted to UTC
    spec_snapshot: Optional[dict] = None
    notes: list[str] = field(default_factory=list)


def quality_report(df: pd.DataFrame, tf: str, calendar: Optional[SessionCalendar] = None,
                   clock: Optional[ServerClock] = None) -> dict:
    rep: dict = {"rows": int(len(df))}
    if df.empty:
        rep["status"] = "EMPTY"
        return rep
    t = df["time"]
    rep["start"], rep["end"] = t.iloc[0].isoformat(), t.iloc[-1].isoformat()
    rep["timezone_aware_utc"] = str(t.dt.tz) == "UTC"
    rep["duplicates"] = int(t.duplicated().sum())
    rep["non_monotonic"] = int((t.diff().dt.total_seconds() < 0).sum())
    rep["bad_ohlc"] = int(((df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1)) |
                           (df["low"] > df[["open", "close"]].min(axis=1)) | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum())
    rep["zero_spread_bars"] = int((df["spread"] <= 0).sum()) if "spread" in df else None
    rep["spread_available"] = "spread" in df and bool((df["spread"] > 0).any())
    step = pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])
    gaps = t.diff()
    rep["gaps_over_1_bar"] = int((gaps > step).sum())
    rep["largest_gap_hours"] = round(gaps.max().total_seconds() / 3600, 2) if len(df) > 1 else 0
    if calendar is not None and clock is not None and tf not in ("D1", "H4"):
        expected = pd.date_range(t.iloc[0], t.iloc[-1], freq=step)
        server = [clock.utc_to_server_naive(x.to_pydatetime()) for x in expected]
        exp_open = [x for x, s in zip(expected, server) if calendar.is_open_server(s)]
        have = set(t)
        missing = [x for x in exp_open if x not in have]
        rep["expected_open_bars"] = len(exp_open)
        rep["missing_open_bars"] = len(missing)
        rep["coverage_pct"] = round(100 * (1 - len(missing) / max(1, len(exp_open))), 2)
        rep["first_missing"] = [m.isoformat() for m in missing[:10]]
    rep["tick_volume_note"] = "tick_volume counts price updates; it is not traded volume"
    problems = rep["duplicates"] + rep["non_monotonic"] + rep["bad_ohlc"]
    rep["status"] = "OK" if problems == 0 else "ISSUES"
    return rep


class DatasetStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, prov: Provenance) -> Path:
        return self.root / prov.source.lower() / prov.symbol / prov.timeframe

    def save(self, df: pd.DataFrame, prov: Provenance, quality: Optional[dict] = None) -> str:
        df = df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        digest = hashlib.sha256(buf.getvalue()).hexdigest()
        version = f"{prov.role}-{prov.timeframe}-{df['time'].iloc[0]:%Y%m%d}-{df['time'].iloc[-1]:%Y%m%d}-{digest[:10]}"
        d = self._dir(prov)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{version}.parquet").write_bytes(buf.getvalue())
        manifest = {"version": version, "sha256": digest, "provenance": asdict(prov),
                    "quality": quality or quality_report(df, prov.timeframe)}
        (d / f"{version}.json").write_text(json.dumps(manifest, indent=2, default=str))
        return version

    def manifests(self) -> list[dict]:
        out = []
        for p in sorted(self.root.rglob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
        return out

    def load(self, version: str) -> tuple[pd.DataFrame, dict]:
        for p in self.root.rglob(f"{version}.parquet"):
            m = json.loads(p.with_suffix(".json").read_text())
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() != m["sha256"]:
                raise ValueError(f"dataset {version} failed integrity check")
            df = pd.read_parquet(io.BytesIO(data))
            df["time"] = pd.to_datetime(df["time"], utc=True)
            return df, m
        raise FileNotFoundError(version)

    def latest(self, source: str, role: str, tf: str) -> Optional[str]:
        cands = [m for m in self.manifests() if m["provenance"]["source"] == source and m["provenance"]["role"] == role
                 and m["provenance"]["timeframe"] == tf]
        if not cands:
            return None
        return sorted(cands, key=lambda m: (m["quality"].get("end", ""), m["provenance"]["retrieved_at"]))[-1]["version"]
