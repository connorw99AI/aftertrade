import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app
from metals_lab.data import bars as B
from metals_lab.data.store import DatasetStore, Provenance, quality_report
from metals_lab.research.runner import HoldoutLocked, Research, param_grid
from metals_lab.research.validation import (chronological_split, deflated_sharpe, stationary_bootstrap_ci,
                                            walk_forward_windows)


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    monkeypatch.setenv("METALS_LAB_DASHBOARD_DIR", str(tmp_path / "nodist"))
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def test_api_contracts(client, cfg):
    from metals_lab.strategies import BOT_IDS
    s = client.get("/api/status").json()
    assert s["terminal_kind"] == "SIMULATED" and s["account"]["state"] == "DEMO" and s["armed"] is True
    assert "login" not in json.dumps(s["account"])
    bots = client.get("/api/bots").json()
    assert len(bots) == len(BOT_IDS) == 24
    frac = cfg["capital"]["bot_fraction"]
    for b in bots:
        for k in ("bot_id", "name", "style", "symbols", "status", "book", "risk_budget_per_trade", "open_positions",
                  "closed_net_pnl", "return_pct", "max_drawdown_closed_pct", "last_signal", "last_audit", "last_risk", "magic"):
            assert k in b
        assert b["book"]["allocation"] == round(200_000 * frac, 2)  # 1/24 each
    d = client.get("/api/bots/S04L").json()
    assert d["strategy"]["citations"] and d["strategy"]["long_rules"]
    assert client.get("/api/strategies").status_code == 200
    assert client.get("/api/risk").json()["capital_snapshot"]["bot_fraction"] == frac == 0.041666
    assert client.get("/api/auditor/decisions").status_code == 200
    assert isinstance(client.get("/api/events?after=0").json(), list)
    assert len(client.get("/api/compare").json()) == 24


def test_arming_from_the_dashboard_is_local_typed_demo_only_and_logged(coord, cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from metals_lab.api.app import State, create_app
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    app = create_app(State(cfg, coord.db, coord, "SIMULATED"))
    local = TestClient(app, client=("127.0.0.1", 50000))
    remote = TestClient(app, client=("192.168.1.20", 50000))
    pv = local.get("/api/arm/preview").json()
    assert not pv["armed"] and pv["problems"] == [] and pv["phrase"].startswith("ARM DEMO ") and pv["bots"] == 24
    assert pv["per_bot"] == round(pv["starting_balance"] * cfg["capital"]["bot_fraction"], 2)
    good = {"phrase": pv["phrase"], "operator": "pytest"}
    assert remote.post("/api/arm", json=good).status_code == 403                  # only from this PC
    assert local.post("/api/arm", json={**good, "phrase": "ARM DEMO 000000"}).status_code == 409
    assert not coord.is_armed()
    coord.db.set_control("kill", "1")
    assert local.post("/api/arm", json=good).status_code == 409                   # kill switch stays a CLI step
    coord.db.set_control("kill", "0")
    r = local.post("/api/arm", json=good).json()
    assert r["armed"] and coord.is_armed() and r["per_bot_allocation"] > 0
    assert coord.db.one("SELECT 1 FROM config_audit WHERE action='ARM (dashboard)' AND operator='pytest'")
    assert local.post("/api/disarm", json={"reason": "test over", "operator": "pytest"}).json()["armed"] is False
    assert not coord.is_armed()


def test_arming_refuses_a_live_account(coord, cfg, tmp_path, monkeypatch, sim):
    from fastapi.testclient import TestClient
    from metals_lab.api.app import State, create_app
    from metals_lab.mt5.sim import SimAccount
    t, _ = sim
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    t.add_account(SimAccount(login=2002, server="Sim-Live", balance=5000.0, trade_mode=2))
    t.switch_account(2002)
    local = TestClient(create_app(State(cfg, coord.db, coord, "SIMULATED")), client=("127.0.0.1", 50000))
    pv = local.get("/api/arm/preview").json()
    assert pv["problems"]
    assert local.post("/api/arm", json={"phrase": pv["phrase"], "operator": "pytest"}).status_code == 409
    assert not coord.is_armed()


def test_bot_controls_and_param_change_rules(client):
    assert client.post("/api/bots/S02L/pause", json={"reason": "t"}).json()["status"] == "PAUSED"
    r = client.put("/api/bots/S02L/params", json={"params": {"rsi_low": 15}, "operator": "me"})
    assert r.status_code == 200 and r.json()["config_version"] == 2
    assert client.put("/api/bots/S02L/params", json={"params": {"bogus": 1}, "operator": "me"}).status_code == 409
    client.post("/api/bots/S02L/start", json={})
    assert client.put("/api/bots/S02L/params", json={"params": {"rsi_low": 10}, "operator": "me"}).status_code == 409


def test_token_required_when_configured(armed, cfg, monkeypatch):
    monkeypatch.setenv("METALS_LAB_API_TOKEN", "s3cret")
    c = TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))
    assert c.post("/api/risk/pause-all", json={}).status_code == 401
    assert c.post("/api/risk/pause-all", json={}, headers={"X-API-Token": "s3cret"}).status_code == 200


