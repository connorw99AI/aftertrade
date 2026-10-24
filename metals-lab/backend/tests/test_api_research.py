import json
from datetime import timedelta

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


def test_api_contracts(client):
    s = client.get("/api/status").json()
    assert s["terminal_kind"] == "SIMULATED" and s["account"]["state"] == "DEMO" and s["armed"] is True
    assert "login" not in json.dumps(s["account"])
    bots = client.get("/api/bots").json()
    assert len(bots) == 20
    for b in bots:
        for k in ("bot_id", "name", "style", "symbols", "status", "book", "risk_budget_per_trade", "open_positions",
                  "closed_net_pnl", "return_pct", "max_drawdown_closed_pct", "last_signal", "last_audit", "last_risk", "magic"):
            assert k in b
        assert b["book"]["allocation"] == 10000.0
    d = client.get("/api/bots/S04L").json()
    assert d["strategy"]["citations"] and d["strategy"]["long_rules"]
    assert client.get("/api/strategies").status_code == 200
    assert client.get("/api/risk").json()["capital_snapshot"]["bot_fraction"] == 0.05
    assert client.get("/api/auditor/decisions").status_code == 200
    assert isinstance(client.get("/api/events?after=0").json(), list)
    assert len(client.get("/api/compare").json()) == 20


def test_no_arm_endpoint_exists(client):
    paths = {r.path for r in client.app.routes}
    assert not any("arm" in p and "disarm" not in p for p in paths)
    assert client.post("/api/arm", json={}).status_code in (404, 405)


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


def test_backtest_endpoint_labels_replay(client):
    r = client.post("/api/backtests", json={"strategy_id": "S08L", "dataset": "synthetic", "synthetic_days": 400}).json()
    import time
    for _ in range(200):
        b = client.get(f"/api/backtests/{r['id']}").json()
        if b["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert b["status"] == "DONE", b.get("diagnostics")
    assert b["label"].startswith("HISTORICAL REPLAY")
    assert any("SYNTHETIC" in x for x in b["reliability"])


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
