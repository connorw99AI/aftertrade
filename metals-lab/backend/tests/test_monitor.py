import json

import pytest
from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app

from conftest import approve, make_open_proposal


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def _open(c, bot="S01", volume=0.04):
    p = make_open_proposal(c, bot=bot, volume=volume)
    audit, risk = approve(c, p)
    r = c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval)
    assert r.ok
    return r


def test_summary_lists_every_bot_with_account_strip(client, armed):
    _open(armed)
    s = client.get("/api/summary").json()
    assert len(s["bots"]) == 10 and s["armed"] and s["open_trades"] == 1
    assert {"balance", "equity", "currency"} <= set(s["account"])
    s01 = next(b for b in s["bots"] if b["bot_id"] == "S01")
    assert s01["open_positions"] and s01["max_entries_per_day"] == 5


def test_positions_show_live_pnl_distances_and_protection(client, armed):
    _open(armed)
    pos = client.get("/api/positions").json()["positions"]
    assert len(pos) == 1
    p = pos[0]
    assert p["in_terminal"] and p["protection_ok"] and p["to_sl"]["money"] < 0 < p["to_tp"]["money"]
    assert p["bot_name"] and p["minutes_open"] >= 0


def test_manual_close_goes_through_pipeline(client, armed):
    r = _open(armed)
    key = client.get("/api/positions").json()["positions"][0]["position_key"]
    res = client.post("/api/positions/close", json={"scope": "position", "target": key, "operator": "pytest"}).json()
    assert res["results"] and res["results"][0]["outcome"].startswith("EXEC_")
    assert armed.gw._b._positions.get(r.position_ticket) is None
    armed.reconciler.run()
    assert client.post("/api/positions/close", json={"scope": "all", "operator": "pytest"}).status_code == 404
    assert any(e["event"] == "MANUAL_CLOSE" for e in client.get("/api/activity").json()["feed"] if e["kind"] == "event")


def test_activity_feed_and_funnel(client, armed):
    _open(armed)
    a = client.get("/api/activity").json()
    assert len(a["funnel"]) == 10 and {"signals", "skipped_sizing", "auditor_held", "risk_rejected", "approved", "filled"} <= set(a["funnel"][0])
    assert next(f for f in a["funnel"] if f["bot_id"] == "S01")["filled"] == 1


def test_history_and_csv(client, armed):
    _open(armed)
    key = client.get("/api/positions").json()["positions"][0]["position_key"]
    client.post("/api/positions/close", json={"scope": "position", "target": key, "operator": "pytest"})
    armed.reconciler.run()
    h = client.get("/api/history").json()
    assert h["totals"]["count"] == 1 and "r_multiple" in h["trades"][0]
    csv = client.get("/api/history.csv")
    assert csv.headers["content-type"].startswith("text/csv") and "trade_id" in csv.text.splitlines()[0]
