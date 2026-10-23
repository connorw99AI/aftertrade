"""Live pipeline against the SimTerminal: gates, bypass attempts, ownership, recovery."""
import copy
import dataclasses
import json
from datetime import timedelta

import pytest

from metals_lab.auditor.news import CsvCalendarProvider, FailingProvider
from metals_lab.domain import Decision, Intent
from metals_lab.execution.approvals import Approval
from metals_lab.execution.coordinator import BOT_IDS, Coordinator
from metals_lab.mt5 import constants as C
from metals_lab.mt5.gateway import ReadOnlyGateway, TradingGateway
from metals_lab.mt5.sim import SimAccount
from metals_lab.store import Store
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
    book = c.bot_book("S01")

    class R:
        clean = True
    rd = c.risk.evaluate(p, None, c._risk_ctx("S01", acct, book, book.equity, book.equity, R(), c.specs["GOLD"], c._tick(c.specs["GOLD"])))
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
        p2 = make_open_proposal(c, bot="S03", role="SILVER", volume=0.01, stop_dist=0.2, tp_dist=0.3)
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
    assert len(ev) == 1 and ev[0]["bot_id"] == "S01"
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
    assert len(ev) == 1 and ev[0]["bot_id"] == "S01"
    assert all(c.ledger.book(b).equity == c.ledger.allocation(b) for b in BOT_IDS if b != "S01")


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
    c.set_bot_status("S01", "PAUSED")
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
    c.set_bot_status("S01", "RUNNING")
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
    assert pos and pos["bot_id"] == "S01" and pos["status"] == "OPEN"
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
    big = make_open_proposal(c, volume=5.0, stop_dist=50.0)  # planned risk >> 0.5% of 10k
    _, r = approve(c, big)
    assert "PLANNED_RISK_EXCEEDS_LIMIT" in r.reasons
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
