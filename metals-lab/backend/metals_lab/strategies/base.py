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
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ..domain import TIMEFRAME_MINUTES, Intent, PositionView, Signal, Style, TargetPosition
from ..instruments import InstrumentSpec


@dataclass(frozen=True)
class Param:
    default: Any
    rationale: str
    grid: tuple = ()


def in_weekend_flat(now: datetime, friday_from: Optional[str]) -> bool:
    """Friday from ``friday_from`` (UTC) until Sunday 22:00 UTC, when metals reopen."""
    if not friday_from:
        return False
    h, m = (int(x) for x in friday_from.split(":"))
    wd = now.weekday()
    return (wd == 4 and (now.hour, now.minute) >= (h, m)) or wd == 5 or (wd == 6 and now.hour < 22)


@dataclass
class StrategyContext:
    now: datetime
    frames: dict[tuple[str, str], pd.DataFrame]   # completed, prepared bars only
    positions: list[PositionView]
    specs: dict[str, InstrumentSpec]
    quotes: dict[str, dict] = field(default_factory=dict)
    weekend_flat_from: Optional[str] = None       # "HH:MM" UTC on Friday: close everything, no entries until Sunday 22:00 UTC

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


def local_window(day: date, tz: str, start: str, end: str) -> tuple[datetime, datetime]:
    """UTC start/end of a local-time window ("HH:MM") on ``day`` in ``tz`` (DST-aware). End before start = next day."""
    z = ZoneInfo(tz)
    (h1, m1), (h2, m2) = (map(int, start.split(":")), map(int, end.split(":")))
    a = datetime(day.year, day.month, day.day, h1, m1, tzinfo=z)
    b = datetime(day.year, day.month, day.day, h2, m2, tzinfo=z)
    if b <= a:
        b += timedelta(days=1)
    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


