"""The Assessor tunes a bot in rounds and only offers the result if it adds net profit without a deeper worst drawdown,
including on the last 20% of history it never tuned on."""
import json
import time

import pytest

from metals_lab import optimise as O
from metals_lab.strategies import build


def test_split_is_60_20_20_by_time():
    p = O.split_periods("2025-01-01", "2025-11-01")
    assert p["search"] == ("2025-01-01", "2025-07-02") and p["choose"] == ("2025-07-03", "2025-09-01")
    assert p["final"] == ("2025-09-02", "2025-11-01")


def test_candidates_try_both_directions_and_add_back_what_was_cut():
    p = build("S01L", {"trade_gold": "off", "silver_windows": "06:00-11:00"}).params
    ids = {c["id"] for c in O.candidates("S01L", p, [{"id": "by_metal:GOLD", "title": "x", "change": {"trade_silver": "off"}}])}
    assert {"stop_atr:wider", "stop_atr:tighter", "reward_risk:further", "reward_risk:nearer", "hold:longer", "hold:shorter",
            "exit:breakeven", "exit:trail3", "metal:GOLD:on", "by_metal:GOLD"} <= ids
    pair = {c["id"] for c in O.candidates("S10L", build("S10L").params, [])}
    assert not any(i.startswith(("exit:", "metal:")) for i in pair)


def _fake(base, wins_final=True):
    """Fake backtests: trail 3 helps everywhere; then a further target helps on top of it; breakeven is noise."""
    per = O.split_periods("2025-01-01", "2025-12-31")

    def run(params, start, end):
        where = "final" if start == per["final"][0] else ("choose" if start == per["choose"][0] else "search")
        net, dd = 100.0, -0.10
        if params.get("trail_atr") == 3.0:
            net, dd = net + 50, -0.08
        if params.get("trail_atr") == 3.0 and params.get("reward_risk", 0) > base["reward_risk"]:
            net += 20
        if params.get("breakeven_r") == 1.0:
            net += 40 if where == "search" else -40
        if params.get("stop_atr", 0) > base["stop_atr"]:
            net, dd = net + 100, -0.30                      # more profit but a deeper drawdown: never passes
        if where == "final" and not wins_final and params != base:
            net = 50.0
        return {"net_pnl": net, "max_drawdown": dd, "n_trades": 40}
    return run


def test_tunes_in_rounds_and_confirms_on_the_untouched_final_period():
    p = build("S01L").params
    res = O.evaluate("S01L", p, [], "2025-01-01", "2025-12-31", _fake(p))
    t = res["tuned"]
    steps = [s["id"] for s in t["steps"]]
    assert steps[0] == "exit:trail3" and steps[1].startswith("reward_risk:further")   # round 2 builds on round 1
    assert t["change"]["trail_atr"] == 3.0 and t["verdict"] == "passed" and t["gain"] > 0
    r1 = {c["id"]: c["verdict"] for c in res["rounds"][0]["candidates"]}
    assert r1["exit:trail3"] == "kept" and r1["exit:breakeven"] == "noise" and r1["stop_atr:wider"] == "no_help"
    assert len(res["rounds"]) <= O.ROUNDS and res["variants_tried"] >= len(res["rounds"][0]["candidates"])


def test_a_tuned_bot_that_fails_the_final_check_is_overfit():
    p = build("S01L").params
    assert O.evaluate("S01L", p, [], "2025-01-01", "2025-12-31", _fake(p, wins_final=False))["tuned"]["verdict"] == "overfit"


def test_nothing_helps_gives_no_tuned_change():
    p = build("S01L").params
    run = lambda params, s, e: {"net_pnl": -50.0 if params != p else 0.0, "max_drawdown": -0.1, "n_trades": 40}
    res = O.evaluate("S01L", p, [], "2025-01-01", "2025-12-31", run)
    assert res["tuned"] is None and len(res["rounds"]) == 1


def test_key_failures_name_what_breaks_a_strategy():
    from metals_lab import review as R
    rows = [{"trade_id": f"T{i}", "role": "GOLD", "side": "LONG", "entry_time": f"2025-0{1 + i % 6}-0{1 + i % 9}T10:00:00+00:00",
             "exit_time": f"2025-0{1 + i % 6}-0{1 + i % 9}T11:00:00+00:00", "entry_price": 2000.0, "exit_price": 1995.0, "stop": 1995.0,
             "target": 2010.0, "net_pnl": -10.0, "gross_pnl": -2.0, "commission": -8.0, "swap": 0.0, "spread_slip_cost": 0.0,
             "planned_risk": 10.0, "exit_reason": "STOP_LOSS", "exit_rationale": ""} for i in range(40)]
    df = R.from_backtest(rows)
    st = R.summarise(df)
    codes = {f["code"] for f in R.key_failures(df, st, {"tuned": None, "variants_tried": 10})}
    assert {"no_edge", "costs"} <= codes
    assert all(f["needs_code"] for f in R.key_failures(df, st, {"tuned": None, "variants_tried": 10}) if f["code"] in ("no_edge", "costs"))


