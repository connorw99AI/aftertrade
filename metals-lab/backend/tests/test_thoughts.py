"""The Lab card's thought line and readiness come only from real checks, statuses and trades."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app
from metals_lab.api.thoughts import readiness, thought
from metals_lab.store import Store

from conftest import approve, make_open_proposal

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
WINDOW = {"intervals": [[720, 1200]]}  # 12:00-20:00 UTC


def view(filters, long, short, verdict="NO_ENTRY"):
    c = lambda label, ok, detail="": {"label": label, "ok": ok, "detail": detail}
    return {"verdict": verdict, "filters": [c(*f) for f in filters], "long": [c(*x) for x in long], "short": [c(*x) for x in short]}


def say(**kw):
    base = dict(side="L", status="RUNNING", kill=False, stopped_for_day=None, opens=[], th=None, metals="both", window=WINDOW, now=NOW)
    return thought(**{**base, **kw})


def test_readiness_counts_only_the_bots_own_side():
    v = view([("Spread ok", True), ("Metal allowed (gold only)", False)],
             [("Bullish candle", True), ("Long trades allowed", True)], [("Bearish candle", False), ("Short trades allowed", False)])
    assert readiness(v, "L") == {"met": 2, "total": 2, "setup_met": 1, "setup_total": 1}
    assert readiness(v, "S")["met"] == 1
    assert readiness({"waiting": True}, "L") is None


def test_thought_names_the_missing_condition():
    th = {"timeframe": "M5", "roles": {"GOLD": view([("Spread ok", True)], [("Broke the low", True), ("Bullish candle", False)], [])}}
    assert say(th=th) == "1 of 2 conditions for a gold long; missing: bullish candle."


def test_thought_reports_a_failing_filter_and_the_clock():
    th = {"timeframe": "M5", "roles": {"GOLD": view([("Not a strong trend (ADX < 30)", False, "ADX 35.8")], [("Bullish candle", True)], [])}}
    assert "Failing check: not a strong trend (ADX < 30), ADX 35.8" in say(th=th)
    assert say(th=th, now=NOW.replace(hour=8)) == "Off duty. My window opens at 12:00 UTC."
    assert say(th=th, now=NOW.replace(hour=21)) == "Off duty. No more trading windows today."


def test_thought_status_trade_and_kill_take_priority():
    th = {"timeframe": "M5", "roles": {}}
    trade = [{"role": "GOLD", "side": "LONG", "opened_at": "2026-10-21T14:05:00+00:00", "sl": 1505.8, "tp": 1531.1, "symbol": "XAUUSD"}]
    assert say(th=th, opens=trade) == "In a gold long since 14:05 UTC. Stop 1505.80, target 1531.10."
    assert say(th=th, kill=True, opens=trade).startswith("The kill switch is on")
    assert say(th=th, status="PAUSED_RISK").startswith("Paused after falling to the drawdown limit")
    assert say(th=th, status="PAUSED").startswith("Paused by you.")
    assert say(th=th, stopped_for_day="daily loss stop") == "Done for today: daily loss stop reached."
    assert say(th=None) == "Waiting for my first bar to close."


def test_summary_carries_thought_readiness_window_and_environment(armed, cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("METALS_LAB_API_TOKEN", raising=False)
    cfg["storage"]["cache_dir"] = str(tmp_path / "cache")
    client = TestClient(create_app(State(cfg, armed.db, armed, "SIMULATED")))
    s = client.get("/api/summary").json()
    for b in s["bots"]:
        assert b["environment"] == "demo" and isinstance(b["thought"], str) and b["thought"]
        assert b["trading_window"]["intervals"] and all(0 <= a < z <= 1440 for a, z in b["trading_window"]["intervals"])
        assert b["config_version"] == 1 and b["timeframes"]
    f = client.get("/api/funnel?bot=S01L&days=7").json()
    assert len(f) == 1 and f[0]["bot_id"] == "S01L"
    assert client.get("/api/funnel?bot=NOPE").status_code == 404


def test_trades_record_the_rule_version_they_opened_under(armed):
    armed.set_bot_status("S01L", "PAUSED")
    params = {k: v["value"] for k, v in armed.strategy("S01L").describe()["params"].items()}
    armed.update_params("S01L", params, "pytest")  # rules v2
    armed.set_bot_status("S01L", "RUNNING")
    p = make_open_proposal(armed, bot="S01L", volume=0.04)
    audit, risk = approve(armed, p)
    r = armed.executor.submit(p, armed.specs["GOLD"], audit.approval, risk.approval)
    assert r.ok
    row = armed.db.one("SELECT config_version FROM positions WHERE bot_id='S01L' AND status='OPEN'")
    assert row["config_version"] == 2


def test_older_databases_gain_the_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trades (trade_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL)")
    con.execute("CREATE TABLE positions (position_key TEXT PRIMARY KEY, bot_id TEXT NOT NULL, status TEXT NOT NULL)")
    con.commit(); con.close()
    db = Store(path)
    for t in ("trades", "positions"):
        assert "config_version" in {r["name"] for r in db.q(f"PRAGMA table_info({t})")}


def test_daily_stops_lock_in_profit():
    from metals_lab.risk.daily import daily_stop
    r = {"daily_loss_pause": 0.30, "daily_lock_from": 0.05, "daily_giveback": 0.5, "daily_profit_stop": 0}
    assert daily_stop(500, 500, 360, r) is None                      # down 28%: still trading
    assert daily_stop(500, 500, 350, r) == "DAILY_LOSS_LIMIT"        # down 30%
    assert daily_stop(500, 650, 600, r) is None                      # best +30%, now +20%: kept more than half
    assert daily_stop(500, 650, 575, r) == "DAILY_PROFIT_LOCK"       # gave back half of +150
    assert daily_stop(500, 520, 501, r) is None                      # best only +4%: lock not active yet
    assert daily_stop(500, 900, 900, r) is None                      # no profit stop
    assert daily_stop(500, 900, 900, {**r, "daily_profit_stop": 0.3}) == "DAILY_PROFIT_TARGET_REACHED"


def test_min_lot_never_risks_more_than_the_cap(armed):
    from metals_lab.domain import Intent
    strat = armed.strategy("S01L")
    p = make_open_proposal(armed, bot="S01L", volume=0.04)
    sig = p.signal
    spec = armed.specs["GOLD"]
    loss = lambda spec, side, entry, stop, vol: abs(entry - stop) * spec.contract_size * vol
    far = sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - 40.0})   # 0.04 lot x $40 = $160 at the stop
    tp = strat.calculate_target_positions([far], 500.0, armed.specs, 0.03, loss, lambda s: 0.0, min_lot=0.04, max_risk_pct=0.10)[0]
    assert tp.skipped and tp.skip_reason.startswith("MIN_LOT_RISK_ABOVE_CAP")
    near = sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - 5.0})   # $20 = 4%: allowed at the floor
    tp = strat.calculate_target_positions([near], 500.0, armed.specs, 0.03, loss, lambda s: 0.0, min_lot=0.04, max_risk_pct=0.10)[0]
    assert not tp.skipped and tp.volume == 0.04


def test_tight_stops_trade_the_big_lot_and_wide_stops_the_small_one(armed):
    strat = armed.strategy("S01L")
    sig = make_open_proposal(armed, bot="S01L", volume=0.1).signal
    loss = lambda spec, side, entry, stop, vol: abs(entry - stop) * spec.contract_size * vol
    size = lambda dist: strat.calculate_target_positions(
        [sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - dist})], 150.0, armed.specs, 0.03, loss, lambda s: 0.0,
        min_lot={"GOLD": 0.05}, big_lot={"GOLD": 0.1}, big_lot_max_risk=0.30)[0]
    tight, wide = size(4.0), size(15.0)          # 0.1 lot: $40 (27% of 150) vs $150 (100%)
    assert tight.volume == 0.1 and not tight.skipped
    assert wide.volume == 0.05 and not wide.skipped   # no cap: the strategy's wide stop still trades, at the smaller size
    assert wide.signal.stop_price == sig.ref_price - 15.0


def test_settings_update_edits_config_once_and_keeps_the_rest(tmp_path):
    import yaml
    from metals_lab.settings_update import apply
    p = tmp_path / "config.yaml"
    p.write_text("mt5:\n  server_timezone: Europe/Athens   # keep me\n\nrisk:\n  risk_per_trade: 0.30\n  min_lot: {GOLD: 0.04, SILVER: 0.02}\n"
                 "  daily_loss_pause: 0.50\n  daily_profit_stop: false\n  max_drawdown_pause: 0.50\n")
    assert apply(p) == ["2026-10-25-risk", "2026-10-25-silver"]
    r = yaml.safe_load(p.read_text())["risk"]
    assert r["min_lot"] == {"GOLD": 0.05, "SILVER": 0.03} and r["big_lot"] == {"GOLD": 0.1, "SILVER": 0.05}
    assert r["daily_loss_pause"] == 0.30 and r["daily_profit_stop"] == 0 and r["risk_per_trade"] == 0.03
    assert r["max_drawdown_pause"] == 0.50 and "# keep me" in p.read_text()
    assert (tmp_path / "config.yaml.bak-2026-10-25-risk").exists()
    assert apply(p) == []   # applied once only
    assert apply(tmp_path / "missing.yaml") == []


def test_every_bot_trades_both_metals_while_the_switch_is_on(armed):
    import json
    from metals_lab.strategies import BOT_IDS, build
    assert all(build(b).p["metals"] == "both" for b in BOT_IDS)           # no gold-only defaults left
    armed.db.x("UPDATE bots SET params_json=? WHERE bot_id='S03L'", (json.dumps({"metals": "gold"}),))
    assert armed.strategy("S03L").p["metals"] == "both"                   # a saved metal filter is overridden
    armed.cfg["trading"]["all_metals"] = False
    assert armed.strategy("S03L").p["metals"] == "gold"                   # switch off: the bot's own setting applies