def in_local_window(now: datetime, tz: str, start: str, end: str) -> bool:
    """Is ``now`` inside a daily local-time window (DST-aware)?"""
    loc = now.astimezone(ZoneInfo(tz))
    m = loc.hour * 60 + loc.minute
    (h1, m1), (h2, m2) = (map(int, start.split(":")), map(int, end.split(":")))
    a, b = h1 * 60 + m1, h2 * 60 + m2
    return a <= m < b if a < b else (m >= a or m < b)


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
    # Assessor-derived defaults for the shared trade filters: {"sides", "trade_gold", "trade_silver",
    # "gold_windows", "silver_windows", "evidence"}. Windows are UK time.
    ASSESSOR_FILTERS: dict = {}
    pair_trades: bool = False      # two-leg groups: side/metal switches do not apply; both metals' windows must be open
    session_text: str = ""         # built-in entry window in words, e.g. "08:00-21:00 London"; "" = any time
    RETIRED_PARAMS: tuple = ()     # parameter names from older versions: ignored if still saved on a bot

    @classmethod
    def param_defs(cls) -> dict[str, Param]:
        from .filters import ALL_DAY
        f = cls.ASSESSOR_FILTERS
        why = f.get("evidence", "No assessor change; trades every side, metal and hour.")
        win = "UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day)."
        exits = {} if cls.pair_trades else {  # a pair's legs exit together on its own signal
            "breakeven_r": Param(0.0, "Move the stop to the entry price once a trade is this many R in profit "
                                      "(R = the distance to the original stop). 0 = off.", (0.0, 0.5, 1.0, 1.5)),
            "trail_atr": Param(0.0, "Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only "
                                    "ever tightens. When on, the fixed target is dropped so winners can run. 0 = off.",
                               (0.0, 2.0, 3.0, 4.0))}
        return {**cls.PARAMS, **exits,
                "sides": Param(f.get("sides", "both"), f"Which sides may open: both, long or short. {why}"),
                "trade_gold": Param(f.get("trade_gold", "on"), "on: this bot may open gold trades; off: it does not trade gold."),
                "trade_silver": Param(f.get("trade_silver", "on"), "on: this bot may open silver trades; off: it does not trade silver."),
                "gold_windows": Param(f.get("gold_windows", ALL_DAY), f"Gold: {win}"),
                "silver_windows": Param(f.get("silver_windows", ALL_DAY), f"Silver: {win}")}

    def __init__(self, params: Optional[dict] = None):
        from .filters import LEGACY_PARAMS, validate
        defs = self.param_defs()
        self.params = {k: p.default for k, p in defs.items()}
        if params:
            params = {k: v for k, v in params.items() if k not in self.RETIRED_PARAMS and k not in LEGACY_PARAMS}
            unknown = set(params) - set(defs)
            if unknown:
                raise ValueError(f"{self.strategy_id}: unknown parameters {sorted(unknown)}")
            self.params.update(params)
        validate(self.params)

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
        if in_weekend_flat(ctx.now, ctx.weekend_flat_from):
            exits = self.exit_signals(ctx) if ctx.positions else []
            done = {s.position_key for s in exits}
            for pos in ctx.positions:
                if pos.key not in done:
                    exits.append(self.close_signal(ctx, pos, "flat for the weekend", {}))
            return exits
        exits = self.exit_signals(ctx)
        exits = exits + self._time_exits(ctx, already={s.position_key for s in exits})
        if exits:
            return exits
        return self.entries(ctx)

    def entries(self, ctx: StrategyContext) -> list[Signal]:
        """The strategy's entry signals after the shared side / metal switch / per-metal trading-window filters."""
        from .filters import in_windows, metal_open, side_allowed
        sigs = self.entry_signals(ctx)
        if not sigs:
            return []
        if self.pair_trades:  # the side of a pair is the side of its silver leg; both legs go or neither
            if not (in_windows(ctx.now, self.p["gold_windows"]) and in_windows(ctx.now, self.p["silver_windows"])):
                return []
            silver = next((x for x in sigs if x.role == "SILVER"), None)
            return sigs if silver is not None and side_allowed(silver.intent, self.p["sides"]) else []
        out = [s for s in sigs if side_allowed(s.intent, self.p["sides"]) and metal_open(self.p, s.role, ctx.now)]
        if float(self.p.get("trail_atr") or 0) > 0:  # the trailing stop replaces the fixed target
            out = [replace(s, target_price=None) if s.intent.is_opening else s for s in out]
        return out

    def manage_stops(self, ctx: StrategyContext) -> list[tuple[str, float, str]]:
        """Stop moves for open trades from the bot's exit options (breakeven, ATR trail): [(position key, new stop, why)].
        A stop only ever moves towards profit, and stays on the right side of the last close. Same code live and in backtests."""
        be, trail = float(self.p.get("breakeven_r") or 0), float(self.p.get("trail_atr") or 0)
        if not ctx.positions or (be <= 0 and trail <= 0):
            return []
        from ..indicators import atr
        out = []
        for pos in ctx.positions:
            f = ctx.frames.get((pos.role, self.primary_timeframe))
            if f is None or len(f) < 15 or pos.sl is None:
                continue
            since = f[f["time"] >= pd.Timestamp(pos.opened_at).floor("min")]
            if since.empty:
                continue
            long = pos.side == "LONG"
            best = float(since["high"].max()) if long else float(since["low"].min())
            close = float(f["close"].iloc[-1])
            init = float(pos.meta.get("init_sl", pos.sl))
            r = abs(pos.entry_price - init)
            cands = []
            if be > 0 and r > 0 and (best - pos.entry_price if long else pos.entry_price - best) >= be * r:
                cands.append((pos.entry_price, f"breakeven after +{be:g}R"))
            if trail > 0:
                a = float(atr(f.iloc[-30:], 14).iloc[-1])
                if a == a and a > 0:
                    cands.append((best - trail * a if long else best + trail * a, f"trailing {trail:g} ATR behind the best price"))
            spec = ctx.specs.get(pos.role)
            gap = max(spec.stops_level_points * spec.point, spec.point) if spec else 0.0
            for px, why in sorted(cands, key=lambda c: -c[0] if long else c[0]):
                px = spec.round_price(px) if spec else px
                better = px > pos.sl if long else px < pos.sl
                valid = px < close - gap if long else px > close + gap
                if better and valid:
                    out.append((pos.key, px, why))
                    break
        return out

    def _filter_checks(self, ctx: StrategyContext, role: str) -> tuple[list, list, list]:
        from .filters import ALL_DAY, METAL_PARAMS, UK, in_windows, metal_on, parse_windows
        now_uk = f"now {ctx.now.astimezone(UK):%H:%M} UK"
        if self.pair_trades:
            g, sv = self.p["gold_windows"], self.p["silver_windows"]
            f = [] if parse_windows(g) == parse_windows(sv) == parse_windows(ALL_DAY) else [
                chk(f"Inside the gold and silver trading windows (UK gold {g}; silver {sv})",
                    in_windows(ctx.now, g) and in_windows(ctx.now, sv), now_uk)]
            sd = self.p["sides"]
            if sd == "both" or role != "SILVER":
                return f, [], []
            return (f, [chk("Silver-cheap direction allowed", sd == "long", "")],
                    [chk("Gold-cheap direction allowed", sd == "short", "")])
        metal = role.lower()
        f = []
        if not metal_on(self.p, role):
            f.append(chk(f"Metal allowed ({metal} switched off)", False, "off in this bot's settings"))
        w = self.p[METAL_PARAMS[role][1]]
        if parse_windows(w) != parse_windows(ALL_DAY):
            f.append(chk(f"Inside the {metal} trading window (UK {w})", in_windows(ctx.now, w), now_uk))
        lo = [chk("Long trades allowed", True, "")] if self.p["sides"] == "long" else ([chk("Long trades allowed", False, "short only")] if self.p["sides"] == "short" else [])
        sh = [chk("Short trades allowed", True, "")] if self.p["sides"] == "short" else ([chk("Short trades allowed", False, "long only")] if self.p["sides"] == "long" else [])
        return f, lo, sh

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
                                   min_lot: float | dict = 0.0, max_risk_pct: float = 0.0,
                                   big_lot: float | dict = 0.0, big_lot_max_risk: float = 0.0,
                                   max_lots: float | dict = 0.0) -> list[TargetPosition]:
        """Risk-based sizing from the bot's own equity only.

        volume = floor_to_step(risk_budget / (stop loss per lot + est. round-trip cost per lot))
        ``min_lot`` is a number or {role: lots}. When > 0 the volume is never below that floor (operator policy: trade at least
        min_lot even when it risks more than the budget). With ``min_lot`` == 0, trades whose
        broker minimum lot would exceed the budget are skipped, never upsized.
        ``big_lot`` (number or {role: lots}) is the size for tighter stops: used instead of ``min_lot`` when its loss at the
        strategy's stop is at most ``big_lot_max_risk`` of equity. Wider stops fall back to ``min_lot``. The stop itself is
        always the strategy's; only the size changes.
        ``max_lots`` (number or {role: lots}) clamps any single trade, however large the risk budget grows.
        ``max_risk_pct`` > 0 is a hard cap: a trade whose size (after the floor) would lose more than that share of
        equity at its stop is skipped, so the floor can never turn into an outsized bet.
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
            floor = float(min_lot.get(s.role, 0.0) if isinstance(min_lot, dict) else min_lot or 0.0)
            big = float(big_lot.get(s.role, 0.0) if isinstance(big_lot, dict) else big_lot or 0.0)
            if big > floor and big_lot_max_risk > 0 and loss_1 * spec.round_volume_down(big) <= equity * big_lot_max_risk:
                floor = big  # tight stop: the larger size still loses no more than big_lot_max_risk at the stop
            if floor > 0:
                vol = max(vol, spec.round_volume_down(max(floor, spec.volume_min)))
            if vol <= 0:
                min_risk = loss_1 * spec.volume_min
                out.append(TargetPosition(s, 0.0, min_risk, budget, True,
                                          f"MIN_LOT_EXCEEDS_RISK_BUDGET (min lot risk {min_risk:.2f} > budget {budget:.2f})"))
                continue
            cap = float(max_lots.get(s.role, 0.0) if isinstance(max_lots, dict) else max_lots or 0.0)
            if cap > 0 and vol > cap:
                vol = spec.round_volume_down(cap)
            if max_risk_pct > 0 and loss_1 * vol > equity * max_risk_pct:
                out.append(TargetPosition(s, 0.0, loss_1 * vol, budget, True,
                                          f"MIN_LOT_RISK_ABOVE_CAP (risk {loss_1 * vol:.2f} > cap {equity * max_risk_pct:.2f})"))
                continue
            out.append(TargetPosition(s, vol, loss_1 * vol, budget))
        return out

    # ------------------------------------------------------- trading window
    def session_windows(self, day: date) -> Optional[list[tuple[datetime, datetime]]]:
        """UTC intervals on ``day`` in which the strategy's own rules allow new entries; None = any time."""
        return None

    def trading_window(self, now: datetime) -> dict:
        """When this bot can actually open trades today (the UK day): its built-in session combined with each metal's
        switch and trading windows. Intervals are minutes after UK midnight."""
        from .filters import METAL_PARAMS, UK, hhmm, merge, metal_on, parse_windows
        day = now.astimezone(UK).date()
        local = datetime(day.year, day.month, day.day)                       # UK midnight, wall clock
        utc_at = lambda m: (local + timedelta(minutes=m)).replace(tzinfo=UK).astimezone(timezone.utc)
        d0, d1 = utc_at(0), utc_at(24 * 60)
        built = []
        for k in (-1, 0, 1):  # windows set in another time zone can start the evening before or end the morning after
            w = self.session_windows(day + timedelta(days=k))
            if w is None:
                built = [(d0, d1)]
                break
            built += w

        def uk_minutes(t: datetime) -> int:
            return 24 * 60 if t >= d1 else int((t.astimezone(UK).replace(tzinfo=None) - local).total_seconds() // 60)

        def allowed(spec: str) -> list[tuple[int, int]]:
            out = []
            for a, b in parse_windows(spec):
                ua, ub = utc_at(a), utc_at(b)
                for c, e in built:
                    lo, hi = max(ua, c, d0), min(ub, e, d1)
                    if lo < hi:
                        out.append((uk_minutes(lo), uk_minutes(hi)))
            joined: list = []
            for a, b in sorted(x for x in out if x[0] < x[1]):  # windows that touch (within a minute) read as one
                if joined and a <= joined[-1][1] + 1:
                    joined[-1] = (joined[-1][0], max(joined[-1][1], b))
                else:
                    joined.append((a, b))
            return joined

        txt = lambda iv: ", ".join(f"{hhmm(a)}-{hhmm(b)}" for a, b in iv) + " UK"
        metals = {}
        for role, (on, win) in METAL_PARAMS.items():
            is_on = self.pair_trades or metal_on(self.p, role)
            iv = allowed(self.p[win]) if is_on else []
            metals[role.lower()] = {"on": is_on, "windows": self.p[win], "intervals": [list(x) for x in iv],
                                    "effective": txt(iv) if iv else ("switched off" if not is_on else "none today")}
        if self.pair_trades:  # both legs open together
            g, sv = metals["gold"]["intervals"], metals["silver"]["intervals"]
            eff = [(max(a, c), min(b, e)) for a, b in g for c, e in sv if max(a, c) < min(b, e)]
        else:
            eff = merge([tuple(x) for m in metals.values() for x in m["intervals"]])
        return {"built_in": self.session_text or "any time", "gold": metals["gold"], "silver": metals["silver"],
                "effective": txt(eff) if eff else "none: no metal is switched on inside its windows today",
                "all_day": eff == [(0, 24 * 60)], "day": str(day), "clock": "UK",
                "intervals": [list(x) for x in eff]}

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
            entries = self.entries(ctx)
        except Exception as e:
            entries, out["error"] = [], f"entry check failed: {e}"
        for role, d in diag.items():
            if "waiting" not in d:
                f, lo, sh = self._filter_checks(ctx, role)
                d = {**d, "filters": list(d.get("filters", [])) + f, "long": list(d.get("long", [])) + lo, "short": list(d.get("short", [])) + sh}
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
                       for k, p in self.param_defs().items()},
            "assessor_filters": self.ASSESSOR_FILTERS,
            "pair_trades": self.pair_trades,
            "trading_window": self.trading_window(_utc_now()),
            "citations": list(self.citations),
            "limitations": self.limitations,
            "risk_metadata": self.risk_metadata(),
        }


def _utc_now() -> datetime:
    from ..timeutil import utc_now
    return utc_now()


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
