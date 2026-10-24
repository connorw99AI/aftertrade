"""Live pipeline against the SimTerminal: gates, bypass attempts, ownership, recovery."""
import copy
import dataclasses
import json
from datetime import timedelta

import pytest

from metals_lab.mt5.guard import check_account

from metals_lab.auditor.news import CsvCalendarProvider, FailingProvider
from metals_lab.domain import Decision, Intent
from metals_lab.execution.approvals import Approval
from metals_lab.execution.coordinator import BOT_IDS, Coordinator
from metals_lab.mt5 import constants as C
from metals_lab.mt5.gateway import ReadOnlyGateway, TradingGateway
from metals_lab.mt5.sim import SimAccount
from metals_lab.store import Store, now_iso
from metals_lab.timeutil import utc_now

from conftest import approve, make_open_proposal


def open_position(c, **kw):
    p = make_open_proposal(c, **kw)
    audit, risk = approve(c, p)
    assert audit.decision == Decision.APPROVED, audit.reasons
    assert risk.decision == Decision.APPROVED, risk.reasons
    r = c.executor.submit(p, c.specs[p.role], audit.approval, risk.approval)
    assert r.ok, r.message
    return p, r


def test_auditor_has_no_order_capability(coord):
    assert not hasattr(coord.auditor, "gw")
    ro = coord.ro
    assert isinstance(ro, ReadOnlyGateway) and not isinstance(ro, TradingGateway)
    assert not hasattr(ro, "order_send") and not hasattr(ro, "order_check")


def test_executor_requires_both_approvals(armed):
    c = armed
    p = make_open_proposal(c)
    audit, risk = approve(c, p)
    spec = c.specs["GOLD"]
    assert not c.executor.submit(p, spec, None, risk.approval).ok            # skip auditor
    assert not c.executor.submit(p, spec, audit.approval, None).ok           # skip risk
    forged = dataclasses.replace(audit.approval, signature="0" * 64)
    assert "signature" in c.executor.submit(p, spec, forged, risk.approval).message
    p2 = make_open_proposal(c, volume=0.2)                                     # approval bound to another proposal
    assert "different proposal" in c.executor.submit(p2, spec, audit.approval, risk.approval).message
    assert c.executor.submit(p, spec, audit.approval, risk.approval).ok
    p3 = dataclasses.replace(p)                                                # replay of used approvals
    assert not c.executor.submit(p3, spec, audit.approval, risk.approval).ok
    assert len([r for r in c.gw._b.sent_requests if r.get("action") == C.TRADE_ACTION_DEAL]) == 1


def test_risk_engine_rejects_without_audit(armed):
    c = armed
    p = make_open_proposal(c)
    from metals_lab.mt5.guard import check_account
    acct = check_account(c.ro)
    book = c.bot_book("S01L")

    class R:
        clean = True
    rd = c.risk.evaluate(p, None, c._risk_ctx("S01L", acct, book, book.equity, book.equity, R(), c.specs["GOLD"], c._tick(c.specs["GOLD"])))
    assert rd.decision == Decision.REJECTED and "AUDIT_GATE" in rd.reasons[0]


def test_expired_approval_rejected(armed):
    c = armed
    p = make_open_proposal(c)
    audit, risk = approve(c, p)
    c.gw._b.advance(c.cfg["execution"]["approval_ttl_seconds"] + 5)
    assert "expired" in c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval).message


def test_not_armed_never_sends(coord):
    p = make_open_proposal(coord)
    audit, risk = approve(coord, p)
    r = coord.executor.submit(p, coord.specs["GOLD"], audit.approval, risk.approval)
    assert not r.ok and "NOT_ARMED" in r.message
    assert coord.gw._b.sent_requests == []


def test_account_switch_invalidates_activation(armed):
    c = armed
    p = make_open_proposal(c)
    audit, risk = approve(c, p)
    t = c.gw._b
    t.add_account(SimAccount(login=2002, server="Sim-Demo2", balance=5000))
    t.switch_account(2002)
    r = c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval)
    assert not r.ok and "ACCOUNT_CHANGED" in r.message
    c.cycle()
    assert not c.is_armed()
    assert c.db.one("SELECT COUNT(*) n FROM approvals WHERE revoked=0 AND used_at IS NULL")["n"] == 0
    assert c.db.one("SELECT 1 FROM alerts WHERE code='ACCOUNT_SWITCHED' AND resolved_at IS NULL")


