"""The Assessor's tester keeps only changes that add net profit without a deeper worst drawdown, on the period they were
found on and again on later history."""
import json
import time

import pandas as pd
import pytest

from metals_lab import optimise as O
from metals_lab.strategies import build


def test_split_is_70_30_by_time():
    a, cut, b = O.split_period("2025-01-01", "2025-11-01")
    assert (a, b) == ("2025-01-01", "2025-11-01") and cut == "2025-08-01"


def test_candidates_try_both_directions_and_add_back_what_was_cut():
    p = build("S01L", {"trade_gold": "off", "silver_windows": "06:00-11:00"}).params
    ids = {c["id"] for c in O.candidates("S01L", p, [{"id": "by_metal:GOLD", "title": "x", "change": {"trade_silver": "off"}}])}
    assert {"stop_atr:wider", "stop_atr:tighter", "reward_risk:further", "reward_risk:nearer", "hold:longer", "hold:shorter",
            "exit:breakeven", "exit:trail3", "metal:GOLD:on"} <= ids
    assert "by_metal:GOLD" in ids                    # the review's own idea is tested too
    assert "windows:SILVER:all" in ids or len(ids) == O.MAX_CANDIDATES
    pair = {c["id"] for c in O.candidates("S10L", build("S10L").params, [])}
    assert not any(i.startswith(("exit:", "metal:")) for i in pair)   # a pair's legs exit together; metals are not switches


def test_only_more_profit_with_no_deeper_drawdown_on_both_periods_passes():
    p = build("S01L").params
    cut = O.split_period("2025-01-01", "2025-12-31")[1]

    def run(params, start, end):  # fake backtests: the "found on" period ends at the cut
        later = start > cut
        if params == p:
            return {"net_pnl": 100.0, "max_drawdown": -0.10, "n_trades": 40}
        if params.get("trail_atr") == 3.0:          # better on both
            return {"net_pnl": 150.0, "max_drawdown": -0.08, "n_trades": 40}
        if params.get("breakeven_r") == 1.0:        # better where found, worse later: noise
            return {"net_pnl": 140.0 if not later else 60.0, "max_drawdown": -0.09, "n_trades": 40}
        if params.get("stop_atr", 0) > p["stop_atr"]:  # more profit but a deeper drawdown: rejected
            return {"net_pnl": 200.0, "max_drawdown": -0.20, "n_trades": 40}
        return {"net_pnl": 90.0, "max_drawdown": -0.10, "n_trades": 40}

    res = O.evaluate("S01L", p, [], "2025-01-01", "2025-12-31", run)
    v = {c["id"]: c["verdict"] for c in res["candidates"]}
    assert v["exit:trail3"] == "passed" and v["exit:breakeven"] == "noise" and v["stop_atr:wider"] == "no_help"
    assert res["candidates"][0]["id"] == "exit:trail3" and res["candidates"][0]["gain"] == 50.0
    assert res["variants_tried"] == len(res["candidates"]) and res["period"]["confirm_start"] > res["period"]["found_end"]


def test_too_few_later_trades_is_unconfirmed():
    p = build("S01L").params
    run = lambda params, s, e: {"net_pnl": 150.0 if params != p else 100.0, "max_drawdown": -0.1, "n_trades": 4}
    res = O.evaluate("S01L", p, [], "2025-01-01", "2025-12-31", run)
    assert {c["verdict"] for c in res["candidates"]} == {"unconfirmed"}


