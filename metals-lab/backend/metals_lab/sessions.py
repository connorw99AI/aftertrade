"""Trade-server clock and metals trading sessions.

Facts this module is built on:

* The MetaTrader5 Python package returns bar/tick times as epoch seconds that,
  in practice, encode the *trade server's* wall clock (commonly UTC+2/UTC+3
  with an EU-style DST switch, but this varies by broker). It must not be
  treated as UTC without verification.
* The Python package does **not** expose trading-session schedules.
  MQL5 does (``SymbolInfoSessionTrade``). ``mql5/ExportSessions.mq5`` is a
  small documented-API script that writes them to a JSON file which this module
  reads. Without it, sessions come from explicit configuration, and gap
  inference from history is offered as a diagnostic only.
* Metals do not follow a US stock-exchange calendar. Holidays/early closes are
  taken from the export/config plus an operator-maintained closure list; quote
  freshness checks in the auditor catch anything missed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd


class ServerClock:
    """Converts between trade-server wall time and UTC."""

    def __init__(self, tz_name: Optional[str] = None, fixed_offset_hours: Optional[float] = None):
        self.tz = ZoneInfo(tz_name) if tz_name and tz_name not in ("auto", "measured") else None
        self.fixed_offset = fixed_offset_hours
        self.source = "iana:" + tz_name if self.tz else ("measured_offset" if fixed_offset_hours is not None else "unset")

    @staticmethod
    def measure_offset_hours(tick_server_epoch: int, utc_now: datetime) -> float:
        """Estimate offset from a *fresh* tick. Rounded to 30 minutes."""
        diff = tick_server_epoch - utc_now.timestamp()
        return round(diff / 1800.0) * 0.5

    def to_utc(self, server_naive: pd.Series | pd.DatetimeIndex) -> pd.Series:
        s = pd.Series(pd.to_datetime(server_naive))
        if self.tz is not None:
            return s.dt.tz_localize(self.tz, ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
        if self.fixed_offset is not None:
            return (s - pd.Timedelta(hours=self.fixed_offset)).dt.tz_localize("UTC")
        raise ValueError("ServerClock has no timezone or measured offset; refusing to guess")

    def server_epoch_to_utc(self, epoch: int) -> datetime:
        naive = datetime(1970, 1, 1) + timedelta(seconds=int(epoch))
        if self.tz is not None:
            return naive.replace(tzinfo=self.tz).astimezone(timezone.utc)
        if self.fixed_offset is not None:
            return (naive - timedelta(hours=self.fixed_offset)).replace(tzinfo=timezone.utc)
        raise ValueError("ServerClock not configured")

    def utc_to_server_naive(self, dt: datetime) -> datetime:
        if self.tz is not None:
            return dt.astimezone(self.tz).replace(tzinfo=None)
        if self.fixed_offset is not None:
            return (dt.astimezone(timezone.utc) + timedelta(hours=self.fixed_offset)).replace(tzinfo=None)
        raise ValueError("ServerClock not configured")

    def verify_against_tick(self, tick_server_epoch: int, utc_now: datetime) -> tuple[bool, str]:
        measured = self.measure_offset_hours(tick_server_epoch, utc_now)
        if self.tz is not None:
            expect = self.tz.utcoffset(utc_now.astimezone(self.tz).replace(tzinfo=None)).total_seconds() / 3600
        elif self.fixed_offset is not None:
            expect = self.fixed_offset
        else:
            return False, f"clock unset; measured offset {measured:+.1f}h"
        ok = abs(measured - expect) < 0.01
        return ok, f"measured {measured:+.1f}h vs configured {expect:+.1f}h"


# ----------------------------------------------------------------------------- sessions
_MQL_DOW_TO_PY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}  # MQL5 SUNDAY=0 -> python Monday=0


@dataclass
class SessionWindow:
    weekday: int  # python convention, Monday=0, in SERVER time
    start: time
    end: time  # end == 00:00 means midnight (24:00)


@dataclass
class SessionCalendar:
    symbol: str
    windows: list[SessionWindow]
    source: str
    closures: set[date] = field(default_factory=set)  # full-day closures, server dates

    def is_open_server(self, t: datetime) -> bool:
        if t.date() in self.closures:
            return False
        tt = t.time()
        for w in self.windows:
            if w.weekday != t.weekday():
                continue
            end_ok = tt < w.end if w.end != time(0, 0) else True
            if w.start <= tt and end_ok:
                return True
        return False

    def minutes_to_close_server(self, t: datetime, horizon_minutes: int = 7 * 24 * 60) -> Optional[int]:
        if not self.is_open_server(t):
            return 0
        step = timedelta(minutes=1)
        cur = t
        for m in range(horizon_minutes):
            cur = cur + step
            if not self.is_open_server(cur):
                return m + 1
        return None

    def public(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "windows": [{"weekday": w.weekday, "start": w.start.strftime("%H:%M"), "end": w.end.strftime("%H:%M")}
                        for w in self.windows],
            "closures": sorted(d.isoformat() for d in self.closures),
        }


def _parse_hhmm(s: str) -> time:
    if s in ("24:00", "24:00:00"):
        return time(0, 0)
    h, m = s.split(":")[:2]
    return time(int(h), int(m))


def calendar_from_config(symbol: str, cfg: dict) -> SessionCalendar:
    """cfg: {"windows": [{"days": [0,1,2,3,4], "start": "01:00", "end": "24:00"}], "closures": ["2026-12-25"]}"""
    windows = []
    for w in cfg.get("windows", []):
        for d in w["days"]:
            windows.append(SessionWindow(int(d), _parse_hhmm(w["start"]), _parse_hhmm(w["end"])))
    closures = {date.fromisoformat(x) for x in cfg.get("closures", [])}
    return SessionCalendar(symbol, windows, "config", closures)


def calendar_from_mql5_export(path: Path, symbol: str, closures: Optional[set[date]] = None) -> Optional[SessionCalendar]:
    """Reads the JSON produced by mql5/ExportSessions.mq5."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    sym = data.get("symbols", {}).get(symbol)
    if not sym:
        return None
    windows = []
    for row in sym.get("trade", []):
        dow, frm, to = int(row["day"]), int(row["from"]), int(row["to"])
        wd = _MQL_DOW_TO_PY[dow]
        start = time(frm // 3600 % 24, frm // 60 % 60)
        end = time(0, 0) if to >= 86400 else time(to // 3600, to // 60 % 60)
        windows.append(SessionWindow(wd, start, end))
    return SessionCalendar(symbol, windows, f"mql5_export:{data.get('exported_at', 'unknown')}", closures or set())


def infer_gaps(bars: pd.DataFrame, timeframe_minutes: int, time_col: str = "server_time") -> pd.DataFrame:
    """Diagnostic: list gaps longer than 2 bars (weekend, daily break, holidays, outages)."""
    t = pd.to_datetime(bars[time_col]).sort_values().reset_index(drop=True)
    d = t.diff()
    mask = d > pd.Timedelta(minutes=2 * timeframe_minutes)
    out = pd.DataFrame({"gap_start": t.shift(1)[mask], "gap_end": t[mask], "gap": d[mask]})
    out["weekday_start"] = out["gap_start"].dt.weekday
    return out.reset_index(drop=True)


def default_metals_calendar(symbol: str) -> SessionCalendar:
    """Placeholder used only for synthetic research data and tests.

    Roughly the common retail metals CFD schedule in a UTC+2/+3 server clock:
    Mon-Fri 01:00-24:00 with a daily one-hour break 00:00-01:00. Real trading
    must use the MQL5 export or explicit config.
    """
    cal = calendar_from_config(symbol, {"windows": [{"days": [0, 1, 2, 3, 4], "start": "01:00", "end": "24:00"}]})
    cal.source = "synthetic_default"
    return cal


def local_session_bounds(day_utc: date, tz: str, start: str, end: str) -> tuple[datetime, datetime]:
    """UTC bounds of a local-time window (e.g. London 08:00-09:00), DST-correct."""
    z = ZoneInfo(tz)
    s = datetime.combine(day_utc, _parse_hhmm(start)).replace(tzinfo=z).astimezone(timezone.utc)
    e = datetime.combine(day_utc, _parse_hhmm(end)).replace(tzinfo=z).astimezone(timezone.utc)
    return s, e
