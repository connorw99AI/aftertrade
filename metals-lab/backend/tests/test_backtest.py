from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from metals_lab.backtest.engine import Backtester
from metals_lab.backtest.models import BacktestConfig, CostModel, ExecutionModel, RiskParams
from metals_lab.domain import Intent, Style
from metals_lab.strategies import build
from metals_lab.strategies.base import Param, Strategy


class OneShot(Strategy):
    """Test strategy: opens one position at a chosen bar, optional exit at another."""
    strategy_id = "T01"
    name = "test"
    style = Style.DAY
    roles = ("GOLD",)
    timeframes = ("H1",)
    primary_timeframe = "H1"
    min_history_bars = 1
    PARAMS = {"at": Param(3, ""), "side": Param("LONG", ""), "stop": Param(5.0, ""), "tp": Param(None, ""),
              "exit_at": Param(None, "")}

    def prepare(self, frames):
        return {k: v.assign(i=np.arange(len(v))) for k, v in frames.items()}

    def entry_signals(self, ctx):
        r = ctx.last("GOLD", "H1")
        if r["i"] != self.p["at"] or ctx.position("GOLD"):
            return []
        long = self.p["side"] == "LONG"
        stop = r["close"] - self.p["stop"] if long else r["close"] + self.p["stop"]
        tp = None if self.p["tp"] is None else (r["close"] + self.p["tp"] if long else r["close"] - self.p["tp"])
        return [self.signal(ctx, "GOLD", Intent.OPEN_LONG if long else Intent.OPEN_SHORT, "t", {}, stop_price=stop, target_price=tp)]

    def exit_signals(self, ctx):
        r = ctx.last("GOLD", "H1")
        if self.p["exit_at"] is not None and r["i"] == self.p["exit_at"]:
            return [self.close_signal(ctx, p, "exit", {}) for p in ctx.positions]
        return []


def frame(rows):
    t = pd.date_range("2026-03-02 08:00", periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "time", t)
    df["tick_volume"], df["spread"], df["real_volume"] = 100, 20, 0
    return df


FLAT = [(2000, 2001, 1999, 2000)] * 4


def cfg(**kw):
    c = BacktestConfig(initial_capital=10000, costs=CostModel(commission_per_lot_side=3.0, slippage_points={"GOLD": 0, "SILVER": 0},
                                                              min_spread_points={"GOLD": 0, "SILVER": 0}, use_swaps=False),
                       risk=RiskParams(risk_per_trade=0.01))
    return replace(c, **kw)


def run(rows, params, specs, c=None, ticks=None):
    s = OneShot(params)
    return Backtester(s, {("GOLD", "H1"): frame(rows)}, specs, c or cfg(), ticks=ticks).run()


def test_fill_at_next_open_with_spread_and_commission(specs):
    rows = FLAT + [(2002, 2003, 2001, 2002)] * 3
    res = run(rows, {"at": 3, "exit_at": 5}, specs)
    t = res.trades.iloc[0]
    assert t["entry_price"] == pytest.approx(2002 + 20 * 0.01)   # ask at next bar open
    assert t["exit_price"] == pytest.approx(2002)                 # bid at the open after the exit signal
    assert t["entry_time"] == frame(rows)["time"].iloc[4]          # no same-bar execution
    assert t["commission"] == pytest.approx(-2 * 3.0 * t["volume"])
    assert t["net_pnl"] == pytest.approx(t["gross_pnl"] + t["commission"])


def test_stop_gap_fills_at_worse_open(specs):
    rows = FLAT + [(2000, 2001, 1999, 2000), (1980, 1981, 1975, 1978)]
    res = run(rows, {"at": 3, "stop": 5.0}, specs)
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "STOP_LOSS" and t["exit_price"] == pytest.approx(1980)  # gapped through 1995.2


def test_ambiguous_bar_assumes_stop_first_and_ticks_resolve(specs):
    rows = FLAT + [(2000, 2001, 1999, 2000), (2000, 2020, 1980, 2000)]
    res = run(rows, {"at": 3, "stop": 5.0, "tp": 5.0}, specs)
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "STOP_LOSS" and t["ambiguous_exit"]
    assert any("adverse-first" in r for r in res.reliability)
    bar_t = frame(rows)["time"].iloc[5]
    ticks = {"GOLD": pd.DataFrame({"time": [bar_t + pd.Timedelta(minutes=1), bar_t + pd.Timedelta(minutes=2)],
                                   "bid": [2010.0, 1980.0], "ask": [2010.2, 1980.2]})}
    res2 = run(rows, {"at": 3, "stop": 5.0, "tp": 5.0}, specs, ticks=ticks)
    assert res2.trades.iloc[0]["exit_reason"] == "TAKE_PROFIT"
    assert res2.diagnostics["ambiguous_bars_tick_resolved"] == 1


