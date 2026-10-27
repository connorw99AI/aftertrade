"""The Lab card's thought line and readiness come only from real checks, statuses and trades."""
import pytest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from metals_lab.api.app import State, create_app
from metals_lab.api.thoughts import readiness, thought
from metals_lab.store import Store

from conftest import approve, make_open_proposal

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
WINDOW = {"intervals": [[720, 1200]], "gold": {"intervals": [[720, 1200]]}}  # 12:00-20:00 UK (NOW is 16:00 BST)


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
    assert say(th=th, now=NOW.replace(hour=8)) == "Off duty. My window opens at 12:00 UK time."
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
    strat = armed.strategy("S01L")
    p = make_open_proposal(armed, bot="S01L", volume=0.04)
    sig = p.signal
    loss = lambda spec, side, entry, stop, vol: abs(entry - stop) * spec.contract_size * vol
    far = sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - 40.0})   # 0.04 lot x $40 = $160 at the stop
    tp = strat.calculate_target_positions([far], 500.0, armed.specs, 0.03, loss, lambda s: 0.0, min_lot=0.04, max_risk_pct=0.10)[0]
    assert tp.skipped and tp.skip_reason.startswith("MIN_LOT_RISK_ABOVE_CAP")
    near = sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - 5.0})   # $20 = 4%: allowed at the floor
    tp = strat.calculate_target_positions([near], 500.0, armed.specs, 0.03, loss, lambda s: 0.0, min_lot=0.04, max_risk_pct=0.10)[0]
    assert not tp.skipped and tp.volume == 0.04


def test_a_strategy_can_have_its_own_trade_cap(armed, cfg):
    from metals_lab.backtest.models import RiskParams
    from metals_lab.risk import trade_cap_for
    assert {trade_cap_for(cfg, b) for b in ("S01L", "S07L", "S10S", "S12L")} == {0.05}      # one cap for every bot by default
    cfg["risk"]["max_trade_risk_by_strategy"] = {"S07": 0.10, "S08": 0.10, "S09": 0.10, "S10": 0.10}
    assert [trade_cap_for(cfg, b) for b in ("S01L", "S06S", "S07L", "S08S", "S09L", "S10S", "S11L")] == [0.05, 0.05, 0.10, 0.10, 0.10, 0.10, 0.05]
    assert RiskParams.from_live(cfg, "S09S").max_trade_risk == 0.10 and RiskParams.from_live(cfg, "S01L").max_trade_risk == 0.05
    sig = make_open_proposal(armed, bot="S07L", volume=0.01).signal
    loss = lambda spec, side, entry, stop, vol: abs(entry - stop) * spec.contract_size * vol
    size = lambda bot, dist: armed.strategy(bot).calculate_target_positions(
        [sig.__class__(**{**sig.__dict__, "stop_price": sig.ref_price - dist})], 227.27, armed.specs, 0.02, loss, lambda s: 0.0,
        min_lot=0.01, max_risk_pct=trade_cap_for(cfg, bot))[0]
    assert size("S01L", 16.0).skipped                                   # 0.01 lot x $16 = 7% of the bot: above 5%
    assert not size("S07L", 16.0).skipped and size("S07L", 16.0).volume == 0.01   # within the wide-stop 10%
    assert size("S07L", 27.0).skipped                                   # 12%: above even the wide-stop cap


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
                 "  daily_loss_pause: 0.50\n  daily_profit_stop: false\n  max_drawdown_pause: 0.50\n"
                 "trading:\n  all_metals: true\n  all_hours: true\n\nnews:\n  enabled: false\n")
    assert apply(p) == ["2026-10-25-risk", "2026-10-25-silver", "2026-10-25-silver-006", "2026-10-25-windows", "2026-10-25-22-bots",
                       "2026-10-26-small-trades", "2026-10-26-wide-stop-cap", "2026-10-27-24-bots", "2026-10-27-one-trade-cap"]
    assert "trading" not in yaml.safe_load(p.read_text()) and yaml.safe_load(p.read_text())["news"] == {"enabled": False}
    r = yaml.safe_load(p.read_text())["risk"]
    assert r["min_lot"] == {"GOLD": 0.01, "SILVER": 0.01} and r["big_lot"] == 0 and r["max_trade_risk"] == 0.05
    assert r["max_trade_risk_by_strategy"] == {} and yaml.safe_load(p.read_text())["capital"]["bot_fraction"] == 0.041666
    assert r["daily_loss_pause"] == 0.30 and r["daily_profit_stop"] == 0 and r["risk_per_trade"] == 0.02
    assert r["max_drawdown_pause"] == 0.50 and "# keep me" in p.read_text()
    assert (tmp_path / "config.yaml.bak-2026-10-25-risk").exists()
    assert apply(p) == []   # applied once only
    assert apply(tmp_path / "missing.yaml") == []


