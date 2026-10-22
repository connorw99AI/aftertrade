"""Core domain types shared by strategies, backtester and live pipeline."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class Intent(str, Enum):
    """Exposure change. Buy/sell alone is ambiguous, so intent is explicit."""

    OPEN_LONG = "OPEN_LONG"      # buy to open
    CLOSE_LONG = "CLOSE_LONG"    # sell to close an existing long
    OPEN_SHORT = "OPEN_SHORT"    # sell to open
    COVER_SHORT = "COVER_SHORT"  # buy to close an existing short

    @property
    def is_opening(self) -> bool:
        return self in (Intent.OPEN_LONG, Intent.OPEN_SHORT)

    @property
    def is_risk_reducing(self) -> bool:
        return not self.is_opening

    @property
    def order_side(self) -> str:
        return "BUY" if self in (Intent.OPEN_LONG, Intent.COVER_SHORT) else "SELL"

    @property
    def position_side(self) -> str:
        return "LONG" if self in (Intent.OPEN_LONG, Intent.CLOSE_LONG) else "SHORT"


class Style(str, Enum):
    SCALP = "SCALP"
    DAY = "DAY"
    SWING = "SWING"


TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


@dataclass
class Signal:
    strategy_id: str
    timestamp: datetime          # decision time (UTC) = close time of last completed bar
    role: str                    # GOLD / SILVER
    symbol: str                  # actual terminal symbol
    intent: Intent
    rationale: str
    indicators: dict[str, Any]
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    max_hold_bars: Optional[int] = None
    ref_price: Optional[float] = None
    group_id: Optional[str] = None      # legs of one explicitly defined trade group
    position_key: Optional[str] = None  # for closes: which internal position
    exposure_change: str = ""

    def __post_init__(self):
        if not self.exposure_change:
            self.exposure_change = {
                Intent.OPEN_LONG: "flat -> long (buy to open)",
                Intent.CLOSE_LONG: "long -> flat (sell to close)",
                Intent.OPEN_SHORT: "flat -> short (sell to open)",
                Intent.COVER_SHORT: "short -> flat (buy to cover)",
            }[self.intent]

    @property
    def signal_id(self) -> str:
        raw = f"{self.strategy_id}|{self.timestamp.isoformat()}|{self.symbol}|{self.intent.value}|{self.group_id}|{self.position_key}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["intent"] = self.intent.value
        d["timestamp"] = self.timestamp.isoformat()
        d["signal_id"] = self.signal_id
        d["indicators"] = {k: _jsonable(v) for k, v in self.indicators.items()}
        return d


def _jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        try:
            return float(v)
        except Exception:
            return str(v)


@dataclass
class PositionView:
    key: str
    role: str
    symbol: str
    side: str            # LONG / SHORT
    volume: float
    entry_price: float
    opened_at: datetime
    bars_held: int = 0
    sl: Optional[float] = None
    tp: Optional[float] = None
    max_hold_bars: Optional[int] = None
    group_id: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class TargetPosition:
    signal: Signal
    volume: float
    planned_risk: float          # account currency loss if stop is hit, incl. est. costs
    risk_budget: float
    skipped: bool = False
    skip_reason: str = ""


class Decision(str, Enum):
    APPROVED = "APPROVED"
    DELAYED = "DELAYED"
    REJECTED = "REJECTED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