def test_live_account_detected_in_cycle_disarms(armed):
    c = armed
    c.gw._b.accounts[c.gw._b.current_login].trade_mode = C.ACCOUNT_TRADE_MODE_REAL
    c.cycle()
    assert not c.is_armed()
    p = make_open_proposal(c)
    assert not c.executor.submit(p, c.specs["GOLD"], None, None).ok


def test_duplicate_prevention(armed):
    c = armed
    p, r = open_position(c)
    audit, risk = approve(c, p)
    r2 = c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval)
    assert not r2.ok and r2.state == "DUPLICATE"


def test_server_side_stop_attached_and_unprotected_closed(armed, sim):
    c = armed
    p, r = open_position(c)
    pos = [x for x in c.ro.positions() if x["ticket"] == r.position_ticket][0]
    assert pos["sl"] == pytest.approx(c.specs["GOLD"].round_price(p.sl))
    t = c.gw._b
    t.drop_sl_on_fill, t.fail_sltp = True, True
    for _ in range(50):  # step past any auditor delay caused by the replayed market (not under test here)
        p2 = make_open_proposal(c, bot="S03L", role="SILVER", volume=0.01, stop_dist=0.2, tp_dist=0.3)
        audit, risk = approve(c, p2)
        if risk is not None:
            break
        sim[1].step()
    assert risk is not None, audit.reasons
    r2 = c.executor.submit(p2, c.specs["SILVER"], audit.approval, risk.approval)
    assert r2.state == "UNPROTECTED_CLOSED"
    assert not [x for x in c.ro.positions() if x["ticket"] == r2.position_ticket]


def test_partial_fill_and_partial_close_no_popup_until_full_close(armed):
    c = armed
    t = c.gw._b
    t.partial_next = [0.5]
    p, r = open_position(c, volume=0.1)
    assert r.state == "PARTIAL" and r.filled_volume == pytest.approx(0.05)
    c.cycle()
    # manual partial close of half through the ledgered magic would be a manual trade; use the executor path
    t._execute({"symbol": "XAUUSD", "type": C.ORDER_TYPE_SELL, "volume": 0.02, "position": r.position_ticket,
                "magic": p.magic, "comment": "partial"})
    c.reconciler.run(utc_now())
    assert not c.db.q("SELECT * FROM events WHERE kind='TRADE_CLOSED'")
    row = c.db.one("SELECT * FROM positions WHERE ticket=?", (r.position_ticket,))
    assert row["status"] == "OPEN" and row["volume_open"] == pytest.approx(0.03)
    t._execute({"symbol": "XAUUSD", "type": C.ORDER_TYPE_SELL, "volume": 0.03, "position": r.position_ticket,
                "magic": p.magic, "comment": "final"})
    c.reconciler.run(utc_now())
    ev = c.db.q("SELECT * FROM events WHERE kind='TRADE_CLOSED'")
    assert len(ev) == 1 and ev[0]["bot_id"] == "S01L"
    pay = json.loads(ev[0]["payload_json"])
    deals = c.db.q("SELECT * FROM deals WHERE position_id=?", (r.position_ticket,))
    assert pay["net"] == pytest.approx(round(sum(d["profit"] + d["commission"] + d["swap"] + d["fee"] for d in deals), 2))
    assert pay["commission"] < 0 and pay["provisional"] is True


def test_popup_only_from_owning_bot_and_not_unrealized(armed):
    c = armed
    p, r = open_position(c)
    t = c.gw._b
    t.set_quote("XAUUSD", t.symbols["XAUUSD"]["bid"] + 3)  # unrealized gain
    c.reconciler.run(utc_now())
    assert not c.db.q("SELECT * FROM events WHERE kind='TRADE_CLOSED'")
    t.set_quote("XAUUSD", p.sl - 1)  # server stop executes
    c.reconciler.run(utc_now())
    ev = c.db.q("SELECT * FROM events WHERE kind='TRADE_CLOSED'")
    assert len(ev) == 1 and ev[0]["bot_id"] == "S01L"
    assert all(c.ledger.book(b).equity == c.ledger.allocation(b) for b in BOT_IDS if b != "S01L")