def test_backtest_endpoint_labels_replay(client, cfg):
    r = client.post("/api/backtests", json={"strategy_id": "S08L", "dataset": "synthetic", "synthetic_days": 400}).json()
    import time
    for _ in range(1200):   # a 400-day M5 backtest can take over a minute on a slow machine
        b = client.get(f"/api/backtests/{r['id']}").json()
        if b["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert b["status"] == "DONE", b.get("diagnostics")
    assert b["label"].startswith("HISTORICAL REPLAY")
    assert any("SYNTHETIC" in x for x in b["reliability"])
    # each bot starts with bot_fraction (1/24) of the starting balance, not whatever an old database allocated
    assert b["metrics"]["initial_capital"] == round(200000.0 * cfg["capital"]["bot_fraction"], 2)


def test_dataset_store_versioning_integrity_and_quality(tmp_path, intraday_frames):
    ds = DatasetStore(tmp_path)
    df = intraday_frames[("GOLD", "M5")].iloc[:500]
    dup = pd.concat([df, df.iloc[[10]]])
    rep = quality_report(dup.sort_values("time"), "M5")
    assert rep["duplicates"] == 1 and rep["status"] == "ISSUES"
    v = ds.save(df, Provenance("SYNTHETIC", "XAUUSD", "GOLD", "M5"))
    loaded, m = ds.load(v)
    assert len(loaded) == 500 and str(loaded["time"].dt.tz) == "UTC" and m["provenance"]["source"] == "SYNTHETIC"
    assert ds.latest("SYNTHETIC", "GOLD", "M5") == v
    p = next(tmp_path.rglob(f"{v}.parquet"))
    p.write_bytes(p.read_bytes()[:-10] + b"0123456789")
    with pytest.raises(ValueError):
        ds.load(v)


def test_bars_from_ticks_measures_spread():
    t = pd.date_range("2026-01-05 10:00", periods=6, freq="20s", tz="UTC")
    ticks = pd.DataFrame({"time": t, "bid": [1, 2, 3, 2, 1, 2.0], "ask": [1.1, 2.1, 3.2, 2.1, 1.1, 2.1]})
    b = B.bars_from_ticks(ticks, "M1", 0.01)
    assert list(b["high"]) == [3.0, 2.0] and b["spread"].iloc[0] == 10 and b["tick_volume"].iloc[0] == 3


def test_splits_and_walk_forward_are_chronological():
    s, e = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")
    sp = chronological_split(s, e)
    assert sp.train[1] == sp.validation[0] and sp.validation[1] == sp.test[0] and sp.test[1] == sp.holdout[0]
    assert sp.holdout[1] == e
    w = walk_forward_windows(s, e, 90, 30, anchored=False)
    assert all(a[1] == a[2] and a[0] < a[1] < a[3] for a in w)
    assert all(w[i][3] <= w[i + 1][3] for i in range(len(w) - 1))
    wa = walk_forward_windows(s, e, 90, 30, anchored=True)
    assert all(x[0] == s for x in wa)


def test_deflated_sharpe_penalises_more_trials():
    r = pd.Series(np.random.default_rng(1).normal(0.001, 0.01, 500))
    one = deflated_sharpe(r, 1, 0.05)["dsr"]
    many = deflated_sharpe(r, 200, 0.05)["dsr"]
    assert many < one


def test_bootstrap_ci_brackets_estimate():
    x = np.random.default_rng(2).normal(1.0, 1.0, 300)
    ci = stationary_bootstrap_ci(x, n_boot=300)
    assert ci["lo"] < ci["estimate"] < ci["hi"]


def test_holdout_touched_only_once(swing_frames, specs):
    used = set()

    def guard(sid, params):
        if sid in used:
            raise HoldoutLocked("used")
        used.add(sid)
    r = Research("S08", swing_frames, specs, holdout_check=guard)
    rep = r.full_study(max_trials=3, wf_train_days=300, wf_test_days=120, touch_holdout=True)
    assert isinstance(rep["holdout_metrics"], dict)
    assert {t["kind"] for t in rep["all_trials"]} >= {"tune_train", "validation", "test", "HOLDOUT", "cost_stress", "sensitivity",
                                                       "walk_forward_oos"}
    with pytest.raises(HoldoutLocked):
        Research("S08", swing_frames, specs, holdout_check=guard).full_study(max_trials=2, wf_train_days=300, wf_test_days=120,
                                                                             touch_holdout=True)
    assert len(param_grid("S01", 10)) == 10


def test_event_filter_comparison_reports_opportunity_cost(swing_frames, specs):
    from metals_lab.backtest.models import BacktestConfig
    r = Research("S08", swing_frames, specs, BacktestConfig(initial_capital=100000))
    start, end = r.start, r.end

    def block_mondays(sig, T):
        return ("DELAYED", ["TEST_FILTER"]) if T.weekday() == 0 else ("APPROVED", [])
    out = r.event_filter_comparison({}, start, end, block_mondays)
    for k in ("unfiltered", "filtered", "blocked_trades", "blocked_profitable", "opportunity_cost", "avoided_losses"):
        assert k in out
    assert out["unfiltered"]["n_trades"] > 0
    assert out["blocked_trades"] >= 0 and out["opportunity_cost"] >= 0 and out["avoided_losses"] >= 0


def test_chart_endpoint_returns_separate_metals_and_positions(client, armed):
    from conftest import approve, make_open_proposal
    p = make_open_proposal(armed)
    audit, risk = approve(armed, p)
    if risk is not None and risk.approval is not None:
        armed.executor.submit(p, armed.specs["GOLD"], audit.approval, risk.approval)
    g = client.get("/api/chart?role=GOLD&tf=M15&bars=60").json()
    s = client.get("/api/chart?role=SILVER&tf=H1&bars=60").json()
    assert g["symbol"] == "XAUUSD" and s["symbol"] == "XAGUSD"
    assert len(g["candles"]) >= 20 and g["candles"][-1]["forming"] is True
    assert all(c["l"] <= min(c["o"], c["c"]) and c["h"] >= max(c["o"], c["c"]) for c in g["candles"])
    assert g["bid"] is not None and s["positions"] == []
    if risk is not None and risk.approval is not None:
        assert g["positions"] and g["positions"][0]["bot_id"] == "S01L"
    assert client.get("/api/chart?role=GOLD&tf=W9").status_code == 400
    assert client.get("/api/chart?role=PLATINUM").status_code == 404


def test_downloaded_history_can_be_listed_and_deleted(client, cfg, intraday_frames):
    ds = DatasetStore(cfg["storage"]["cache_dir"])
    df = intraday_frames[("GOLD", "M5")].iloc[:300]
    a = ds.save(df, Provenance("MT5_TERMINAL", "XAUUSD", "GOLD", "M5"))
    b = ds.save(df.iloc[:200], Provenance("MT5_TERMINAL", "XAUUSD", "GOLD", "M15"))
    listed = client.get("/api/data/downloads").json()
    assert {d["version"] for d in listed["downloads"]} == {a, b} and listed["total_bytes"] > 0
    assert client.post("/api/data/delete", json={"versions": [a], "operator": "me"}).json()["deleted"] == 1
    assert [d["version"] for d in client.get("/api/data/downloads").json()["downloads"]] == [b]
    assert client.post("/api/data/delete", json={"all": True, "operator": "me"}).json()["deleted"] == 1
    assert client.get("/api/data/downloads").json()["downloads"] == []
    assert client.post("/api/data/delete", json={"all": True, "operator": "me"}).status_code == 404
    assert client.post("/api/data/delete", json={"all": True}).status_code == 422  # operator name required