def test_every_bot_starts_on_both_metals_all_day_and_keeps_its_own_settings(armed):
    import json
    from metals_lab.strategies import BOT_IDS, build
    for b in BOT_IDS:
        p = build(b).p
        assert p["trade_gold"] == p["trade_silver"] == "on" and p["gold_windows"] == p["silver_windows"] == "00:00-24:00"
        assert p.get("use_session", "off") == ("on" if b.startswith("S02") else "off")   # S02 keeps to Asian hours
    saved = {"trade_gold": "off", "silver_windows": "06:00-11:00, 13:00-17:00"}
    armed.db.x("UPDATE bots SET params_json=? WHERE bot_id='S03L'", (json.dumps(saved),))
    s = armed.strategy("S03L")                                             # no switch overrides a bot's own settings now
    assert s.p["trade_gold"] == "off" and s.p["silver_windows"] == "06:00-11:00, 13:00-17:00"


def test_thought_names_the_metal_that_is_off_duty():
    th = {"timeframe": "M5", "roles": {"GOLD": view([("Inside the gold trading window (UK 18:00-20:00)", False, "now 16:00 UK")],
                                                    [("Bullish candle", True)], [])}}
    w = {"intervals": [[720, 1200]], "gold": {"intervals": [[1080, 1200]]}}
    assert say(th=th, window=w) == ("Off duty on gold until 18:00 UK time. "
                                    "Failing check: inside the gold trading window (UK 18:00-20:00).")
    assert say(th=th, metals="none") == "Gold and silver are both switched off in my settings."


def test_guard_entry_blocks():
    from datetime import timedelta
    from metals_lab.risk.guard import entry_blocks, loss_streak, week_start
    r = {"max_consecutive_losses": 4, "cooldown_minutes_after_loss": 30, "max_lots_per_trade": {"GOLD": 0.5}, "max_lots_per_bot": 1.0,
         "daily_loss_pause": 0.30, "daily_headroom_check": True}
    ok = dict(risk=r, now=NOW, role="GOLD", volume=0.1, bot_open_lots=0.0, streak=0, last_loss_at=None, week_stopped=None,
              day_loss_pct=0.0, planned_risk_pct=0.1)
    assert entry_blocks(**ok) == []
    assert entry_blocks(**{**ok, "streak": 4}) == ["LOSS_STREAK_LIMIT"]
    assert entry_blocks(**{**ok, "streak": 1, "last_loss_at": NOW - timedelta(minutes=10)}) == ["LOSS_COOLDOWN"]
    assert entry_blocks(**{**ok, "streak": 1, "last_loss_at": NOW - timedelta(minutes=40)}) == []
    assert entry_blocks(**{**ok, "volume": 0.6}) == ["LOTS_ABOVE_TRADE_CAP"]
    assert entry_blocks(**{**ok, "bot_open_lots": 0.95}) == ["LOTS_ABOVE_BOT_CAP"]
    assert entry_blocks(**{**ok, "day_loss_pct": 0.25}) == ["DAILY_HEADROOM"]
    assert entry_blocks(**{**ok, "week_stopped": "WEEKLY_LOSS_LIMIT"}) == ["WEEKLY_LOSS_LIMIT"]
    assert loss_streak([-1, -2, 3, -4]) == 2 and week_start(NOW) == "2026-10-19"


def test_live_losing_streak_pauses_the_bot_until_start(armed):
    for i in range(4):
        armed.db.x("INSERT INTO trades(trade_id,bot_id,exit_time,net,provisional) VALUES(?,?,?,?,1)",
                   (f"t{i}", "S01L", f"2099-01-0{i + 1}T00:00:00+00:00", -5.0))
    assert armed.guard_state("S01L")["streak"] == 4
    armed.cycle()
    row = armed.db.one("SELECT status, pause_reason FROM bots WHERE bot_id='S01L'")
    assert row["status"] == "PAUSED_RISK" and "4 losses in a row" in row["pause_reason"]
    armed.set_bot_status("S01L", "RUNNING", operator="pytest")
    armed.db.set_control("streak_reset:S01L", "2099-12-31T00:00:00+00:00")  # Start clears the streak (the trades above are future-dated)
    assert armed.guard_state("S01L")["streak"] == 0


