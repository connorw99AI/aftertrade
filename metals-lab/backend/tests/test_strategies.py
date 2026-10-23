import numpy as np
import pandas as pd
import pytest

from metals_lab.domain import TIMEFRAME_MINUTES, Intent, PositionView, Style
from metals_lab.strategies import STRATEGY_CLASSES, build, magic_for, strategy_for_magic
from metals_lab.strategies.base import StrategyContext, contract_loss

from conftest import frames_for_strategy, scan_signals

INTRADAY = ["S01", "S02", "S03", "S04", "S05", "S06"]
SWING = ["S07", "S08", "S09", "S10"]
# Loosened only where a cost/regime filter blocks every synthetic bar; rules are otherwise unchanged.
SCAN_PARAMS = {"S02": {"max_spread_atr": 5.0}, "S01": {"max_spread_atr": 5.0}, "S03": {"max_spread_atr": 5.0},
               "S06": {"max_spread_atr": 5.0}, "S07": {"max_spread_atr": 5.0}, "S08": {"max_spread_atr": 5.0},
               "S09": {"max_spread_atr": 5.0, "entry_strength": 0.25}, "S04": {"max_spread_atr": 5.0},
               "S05": {"max_spread_atr": 5.0}}


def test_ten_distinct_strategies_with_full_metadata():
    assert len(STRATEGY_CLASSES) == 10
    styles = [c.style for c in STRATEGY_CLASSES.values()]
    assert styles.count(Style.SCALP) == 3 and styles.count(Style.DAY) == 7
    tfs = [c.primary_timeframe for c in STRATEGY_CLASSES.values()]
    # operator rule: nothing trades slower than M15, and only one bot trades M1
    assert {tf: tfs.count(tf) for tf in set(tfs)} == {"M1": 1, "M5": 4, "M15": 5}
    names = {c.name for c in STRATEGY_CLASSES.values()}
    assert len(names) == 10
    for sid, cls in STRATEGY_CLASSES.items():
        s = cls()
        d = s.describe()
        for k in ("long_rules", "short_rules", "exit_rules", "invalidation_rules", "filters", "description", "limitations"):
            assert d[k], f"{sid} missing {k}"
        assert d["citations"], sid
        assert d["min_history_bars"] > 0 and d["timeframes"]
        for p in d["params"].values():
            assert p["rationale"]
        assert callable(s.generate_signals) and callable(s.calculate_target_positions)
        assert s.risk_metadata()["strategy_id"] == sid


def test_magic_numbers_unique_and_reversible():
    mags = {magic_for(s) for s in STRATEGY_CLASSES}
    assert len(mags) == 10
    for s in STRATEGY_CLASSES:
        assert strategy_for_magic(magic_for(s)) == s
    assert strategy_for_magic(0) is None


def test_unknown_parameter_rejected():
    with pytest.raises(ValueError):
        build("S01", {"not_a_param": 1})


@pytest.mark.parametrize("sid", INTRADAY + SWING)
def test_long_and_short_entries(sid, specs):
    s = build(sid, SCAN_PARAMS.get(sid, {}))
    frames = frames_for_strategy(type(s))
    found = scan_signals(s, frames, specs, {Intent.OPEN_LONG, Intent.OPEN_SHORT})
    assert Intent.OPEN_LONG in found, f"{sid} produced no OPEN_LONG on synthetic data"
    assert Intent.OPEN_SHORT in found, f"{sid} produced no OPEN_SHORT on synthetic data"
    for intent, sig in found.items():
        assert sig.stop_price is not None
        if intent == Intent.OPEN_LONG:
            assert sig.stop_price < sig.ref_price and sig.exposure_change.startswith("flat -> long")
        else:
            assert sig.stop_price > sig.ref_price and sig.exposure_change.startswith("flat -> short")
        assert sig.indicators and sig.rationale and sig.timestamp.tzinfo is not None
        text = s.explain_signal(sig)
        assert sig.symbol in text and intent.value in text


def test_exit_intents_distinguish_close_long_and_cover_short(specs):
    s = build("S01")
    fr0 = frames_for_strategy(type(s))
    prepared = s.prepare({("GOLD", "M1"): fr0[("GOLD", "M1")], ("SILVER", "M1"): fr0[("SILVER", "M1")]})
    T = prepared[("GOLD", "M1")]["time"].iloc[800] + pd.Timedelta(minutes=1)
    fr = {k: v.iloc[:801] for k, v in prepared.items()}
    long_pos = PositionView("k1", "GOLD", "XAUUSD", "LONG", 0.1, 1.0, T.to_pydatetime(), bars_held=99, max_hold_bars=12)
    short_pos = PositionView("k2", "SILVER", "XAGUSD", "SHORT", 0.1, 1.0, T.to_pydatetime(), bars_held=99, max_hold_bars=12)
    sigs = s.generate_signals(StrategyContext(T.to_pydatetime(), fr, [long_pos, short_pos], specs))
    by_key = {x.position_key: x.intent for x in sigs}
    assert by_key["k1"] == Intent.CLOSE_LONG and Intent.CLOSE_LONG.order_side == "SELL"
    assert by_key["k2"] == Intent.COVER_SHORT and Intent.COVER_SHORT.order_side == "BUY"
    assert all(x.intent.is_risk_reducing for x in sigs)
    assert Intent.OPEN_SHORT.order_side == "SELL" and Intent.OPEN_SHORT.is_opening