def test_manual_trade_and_deposit_block_entries(armed):
    c = armed
    t = c.gw._b
    t.open_manual_position("XAUUSD", C.ORDER_TYPE_BUY, 0.01)
    t.deposit(5000)
    rep = c.reconciler.run(utc_now())
    assert not rep.clean
    codes = {a["code"].split("_")[0] + "_" + a["code"].split("_")[1] for a in c.db.blocking_alerts()}
    assert {"UNKNOWN_DEAL", "UNKNOWN_POSITION", "BALANCE_OP"} <= codes
    p = make_open_proposal(c)
    audit, risk = approve(c, p)
    assert risk.decision == Decision.REJECTED and any("BLOCKING_ALERTS" in x for x in risk.reasons)
    # deposit is visible but not distributed
    assert all(c.ledger.allocation(b) == 10000.0 for b in BOT_IDS)


def test_close_still_possible_while_paused_and_killed(armed):
    c = armed
    p, r = open_position(c)
    c.set_bot_status("S01L", "PAUSED")
    c.db.set_control("global_pause", "1")
    out = c.kill("test")
    assert "EXEC_FILLED" in out["closed_attempts"][0]["outcome"]
    assert not c.ro.positions()
    assert not c.is_armed()
    p2 = make_open_proposal(c)
    a2, r2 = approve(c, p2)
    assert r2.decision == Decision.REJECTED and "KILL_SWITCH_ACTIVE" in r2.reasons


def test_timeout_then_restart_recovery(cfg, sim):
    t, _ = sim
    store = Store(":memory:")
    c = Coordinator(cfg, TradingGateway(t, is_simulated=True), store)
    c.connect()
    c.arm("pytest")
    c.set_bot_status("S01L", "RUNNING")
    t.timeout_next = 1  # server executes, reply lost
    p = make_open_proposal(c)
    audit, risk = approve(c, p)
    r = c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval)
    assert r.state == "UNKNOWN"
    # entries for this bot/symbol are blocked while unresolved
    p_again = make_open_proposal(c)
    a2, r2 = approve(c, p_again)
    assert r2.decision == Decision.REJECTED and "ORDER_IN_FLIGHT_FOR_SYMBOL" in r2.reasons
    # "restart": new coordinator on the same database recovers ownership from the terminal records
    c2 = Coordinator(cfg, TradingGateway(t, is_simulated=True), store)
    c2.connect()
    rep = c2.reconciler.run(utc_now())
    it = store.one("SELECT * FROM intents WHERE intent_id=?", (r.intent_id,))
    assert it["state"] == "FILLED"
    pos = store.one("SELECT * FROM positions WHERE intent_id=?", (r.intent_id,))
    assert pos and pos["bot_id"] == "S01L" and pos["status"] == "OPEN"
    assert not any(i.startswith("UNKNOWN_POSITION") for i in rep.issues)


def test_disconnect_reconnect_reverifies(armed):
    c = armed
    t = c.gw._b
    t.connected = False
    s = c.cycle()
    assert s.get("error") == "disconnected"
    assert c.db.one("SELECT 1 FROM alerts WHERE code='DISCONNECTED' AND resolved_at IS NULL")
    t.connected = True
    c.cycle()
    assert c.db.one("SELECT 1 FROM alerts WHERE code='DISCONNECTED' AND resolved_at IS NULL") is None
    assert c.is_armed()


def test_news_outage_requires_manual_review(armed):
    c = armed
    c.cfg["news"]["enabled"] = True
    c.cfg["news"]["unknown_policy"] = "manual_review"
    c.auditor.news = FailingProvider()
    p = make_open_proposal(c)
    audit, _ = approve(c, p)
    assert audit.decision == Decision.MANUAL_REVIEW_REQUIRED and audit.news_status == "UNKNOWN"
    assert "absence of data is not absence of news" in " ".join(audit.reasons)
    # exits are not blocked by missing news
    close = make_open_proposal(c, intent=Intent.OPEN_LONG)
    closing = dataclasses.replace(close, intent=Intent.CLOSE_LONG)
    a2, _ = approve(c, closing, owned={"volume": 1})
    assert a2.decision == Decision.APPROVED