def test_short_uses_ask_for_stops(specs):
    # bid high 2004.9 + spread 0.2 = ask 2005.1 >= stop 2005.0 -> stopped although bid never reached it
    rows = FLAT + [(2000, 2000.5, 1999, 2000), (2000, 2004.9, 1999, 2000)]
    res = run(rows, {"at": 3, "side": "SHORT", "stop": 5.0}, specs)
    assert res.trades.iloc[0]["exit_reason"] == "STOP_LOSS"


def test_lot_rounding_and_min_lot_skip(specs):
    res = run(FLAT + FLAT, {"at": 3, "stop": 7.0}, specs)
    v = res.trades.iloc[0]["volume"]
    assert v == pytest.approx(round(v, 2)) and v <= 0.14
    small = run(FLAT + FLAT, {"at": 3, "stop": 7.0}, specs, cfg(initial_capital=500))
    assert small.metrics["n_trades"] == 0 and small.diagnostics["skipped_min_lot"] == 1


def test_margin_rejection(specs):
    c = cfg(execution=ExecutionModel(leverage=1.0))
    res = run(FLAT + FLAT, {"at": 3, "stop": 1.0}, specs, c)
    assert res.diagnostics["rejected_margin"] == 1


def test_swap_charged_at_rollover(specs):
    t = pd.date_range("2026-03-02 18:00", periods=8, freq="1h", tz="UTC")
    df = pd.DataFrame({"time": t, "open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.0, "tick_volume": 1, "spread": 20,
                       "real_volume": 0})
    c = cfg()
    c = replace(c, costs=replace(c.costs, use_swaps=True, rollover_utc_hour=21))
    res = Backtester(OneShot({"at": 1, "stop": 5.0}), {("GOLD", "H1"): df}, specs, c).run()
    tr = res.trades.iloc[0]
    expected = specs["GOLD"].swap_long * specs["GOLD"].point * specs["GOLD"].contract_size * tr["volume"]
    assert tr["swap"] == pytest.approx(expected)  # exactly one rollover crossed (Monday->Tuesday)


def test_rejections_and_partials_are_seeded_and_reported(specs):
    c = cfg(execution=ExecutionModel(reject_prob=1.0))
    assert run(FLAT + FLAT, {"at": 3}, specs, c).diagnostics["rejected_random"] == 1
    c2 = cfg(execution=ExecutionModel(partial_prob=1.0, partial_fraction=0.5))
    r = run(FLAT + FLAT, {"at": 3}, specs, c2)
    assert r.diagnostics["partial_fills"] == 1 and bool(r.trades.iloc[0]["partial_fill"])


def test_deterministic_and_labels(intraday_frames, specs):
    from conftest import frames_for_strategy
    from metals_lab.strategies import STRATEGY_CLASSES
    fr = frames_for_strategy(STRATEGY_CLASSES["S03"])
    a = Backtester(build("S03"), fr, specs, data_meta={"x": {"source": "SYNTHETIC"}}).run()
    b = Backtester(build("S03"), fr, specs, data_meta={"x": {"source": "SYNTHETIC"}}).run()
    pd.testing.assert_frame_equal(a.trades.drop(columns=["entry_indicators"]), b.trades.drop(columns=["entry_indicators"]))
    assert any("SCALPER_ON_OHLC" in r for r in a.reliability)
    assert any("SYNTHETIC" in r for r in a.reliability)
    assert "tick_volume" in intraday_frames[("GOLD", "M5")].columns and "volume" not in intraday_frames[("GOLD", "M5")].columns


def test_daily_loss_pause_blocks_new_entries(specs):
    rows = FLAT + [(2000, 2001, 1999, 2000), (1950, 1951, 1940, 1945)] + FLAT
    c = cfg(risk=RiskParams(risk_per_trade=0.05, daily_loss_pause=0.01))
    s = OneShot({"at": 3, "stop": 5.0})
    res = Backtester(s, {("GOLD", "H1"): frame(rows)}, specs, c).run()
    assert res.diagnostics["daily_pauses"] >= 1


def test_min_lot_floor_and_daily_entry_cap(specs):
    rows = FLAT * 3
    # stop $500 away: 0.01 lot risks $500 > the 0.5% budget ($50), so without a floor the trade is skipped
    c = cfg(risk=RiskParams(risk_per_trade=0.005))
    res = Backtester(OneShot({"at": 3, "stop": 500.0, "exit_at": 5}), {("GOLD", "H1"): frame(rows)}, specs, c).run()
    assert len(res.trades) == 0 and res.diagnostics["skipped_min_lot"] == 1
    c2 = cfg(risk=RiskParams(risk_per_trade=0.005, min_lot=0.1))
    res2 = Backtester(OneShot({"at": 3, "stop": 500.0, "exit_at": 5}), {("GOLD", "H1"): frame(rows)}, specs, c2).run()
    assert len(res2.trades) == 1 and abs(res2.trades.iloc[0]["volume"] - 0.1) < 1e-12


