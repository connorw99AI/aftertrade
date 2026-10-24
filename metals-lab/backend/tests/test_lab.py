import time

import pytest
from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app
from metals_lab.api.lab import fx_from_spec


@pytest.fixture
def client(armed, cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    return TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))


def _wait(client, ids, limit=600):
    for _ in range(limit):
        rows = {r["id"]: r for r in client.get("/api/backtests").json()}
        if all(rows[i]["status"] not in ("RUNNING", "QUEUED") for i in ids):
            return rows
        time.sleep(0.1)
    raise AssertionError("backtests did not finish")


def test_missing_history_gives_plain_error(client):
    r = client.post("/api/backtests", json={"strategy_id": "S08L"}).json()
    row = _wait(client, [r["id"]])[r["id"]]
    assert row["status"] == "FAILED" and "Download history from MT5" in row["error"]
    st = client.get("/api/data/status").json()
    assert not next(b for b in st["bots"] if b["bot_id"] == "S08L")["ready"]


def test_fetch_then_backtest_on_terminal_history_in_account_currency(client, armed):
    j = client.post("/api/data/fetch", json={"days": 20, "timeframes": ["M15", "H1"]}).json()
    assert j["state"] == "RUNNING"
    for _ in range(300):
        st = client.get("/api/data/status").json()
        if st["fetch"]["state"] == "DONE":
            break
        time.sleep(0.1)
    assert st["fetch"]["state"] == "DONE" and not st["fetch"]["failed"], st["fetch"]
    assert next(b for b in st["bots"] if b["bot_id"] == "S08L")["ready"]
    r = client.post("/api/backtests", json={"strategy_id": "S08L"}).json()
    row = _wait(client, [r["id"]])[r["id"]]
    assert row["status"] == "DONE", row.get("error")
    assert row["metrics"]["initial_capital"] == armed.bot_book("S08L").allocation
    assert "account_currency" in row["metrics"] and not any("SYNTHETIC" in x for x in row["reliability"])


def test_run_all_and_clear(client):
    r = client.post("/api/backtests/run-all", json={"dataset": "synthetic", "synthetic_days": 15}).json()
    assert len(r["ids"]) == 20
    rows = _wait(client, r["ids"], limit=3000)
    assert all(rows[i]["status"] == "DONE" for i in r["ids"]), [rows[i].get("error") for i in r["ids"]]
    c = client.post("/api/backtests/clear").json()
    assert c["deleted_backtests"] == 20 and client.get("/api/backtests").json() == []


def test_fx_from_tick_value():
    from metals_lab.instruments import InstrumentSpec
    s = InstrumentSpec(role="GOLD", symbol="XAUUSD", contract_size=100, tick_size=0.01, tick_value=0.7469)
    assert abs(fx_from_spec(s) - 0.7469) < 1e-9