def test_next_up_lists_bots_one_condition_from_an_entry():
    from metals_lab.api.thoughts import next_up
    th = {"bar_time": "x", "roles": {
        "GOLD": view([("Spread ok", True)], [("Broke the low", True), ("Bullish candle", False)], []),
        "SILVER": view([("Spread ok", True)], [("Broke the low", False), ("Bullish candle", False)], [])}}
    base = dict(side="L", status="RUNNING", kill=False, stopped_for_day=None, opens=[], th=th, metals="both", window=WINDOW, now=NOW)
    n = next_up(**base)
    assert n["metal"] == "gold" and n["missing"] == ["Bullish candle"] and n["met"] == 1 and not n["signal"]
    sig = {**th, "roles": {**th["roles"], "SILVER": view([("Spread ok", True)], [("Broke the low", False)], [], verdict="ENTRY_LONG")}}
    assert next_up(**{**base, "th": sig})["metal"] == "silver"                       # an entry signal comes first
    assert next_up(**{**base, "status": "PAUSED"}) is None
    assert next_up(**{**base, "opens": [{"role": "GOLD"}]}) is None                   # already in a trade
    assert next_up(**{**base, "now": NOW.replace(hour=21)}) is None                   # outside its window
    blocked = {**th, "roles": {"GOLD": view([("Spread ok", False)], [("Broke the low", True), ("Bullish candle", False)], [])}}
    assert next_up(**{**base, "th": blocked}) is None                                  # a filter fails


def test_launcher_resplits_a_running_lab_for_24_bots_keeping_every_trade(tmp_path):
    from metals_lab.cli import RESPLIT_MARK, pending_resplit
    from metals_lab.ledger import Ledger
    from metals_lab.store import Store
    from metals_lab.strategies import BOT_IDS
    cfg_p, db_p = tmp_path / "config.yaml", tmp_path / "metals_lab.db"
    cfg_p.write_text("capital:\n  bot_fraction: 0.041666\n")
    old22 = [b for b in BOT_IDS if not b.startswith("S12")]
    st = Store(db_p)
    led = Ledger(st)
    led.initialize("f" * 64, 5000.0, "GBP", 0.045454, old22)             # a running lab: 22 bots at 227.27
    led.post_deal("S03L", {"ticket": 1, "profit": 12.0, "commission": 0.0, "swap": 0.0, "fee": 0.0, "symbol": "XAUUSD"})
    led.post_deal("S08S", {"ticket": 2, "profit": -30.0, "commission": -1.0, "swap": 0.0, "fee": 0.0, "symbol": "XAGUSD"})
    st.x("INSERT INTO positions(position_key,bot_id,status) VALUES('open1','S01L','OPEN')")   # an open trade does not block it
    st.conn.close()
    assert "starting share is now 208.33" in pending_resplit(cfg_p, db_p)
    st = Store(db_p)
    led = Ledger(st)
    assert led.book("S03L").equity == pytest.approx(220.33) and led.book("S08S").equity == pytest.approx(177.33)
    assert led.book("S12L").equity == pytest.approx(208.33) and led.book("S12S").equity == pytest.approx(208.33)
    assert sum(led.allocation(b) for b in BOT_IDS) == pytest.approx(5000.0, abs=0.2)
    assert st.one("SELECT COUNT(*) n FROM ledger WHERE kind='DEAL'")["n"] >= 2 or st.one("SELECT COUNT(*) n FROM ledger")["n"] > 24
    assert st.one("SELECT COUNT(*) n FROM config_audit WHERE action='ALLOCATION_RESPLIT'")["n"] == 24
    st.conn.close()
    assert RESPLIT_MARK in cfg_p.read_text() and pending_resplit(cfg_p, db_p) == ""   # once only


def test_launcher_starts_a_new_lab_when_the_demo_account_changes(tmp_path, cfg):
    from metals_lab.cli import new_lab_if_account_changed
    from metals_lab.ledger import Ledger
    from metals_lab.mt5.gateway import TradingGateway
    from metals_lab.mt5.guard import check_account
    from metals_lab.mt5.sim import SimAccount, SimTerminal
    from metals_lab.store import Store
    from metals_lab.strategies import BOT_IDS
    t = SimTerminal()
    gw = TradingGateway(t, is_simulated=True)
    db_p = tmp_path / "metals_lab.db"
    st = Store(db_p)
    Ledger(st).initialize(check_account(gw.read_only()).fingerprint, 10000.0, "GBP", 0.041666, BOT_IDS)
    st.conn.close()
    assert new_lab_if_account_changed(cfg, db_p, gw) == "" and db_p.exists()          # same account: the lab carries on
    t.add_account(SimAccount(login=7007, server="Sim-Live", trade_mode=2))
    t.switch_account(7007)
    assert new_lab_if_account_changed(cfg, db_p, gw) == "" and db_p.exists()          # a live account never touches it
    t.add_account(SimAccount(login=8008, server="Sim-Demo"))
    t.switch_account(8008)
    msg = new_lab_if_account_changed(cfg, db_p, gw)
    assert "New MT5 demo account" in msg and "24 bots" in msg and not db_p.exists()
    assert list((tmp_path / "archive").glob("metals_lab-*.db"))                       # archived, not deleted