def _done_assessment(armed, bot, change, verdict="passed"):
    per = O.split_periods("2025-01-01", "2025-12-31")
    m = lambda net: {"net": net, "dd": -0.08, "n": 40}
    res = {"periods": per, "rounds": [], "variants_tried": 9, "rule": "x", "failures": [],
           "tuned": {"change": change, "verdict": verdict, "gain": 15.0,
                     "steps": [{"round": 1, "id": "exit:trail3", "title": "Trail", "change": change, "search": m(150), "choose": m(60), "gain": 10}],
                     "original": {"search": m(100), "choose": m(50), "final": m(30)}, "tuned": {"search": m(150), "choose": m(60), "final": m(45)}}}
    cv = armed.db.one("SELECT config_version FROM bots WHERE bot_id=?", (bot,))["config_version"]
    cur = armed.db.x("INSERT INTO optimisations(bot_id,created_at,finished_at,status,dataset,start,\"end\",config_version,result_json) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (bot, "2099-10-25T00:00:00", "2099-10-25T00:10:00", "DONE", "mt5", "2025-01-01",
                                                   "2025-12-31", cv, json.dumps(res)))
    return cur.lastrowid


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from metals_lab.api.app import State, create_app
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def test_only_a_passed_tuned_change_can_be_applied_and_then_folds(armed, client):
    armed.db.x("UPDATE bots SET status='PAUSED' WHERE bot_id='S03L'")
    bad = {"suggestion_id": "by_session:GOLD Asia 00-07", "change": {"gold_windows": "07:00-24:00"}, "operator": "pytest"}
    assert client.post("/api/review/S03L/apply", json=bad).status_code == 409          # untested
    oid = _done_assessment(armed, "S03L", {"trail_atr": 3.0})
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L")
    assert s["id"] == f"test:tuned:{oid}" and s["tested"]["verdict"] == "passed" and "Final check" in s["evidence"]
    ok = {"suggestion_id": s["id"], "change": s["change"], "operator": "pytest"}
    assert client.post("/api/review/S03L/apply", json={**ok, "change": {"trail_atr": 2.0}}).status_code == 409
    row = client.post("/api/review/S03L/apply", json=ok).json()
    assert armed.strategy("S03L").p["trail_atr"] == 3.0 and row["config_version"] == 2
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L" and x["id"] == ok["suggestion_id"])
    assert s["applied"]["config_version"] == 2 and s["tested"]["stale"]
    assert client.post("/api/review/S03L/apply", json=ok).status_code == 409            # stale: assess again first


def test_overfit_is_not_offered(armed, client):
    _done_assessment(armed, "S02L", {"trail_atr": 3.0}, verdict="overfit")
    assert not any(x["bot_id"] == "S02L" and x.get("tested") for x in client.get("/api/assessor").json()["suggestions"])
    assert client.get("/api/assessor").json()["tests"]["S02L"]["result"]["tuned"]["verdict"] == "overfit"


def test_clear_suggestions_then_clear_history(armed, client):
    from metals_lab import assessor
    _done_assessment(armed, "S02L", {"trail_atr": 3.0})
    assessor.store_review(armed.db, "S02L", "demo", "x", {"stats": {"n": 1}, "suggestions": [{"id": "r", "title": "t", "rule_change": "do x", "change": None}]})
    armed.db.x("INSERT INTO applied_suggestions(bot_id,suggestion_id,change_json,applied_at) VALUES('S02L','a','{}','x')")
    assert any(x["bot_id"] == "S02L" for x in client.get("/api/assessor").json()["suggestions"])
    assert client.post("/api/assessor/clear-suggestions", json={}).status_code == 422          # operator name required
    client.post("/api/assessor/clear-suggestions", json={"operator": "pytest"})
    a = client.get("/api/assessor").json()
    assert not any(x["bot_id"] == "S02L" for x in a["suggestions"]) and a["tests"]["S02L"] is None
    assert armed.db.one("SELECT COUNT(*) n FROM applied_suggestions")["n"] == 1                # applied records kept
    out = client.post("/api/assessor/clear-history", json={"operator": "pytest"}).json()
    assert out["reviews"] >= 1 and out["applied_records"] == 1
    assert armed.db.one("SELECT COUNT(*) n FROM reviews")["n"] == 0
    assert armed.db.one("SELECT 1 FROM config_audit WHERE action='ASSESSOR_CLEAR_HISTORY' AND operator='pytest'")


