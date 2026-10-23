"""Strategy contract.

A strategy is a pure function of completed bars. It knows nothing about the
UI, the broker, the auditor or the risk engine. It proposes exposure changes;
it can never submit an order.

Lifecycle per decision time ``t`` (close time of the last completed bar):
  1. ``prepare(frames)`` adds causal indicator columns (done once over a frame;
     row i only uses rows <= i, verified by the no-lookahead tests).
  2. ``generate_signals(ctx)`` reads only the truncated frames in ``ctx`` and
     returns exits first, then entries.
  3. ``calculate_target_positions`` turns opening signals into volumes from the
     bot's *own* equity and the planned stop distance.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd

from ..domain import TIMEFRAME_MINUTES, Intent, PositionView, Signal, Style, TargetPosition
from ..instruments import InstrumentSpec


@dataclass(frozen=True)
class Param:
    default: Any
    rationale: str
    grid: tuple = ()


@dataclass
class StrategyContext:
    now: datetime
    frames: dict[tuple[str, str], pd.DataFrame]   # completed, prepared bars only
    positions: list[PositionView]
    specs: dict[str, InstrumentSpec]
    quotes: dict[str, dict] = field(default_factory=dict)

    def frame(self, role: str, tf: str) -> pd.DataFrame:
        return self.frames[(role, tf)]

    def last(self, role: str, tf: str, back: int = 1) -> pd.Series:
        return self.frames[(role, tf)].iloc[-back]

    def has(self, role: str, tf: str, n: int) -> bool:
        f = self.frames.get((role, tf))
        return f is not None and len(f) >= n

    def position(self, role: str) -> Optional[PositionView]:
        for p in self.positions:
            if p.role == role:
                return p
        return None


# loss_fn(spec, side, entry, stop, volume) -> loss in account currency (positive number)
LossFn = Callable[[InstrumentSpec, str, float, float, float], float]


def contract_loss(spec: InstrumentSpec, side: str, entry: float, stop: float, volume: float,
                  fx_to_account: float = 1.0) -> float:
    return abs(entry - stop) * spec.contract_size * volume * fx_to_account


class Strategy(ABC):
    strategy_id: str = ""
    name: str = ""
    description: str = ""
    style: Style = Style.DAY
    roles: tuple[str, ...] = ("GOLD", "SILVER")
    timeframes: tuple[str, ...] = ("M5",)
    primary_timeframe: str = "M5"
    min_history_bars: int = 300
    long_rules: str = ""
    short_rules: str = ""
    exit_rules: str = ""
    invalidation_rules: str = ""
    filters: str = ""
    citations: tuple[str, ...] = ()
    limitations: str = ""
    PARAMS: dict[str, Param] = {}
    magic_offset: int = 0

    def __init__(self, params: Optional[dict] = None):
        self.params = {k: p.default for k, p in self.PARAMS.items()}
        if params:
            unknown = set(params) - set(self.PARAMS)
            if unknown:
                raise ValueError(f"{self.strategy_id}: unknown parameters {sorted(unknown)}")
            self.params.update(params)

    @property
    def p(self) -> dict:
        return self.params

    # ------------------------------------------------------------------ data
    def required_data(self) -> list[tuple[str, str, int]]:
        return [(r, tf, self.min_history_bars) for r in self.roles for tf in self.timeframes]

    @abstractmethod
    def prepare(self, frames: dict[tuple[str, str], pd.DataFrame]) -> dict[tuple[str, str], pd.DataFrame]:
        ...

    # --------------------------------------------------------------- signals
    @abstractmethod
    def entry_signals(self, ctx: StrategyContext) -> list[Signal]:
        ...

    def exit_signals(self, ctx: StrategyContext) -> list[Signal]:
        return []

    def generate_signals(self, ctx: StrategyContext) -> list[Signal]:
        exits = self.exit_signals(ctx)
        exits = exits + self._time_exits(ctx, already={s.position_key for s in exits})
        if exits:
            return exits
        return self.entry_signals(ctx)

    def _time_exits(self, ctx: StrategyContext, already: set) -> list[Signal]:
        out = []
        for pos in ctx.positions:
            if pos.key in already or pos.max_hold_bars is None or pos.bars_held < pos.max_hold_bars:
                continue
            intent = Intent.CLOSE_LONG if pos.side == "LONG" else Intent.COVER_SHORT
            out.append(self.signal(ctx, pos.role, intent, f"max holding period reached ({pos.bars_held} >= {pos.max_hold_bars} bars)",
                                   {"bars_held": pos.bars_held}, position_key=pos.key, group_id=pos.group_id))
        return out

    def signal(self, ctx: StrategyContext, role: str, intent: Intent, rationale: str, indicators: dict, **kw) -> Signal:
        spec = ctx.specs[role]
        ref = kw.pop("ref_price", None)
        if ref is None:
            ref = float(ctx.last(role, self.primary_timeframe)["close"])
        return Signal(self.strategy_id, ctx.now, role, spec.symbol, intent, rationale,
                      {k: (round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                       for k, v in indicators.items()}, ref_price=ref, **kw)

    def close_signal(self, ctx: StrategyContext, pos: PositionView, rationale: str, indicators: dict) -> Signal:
        intent = Intent.CLOSE_LONG if pos.side == "LONG" else Intent.COVER_SHORT
        return self.signal(ctx, pos.role, intent, rationale, indicators, position_key=pos.key, group_id=pos.group_id)

    # ---------------------------------------------------------------- sizing
    def calculate_target_positions(self, signals: list[Signal], equity: float, specs: dict[str, InstrumentSpec],
                                   risk_pct: float, loss_fn: LossFn, cost_per_lot: Callable[[InstrumentSpec], float],
                                   min_lot: float = 0.0) -> list[TargetPosition]:
        """Risk-based sizing from the bot's own equity only.

        volume = floor_to_step(risk_budget / (stop loss per lot + est. round-trip cost per lot))
        With ``min_lot`` > 0 the volume is never below that floor (operator policy: trade at least
        min_lot even when it risks more than the budget). With ``min_lot`` == 0, trades whose
        broker minimum lot would exceed the budget are skipped, never upsized.
        """
        out: list[TargetPosition] = []
        legs_per_group: dict[str, int] = {}
        for s in signals:
            if s.intent.is_opening and s.group_id:
                legs_per_group[s.group_id] = legs_per_group.get(s.group_id, 0) + 1
        for s in signals:
            if not s.intent.is_opening:
                out.append(TargetPosition(s, volume=-1.0, planned_risk=0.0, risk_budget=0.0))
                continue
            spec = specs[s.role]
            budget = equity * risk_pct / legs_per_group.get(s.group_id, 1) if s.group_id else equity * risk_pct
            if s.stop_price is None or s.ref_price is None or s.stop_price == s.ref_price:
                out.append(TargetPosition(s, 0.0, 0.0, budget, True, "NO_DEFINED_STOP"))
                continue
            loss_1 = loss_fn(spec, s.intent.position_side, s.ref_price, s.stop_price, 1.0) + cost_per_lot(spec)
            raw = budget / loss_1 if loss_1 > 0 else 0.0
            vol = spec.round_volume_down(raw)
            if min_lot > 0:
                vol = max(vol, spec.round_volume_down(max(min_lot, spec.volume_min)))
            if vol <= 0:
                min_risk = loss_1 * spec.volume_min
                out.append(TargetPosition(s, 0.0, min_risk, budget, True,
                                          f"MIN_LOT_EXCEEDS_RISK_BUDGET (min lot risk {min_risk:.2f} > budget {budget:.2f})"))
                continue
            out.append(TargetPosition(s, vol, loss_1 * vol, budget))
        return out

    # ------------------------------------------------------------ metadata
    def risk_metadata(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "style": self.style.value,
            "requires_stop": True,
            "max_positions": len(self.roles),
            "uses_trade_groups": False,
            "primary_timeframe": self.primary_timeframe,
            "max_hold_bars": self.params.get("max_hold_bars"),
            "ohlc_reliability": "LOW (intrabar ordering unknown; needs tick data)" if self.style == Style.SCALP else "MODERATE",
        }

    def explain_signal(self, signal: Signal) -> str:
        ind = ", ".join(f"{k}={v}" for k, v in signal.indicators.items())
        lvl = []
        if signal.stop_price is not None:
            lvl.append(f"stop {signal.stop_price:.5g}")
        if signal.target_price is not None:
            lvl.append(f"target {signal.target_price:.5g}")
        if signal.max_hold_bars:
            lvl.append(f"max hold {signal.max_hold_bars} x {self.primary_timeframe}")
        return (f"[{self.strategy_id} {self.name}] {signal.timestamp:%Y-%m-%d %H:%M} UTC {signal.symbol}: "
                f"{signal.intent.value} ({signal.exposure_change}). Why: {signal.rationale}. "
                f"{'; '.join(lvl)}. Indicators: {ind}")

    # ---------------------------------------------------------- live thinking
    def diagnose(self, ctx: StrategyContext) -> dict:
        """Per-role checklist of this strategy's entry conditions on the latest completed bar.

        Returns {role: {"price": float, "levels": [...], "filters": [...], "long": [...], "short": [...]}}
        or {role: {"waiting": "reason"}}. Strategies override this; the verdict itself always
        comes from ``entry_signals`` so the explanation cannot disagree with real behaviour."""
        return {}

    def think(self, ctx: StrategyContext) -> dict:
        out = {"strategy_id": self.strategy_id, "timeframe": self.primary_timeframe, "as_of": ctx.now.isoformat(), "roles": {}}
        try:
            diag = self.diagnose(ctx)
        except Exception as e:  # never let an explanation break trading
            diag, out["error"] = {}, f"diagnosis failed: {e}"
        try:
            entries = self.entry_signals(ctx)
        except Exception as e:
            entries, out["error"] = [], f"entry check failed: {e}"
        for role, d in diag.items():
            pos = ctx.position(role)
            sig = next((x for x in entries if x.role == role), None)
            if "waiting" in d:
                verdict, summary = "WAITING_DATA", d["waiting"]
            elif pos is not None:
                verdict = f"IN_TRADE_{pos.side}"
                summary = f"in a {pos.side.lower()} trade since {pos.opened_at:%d %b %H:%M} UTC ({pos.bars_held} bars); managing exits"
            elif sig is not None:
                verdict = "ENTRY_LONG" if sig.intent == Intent.OPEN_LONG else "ENTRY_SHORT"
                summary = f"entry: {sig.rationale}"
            else:
                verdict = "NO_ENTRY"
                f_ok = sum(1 for c in d.get("filters", []) if c["ok"])
                best = max(("long", "short"), key=lambda k: sum(1 for c in d.get(k, []) if c["ok"]))
                n_ok = sum(1 for c in d.get(best, []) if c["ok"])
                summary = (f"no entry: {n_ok} of {len(d.get(best, []))} {best} conditions met, "
                           f"{f_ok} of {len(d.get('filters', []))} filters passed")
            out["roles"][role] = {**d, "verdict": verdict, "summary": summary,
                                  "signal": sig.to_dict() if sig is not None else None}
        return out

    def describe(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "style": self.style.value,
            "roles": list(self.roles),
            "timeframes": list(self.timeframes),
            "primary_timeframe": self.primary_timeframe,
            "min_history_bars": self.min_history_bars,
            "long_rules": self.long_rules,
            "short_rules": self.short_rules,
            "exit_rules": self.exit_rules,
            "invalidation_rules": self.invalidation_rules,
            "filters": self.filters,
            "params": {k: {"value": self.params[k], "default": p.default, "rationale": p.rationale, "grid": list(p.grid)}
                       for k, p in self.PARAMS.items()},
            "citations": list(self.citations),
            "limitations": self.limitations,
            "risk_metadata": self.risk_metadata(),
        }


def bar_close_time(df: pd.DataFrame, tf: str) -> pd.Series:
    return df["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[tf])


def spread_price(row: pd.Series, spec: InstrumentSpec) -> float:
    return float(row.get("spread", 0) or 0) * spec.point


def chk(label: str, ok, detail: str = "") -> dict:
    """One line of a bot's live checklist. NaN/None counts as not met (not enough data)."""
    try:
        good = bool(ok) and not pd.isna(ok)
    except (TypeError, ValueError):
        good = bool(ok)
    return {"label": label, "ok": good, "detail": detail}


def fmt(v, digits: int = 2) -> str:
    try:
        if v is None or pd.isna(v):
            return "n/a"
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def level(label: str, price, kind: str = "line") -> dict | None:
    try:
        if price is None or pd.isna(price):
            return None
        return {"label": label, "price": float(price), "kind": kind}
    except (TypeError, ValueError):
        return None
