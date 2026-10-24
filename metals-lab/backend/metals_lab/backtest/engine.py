"""Deterministic event-driven bar backtester.

Timing (no lookahead):
  * at bar close T the strategy sees only bars whose close time <= T;
  * orders created at T fill at the OPEN of the next available bar of that
    instrument (a later bar if the market was closed - flagged as a gap fill);
  * protective stops/targets are evaluated on each subsequent bar's range.

Price model: bars are BID. ask = bid + spread. Buys fill at ask + slippage,
sells at bid - slippage. Stops that gap fill at the (worse) open. If a bar
touches both stop and target, the stop is assumed first unless tick data for
that bar resolves the order; every such case is counted as ``ambiguous_exit``.
"""
from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..domain import TIMEFRAME_MINUTES, Intent, PositionView, Signal, Style
from ..instruments import InstrumentSpec
from ..strategies.base import Strategy, StrategyContext, contract_loss
from ..risk.daily import daily_stop
from ..risk.guard import LOSS_LIMITS, in_cooldown, weekly_stop
from . import metrics as M
from .models import BacktestConfig


def _ns(df: pd.DataFrame) -> pd.DataFrame:
    """Copy with nanosecond UTC timestamps (pandas>=3 may default to us resolution)."""
    df = df.reset_index(drop=True).copy()
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.as_unit("ns")
    return df


AuditFn = Callable[[Signal, pd.Timestamp], tuple[str, list[str]]]


class NoDataInRange(ValueError):
    """The requested backtest dates have no downloaded price bars."""


