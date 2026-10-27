import numpy as np
import pandas as pd
import pytest

from metals_lab.domain import TIMEFRAME_MINUTES, Intent, PositionView, Signal, Style
from datetime import datetime, timezone

from metals_lab.strategies import STRATEGY_CLASSES, build, magic_for, strategy_for_magic
from metals_lab.strategies.base import StrategyContext, contract_loss

from conftest import frames_for_strategy, scan_signals

INTRADAY = ["S01", "S02", "S03", "S04", "S05", "S06", "S12"]
SWING = ["S07", "S08", "S09", "S10"]
# Loosened only where a cost/regime filter blocks every synthetic bar; rules are otherwise unchanged.
SCAN_PARAMS = {"S02": {"max_spread_atr": 5.0}, "S01": {"max_spread_atr": 5.0}, "S03": {"max_spread_atr": 5.0},
               "S06": {"max_spread_atr": 5.0}, "S07": {"max_spread_atr": 5.0}, "S08": {"max_spread_atr": 5.0},
               "S09": {"max_spread_atr": 5.0, "entry_strength": 0.25}, "S04": {"max_spread_atr": 5.0},
               "S05": {"max_spread_atr": 5.0}, "S12": {"max_spread_atr": 5.0}}


def test_twelve_distinct_strategies_with_full_metadata():
    assert len(STRATEGY_CLASSES) == 12
    styles = [c.style for c in STRATEGY_CLASSES.values()]
    assert styles.count(Style.SCALP) == 3 and styles.count(Style.DAY) == 9
    tfs = [c.primary_timeframe for c in STRATEGY_CLASSES.values()]
    # operator rule: nothing trades slower than M15
    assert {tf: tfs.count(tf) for tf in set(tfs)} == {"M5": 7, "M15": 5}  # no one-minute entries: deep history for every bot
    names = {c.name for c in STRATEGY_CLASSES.values()}
    assert len(names) == 12
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
    from metals_lab.strategies import BOT_IDS
    assert len(BOT_IDS) == 24
    mags = {magic_for(b) for b in BOT_IDS}
    assert len(mags) == 24
    for b in BOT_IDS:
        assert strategy_for_magic(magic_for(b)) == b
    assert strategy_for_magic(0) is None
    assert strategy_for_magic(7710003) is None  # pre-split magic numbers are not claimed


def test_bot_halves_trade_only_their_side(specs):
    from metals_lab.strategies import bot_name
    for sid in ("S03", "S08", "S09"):
        fr = frames_for_strategy(STRATEGY_CLASSES[sid])
        for half, want in (("L", Intent.OPEN_LONG), ("S", Intent.OPEN_SHORT)):
            s = build(sid + half, {"max_spread_atr": 5.0})
            found = scan_signals(s, fr, specs, {Intent.OPEN_LONG, Intent.OPEN_SHORT})
            assert set(found) <= {want}, f"{sid}{half} opened {set(found)}"
    assert bot_name("S03L") == "Failed Breakout Reversal (long)" and "silver cheap" in bot_name("S10L")
    assert build("S03S", {"sides": "long"}).p["sides"] == "short"  # the half's side cannot be overridden


def test_trading_windows_parse_in_uk_time():
    from metals_lab.strategies.filters import format_windows, in_windows, parse_windows, windows_minus
    assert parse_windows("00:00-24:00") == [(0, 1440)] == parse_windows("all")
    assert parse_windows("06:00-11:00, 13:00-17:00") == [(360, 660), (780, 1020)]
    assert parse_windows("22:00-02:00") == [(0, 120), (1320, 1440)]                    # crosses midnight
    assert parse_windows("06:00-09:00, 08:00-11:00") == [(360, 660)]                   # overlaps merge
    assert format_windows(parse_windows("22:00-02:00, 09:20-13:00")) == "09:20-13:00, 22:00-02:00"
    for bad in ("25:00-26:00", "6-11", "06:00-06:00", "06:00", "06:61-07:00", ","):
        with pytest.raises(ValueError):
            parse_windows(bad)
    summer, winter = datetime(2026, 7, 6, 10, 30, tzinfo=timezone.utc), datetime(2026, 12, 7, 10, 30, tzinfo=timezone.utc)
    assert in_windows(summer, "11:00-12:00") and not in_windows(winter, "11:00-12:00")  # 11:30 BST vs 10:30 GMT
    assert windows_minus("00:00-24:00", 7 * 60, 12 * 60) == "12:00-07:00"
    assert windows_minus("06:00-11:00, 13:00-17:00", 7 * 60, 12 * 60) == "06:00-07:00, 13:00-17:00"
    assert windows_minus("13:00-17:00", 7 * 60, 12 * 60) is None                       # nothing changes
    assert windows_minus("08:00-10:00", 7 * 60, 12 * 60) is None                       # nothing would be left
    with pytest.raises(ValueError):
        build("S03L", {"gold_windows": "25:00-26:00"})
    with pytest.raises(ValueError):
        build("S03L", {"trade_silver": "maybe"})


