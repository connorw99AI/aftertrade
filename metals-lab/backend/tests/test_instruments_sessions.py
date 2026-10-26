import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from metals_lab.instruments import classify, discover, spec_from_info
from metals_lab.mt5 import constants as C
from metals_lab.mt5.gateway import ReadOnlyGateway
from metals_lab.mt5.sim import SimTerminal, default_symbols
from metals_lab.sessions import (ServerClock, calendar_from_config, calendar_from_mql5_export, infer_gaps,
                                 local_session_bounds)


def test_discovery_rejects_equities_and_resolves_metals():
    res = discover(ReadOnlyGateway(SimTerminal(), is_simulated=True))
    assert res.resolved["GOLD"].symbol == "XAUUSD"
    assert res.resolved["SILVER"].symbol == "XAGUSD"
    miner = next(c for c in res.candidates if c.symbol == "GOLD.US")
    assert not miner.accepted and miner.kind == "EXCHANGE_STOCK"
    assert "EURUSD" not in {c.symbol for c in res.candidates if c.role}


def test_multiple_candidates_require_explicit_choice():
    syms = default_symbols()
    alt = dict(syms["XAUUSD"], name="XAUUSD.pro")
    syms["XAUUSD.pro"] = alt
    gw = ReadOnlyGateway(SimTerminal(symbols=syms), is_simulated=True)
    res = discover(gw)
    assert "GOLD" not in res.resolved and "multiple" in res.unresolved["GOLD"]
    res2 = discover(gw, {"GOLD": "XAUUSD.pro"})
    assert res2.resolved["GOLD"].symbol == "XAUUSD.pro"
    res3 = discover(gw, {"GOLD": "GOLD.US"})  # explicit choice cannot force an equity in
    assert "GOLD" not in res3.resolved


def test_contract_kinds_distinguished():
    base = default_symbols()["XAUUSD"]
    assert classify(dict(base, trade_calc_mode=C.SYMBOL_CALC_MODE_FOREX)).kind == "SPOT_OTC_FOREX_STYLE"
    assert classify(dict(base, trade_calc_mode=C.SYMBOL_CALC_MODE_EXCH_FUTURES, name="GCZ6", description="Gold Futures")).kind == "EXCHANGE_FUTURES"
    assert classify(base).kind == "CFD"


def test_lot_rounding_and_filling():
    s = spec_from_info("GOLD", dict(default_symbols()["XAUUSD"], volume_step=0.01, volume_min=0.01))
    assert s.round_volume_down(0.0299999) == 0.02
    assert s.round_volume_down(0.009) == 0.0
    assert s.round_volume_down(1000) == 100.0
    assert s.preferred_filling() == C.ORDER_FILLING_FOK
    s2 = spec_from_info("GOLD", dict(default_symbols()["XAUUSD"], filling_mode=C.SYMBOL_FILLING_IOC))
    assert s2.preferred_filling() == C.ORDER_FILLING_IOC
    s3 = spec_from_info("GOLD", dict(default_symbols()["XAUUSD"], filling_mode=0))
    assert s3.preferred_filling() == C.ORDER_FILLING_RETURN
    assert s.round_price(2000.006) == 2000.01


def test_server_clock_dst_aware():
    clk = ServerClock("Europe/Athens")
    winter = clk.to_utc(pd.Series([pd.Timestamp("2026-01-15 10:00")])).iloc[0]
    summer = clk.to_utc(pd.Series([pd.Timestamp("2026-07-15 10:00")])).iloc[0]
    assert winter.hour == 8 and summer.hour == 7
    assert clk.utc_to_server_naive(datetime(2026, 7, 15, 7, tzinfo=timezone.utc)).hour == 10


def test_measured_offset_and_verification():
    now = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    server_epoch = int(now.timestamp()) + 2 * 3600 + 7
    assert ServerClock.measure_offset_hours(server_epoch, now) == 2.0
    ok, _ = ServerClock(fixed_offset_hours=2).verify_against_tick(server_epoch, now)
    bad, _ = ServerClock(fixed_offset_hours=3).verify_against_tick(server_epoch, now)
    assert ok and not bad
    with pytest.raises(ValueError):
        ServerClock().server_epoch_to_utc(server_epoch)


def test_session_calendar_and_closures():
    cal = calendar_from_config("XAUUSD", {"windows": [{"days": [0, 1, 2, 3, 4], "start": "01:00", "end": "24:00"}],
                                          "closures": ["2026-12-25"]})
    assert cal.is_open_server(datetime(2026, 12, 21, 12))
    assert not cal.is_open_server(datetime(2026, 12, 21, 0, 30))  # daily break
    assert not cal.is_open_server(datetime(2026, 12, 25, 12))     # holiday
    assert not cal.is_open_server(datetime(2026, 12, 26, 12))     # Saturday
    assert cal.minutes_to_close_server(datetime(2026, 12, 21, 23, 50)) == 10


def test_mql5_export_parsing(tmp_path):
    data = {"exported_at": "2026.10.20 10:00:00", "symbols": {"XAUUSD": {"trade": [
        {"day": 1, "index": 0, "from": 3600, "to": 86400}, {"day": 5, "index": 0, "from": 3600, "to": 85800}], "quote": []}}}
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    cal = calendar_from_mql5_export(p, "XAUUSD")
    assert cal.source.startswith("mql5_export")
    assert cal.is_open_server(datetime(2026, 10, 19, 12))     # Monday (MQL day 1)
    assert not cal.is_open_server(datetime(2026, 10, 23, 23, 55))  # Friday after 23:50
    assert calendar_from_mql5_export(tmp_path / "missing.json", "XAUUSD") is None


def test_london_session_dst():
    s_w, _ = local_session_bounds(date(2026, 1, 15), "Europe/London", "08:00", "09:00")
    s_s, _ = local_session_bounds(date(2026, 7, 15), "Europe/London", "08:00", "09:00")
    assert s_w.hour == 8 and s_s.hour == 7


def test_gap_inference_is_diagnostic():
    t = pd.date_range("2026-01-02 20:00", periods=4, freq="5min").tolist() + [pd.Timestamp("2026-01-04 23:00")]
    gaps = infer_gaps(pd.DataFrame({"server_time": t}), 5)
    assert len(gaps) == 1 and gaps["gap"].iloc[0] > pd.Timedelta(hours=40)