def _done_test(armed, bot, change, verdict="passed"):
    res = {"period": {"start": "2025-01-01", "found_end": "2025-09-12", "confirm_start": "2025-09-13", "end": "2025-12-31"},
           "baseline": {"in": {"net": 100.0, "dd": -0.1, "n": 40}, "out": {"net": 30.0, "dd": -0.05, "n": 15}},
           "candidates": [{"id": "exit:trail3", "title": "Trail", "change": change, "origin": "tried", "verdict": verdict,
                           "in": {"net": 150.0, "dd": -0.08, "n": 40}, "out": {"net": 45.0, "dd": -0.04, "n": 15}, "gain": 15.0}],
           "variants_tried": 9, "rule": "x"}
    cv = armed.db.one("SELECT config_version FROM bots WHERE bot_id=?", (bot,))["config_version"]
    armed.db.x("INSERT INTO optimisations(bot_id,created_at,finished_at,status,dataset,start,\"end\",config_version,result_json) "
               "VALUES(?,?,?,?,?,?,?,?,?)", (bot, "2026-10-25T00:00:00", "2026-10-25T00:10:00", "DONE", "mt5", "2025-01-01",
                                             "2025-12-31", cv, json.dumps(res)))


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from metals_lab.api.app import State, create_app
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def test_only_passed_tested_changes_can_be_applied_and_then_fold(armed, client):
    from metals_lab import assessor
    armed.db.x("UPDATE bots SET status='PAUSED' WHERE bot_id='S03L'")
    body = {"suggestion_id": "by_session:GOLD Asia 00-07", "change": {"gold_windows": "07:00-24:00"}, "operator": "pytest"}
    assert client.post("/api/review/S03L/apply", json=body).status_code == 409          # untested
    _done_test(armed, "S03L", {"trail_atr": 3.0})
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L")
    assert s["id"] == "test:exit:trail3" and s["tested"] and "Confirmed on" in s["evidence"] and s["applied"] is None
    ok = {"suggestion_id": s["id"], "change": s["change"], "operator": "pytest"}
    assert client.post("/api/review/S03L/apply", json={**ok, "change": {"trail_atr": 2.0}}).status_code == 409  # not what passed
    row = client.post("/api/review/S03L/apply", json=ok).json()
    assert armed.strategy("S03L").p["trail_atr"] == 3.0 and row["config_version"] == 2
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L" and x["id"] == ok["suggestion_id"])
    assert s["applied"]["config_version"] == 2 and s["tested"]["stale"]                 # folded; the test is now out of date
    assert client.post("/api/review/S03L/apply", json=ok).status_code == 409            # stale: test again first
    assert assessor.latest_test(armed.db, "S03L")["stale"]


def test_noise_is_not_offered(armed, client):
    _done_test(armed, "S02L", {"trail_atr": 3.0}, verdict="noise")
    assert not any(x["bot_id"] == "S02L" and x.get("tested") for x in client.get("/api/assessor").json()["suggestions"])
    t = client.get("/api/assessor").json()["tests"]["S02L"]
    assert t["status"] == "DONE" and t["result"]["candidates"][0]["verdict"] == "noise"


def test_test_now_runs_real_backtests_on_the_bots_last_backtest_period(armed, client):
    assert client.post("/api/optimise/S08L", json={}).status_code == 409                # no backtest yet
    r = client.post("/api/backtests", json={"strategy_id": "S08L", "dataset": "synthetic", "synthetic_days": 40}).json()
    for _ in range(900):
        if client.get(f"/api/backtests/{r['id']}").json()["status"] != "RUNNING":
            break
        time.sleep(0.2)
    assert armed.db.one("SELECT 1 FROM optimisations WHERE bot_id='S08L'") is None       # synthetic: no automatic test
    q = client.post("/api/optimise/S08L", json={}).json()
    assert q["status"] in ("QUEUED", "RUNNING") and q["dataset"] == "synthetic"
    for _ in range(3000):
        t = client.get("/api/optimise/S08L").json()
        if t["status"] in ("DONE", "FAILED"):
            break
        time.sleep(0.2)
    assert t["status"] == "DONE", t.get("error")
    res = t["result"]
    assert res["baseline"]["in"]["n"] >= 0 and res["variants_tried"] >= 5
    assert all(c["verdict"] in ("passed", "noise", "unconfirmed", "no_help") for c in res["candidates"])
