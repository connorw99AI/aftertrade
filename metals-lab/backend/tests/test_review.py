import time

import numpy as np
import pandas as pd

from metals_lab import review as R


def _trades(n=40):
    rows = []
    t0 = pd.Timestamp("2026-03-02 00:00", tz="UTC")
    for i in range(n):
        asia = i % 2 == 0
        entry = t0 + pd.Timedelta(days=i // 2, hours=(2 if asia else 9))
        win = not asia
        rows.append({"trade_id": f"T{i}", "role": "GOLD", "side": "LONG", "entry_time": str(entry),
                     "exit_time": str(entry + pd.Timedelta(minutes=30)), "entry_price": 2000.0,
                     "exit_price": 2010.0 if win else 1995.0, "stop": 1995.0, "target": 2010.0,
                     "net_pnl": 20.0 if win else -10.0, "gross_pnl": 21.0 if win else -9.0, "commission": -0.5, "swap": 0.0,
                     "spread_slip_cost": 0.5, "planned_risk": 10.0, "exit_reason": "TAKE_PROFIT" if win else "STOP_LOSS",
                     "exit_rationale": ""})
    return rows


def test_review_finds_losing_session_and_maps_to_hour_param():
    df = R.from_backtest(_trades())
    out = R.review("S01", df, {"start_hour": 0, "end_hour": 20, "stop_atr": 1.2, "reward_risk": 2.0, "max_hold_bars": 30})
    st = out["stats"]
    assert st["n"] == 40 and st["win_rate"] == 50.0 and st["expectancy_r"] == 0.5
    sess = {g["key"]: g for g in st["by_session"]}
    assert sess["Asia 00-07"]["net"] < 0 < sess["London 07-12"]["net"]
    s = next(x for x in out["suggestions"] if x["id"] == "by_session:Asia 00-07")
    assert s["kind"] == "parameter" and s["change"] == {"start_hour": 7}


def test_excursions_flag_give_back():
    df = R.from_backtest(_trades())
    times = pd.date_range("2026-03-01", "2026-03-25", freq="5min", tz="UTC")
    bars = pd.DataFrame({"time": times, "high": 2006.0, "low": 1994.0})  # every trade went +6 (1.2R) and -6
    out = R.review("S03", df, {"reward_risk": 1.5, "max_hold_bars": 10}, {"GOLD": bars})
    ex = out["stats"]["excursions"]
    assert ex["stopped_after_reaching_1r_pct"] == 100.0 and ex["winners_near_stop_pct"] == 100.0
    s = next(x for x in out["suggestions"] if x["id"] == "give_back_target")
    assert s["change"] == {"reward_risk": 1.0}


def test_small_sample_gives_no_suggestions():
    out = R.review("S01", R.from_backtest(_trades(6)), {})
    assert [s["id"] for s in out["suggestions"]] == ["sample"]


def test_live_exit_classification():
    assert R.classify_live_exit("server-side stop/target ([sl 1990])", "LONG", 1990.1, 1990.0, 2010.0) == "STOP_LOSS"
    assert R.classify_live_exit("server-side stop/target ([tp 2010])", "LONG", 2009.9, 1990.0, 2010.0) == "TAKE_PROFIT"
    assert R.classify_live_exit("MANUAL CLOSE by me: x", "LONG", 2000, 1990, 2010) == "MANUAL"
    assert R.classify_live_exit("flat for the weekend", "LONG", 2000, 1990, 2010) == "WEEKEND"
    assert R.classify_live_exit("max holding period reached (30 >= 30 bars)", "LONG", 2000, 1990, 2010) == "TIME"


def test_review_and_test_change_via_api(armed, cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from metals_lab.api.app import State, create_app
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    client = TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))
    r = client.post("/api/backtests", json={"strategy_id": "S01", "dataset": "synthetic", "synthetic_days": 30}).json()
    for _ in range(600):
        b = client.get(f"/api/backtests/{r['id']}").json()
        if b["status"] != "RUNNING":
            break
        time.sleep(0.1)
    rv = client.get(f"/api/review/S01?source=backtest:{r['id']}").json()
    assert rv["source"].startswith("backtest") and rv["stats"]["n"] == b["metrics"]["n_trades"]
    assert client.get("/api/review/S01").json()["stats"]["n"] == 0
    t = client.post("/api/review/S01/test", json={"change": {"stop_atr": 1.5}, "dataset": "synthetic"}).json()
    assert t["candidate_id"] != t["baseline_id"]
    for _ in range(900):
        rows = {x["id"]: x for x in client.get("/api/backtests").json()}
        if all(rows[i]["status"] not in ("RUNNING", "QUEUED") for i in (t["baseline_id"], t["candidate_id"])):
            break
        time.sleep(0.1)
    assert rows[t["candidate_id"]]["params"]["stop_atr"] == 1.5 and rows[t["baseline_id"]]["params"]["stop_atr"] == 1.2
    assert client.post("/api/review/S01/test", json={"change": {"nope": 1}}).status_code == 400
