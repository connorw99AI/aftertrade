import time

import pytest
from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app

from conftest import approve, make_open_proposal


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def _open_and_close(c, client, bot="S08L"):
    p = make_open_proposal(c, bot=bot, volume=0.04)
    audit, risk = approve(c, p)
    assert c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval).ok
    key = client.get("/api/positions").json()["positions"][0]["position_key"]
    client.post("/api/positions/close", json={"scope": "position", "target": key, "operator": "pytest"})


def test_assessor_reviews_automatically_when_a_trade_closes(armed, client):
    _open_and_close(armed, client)
    armed.cycle()  # reconciliation sees the closing deal and the assessor runs
    rows = armed.db.q("SELECT bot_id, source, trigger FROM reviews")
    assert any(r["bot_id"] == "S08L" and r["source"] == "demo" and r["trigger"] == "trade closed" for r in rows)
    a = client.get("/api/assessor").json()
    assert len(a["cards"]) == 20 and a["last_review_at"]
    assert next(c for c in a["cards"] if c["bot_id"] == "S08L")["demo"]["grade"]["grade"] == "too early"


def test_history_shows_entry_and_exit_per_leg(armed, client):
    _open_and_close(armed, client)
    armed.reconciler.run()
    t = client.get("/api/history").json()["trades"][0]
    leg = t["legs"][0]
    assert leg["entry_price"] and leg["exit_price"] and leg["sl"] and leg["tp"] and leg["price_move"] is not None


def test_assessor_reviews_finished_backtests_and_run_all(armed, client):
    r = client.post("/api/backtests", json={"strategy_id": "S09L", "dataset": "synthetic", "synthetic_days": 20}).json()
    for _ in range(600):
        if client.get(f"/api/backtests/{r['id']}").json()["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert armed.db.one("SELECT 1 FROM reviews WHERE bot_id='S09L' AND source=? AND trigger='backtest finished'", (f"backtest:{r['id']}",))
    assert client.post("/api/assessor/run").json()["reviews"] >= 20


def test_date_range_download_and_backtest(armed, client):
    from metals_lab.timeutil import utc_now
    end = utc_now().date()
    start = end.fromordinal(end.toordinal() - 12)
    j = client.post("/api/data/fetch", json={"start": str(start), "end": str(end), "timeframes": ["M15", "H1"]}).json()
    assert j["start"] == str(start)
    for _ in range(300):
        st = client.get("/api/data/status").json()
        if st["fetch"]["state"] == "DONE":
            break
        time.sleep(0.1)
    assert not st["fetch"]["failed"], st["fetch"]
    row = next(d for d in st["datasets"] if d["role"] == "GOLD" and d["timeframe"] == "M15")
    assert row["start"][:10] >= str(start.fromordinal(start.toordinal() - 1))
    mid = start.fromordinal(start.toordinal() + 6)
    r = client.post("/api/backtests", json={"strategy_id": "S08L", "start": str(mid), "end": str(end)}).json()
    for _ in range(600):
        b = client.get(f"/api/backtests/{r['id']}").json()
        if b["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert b["status"] == "DONE", b.get("error")
    assert b["config"]["config"]["start"] == str(mid)
    assert client.post("/api/data/fetch", json={"start": str(end), "end": str(start)}).status_code == 400


def test_applied_suggestion_is_recorded_and_folds_until_a_different_one_appears(armed, client):
    from metals_lab import assessor
    sug = {"id": "by_session:SILVER Asia 00-07", "title": "Silver Asia 00-07 UK loses", "kind": "parameter",
           "strength": "moderate", "change": {"silver_windows": "07:00-24:00"}}
    rid = assessor.store_review(armed.db, "S03L", "backtest:1", "manual", {"stats": {"n": 40}, "suggestions": [sug]})
    armed.db.x("UPDATE bots SET status='RUNNING' WHERE bot_id='S03L'")
    body = {"suggestion_id": sug["id"], "change": sug["change"], "operator": "pytest", "review_id": rid}
    assert client.post("/api/review/S03L/apply", json=body).status_code == 409   # running: pause first
    armed.db.x("UPDATE bots SET status='PAUSED' WHERE bot_id='S03L'")
    row = client.post("/api/review/S03L/apply", json=body).json()
    assert armed.strategy("S03L").p["silver_windows"] == "07:00-24:00" and armed.strategy("S03L").p["gold_windows"] == "00:00-24:00"
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L")
    assert s["applied"]["config_version"] == row["config_version"] == 2
    assert armed.db.one("SELECT 1 FROM config_audit WHERE bot_id='S03L' AND action='PARAMS'")
    # a newer review suggesting a different change shows as open again
    assessor.store_review(armed.db, "S03L", "backtest:2", "manual",
                          {"stats": {"n": 60}, "suggestions": [{**sug, "change": {"silver_windows": "07:00-21:00"}}]})
    s = next(x for x in client.get("/api/assessor").json()["suggestions"] if x["bot_id"] == "S03L")
    assert s["applied"] is None
    assert client.post("/api/review/S03L/apply", json={**body, "change": {}}).status_code == 409