class EveryBar(OneShot):
    """Opens whenever flat and closes on the next bar."""
    def entry_signals(self, ctx):
        r = ctx.last("GOLD", "H1")
        if ctx.position("GOLD"):
            return []
        return [self.signal(ctx, "GOLD", Intent.OPEN_LONG, "t", {}, stop_price=r["close"] - 5.0)]

    def exit_signals(self, ctx):
        return [self.close_signal(ctx, p, "exit", {}) for p in ctx.positions if p.bars_held >= 1]


def test_daily_entry_cap_in_backtest(specs):
    rows = FLAT * 4  # 16 hourly bars, 08:00-23:00 of one UTC day
    uncapped = Backtester(EveryBar(), {("GOLD", "H1"): frame(rows)}, specs, cfg()).run()
    capped = Backtester(EveryBar(), {("GOLD", "H1"): frame(rows)}, specs,
                        cfg(risk=RiskParams(risk_per_trade=0.01, max_entries_per_day=3))).run()
    assert len(uncapped.trades) > 3 and len(capped.trades) == 3 and capped.diagnostics["blocked_daily_entry_cap"] > 0


def test_daily_profit_stop_in_backtest(specs):
    rows = [(2000 + 30 * i, 2031 + 30 * i, 2000 + 30 * i, 2030 + 30 * i) for i in range(12)]  # steady rally: every trade wins
    c = cfg(risk=RiskParams(risk_per_trade=0.05, daily_profit_stop=0.20))
    res = Backtester(EveryBar(), {("GOLD", "H1"): frame(rows)}, specs, c).run()
    free = Backtester(EveryBar(), {("GOLD", "H1"): frame(rows)}, specs, cfg(risk=RiskParams(risk_per_trade=0.05))).run()
    assert res.diagnostics["daily_profit_stops"] == 1 and len(res.trades) < len(free.trades)


class Keeps(OneShot):
    """Buys whenever flat, with a tight stop: in a falling market it keeps losing."""
    def entry_signals(self, ctx):
        r = ctx.last("GOLD", "H1")
        if ctx.position("GOLD"):
            return []
        return [self.signal(ctx, "GOLD", Intent.OPEN_LONG, "t", {}, stop_price=r["close"] - 3)]


def test_drawdown_pause_restarts_next_day_like_a_manual_restart(specs):
    rows = [(2000 - 6 * i, 2001 - 6 * i, 1990 - 6 * i, 1994 - 6 * i) for i in range(60)]  # 2.5 days, always falling
    risk = RiskParams(risk_per_trade=0.01, daily_loss_pause=0.99, max_drawdown_pause=0.02)
    days = lambda res: {str(t)[:10] for t in res.trades["entry_time"]}
    on = Backtester(Keeps({}), {("GOLD", "H1"): frame(rows)}, specs, cfg(risk=risk)).run()
    off = Backtester(Keeps({}), {("GOLD", "H1"): frame(rows)}, specs, cfg(risk=risk, restart_after_drawdown=False)).run()
    assert off.metrics["drawdown_restarts"] == 0 and len(days(off)) == 1      # stopped for good on day one
    assert on.metrics["drawdown_restarts"] >= 1 and len(days(on)) >= 2        # restarted the next day and kept trading
    assert any(r.startswith("DRAWDOWN_RESTARTS") for r in on.reliability)


def test_dates_with_no_price_history_fail_clearly_instead_of_zero_trades(specs):
    from metals_lab.backtest.engine import NoDataInRange
    rows = FLAT * 3
    with pytest.raises(NoDataInRange, match="no H1 price history between 2013-01-01 and 2019-12-31.*covers: gold H1 2026-03-02"):
        run(rows, {"at": 3}, specs, cfg(start="2013-01-01", end="2020-01-01"))


def test_dates_reaching_past_the_history_are_flagged(specs):
    res = run(FLAT * 3, {"at": 3}, specs, cfg(start="2026-01-01"))
    assert any(x.startswith("PARTIAL_DATA") and "2026-03-02" in x for x in res.reliability)
    assert not any(x.startswith("PARTIAL_DATA") for x in run(FLAT * 3, {"at": 3}, specs, cfg(start="2026-03-02")).reliability)