def test_each_metal_opens_only_inside_its_own_windows():
    from types import SimpleNamespace
    s = build("S03L", {"gold_windows": "06:00-11:00", "silver_windows": "13:00-17:00"})
    mk = lambda role, sym: Signal("S03L", datetime(2026, 12, 7, tzinfo=timezone.utc), role, sym, Intent.OPEN_LONG, "t", {},
                                  stop_price=1.0, ref_price=2.0)
    s.entry_signals = lambda ctx: [mk("GOLD", "XAUUSD"), mk("SILVER", "XAGUSD")]
    at = lambda h: SimpleNamespace(now=datetime(2026, 12, 7, h, tzinfo=timezone.utc))  # winter: UK = UTC
    assert [x.role for x in s.entries(at(8))] == ["GOLD"]
    assert [x.role for x in s.entries(at(14))] == ["SILVER"]
    assert s.entries(at(12)) == []
    off = build("S03L", {"trade_gold": "off"})
    off.entry_signals = s.entry_signals
    assert [x.role for x in off.entries(at(8))] == ["SILVER"]
    pair = build("S10L", {"gold_windows": "06:00-11:00", "silver_windows": "09:00-17:00"})  # both legs or neither
    pair.entry_signals = lambda ctx: [mk("GOLD", "XAUUSD"), mk("SILVER", "XAGUSD")]
    assert pair.entries(at(8)) == [] and len(pair.entries(at(10))) == 2


def test_saved_metal_and_hour_filters_from_older_versions_are_dropped():
    s = build("S03L", {"metals": "gold", "hours_utc": "12-21"})
    assert s.p["trade_gold"] == s.p["trade_silver"] == "on" and s.p["gold_windows"] == "00:00-24:00"
    assert "metals" not in s.p and "hours_utc" not in s.p


def test_unknown_parameter_rejected():
    with pytest.raises(ValueError):
        build("S01", {"not_a_param": 1})


@pytest.mark.parametrize("sid", INTRADAY + SWING)
def test_long_and_short_entries(sid, specs):
    s = build(sid, {**SCAN_PARAMS.get(sid, {}), "sides": "both"})  # rules without the assessor filters
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
    prepared = s.prepare({("GOLD", "M5"): fr0[("GOLD", "M5")], ("SILVER", "M5"): fr0[("SILVER", "M5")]})
    T = prepared[("GOLD", "M5")]["time"].iloc[800] + pd.Timedelta(minutes=5)
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
        sigs = s.entries(ctx)
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


def test_weekend_flat_closes_everything_and_blocks_entries(specs):
    from datetime import datetime, timezone
    from metals_lab.strategies.base import in_weekend_flat
    assert not in_weekend_flat(datetime(2026, 10, 23, 19, 59, tzinfo=timezone.utc), "20:00")   # Friday before cutoff
    assert in_weekend_flat(datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc), "20:00")
    assert in_weekend_flat(datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc), "20:00")        # Saturday
    assert not in_weekend_flat(datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc), "20:00")    # Sunday reopen
    assert not in_weekend_flat(datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc), None)
    s = build("S06")
    frames = frames_for_strategy(type(s))
    prepared = s.prepare({k: v for k, v in frames.items() if k[1] in s.timeframes})
    fri = datetime(2026, 10, 23, 20, 15, tzinfo=timezone.utc)
    pos = PositionView("S06-GOLD-1", "GOLD", "XAUUSD", "LONG", 0.04, 2000.0, fri, 3, None, None, 64, None)
    ctx = StrategyContext(fri, prepared, [pos], specs, weekend_flat_from="20:00")
    sigs = s.generate_signals(ctx)
    assert [x.intent for x in sigs] == [Intent.CLOSE_LONG] and sigs[0].position_key == "S06-GOLD-1"
    assert s.generate_signals(StrategyContext(fri, prepared, [], specs, weekend_flat_from="20:00")) == []