def test_news_blackout_delays_with_reassessment(armed, tmp_path):
    c = armed
    c.cfg["news"]["enabled"] = True
    now = utc_now()
    f = tmp_path / "cal.csv"
    f.write_text(f"# as_of: {now.isoformat()}\nevent_time_utc,currency,title,impact,known_since_utc\n"
                 f"{(now + timedelta(minutes=10)).isoformat()},USD,CPI,HIGH,\n")
    c.auditor.news = CsvCalendarProvider(f)
    p = make_open_proposal(c)
    audit, _ = approve(c, p)
    assert audit.decision == Decision.DELAYED and "R13_NEWS_BLACKOUT" in audit.triggered
    assert audit.reassess_at > now + timedelta(minutes=39)
    stale = tmp_path / "stale.csv"
    stale.write_text("# as_of: 2020-01-01T00:00:00Z\nevent_time_utc,currency,title,impact,known_since_utc\n")
    assert CsvCalendarProvider(stale).events(now, now, now).status == "UNKNOWN"


def test_stale_quote_and_spread_blowout_delay_entries(armed):
    c = armed
    t = c.gw._b
    bid = t.symbols["XAUUSD"]["bid"]
    t.set_quote("XAUUSD", bid, bid + 3.0)  # 300 points spread
    audit, _ = approve(c, make_open_proposal(c))
    assert audit.decision == Decision.DELAYED and "R04_SPREAD_EXPANSION" in audit.triggered
    t.set_quote("XAUUSD", bid, stale_seconds=600)
    audit2, _ = approve(c, make_open_proposal(c))
    assert "R03_STALE_QUOTE" in audit2.triggered


def test_risk_limits_and_stop_validation(armed):
    c = armed
    c.cfg["risk"]["pre_trade_limits"] = True
    c.cfg["risk"]["per_trade_limit_enabled"] = True
    big = make_open_proposal(c, volume=5.0, stop_dist=50.0)  # planned risk >> 0.5% of 10k
    _, r = approve(c, big)
    assert "PLANNED_RISK_EXCEEDS_LIMIT" in r.reasons
    c.cfg["risk"]["per_trade_limit_enabled"] = False  # operator policy: no per-trade cap; the planned risk is still recorded
    _, r0 = approve(c, make_open_proposal(c, volume=0.5, stop_dist=10.0))
    assert "PLANNED_RISK_EXCEEDS_LIMIT" not in r0.reasons and r0.metrics["planned_risk"] > r0.metrics["risk_limit"]
    tight = make_open_proposal(c, stop_dist=0.05)             # inside broker stops level
    _, r2 = approve(c, tight)
    assert "STOP_INSIDE_BROKER_STOPS_LEVEL" in r2.reasons
    off = make_open_proposal(c, volume=0.123)
    _, r3 = approve(c, off)
    assert any(x.startswith("VOLUME_NOT_ON_STEP") for x in r3.reasons)


def test_shadow_mode_records_decisions_without_orders(coord, sim):
    _, replay = sim
    for b in BOT_IDS:
        coord.set_bot_status(b, "RUNNING")
    for _ in range(400):
        replay.step()
        coord.cycle()
    assert coord.gw._b.sent_requests == []
    modes = {r["mode"] for r in coord.db.q("SELECT mode FROM signals_log")}
    assert modes and modes <= {"SHADOW"}


def test_shadow_mode_sizes_on_would_be_allocation(coord):
    b = coord.bot_book("S08L")
    assert b.shadow and b.allocation == 10000.0  # 5% of the 200,000 simulated balance, not 0
    coord.arm("pytest")
    real = coord.bot_book("S08L")
    assert not real.shadow and real.allocation == 10000.0


