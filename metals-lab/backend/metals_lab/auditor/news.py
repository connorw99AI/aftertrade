"""Replaceable economic-calendar providers.

A provider must say whether it *knows*. Missing, stale or failed calendars
produce status UNKNOWN - never "no news".

CsvCalendarProvider format (UTF-8):

    # as_of: 2026-10-20T00:00:00Z
    # source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm ; https://www.bls.gov/schedule/
    event_time_utc,currency,title,impact,known_since_utc
    2026-11-06T13:30:00Z,USD,Employment Situation (NFP),HIGH,2025-12-01T00:00:00Z

``as_of`` = when the operator last verified the file against authoritative
sources. If it is older than ``max_age_hours`` the provider reports UNKNOWN.
``known_since_utc`` enables point-in-time historical use (an event is only
visible to a backtest decision after it was scheduled/known).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


@dataclass
class NewsEvent:
    time: datetime
    currency: str
    title: str
    impact: str
    known_since: Optional[datetime] = None

    def public(self) -> dict:
        return {"time": self.time.isoformat(), "currency": self.currency, "title": self.title, "impact": self.impact}


@dataclass
class NewsResult:
    status: str                      # OK | UNKNOWN
    events: list[NewsEvent] = field(default_factory=list)
    source: str = ""
    reason: str = ""
    as_of: Optional[str] = None


class NewsProvider:
    name = "base"

    def events(self, start: datetime, end: datetime, as_of: datetime, point_in_time: bool = False) -> NewsResult:
        raise NotImplementedError


class NullProvider(NewsProvider):
    name = "none"

    def events(self, start, end, as_of, point_in_time=False) -> NewsResult:
        return NewsResult("UNKNOWN", source=self.name, reason="no news provider configured")


class FailingProvider(NewsProvider):
    """Test helper simulating an outage."""
    name = "failing"

    def events(self, start, end, as_of, point_in_time=False) -> NewsResult:
        return NewsResult("UNKNOWN", source=self.name, reason="provider outage")


def _parse_dt(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class CsvCalendarProvider(NewsProvider):
    name = "csv"

    def __init__(self, path: str | Path, max_age_hours: float = 192):
        self.path = Path(path)
        self.max_age = timedelta(hours=max_age_hours)

    def _load(self) -> tuple[Optional[datetime], list[NewsEvent], str]:
        if not self.path.exists():
            return None, [], f"calendar file not found: {self.path.name}"
        as_of = None
        lines = []
        for line in self.path.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("#"):
                if line.lower().startswith("# as_of:"):
                    try:
                        as_of = _parse_dt(line.split(":", 1)[1])
                    except ValueError:
                        as_of = None
                continue
            if line.strip():
                lines.append(line)
        evs = []
        for row in csv.DictReader(lines):
            try:
                evs.append(NewsEvent(_parse_dt(row["event_time_utc"]), row.get("currency", ""), row.get("title", ""),
                                     row.get("impact", "").upper(),
                                     _parse_dt(row["known_since_utc"]) if row.get("known_since_utc") else None))
            except (KeyError, ValueError):
                continue
        return as_of, evs, ""

    def events(self, start, end, as_of, point_in_time=False) -> NewsResult:
        file_as_of, evs, err = self._load()
        if err:
            return NewsResult("UNKNOWN", source=self.name, reason=err)
        if not point_in_time:
            if file_as_of is None:
                return NewsResult("UNKNOWN", source=self.name, reason="calendar has no '# as_of:' verification header")
            if as_of - file_as_of > self.max_age:
                return NewsResult("UNKNOWN", source=self.name, reason=f"calendar last verified {file_as_of:%Y-%m-%d}, older than limit",
                                  as_of=file_as_of.isoformat())
            if end > file_as_of + timedelta(days=60):
                # Window extends far beyond what the operator verified.
                return NewsResult("UNKNOWN", source=self.name, reason="query window extends beyond verified calendar horizon")
        sel = [e for e in evs if start <= e.time <= end and (not point_in_time or e.known_since is None or e.known_since <= as_of)]
        return NewsResult("OK", sel, self.name, as_of=file_as_of.isoformat() if file_as_of else None)


def build_provider(cfg: dict) -> NewsProvider:
    n = cfg.get("news", {})
    if n.get("provider") == "csv":
        return CsvCalendarProvider(n.get("path", "config/news_calendar.csv"), n.get("max_age_hours", 192))
    return NullProvider()