@pytest.mark.parametrize("sid", INTRADAY + SWING)
def test_no_lookahead_in_prepared_indicators(sid):
    s = build(sid)
    frames = frames_for_strategy(type(s))
    keys = {(r, tf) for r in s.roles for tf in s.timeframes}
    full = s.prepare({k: frames[k] for k in keys})
    ptf_len = len(frames[(s.roles[0], s.primary_timeframe)])
    rng = np.random.default_rng(0)
    for frac in rng.uniform(0.4, 0.9, 3):
        cut_time = frames[(s.roles[0], s.primary_timeframe)]["time"].iloc[int(ptf_len * frac)]
        trunc_in = {k: frames[k][frames[k]["time"] <= cut_time].reset_index(drop=True) for k in keys}
        trunc = s.prepare(trunc_in)
        for k in keys:
            a = full[k][full[k]["time"] <= cut_time].reset_index(drop=True)
            b = trunc[k]
            assert len(a) == len(b)
            for col in b.columns:
                if col in ("time", "ldate"):
                    continue
                x, y = a[col], b[col]
                if pd.api.types.is_numeric_dtype(x) and not pd.api.types.is_bool_dtype(x):
                    np.testing.assert_allclose(x.to_numpy(float), y.to_numpy(float), rtol=1e-9, atol=1e-9, equal_nan=True,
                                               err_msg=f"{sid} {k} column {col} uses future data")
                else:
                    assert (x.astype(str) == y.astype(str)).all(), f"{sid} {k} column {col} uses future data"


def test_sizing_uses_own_equity_and_skips_min_lot(specs):
    s = build("S08")
    from metals_lab.domain import Signal
    from datetime import datetime, timezone
    sig = Signal("S08", datetime(2025, 1, 1, tzinfo=timezone.utc), "GOLD", "XAUUSD", Intent.OPEN_LONG, "t", {}, stop_price=1990.0,
                 ref_price=2000.0)
    loss = lambda spec, side, e, st, v: contract_loss(spec, side, e, st, v)  # noqa: E731
    big = s.calculate_target_positions([sig], 10000.0, specs, 0.005, loss, lambda spec: 0.0)[0]
    assert not big.skipped and abs(big.volume - 0.05) < 1e-12 and big.planned_risk <= 50.0 + 1e-9
    small = s.calculate_target_positions([sig], 1000.0, specs, 0.005, loss, lambda spec: 0.0)[0]
    # 0.01 lot x 100 oz x $10 = $10 > $5 budget -> skip, never upsize
    assert small.skipped and "MIN_LOT_EXCEEDS_RISK_BUDGET" in small.skip_reason and small.volume == 0.0
    # operator minimum lot: trade 0.1 even though it risks more than the budget; larger risk-sized volumes stay larger
    floor = s.calculate_target_positions([sig], 1000.0, specs, 0.005, loss, lambda spec: 0.0, min_lot=0.1)[0]
    assert not floor.skipped and floor.volume == 0.1 and abs(floor.planned_risk - 100.0) < 1e-9
    rich = s.calculate_target_positions([sig], 500000.0, specs, 0.005, loss, lambda spec: 0.0, min_lot=0.1)[0]
    assert rich.volume == 2.5


def test_pair_strategy_emits_two_leg_group(specs):
    s = build("S10")
    found = scan_signals(s, frames_for_strategy(type(s)), specs, {Intent.OPEN_LONG, Intent.OPEN_SHORT})
    assert found
    sig = next(iter(found.values()))
    assert sig.group_id and sig.group_id.startswith("S10-")
    assert s.risk_metadata()["uses_trade_groups"] is True


@pytest.mark.parametrize("sid", INTRADAY + SWING)
def test_thinking_checklist_agrees_with_real_entries(sid, specs):
    """At every bar where the strategy truly signals an entry, its checklist must show all of that side met;
    at bars with no entry, the verdict must not claim one."""
    s = build(sid, SCAN_PARAMS.get(sid, {}))
    frames = frames_for_strategy(type(s))
    keys = {(r, tf) for r in s.roles for tf in s.timeframes}
    prepared = s.prepare({k: frames[k] for k in keys})
    ptf = s.primary_timeframe
    close = {k: (pd.to_datetime(df["time"], utc=True).dt.as_unit("ns") + pd.Timedelta(minutes=TIMEFRAME_MINUTES[k[1]]))
             .astype("int64").to_numpy() for k, df in prepared.items()}
    times = prepared[(s.roles[0], ptf)]["time"]
    entries_seen = no_entry_seen = 0
    for i in range(s.min_history_bars, len(times)):
        T = times.iloc[i] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[ptf])
        fr = {k: df.iloc[: np.searchsorted(close[k], T.as_unit("ns").value, side="right")] for k, df in prepared.items()}
        ctx = StrategyContext(T.to_pydatetime(), fr, [], specs)
        sigs = s.entry_signals(ctx)
        th = s.think(ctx)
        assert "error" not in th, th.get("error")
        for role, r in th["roles"].items():
            sig = next((x for x in sigs if x.role == role), None)
            if sig is None:
                assert not r["verdict"].startswith("ENTRY")
                no_entry_seen += 1
                continue
            side = "long" if sig.intent == Intent.OPEN_LONG else "short"
            assert r["verdict"] == ("ENTRY_LONG" if side == "long" else "ENTRY_SHORT")
            failed = [c["label"] for c in r["filters"] + r[side] if not c["ok"]]
            assert not failed, f"{sid} {role} entered but checklist shows unmet: {failed}"
            entries_seen += 1
        if entries_seen >= 4 and no_entry_seen > 50:
            break
    assert entries_seen > 0 and no_entry_seen > 0