def test_per_bot_risk_override_and_cap(armed):
    from metals_lab.risk import risk_pct_for
    c = armed
    c.cfg["risk"]["risk_per_trade"] = 0.005
    assert risk_pct_for(c.cfg, "S01L") == 0.005
    c.cfg["strategies"]["S01L"] = {"risk_per_trade": 0.02}
    assert risk_pct_for(c.cfg, "S01L") == 0.02 and risk_pct_for(c.cfg, "S02L") == 0.005
    c.cfg["strategies"]["S01L"] = {"risk_per_trade": 0.5}
    assert risk_pct_for(c.cfg, "S01L") == 0.03  # hard cap
    # a trade risking ~1.5% of the bot is rejected at 0.5% but approved with a 2% override
    c.cfg["risk"]["pre_trade_limits"] = True
    c.cfg["risk"]["per_trade_limit_enabled"] = True
    c.cfg["strategies"]["S01L"] = {}
    p = make_open_proposal(c, volume=0.5, stop_dist=3.0)
    _, r = approve(c, p)
    assert r is not None and "PLANNED_RISK_EXCEEDS_LIMIT" in r.reasons
    c.cfg["strategies"]["S01L"] = {"risk_per_trade": 0.02}
    p2 = make_open_proposal(c, volume=0.5, stop_dist=3.0)
    _, r2 = approve(c, p2)
    assert r2 is not None and "PLANNED_RISK_EXCEEDS_LIMIT" not in r2.reasons


def test_daily_entry_cap(armed):
    from metals_lab.domain import Signal
    c = armed
    assert c.entries_today("S01L") == 0
    for i in range(5):  # five shadow-approved opens today (a two-leg group would count once)
        sig = Signal("S01", utc_now() - timedelta(seconds=i), "GOLD", "XAUUSD", Intent.OPEN_LONG, f"t{i}", {})
        c.db.x("INSERT INTO signals_log(ts,bot_id,mode,outcome,signal_json,explanation) VALUES(?,?,?,?,?,?)",
               (now_iso(), "S01L", "SHADOW", "SHADOW_APPROVED", json.dumps(sig.to_dict(), default=str), ""))
    assert c.entries_today("S01L") == 5 and c.entries_today("S02L") == 0
    _, r = approve(c, make_open_proposal(c))
    assert "MAX_ENTRIES_PER_DAY" in r.reasons
    _, r2 = approve(c, make_open_proposal(c, bot="S02L"))
    assert "MAX_ENTRIES_PER_DAY" not in r2.reasons


def test_exposure_caps_are_per_bot_and_in_account_currency(armed):
    c = armed
    c.cfg["risk"]["pre_trade_limits"] = True
    c.cfg["risk"]["per_trade_limit_enabled"] = False
    p = make_open_proposal(c, volume=1.0, stop_dist=3.0)
    _, r = approve(c, p)
    spec = c.specs["GOLD"]
    exp = c.risk.notional(spec, 1.0, r.metrics["entry_ref"]) / r.metrics["bot_equity"]
    assert r.metrics["exposure_scope"] == "per_bot" and abs(r.metrics["gross_exposure_after"] - round(exp, 3)) < 1e-6
    c.cfg["risk"]["max_metals_gross_exposure"] = exp * 0.9
    _, r2 = approve(c, make_open_proposal(c, volume=1.0, stop_dist=3.0))
    assert "GROSS_METALS_EXPOSURE" in r2.reasons
    c.cfg["risk"]["exposure_scope"] = "account"  # the same trade is small against the whole account
    _, r3 = approve(c, make_open_proposal(c, volume=1.0, stop_dist=3.0))
    assert "GROSS_METALS_EXPOSURE" not in r3.reasons


def test_daily_profit_stop(armed):
    c = armed
    up = c.bot_book("S01L").equity / 1.35  # bot is up 35% on the day
    _, r = approve(c, make_open_proposal(c), day_start=up)
    assert "DAILY_PROFIT_TARGET_REACHED" in r.reasons
    _, r1 = approve(c, make_open_proposal(c), day_start=c.bot_book("S01L").equity / 1.10)  # up 10%: still trading
    assert "DAILY_PROFIT_TARGET_REACHED" not in r1.reasons
    c.cfg["risk"]["daily_profit_stop"] = 0
    _, r2 = approve(c, make_open_proposal(c), day_start=up)
    assert "DAILY_PROFIT_TARGET_REACHED" not in r2.reasons