def test_min_lot_per_metal(specs):
    from metals_lab.domain import Signal
    from datetime import datetime, timezone
    s = build("S08")
    loss = lambda spec, side, e, st, v: contract_loss(spec, side, e, st, v)  # noqa: E731
    g = Signal("S08", datetime(2025, 1, 1, tzinfo=timezone.utc), "GOLD", "XAUUSD", Intent.OPEN_LONG, "t", {}, stop_price=1990.0, ref_price=2000.0)
    v = Signal("S08", datetime(2025, 1, 1, tzinfo=timezone.utc), "SILVER", "XAGUSD", Intent.OPEN_LONG, "t", {}, stop_price=24.5, ref_price=25.0)
    tg, tv = s.calculate_target_positions([g, v], 300.0, specs, 0.03, loss, lambda spec: 0.0, min_lot={"GOLD": 0.04, "SILVER": 0.01})
    assert tg.volume == 0.04 and tv.volume == 0.01 and not tg.skipped and not tv.skipped


def test_trading_window_combines_session_and_metal_windows_in_uk_time():
    from datetime import datetime, timezone
    winter, summer = datetime(2026, 12, 7, 12, tzinfo=timezone.utc), datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
    both = {"gold_windows": "12:00-22:00", "silver_windows": "12:00-22:00"}
    s03 = build("S03L", {"use_session": "on", **both})  # 08:00-21:00 London
    assert s03.trading_window(winter)["effective"] == s03.trading_window(summer)["effective"] == "12:00-21:00 UK"
    s04 = build("S04L", {"use_session": "on"})  # New York opening range: entries 09:00-11:30 New York
    assert s04.trading_window(winter)["effective"] == "14:00-16:30 UK"
    assert s04.trading_window(datetime(2026, 3, 16, 12, tzinfo=timezone.utc))["effective"] == "13:00-15:30 UK"  # US on summer time first
    # default: every hour the market is open; S04/S05 only pause while their range is built
    assert build("S03L", {}).trading_window(summer)["effective"] == "00:00-24:00 UK"
    assert build("S04L", {}).trading_window(summer)["effective"] == "00:00-13:30, 14:00-24:00 UK"
    assert build("S05S", {}).trading_window(winter)["effective"] == "00:00-08:00, 13:00-24:00 UK"
    w = build("S03L", {"gold_windows": "06:00-11:00, 13:00-17:00", "silver_windows": "22:00-02:00"}).trading_window(winter)
    assert w["gold"]["effective"] == "06:00-11:00, 13:00-17:00 UK" and w["silver"]["effective"] == "00:00-02:00, 22:00-24:00 UK"
    assert w["intervals"] == [[0, 120], [360, 660], [780, 1020], [1320, 1440]]
    off = build("S03L", {"trade_gold": "off", "silver_windows": "09:00-10:00"}).trading_window(winter)
    assert off["gold"]["effective"] == "switched off" and off["effective"] == "09:00-10:00 UK"
    clash = build("S03L", {"use_session": "on", "gold_windows": "00:00-06:00", "silver_windows": "00:00-06:00"})
    assert clash.trading_window(winter)["effective"].startswith("none")


def test_trend_bots_read_the_slower_chart():
    assert build("S01L", {}).timeframes == ("M5", "M15")
    assert build("S08L", {}).timeframes == ("M15", "H1") and build("S09S", {}).timeframes == ("M15", "H1")


def test_old_saved_session_params_are_ignored_not_fatal():
    build("S02L", {"entry_start_hour": 0, "entry_end_hour": 6, "flat_hour": 7})