def test_nothing_goes_to_the_engineer_until_sent(armed, client):
    _done_assessment(armed, "S01L", {"trail_atr": 3.0})
    res = json.loads(armed.db.one("SELECT result_json FROM optimisations WHERE bot_id='S01L'")["result_json"])
    res["failures"] = [{"code": "no_edge", "title": "No edge", "detail": "loses", "needs_code": True}]
    armed.db.x("UPDATE optimisations SET result_json=? WHERE bot_id='S01L'", (json.dumps(res),))
    assert client.get("/api/workshop?status=OPEN").json() == []                           # not sent automatically
    f = client.get("/api/optimise/S01L").json()["result"]["failures"][0]
    assert f["sent"] is False and f["rule"].startswith("redesign")
    body = {"bot_id": "S01L", "title": f["title"], "rule": f["rule"], "evidence": f["detail"], "operator": "pytest"}
    r = client.post("/api/workshop/send", json=body).json()
    assert r["status"] == "OPEN" and "OFF by default" in r["spec_md"] and r["operator"] == "pytest"
    assert client.post("/api/workshop/send", json=body).json()["id"] == r["id"]          # not raised twice
    assert client.get("/api/optimise/S01L").json()["result"]["failures"][0]["sent"] is True
    done = client.post(f"/api/workshop/{r['id']}/status", json={"status": "DONE", "operator": "pytest"}).json()
    assert done["status"] == "DONE" and client.get("/api/workshop?status=OPEN").json() == []


def test_each_closed_trade_gets_a_note():
    from metals_lab import assessor
    pos = [{"role": "GOLD", "side": "LONG", "sl": 1990.0, "tp": 2020.0, "planned_risk": 10.0,
            "signal_json": json.dumps({"stop_price": 1990.0, "ref_price": 2000.0, "rationale": "broke the high"})}]
    t = {"trade_id": "t1", "bot_id": "S03L", "side": "LONG", "entry_price": 2000.0, "exit_price": 2015.0, "net": 15.0,
         "entry_time": "2026-03-02T10:00:00+00:00", "exit_time": "2026-03-02T11:00:00+00:00",
         "exit_rationale": "server-side stop/target ([tp 2020])"}
    n = assessor.assess_trade(t, pos)
    assert n["verdict"] == "good" and n["r"] == 1.5 and n["entry_reason"] == "broke the high" and n["slippage_r"] == 0.0
    late = assessor.assess_trade({**t, "entry_price": 2004.0, "net": -10.0}, pos)
    assert late["verdict"] == "execution" and late["slippage_r"] > 0.25
    import pandas as pd
    bars = pd.DataFrame({"time": pd.date_range("2026-03-02 10:00", periods=12, freq="5min", tz="UTC"), "high": 2012.0, "low": 1990.0})
    unlucky = assessor.assess_trade({**t, "exit_price": 1990.0, "net": -10.0}, pos, {"GOLD": bars})
    assert unlucky["verdict"] == "unlucky" and unlucky["best_r"] == 1.2
    no_stop = assessor.assess_trade(t, [{**pos[0], "sl": None}])
    assert no_stop["verdict"] == "execution" and "traded without a stop" in no_stop["rule_issues"]


def test_assess_and_test_all_run_real_backtests(armed, client):
    assert client.post("/api/optimise/S08L", json={}).status_code == 409                # no history, no backtest yet
    r = client.post("/api/backtests", json={"strategy_id": "S08L", "dataset": "synthetic", "synthetic_days": 40}).json()
    for _ in range(900):
        if client.get(f"/api/backtests/{r['id']}").json()["status"] != "RUNNING":
            break
        time.sleep(0.2)
    assert armed.db.one("SELECT 1 FROM reviews WHERE bot_id='S08L'") is None             # no review per backtest run any more
    q = client.post("/api/optimise/all", json={}).json()
    assert q["queued"] == ["S08L"] and len(q["skipped"]) == 21
    for _ in range(4000):
        t = client.get("/api/optimise/S08L").json()
        if t["status"] in ("DONE", "FAILED"):
            break
        time.sleep(0.2)
    assert t["status"] == "DONE", t.get("error")
    res = t["result"]
    assert res["rounds"] and res["backtest_id"] and isinstance(res["failures"], list)
    assert armed.db.one("SELECT 1 FROM reviews WHERE bot_id='S08L' AND source LIKE 'assessment:%'")