def test_no_total_loss_pause_when_disabled(armed):
    c = armed
    assert c.cfg["risk"]["max_drawdown_pause"] == 0.50  # operator default
    c.cfg["risk"]["max_drawdown_pause"] = 0
    _, r = approve(c, make_open_proposal(c))
    assert "MAX_DRAWDOWN_LIMIT" not in r.reasons


def test_default_min_lot_is_per_metal(cfg):
    assert cfg["risk"]["min_lot"] == {"GOLD": 0.04, "SILVER": 0.02}


def test_news_off_by_default(armed):
    c = armed
    c.auditor.news = FailingProvider()
    audit, _ = approve(c, make_open_proposal(c))
    assert audit.news_status == "OFF" and not any(t.startswith("R12") or t.startswith("R13") for t in audit.triggered)


def _setup_audit(c, p):
    from metals_lab.execution.coordinator import LiveMarketView
    from metals_lab.mt5.guard import check_account
    view = LiveMarketView(c, {}, utc_now())
    return c.auditor.assess(view, p.role, True, p.proposal_hash, check_account(c.ro).fingerprint, proposal=p)


def test_auditor_checks_trade_setup(armed):
    c = armed
    good = _setup_audit(c, make_open_proposal(c, volume=0.04))
    assert good.decision == Decision.APPROVED and good.evidence["setup"]["stop_distance"] > 0
    assert "R19_NO_STOP" in _setup_audit(c, dataclasses.replace(make_open_proposal(c, volume=0.04), sl=None)).triggered
    tk = c.ro.symbol_info_tick("XAUUSD")
    wrong = dataclasses.replace(make_open_proposal(c, volume=0.04), sl=tk["ask"] + 5.0)
    assert "R20_STOP_WRONG_SIDE" in _setup_audit(c, wrong).triggered
    bad_tp = dataclasses.replace(make_open_proposal(c, volume=0.04), tp=tk["ask"] - 5.0)
    assert "R23_TARGET_WRONG_SIDE" in _setup_audit(c, bad_tp).triggered
    assert "R18_BELOW_MIN_LOT" in _setup_audit(c, make_open_proposal(c, volume=0.01)).triggered
    assert "R17_VOLUME_INVALID" in _setup_audit(c, make_open_proposal(c, volume=0.043)).triggered
    far = dataclasses.replace(make_open_proposal(c, volume=0.04), sl=tk["ask"] - 10000 * c.specs["GOLD"].point * 100)
    assert "R22_STOP_ABNORMAL" in _setup_audit(c, far).triggered


def test_pre_trade_limits_off_lets_large_trades_through(armed):
    c = armed
    _, r = approve(c, make_open_proposal(c, volume=3.0, stop_dist=3.0))
    assert not {"PLANNED_RISK_EXCEEDS_LIMIT", "BOT_MARGIN_FRACTION_LIMIT", "ACCOUNT_MARGIN_LIMIT", "GROSS_METALS_EXPOSURE",
                "CORRELATED_METALS_EXPOSURE"} & set(r.reasons)
    c.cfg["risk"]["pre_trade_limits"] = True
    c.cfg["risk"]["per_trade_limit_enabled"] = True
    _, r2 = approve(c, make_open_proposal(c, volume=0.5, stop_dist=10.0))
    assert "PLANNED_RISK_EXCEEDS_LIMIT" in r2.reasons


def test_missing_stop_and_target_are_restored(armed):
    c = armed
    p = make_open_proposal(c, volume=0.04)
    audit, risk = approve(c, p)
    res = c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval)
    assert res.ok
    pos = c.gw._b._positions[res.position_ticket]
    pos.sl, pos.tp = 0.0, 0.0                          # someone removed them in the terminal
    out = c.executor.verify_protection(c.db.q("SELECT * FROM positions WHERE status='OPEN'"), c.specs)
    assert out and out[0]["restored"]
    assert pos.sl > 0 and pos.tp > 0
    assert c.executor.verify_protection(c.db.q("SELECT * FROM positions WHERE status='OPEN'"), c.specs) == []