def test_range_bots_keep_their_range_until_the_next_one_when_sessions_are_off():
    import numpy as np
    t = pd.date_range("2026-07-06 12:00", periods=24 * 12, freq="5min", tz="UTC")  # 12:00 UTC Monday to 12:00 Tuesday
    price = np.full(len(t), 2000.0)
    i_range = (t >= pd.Timestamp("2026-07-06 12:30", tz="UTC")) & (t < pd.Timestamp("2026-07-06 13:00", tz="UTC"))
    price[i_range] = np.linspace(1995, 2005, i_range.sum())                 # Monday's opening range ~1995-2005
    late = t >= pd.Timestamp("2026-07-07 02:00", tz="UTC")                   # 22:00 New York: well past 11:30
    price[late] = 2030.0                                                     # breaks out overnight
    df = pd.DataFrame({"time": t, "open": price, "high": price + 0.5, "low": price - 0.5, "close": price,
                       "tick_volume": 1, "spread": 10, "real_volume": 0})
    off = build("S04L", {}).prepare({("GOLD", "M5"): df})[("GOLD", "M5")]
    on = build("S04L", {"use_session": "on"}).prepare({("GOLD", "M5"): df})[("GOLD", "M5")]
    assert (off.loc[late, "orb_dir"] == 1).any()                             # overnight breakout of Monday's range counts
    assert not (on.loc[late, "orb_dir"] != 0).any()                          # with sessions on it is past the cut-off
    assert not off["time_exit"].any()


def test_s12_vwap_restarts_at_the_london_open_and_takes_one_entry_per_crossover(specs):
    from zoneinfo import ZoneInfo
    s = build("S12")
    frames = frames_for_strategy(type(s))
    df = s.prepare({("GOLD", "M5"): frames[("GOLD", "M5")]})[("GOLD", "M5")]
    uk = df["time"].dt.tz_convert(ZoneInfo("Europe/London"))
    first = df[(uk.dt.hour == 8) & (uk.dt.minute == 0)].head(20)
    assert len(first) and np.allclose(first["vwap"], (first["high"] + first["low"] + first["close"]) / 3)   # restarts at 08:00 UK
    cross_id = (df["ema_f"] > df["ema_s"]).ne((df["ema_f"] > df["ema_s"]).shift(1)).cumsum()
    per_cross = (df["go_long"] | df["go_short"]).groupby(cross_id).sum()
    assert per_cross.max() == 1 and per_cross.sum() > 10                    # never a second entry on the same crossover
    bad = df[df["go_long"] & ((df["close"] <= df["vwap"]) | (df["vwap_slope"] <= 0) | (df["ema_f"] <= df["ema_s"]))]
    assert bad.empty


def test_s12_enters_only_in_london_hours_and_exits_back_through_vwap(specs):
    from zoneinfo import ZoneInfo
    s = build("S12", {"max_spread_atr": 5.0})
    frames = frames_for_strategy(type(s))
    pre = s.prepare({(r, "M5"): frames[(r, "M5")] for r in s.roles})
    g = pre[("GOLD", "M5")]
    hits = g.index[g["go_long"] | g["go_short"]]
    times = []
    for i in hits[:300]:
        T = g["time"].iloc[i] + pd.Timedelta(minutes=5)
        ctx = StrategyContext(T.to_pydatetime(), {k: v.iloc[: i + 1] for k, v in pre.items()}, [], specs)
        times += [T for x in s.entry_signals(ctx) if x.role == "GOLD"]
    uk = [t.tz_convert(ZoneInfo("Europe/London")) for t in times]
    assert uk and all((8, 30) <= (t.hour, t.minute) < (20, 0) for t in uk)
    assert len(times) < len(hits)                                           # crossovers outside the hours are skipped
    # a long whose bar closes below VWAP is closed
    i = int(g.index[(g["close"] < g["vwap"]) & (g["time"].dt.tz_convert(ZoneInfo("Europe/London")).dt.hour == 12)][0])
    T = g["time"].iloc[i] + pd.Timedelta(minutes=5)
    pos = PositionView(key="k", role="GOLD", symbol="XAUUSD", side="LONG", volume=0.01, entry_price=float(g["close"].iloc[i - 1]),
                       opened_at=T.to_pydatetime(), sl=None, tp=None, bars_held=1, max_hold_bars=24)
    ctx = StrategyContext(T.to_pydatetime(), {k: v.iloc[: i + 1] for k, v in pre.items()}, [pos], specs)
    assert [x.rationale for x in s.exit_signals(ctx)] == ["closed back through VWAP"]