def _day(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


def _day_before(end: str) -> str:
    return (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


@dataclass
class _Pos:
    key: str
    role: str
    side: str
    volume: float
    entry: float
    sl: Optional[float]
    tp: Optional[float]
    opened_at: pd.Timestamp
    planned_risk: float
    signal: Signal
    max_hold: Optional[int]
    group_id: Optional[str]
    bars_held: int = 0
    commission: float = 0.0
    swap: float = 0.0
    spread_slip_cost: float = 0.0
    partial: bool = False


@dataclass
class _Order:
    signal: Signal
    volume: float
    planned_risk: float
    created: pd.Timestamp
    waited: int = 0


@dataclass
class BacktestResult:
    strategy_id: str
    params: dict
    trades: pd.DataFrame
    equity: pd.Series
    metrics: dict
    diagnostics: dict
    assumptions: dict
    reliability: list[str]
    decisions: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"strategy_id": self.strategy_id, "params": self.params, "metrics": self.metrics,
                "diagnostics": self.diagnostics, "reliability": self.reliability}


class Backtester:
    def __init__(self, strategy: Strategy, frames: dict[tuple[str, str], pd.DataFrame], specs: dict[str, InstrumentSpec],
                 config: Optional[BacktestConfig] = None, audit_fn: Optional[AuditFn] = None,
                 ticks: Optional[dict[str, pd.DataFrame]] = None, data_meta: Optional[dict] = None,
                 record_decisions: bool = False):
        self.s = strategy
        self.cfg = config or BacktestConfig()
        self.specs = specs
        self.audit_fn = audit_fn
        self.ticks = ticks or {}
        self.data_meta = data_meta or {}
        self.record_decisions = record_decisions
        need = {(r, tf) for r in strategy.roles for tf in strategy.timeframes}
        missing = need - set(frames)
        if missing:
            raise ValueError(f"missing data for {sorted(missing)}")
        self.frames = strategy.prepare({k: _ns(frames[k]) for k in need})
        self.rng = np.random.default_rng(self.cfg.execution.seed)
        self._ids = itertools.count(1)

    # ----------------------------------------------------------------- costs
    def _spread_pts(self, role: str, bar_spread: float) -> float:
        c = self.cfg.costs
        base = bar_spread if c.spread_source == "bar" and bar_spread > 0 else c.fixed_spread_points[role]
        return max(base, c.min_spread_points.get(role, 0)) * c.spread_multiplier

    def _slip(self, role: str) -> float:
        c = self.cfg.costs
        return c.slippage_points[role] * c.slippage_multiplier * self.specs[role].point

    def _commission(self, vol: float) -> float:
        c = self.cfg.costs
        return -c.commission_per_lot_side * c.commission_multiplier * vol

    def _cost_per_lot(self, spec: InstrumentSpec) -> float:
        role = spec.role
        pts = self._spread_pts(role, 0) + 2 * self.cfg.costs.slippage_points[role] * self.cfg.costs.slippage_multiplier
        return pts * spec.point * spec.contract_size * self.cfg.costs.fx_to_account + 2 * -self._commission(1.0)

    def _loss(self, spec, side, entry, stop, vol):
        return contract_loss(spec, side, entry, stop, vol, self.cfg.costs.fx_to_account)

    # ------------------------------------------------------------------ run
    def run(self) -> BacktestResult:
        s, cfg = self.s, self.cfg
        ptf = s.primary_timeframe
        step = pd.Timedelta(minutes=TIMEFRAME_MINUTES[ptf])
        fx = cfg.costs.fx_to_account
        close_ns = {k: (df["time"] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[k[1]])).astype("int64").to_numpy()
                    for k, df in self.frames.items()}
        prim = {r: self.frames[(r, ptf)] for r in s.roles}
        arrays = {r: {c: df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "spread")} for r, df in prim.items()}
        tindex = {r: pd.Index(df["time"].astype("int64")) for r, df in prim.items()}
        timeline = np.unique(np.concatenate([tindex[r].to_numpy() for r in s.roles]))
        if cfg.start:
            timeline = timeline[timeline >= pd.Timestamp(cfg.start, tz="UTC").value]
        if cfg.end:
            timeline = timeline[timeline < pd.Timestamp(cfg.end, tz="UTC").value]
        if not len(timeline):
            # never report "0 trades" for a period there is no price data for: say what data exists instead
            have = "; ".join(f"{r.lower()} {ptf} {_day(df['time'].iloc[0])} to {_day(df['time'].iloc[-1])}" if len(df) else f"{r.lower()} {ptf} none"
                             for r, df in prim.items())
            raise NoDataInRange(f"no {ptf} price history between {cfg.start or 'the start'} and {_day_before(cfg.end) if cfg.end else 'today'}. "
                                f"Downloaded history covers: {have}. Download that period from MT5 first; MT5 only returns as much "
                                f"history as the broker keeps and its 'Max bars in chart' setting allows.")
        self._coverage_gap = self._coverage_note(prim, timeline)

        cash = cfg.initial_capital
        positions: dict[str, _Pos] = {}
        pending: list[_Order] = []
        trades: list[dict] = []
        eq_t, eq_v = [], []
        last_close = {r: np.nan for r in s.roles}
        last_spread = {r: 0.0 for r in s.roles}
        diag = {"decisions": 0, "signals": 0, "rejected_random": 0, "partial_fills": 0, "rejected_margin": 0,
                "rejected_stops": 0, "skipped_min_lot": 0, "skipped_no_stop": 0, "blocked_auditor": 0,
                "blocked_risk_pause": 0, "blocked_max_positions": 0, "blocked_daily_entry_cap": 0, "gap_fills": 0, "ambiguous_bars_tick_resolved": 0,
                "group_leg_failures": 0, "daily_pauses": 0, "daily_profit_stops": 0, "daily_profit_locks": 0, "weekly_pauses": 0, "loss_streak_pauses": [], "blocked_cooldown": 0,
                "blocked_lot_cap": 0, "blocked_daily_headroom": 0, "guard_closes": 0, "drawdown_pause_at": None, "drawdown_restarts": [], "swap_mode_unsupported": []}
        decisions: list[dict] = []
        peak = cash
        day_key, day_start_eq, day_high_eq, paused_today, dd_paused = None, cash, cash, False, False
        week_key, week_start_eq, paused_week = None, cash, False
        guard = {"streak": 0, "last_loss": None, "streak_paused": False}
        entries_day: set = set()
        roll_key_prev = None
        rh = cfg.costs.rollover_utc_hour

        def equity_now() -> float:
            u = 0.0
            for p in positions.values():
                spec = self.specs[p.role]
                px = last_close[p.role] if p.side == "LONG" else last_close[p.role] + last_spread[p.role] * spec.point
                if np.isnan(px):
                    continue
                sign = 1 if p.side == "LONG" else -1
                u += sign * (px - p.entry) * spec.contract_size * p.volume * fx + p.swap
            return cash + u

        def close_pos(p: _Pos, price: float, t: pd.Timestamp, reason: str, rationale: str = "", ambiguous: bool = False,
                      exit_cost: float = 0.0):
            nonlocal cash
            spec = self.specs[p.role]
            sign = 1 if p.side == "LONG" else -1
            gross = sign * (price - p.entry) * spec.contract_size * p.volume * fx
            com = self._commission(p.volume)
            cash += gross + com + p.swap
            total_com = p.commission + com
            net = gross + total_com + p.swap
            trades.append({
                "trade_id": p.key, "strategy_id": s.strategy_id, "role": p.role, "symbol": spec.symbol, "side": p.side,
                "volume": p.volume, "entry_time": p.opened_at, "entry_price": p.entry, "exit_time": t, "exit_price": price,
                "exit_reason": reason, "gross_pnl": round(gross, 4), "commission": round(total_com, 4),
                "swap": round(p.swap, 4), "spread_slip_cost": round(p.spread_slip_cost + exit_cost, 4),
                "net_pnl": round(net, 4), "planned_risk": round(p.planned_risk, 4),
                "r_multiple": round(net / p.planned_risk, 4) if p.planned_risk else None, "bars_held": p.bars_held,
                "entry_rationale": p.signal.rationale, "entry_indicators": p.signal.indicators, "exit_rationale": rationale,
                "group_id": p.group_id, "ambiguous_exit": ambiguous, "partial_fill": p.partial,
                "stop": p.sl, "target": p.tp,
            })
            del positions[p.key]
            if net < 0:  # losing streak and cooldown, per the guard
                guard["streak"] += 1
                guard["last_loss"] = t
            else:
                guard["streak"] = 0

        def execute(o: _Order, i: int, t: pd.Timestamp) -> bool:
            nonlocal cash
            sig = o.signal
            role = sig.role
            spec = self.specs[role]
            a = arrays[role]
            spr = self._spread_pts(role, a["spread"][i]) * spec.point
            slip = self._slip(role)
            bid_o, ask_o = a["open"][i], a["open"][i] + spr
            if sig.intent.is_risk_reducing:
                p = positions.get(sig.position_key)
                if p is None:
                    return True
                px = bid_o - slip if p.side == "LONG" else ask_o + slip
                cost = (slip + (spr if p.side == "SHORT" else 0)) * spec.contract_size * p.volume * fx
                close_pos(p, px, t, "SIGNAL_EXIT", sig.rationale, exit_cost=cost)
                return True
            if self.rng.random() < cfg.execution.reject_prob:
                diag["rejected_random"] += 1
                return False
            vol = o.volume
            partial = False
            if self.rng.random() < cfg.execution.partial_prob:
                v2 = spec.round_volume_down(vol * cfg.execution.partial_fraction)
                if v2 > 0:
                    vol, partial = v2, True
                    diag["partial_fills"] += 1
            long = sig.intent == Intent.OPEN_LONG
            fill = ask_o + slip if long else bid_o - slip
            if sig.stop_price is None or (long and sig.stop_price >= fill) or (not long and sig.stop_price <= fill) \
                    or abs(fill - sig.stop_price) < spec.min_stop_distance():
                diag["rejected_stops"] += 1
                return False
            used = sum(p.volume * self.specs[p.role].contract_size * last_close[p.role] / cfg.execution.leverage * fx
                       for p in positions.values() if not np.isnan(last_close[p.role]))
            need = vol * spec.contract_size * fill / cfg.execution.leverage * fx
            if used + need > equity_now() * cfg.risk.max_margin_fraction:
                diag["rejected_margin"] += 1
                return False
            key = f"{s.strategy_id}-{next(self._ids)}"
            com = self._commission(vol)
            cash += com
            entry_cost = (slip + (spr if long else 0)) * spec.contract_size * vol * fx
            positions[key] = _Pos(key, role, "LONG" if long else "SHORT", vol, fill, sig.stop_price, sig.target_price, t,
                                  self._loss(spec, "", fill, sig.stop_price, vol) + self._cost_per_lot(spec) * vol,
                                  sig, sig.max_hold_bars, sig.group_id, commission=com, spread_slip_cost=entry_cost,
                                  partial=partial)
            return True

        def check_stops(p: _Pos, i: int, t: pd.Timestamp):
            spec = self.specs[p.role]
            a = arrays[p.role]
            spr = self._spread_pts(p.role, a["spread"][i]) * spec.point
            slip = self._slip(p.role)
            o, h, l = a["open"][i], a["high"][i], a["low"][i]
            if p.side == "LONG":
                sl_hit = p.sl is not None and l <= p.sl
                tp_hit = p.tp is not None and h >= p.tp
                sl_px = (o - slip) if (p.sl is not None and o <= p.sl) else (p.sl - slip if p.sl else None)
                tp_px = o if (p.tp is not None and o >= p.tp) else p.tp
            else:
                ao, ah, al = o + spr, h + spr, l + spr
                sl_hit = p.sl is not None and ah >= p.sl
                tp_hit = p.tp is not None and al <= p.tp
                sl_px = (ao + slip) if (p.sl is not None and ao >= p.sl) else (p.sl + slip if p.sl else None)
                tp_px = ao if (p.tp is not None and ao <= p.tp) else p.tp
            if not (sl_hit or tp_hit):
                return
            ambiguous = sl_hit and tp_hit
            first = "SL"
            if ambiguous:
                res = self._resolve_with_ticks(p, t, spr)
                if res:
                    first = res
                    diag["ambiguous_bars_tick_resolved"] += 1
            elif tp_hit:
                first = "TP"
            if first == "SL":
                close_pos(p, sl_px, t, "STOP_LOSS", "protective stop", ambiguous=ambiguous and first == "SL",
                          exit_cost=slip * spec.contract_size * p.volume * fx)
            else:
                close_pos(p, tp_px, t, "TAKE_PROFIT", "target reached", ambiguous=False)

        for tns in timeline:
            t = pd.Timestamp(tns, tz="UTC")
            bars_now = {}
            for r in s.roles:
                loc = tindex[r].get_indexer([tns])[0]
                if loc >= 0:
                    bars_now[r] = loc
            # swaps at rollover
            rk = (t - pd.Timedelta(hours=rh)).floor("1D")
            if roll_key_prev is not None and rk != roll_key_prev and cfg.costs.use_swaps:
                wd = roll_key_prev.weekday()
                for p in positions.values():
                    spec = self.specs[p.role]
                    if spec.swap_mode != 1:
                        if spec.symbol not in diag["swap_mode_unsupported"]:
                            diag["swap_mode_unsupported"].append(spec.symbol)
                        continue
                    pts = spec.swap_long if p.side == "LONG" else spec.swap_short
                    mult = 3 if wd == (spec.swap_rollover3days - 1) % 7 else 1
                    # one charge per rollover event; the broker's triple-swap day covers the weekend
                    p.swap += pts * spec.point * spec.contract_size * p.volume * fx * mult * cfg.costs.swap_multiplier
            roll_key_prev = rk
            # 1) pending orders fill at this bar's open
            still = []
            groups_ready: dict[str, bool] = {}
            for o in pending:
                gid = o.signal.group_id
                if gid and o.signal.intent.is_opening:
                    legs = [x for x in pending if x.signal.group_id == gid and x.signal.intent.is_opening]
                    groups_ready[gid] = all(x.signal.role in bars_now for x in legs)
            done_groups: dict[str, list[str]] = {}
            failed_groups: set[str] = set()
            for o in pending:
                r = o.signal.role
                gid = o.signal.group_id if o.signal.intent.is_opening else None
                if r not in bars_now or (gid and not groups_ready.get(gid)):
                    o.waited += 1
                    if o.waited <= 3 * cfg.execution.max_entry_gap_bars:
                        still.append(o)
                    continue
                if gid and gid in failed_groups:
                    continue
                i = bars_now[r]
                if t - o.created > step * cfg.execution.max_entry_gap_bars:
                    diag["gap_fills"] += 1
                before = set(positions)
                ok = execute(o, i, t)
                if gid:
                    if ok:
                        done_groups.setdefault(gid, []).extend(set(positions) - before)
                    else:
                        failed_groups.add(gid)
            for gid in failed_groups:
                diag["group_leg_failures"] += 1
                for k in done_groups.get(gid, []):
                    p = positions.get(k)
                    if p:
                        a = arrays[p.role]
                        i = bars_now[p.role]
                        spr = self._spread_pts(p.role, a["spread"][i]) * self.specs[p.role].point
                        px = a["open"][i] - self._slip(p.role) if p.side == "LONG" else a["open"][i] + spr + self._slip(p.role)
                        close_pos(p, px, t, "GROUP_LEG_FAILED", "other leg could not be opened")
            pending = still
            # 2) stops/targets, holding counters, marks
            for r, i in bars_now.items():
                for p in [p for p in positions.values() if p.role == r]:
                    check_stops(p, i, t)
                for p in positions.values():
                    if p.role == r:
                        p.bars_held += 1
                last_close[r] = arrays[r]["close"][i]
                last_spread[r] = self._spread_pts(r, arrays[r]["spread"][i])
            eq = equity_now()
            eq_t.append(t + step)
            eq_v.append(eq)
            dk = t.floor("1D")
            if dk != day_key:
                day_key, day_start_eq, day_high_eq, paused_today = dk, eq, eq, False
                entries_day = set()
                if dd_paused and cfg.restart_after_drawdown and eq > 0:
                    # what a manual restart does live: running again, the drawdown measured from today's equity
                    dd_paused, peak = False, eq
                    guard["streak"], guard["streak_paused"] = 0, False
                    diag["drawdown_restarts"].append({"at": str(dk.date()), "equity": round(eq, 2)})
                if guard["streak_paused"] and cfg.restart_after_drawdown:
                    guard["streak"], guard["streak_paused"] = 0, False  # pressing Start the next day clears the streak
                wk = dk - pd.Timedelta(days=dk.weekday())
                if wk != week_key:
                    week_key, week_start_eq, paused_week = wk, eq, False
            day_high_eq = max(day_high_eq, eq)
            trips = []
            stop = None if paused_today else daily_stop(day_start_eq, day_high_eq, eq, asdict(cfg.risk))
            if stop:
                paused_today = True
                trips.append(stop)
                diag[{"DAILY_LOSS_LIMIT": "daily_pauses", "DAILY_PROFIT_TARGET_REACHED": "daily_profit_stops"}.get(stop, "daily_profit_locks")] += 1
            wstop = None if paused_week else weekly_stop(week_start_eq, eq, asdict(cfg.risk))
            if wstop:
                paused_week = True
                trips.append(wstop)
                diag["weekly_pauses"] += 1
            peak = max(peak, eq)
            if not dd_paused and cfg.risk.max_drawdown_pause and eq < peak * (1 - cfg.risk.max_drawdown_pause):
                dd_paused = True
                trips.append("MAX_DRAWDOWN_LIMIT")
                diag["drawdown_pause_at"] = str(t)
            if cfg.risk.max_consecutive_losses and not guard["streak_paused"] and guard["streak"] >= cfg.risk.max_consecutive_losses:
                guard["streak_paused"] = True
                trips.append("LOSS_STREAK_LIMIT")
                diag["loss_streak_pauses"].append({"at": str(t), "losses": guard["streak"]})
            if cfg.risk.close_on_breach and positions and any(x in LOSS_LIMITS for x in trips):
                for p in list(positions.values()):  # close on breach, at the bar's close
                    spec = self.specs[p.role]
                    px = last_close[p.role] if p.side == "LONG" else last_close[p.role] + last_spread[p.role] * spec.point
                    close_pos(p, px, t + step, "GUARD_BREACH", "closed when a loss limit tripped (" + ", ".join(trips) + ")")
                    diag["guard_closes"] += 1
                eq = equity_now()
            # 3) decision at bar close
            T = t + step
            frames = {}
            for k, df in self.frames.items():
                end = np.searchsorted(close_ns[k], T.value, side="right")
                frames[k] = df.iloc[:end]
            if any(len(f) < 2 for f in frames.values()):
                continue
            pviews = [PositionView(p.key, p.role, self.specs[p.role].symbol, p.side, p.volume, p.entry, p.opened_at.to_pydatetime(),
                                   p.bars_held, p.sl, p.tp, p.max_hold, p.group_id) for p in positions.values()]
            ctx = StrategyContext(T.to_pydatetime(), frames, pviews, self.specs, weekend_flat_from=cfg.risk.weekend_flat_from)
            diag["decisions"] += 1
            sigs = s.generate_signals(ctx)
            if not sigs:
                continue
            diag["signals"] += len(sigs)
            pending_keys = {o.signal.position_key for o in pending if o.signal.position_key}
            exits = [x for x in sigs if x.intent.is_risk_reducing and x.position_key in positions and x.position_key not in pending_keys]
            for x in exits:
                pending.append(_Order(x, -1, 0.0, T))
            entries = [x for x in sigs if x.intent.is_opening]
            if not entries:
                continue
            if paused_today or dd_paused or paused_week or guard["streak_paused"]:
                diag["blocked_risk_pause"] += len(entries)
                self._rec(decisions, entries, "RISK_PAUSED")
                continue
            if guard["streak"] > 0 and in_cooldown(guard["last_loss"], T, asdict(cfg.risk)):
                diag["blocked_cooldown"] += len(entries)
                self._rec(decisions, entries, "LOSS_COOLDOWN")
                continue
            if len(positions) + len([o for o in pending if o.signal.intent.is_opening]) + len(entries) > cfg.risk.max_open_positions:
                diag["blocked_max_positions"] += len(entries)
                continue
            if cfg.risk.max_entries_per_day and len(entries_day) >= cfg.risk.max_entries_per_day:
                diag["blocked_daily_entry_cap"] += len(entries)
                self._rec(decisions, entries, "MAX_ENTRIES_PER_DAY")
                continue
            if self.audit_fn:
                verdicts = [self.audit_fn(x, T) for x in entries]
                if any(v[0] != "APPROVED" for v in verdicts):
                    diag["blocked_auditor"] += len(entries)
                    self._rec(decisions, entries, "AUDITOR:" + ";".join(r for v in verdicts for r in v[1]))
                    continue
            tps = s.calculate_target_positions(entries, eq, self.specs, cfg.risk.risk_per_trade, self._loss, self._cost_per_lot,
                                               min_lot=cfg.risk.min_lot, max_risk_pct=cfg.risk.max_trade_risk,
                                               big_lot=cfg.risk.big_lot, big_lot_max_risk=cfg.risk.big_lot_max_risk,
                                               max_lots=cfg.risk.max_lots_per_trade)
            if any(tp.skipped for tp in tps) and any(x.group_id for x in entries):
                diag["skipped_min_lot"] += len(entries)  # trade groups are all-or-nothing
                self._rec(decisions, entries, "GROUP_SKIPPED:" + ";".join(tp.skip_reason for tp in tps if tp.skipped))
                continue
            open_lots = sum(p.volume for p in positions.values()) + sum(o.volume for o in pending if o.signal.intent.is_opening)
            for tp in tps:
                if tp.skipped:
                    diag["skipped_min_lot" if "MIN_LOT" in tp.skip_reason else "skipped_no_stop"] += 1
                    self._rec(decisions, [tp.signal], tp.skip_reason)
                    continue
                if cfg.risk.max_lots_per_bot and open_lots + tp.volume > cfg.risk.max_lots_per_bot + 1e-9:
                    diag["blocked_lot_cap"] += 1
                    self._rec(decisions, [tp.signal], "LOTS_ABOVE_BOT_CAP")
                    continue
                if cfg.risk.daily_headroom_check and eq > 0 and cfg.risk.daily_loss_pause and \
                        max(0.0, (day_start_eq - eq) / day_start_eq) + tp.planned_risk / eq > cfg.risk.daily_loss_pause:
                    diag["blocked_daily_headroom"] += 1
                    self._rec(decisions, [tp.signal], "DAILY_HEADROOM")
                    continue
                open_lots += tp.volume
                pending.append(_Order(tp.signal, tp.volume, tp.planned_risk, T))
                entries_day.add(tp.signal.group_id or tp.signal.signal_id)
                self._rec(decisions, [tp.signal], "QUEUED")

        # close anything still open at the last bar close (marked)
        if len(timeline):
            t_end = pd.Timestamp(timeline[-1], tz="UTC") + step
            for p in list(positions.values()):
                spec = self.specs[p.role]
                px = last_close[p.role] if p.side == "LONG" else last_close[p.role] + last_spread[p.role] * spec.point
                close_pos(p, px, t_end, "END_OF_DATA", "closed at end of backtest (mark price)")
        trades_df = pd.DataFrame(trades)
        if trades_df.empty:
            trades_df = pd.DataFrame(columns=["net_pnl", "gross_pnl", "commission", "swap", "spread_slip_cost", "side",
                                              "bars_held", "ambiguous_exit", "r_multiple"])
        equity = pd.Series(eq_v, index=pd.DatetimeIndex(eq_t), name="equity")
        mets = M.summarize(trades_df, equity, cfg.initial_capital)
        mets["stopped_by_drawdown_at"] = diag["drawdown_pause_at"]
        mets["drawdown_restarts"] = len(diag["drawdown_restarts"])
        mets["loss_streak_pauses"] = len(diag["loss_streak_pauses"])
        mets["guard_closes"] = diag["guard_closes"]
        rel = self._reliability(mets)
        if diag["loss_streak_pauses"]:
            rel.insert(0, f"LOSS_STREAK_PAUSES: {len(diag['loss_streak_pauses'])} pause(s) after {cfg.risk.max_consecutive_losses} losses in a row; "
                          "each time the bot was restarted the next day as if you pressed Start.")
        rs = diag["drawdown_restarts"]
        if rs:
            rel.insert(0, f"DRAWDOWN_RESTARTS: the {cfg.risk.max_drawdown_pause:.0%} drawdown pause hit {len(rs)} time(s); each time the "
                          f"bot was restarted the next day as if you pressed Start (first restart {rs[0]['at']} at "
                          f"{rs[0]['equity']:.2f}). Without restarts it would have stopped on {diag['drawdown_pause_at'][:10]}.")
        elif diag["drawdown_pause_at"]:
            rel.insert(0, f"DRAWDOWN_PAUSE: stopped opening trades on {diag['drawdown_pause_at'][:10]} after a "
                          f"{cfg.risk.max_drawdown_pause:.0%} fall from its peak and was not restarted")
        assumptions = {"config": cfg.describe(), "price_model": "BID bars; ask=bid+spread; next-bar-open fills",
                       "intrabar": "stop assumed before target when both touched (unless tick-resolved)",
                       "margin": "notional/leverage approximation; live uses order_calc_margin",
                       "data": self.data_meta}
        return BacktestResult(s.strategy_id, dict(s.params), trades_df, equity, mets, diag, assumptions, rel, decisions)

    def _rec(self, store, sigs, outcome):
        if self.record_decisions:
            for x in sigs:
                store.append({"time": x.timestamp.isoformat(), "symbol": x.symbol, "intent": x.intent.value,
                              "outcome": outcome, "rationale": x.rationale})

    def _resolve_with_ticks(self, p: _Pos, t: pd.Timestamp, spr: float) -> Optional[str]:
        ticks = self.ticks.get(p.role)
        if ticks is None or ticks.empty:
            return None
        end = t + pd.Timedelta(minutes=TIMEFRAME_MINUTES[self.s.primary_timeframe])
        w = ticks[(ticks["time"] >= t) & (ticks["time"] < end)]
        for _, row in w.iterrows():
            if p.side == "LONG":
                if row["bid"] <= p.sl:
                    return "SL"
                if p.tp and row["bid"] >= p.tp:
                    return "TP"
            else:
                if row["ask"] >= p.sl:
                    return "SL"
                if p.tp and row["ask"] <= p.tp:
                    return "TP"
        return None

    def _coverage_note(self, prim: dict, timeline) -> Optional[str]:
        """A warning when the requested dates reach beyond the downloaded history (the run silently covers less)."""
        ptf = self.s.primary_timeframe
        first, last = pd.Timestamp(int(timeline[0]), tz="UTC"), pd.Timestamp(int(timeline[-1]), tz="UTC")
        want_from = pd.Timestamp(self.cfg.start, tz="UTC") if self.cfg.start else None
        want_to = pd.Timestamp(self.cfg.end, tz="UTC") - pd.Timedelta(days=1) if self.cfg.end else None
        short = (want_from is not None and first - want_from > pd.Timedelta(days=7)) or \
                (want_to is not None and want_to - last > pd.Timedelta(days=7))
        if not short:
            return None
        return (f"PARTIAL_DATA: you asked for {_day(want_from) if want_from is not None else 'the start'} to "
                f"{_day(want_to) if want_to is not None else 'today'}, but {ptf} history only covers {_day(first)} to {_day(last)}")

    def _reliability(self, mets: dict) -> list[str]:
        out = [self._coverage_gap] if getattr(self, "_coverage_gap", None) else []
        src = {str(v.get("source")) for v in self.data_meta.values()} if self.data_meta else set()
        if "SYNTHETIC" in src or not src:
            out.append("SYNTHETIC_OR_UNKNOWN_DATA: results say nothing about real markets")
        if self.s.style == Style.SCALP and not self.ticks:
            out.append("SCALPER_ON_OHLC: intrabar ordering unknown; results unreliable without tick data")
        if mets.get("ambiguous_exits"):
            out.append(f"{mets['ambiguous_exits']} exits resolved by adverse-first assumption")
        if mets["n_trades"] < 30:
            out.append("FEW_TRADES: <30 trades")
        if self.cfg.costs.fx_to_account == 1.0:
            out.append("ASSUMES_USD_ACCOUNT (no currency conversion applied)")
        return out