def test_retiring_a_half_keeps_its_balance_on_its_own_book(armed):
    c = armed
    out = c.retire("S08L", "assessor says longs lose", "pytest")
    assert out["equity"] == 10000.0
    assert c.bot_book("S08L").equity == c.bot_book("S08S").equity == 10000.0  # halves are independent
    assert c.db.one("SELECT status FROM bots WHERE bot_id='S08L'")["status"] == "RETIRED"
    out2 = c.unretire("S08L", "pytest")
    assert out2["from_other_half"] == out2["from_pool"] == 0.0 and c.bot_book("S08L").equity == 10000.0
    assert c.db.one("SELECT status FROM bots WHERE bot_id='S08L'")["status"] == "STOPPED"


def test_unretire_returns_a_balance_moved_by_an_older_lab(armed):
    c = armed
    c.retire("S07S", "x", "pytest")
    c.ledger.transfer_equity("S07S", "S07L", "x", "pytest")  # what retiring did before the halves were independent
    assert c.bot_book("S07L").equity == 20000.0 and c.bot_book("S07S").equity == 0
    out = c.unretire_all("pytest")
    assert [o["bot_id"] for o in out] == ["S07S"] and out[0]["from_other_half"] == 10000.0
    assert c.bot_book("S07L").equity == c.bot_book("S07S").equity == 10000.0
    assert c.unretire("S07S", "pytest")["from_other_half"] == 0.0  # nothing moved twice


def test_cannot_retire_with_open_trades(armed):
    c = armed
    p = make_open_proposal(c, bot="S09L", volume=0.04)
    audit, risk = approve(c, p)
    assert c.executor.submit(p, c.specs["GOLD"], audit.approval, risk.approval).ok
    with pytest.raises(RuntimeError):
        c.retire("S09L", "x", "pytest")


def test_fresh_lab_starts_with_assessor_retirements(cfg, sim):
    t, _ = sim
    cfg = copy.deepcopy(cfg)
    cfg["capital"]["apply_assessor_retirements"] = True
    c = Coordinator(cfg, TradingGateway(t, is_simulated=True), Store(":memory:"))
    c.connect()
    c.arm("pytest")
    st = {r["bot_id"]: r["status"] for r in c.db.q("SELECT bot_id, status FROM bots")}
    assert st["S03S"] == "RETIRED" and st["S03L"] == "STOPPED" and st["S01L"] == st["S01S"] == "RETIRED"
    assert all(c.bot_book(b).allocation == 10000.0 for b in ("S03L", "S03S", "S01L", "S08L"))  # every half its own 5%


def test_clock_check_ignores_stale_prices_and_flags_a_real_mismatch(armed):
    from datetime import datetime, timezone

    from metals_lab.sessions import ServerClock
    c = armed
    c.clock = ServerClock("Europe/Athens")  # +3h on 23 Oct 2026 (summer time)
    now = datetime(2026, 10, 23, 21, 15, tzinfo=timezone.utc)
    stale = int(now.timestamp()) + 3 * 3600 - 30 * 60  # Friday night: last price 30 minutes old
    c.ro.symbol_info_tick = lambda sym: {"time": stale}
    c._last_tick_epoch.clear()
    c._verify_clock(now)
    c._verify_clock(now)  # tick did not move: market closed, nothing to measure
    assert not c.db.one("SELECT 1 FROM alerts WHERE code='CLOCK_MISMATCH' AND resolved_at IS NULL")
    t = {"v": int(now.timestamp()) + 2 * 3600}  # live prices, but the broker really is on +2h
    c.ro.symbol_info_tick = lambda sym: {"time": t["v"]}
    c._verify_clock(now)
    t["v"] += 10
    c._verify_clock(now.replace(second=10))
    assert c.db.one("SELECT 1 FROM alerts WHERE code='CLOCK_MISMATCH' AND resolved_at IS NULL")
