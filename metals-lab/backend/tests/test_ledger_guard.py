import pytest

from metals_lab.execution.coordinator import BOT_IDS
from metals_lab.ledger import Ledger
from metals_lab.mt5 import constants as C
from metals_lab.mt5.gateway import LiveAccountBlocked, MT5Error, ReadOnlyGateway, TradingGateway
from metals_lab.mt5.guard import AccountState, check_account, execution_blockers, fingerprint
from metals_lab.mt5.sim import SimAccount, SimTerminal
from metals_lab.store import Store

FP = "f" * 64


def test_equal_share_allocation_snapshot_once():
    led = Ledger(Store(":memory:"))
    led.initialize(FP, 22000.0, "USD", 1 / 22, BOT_IDS)
    assert all(led.allocation(b) == 1000.0 for b in BOT_IDS)
    led.initialize(FP, 50000.0, "USD", 1 / 22, BOT_IDS)  # later balance changes never reset allocations
    assert all(led.allocation(b) == 1000.0 for b in BOT_IDS)
    assert led.unallocated_pool(FP) == 0.0


def test_bot_capital_isolation_and_idempotent_deals():
    led = Ledger(Store(":memory:"))
    led.initialize(FP, 22000.0, "USD", 1 / 22, BOT_IDS)
    deal = {"ticket": 77, "profit": -250.0, "commission": -3.5, "swap": -1.0, "fee": 0.0, "symbol": "XAUUSD"}
    led.post_deal("S01L", deal)
    led.post_deal("S01L", deal)  # reconciliation re-run: no double booking
    assert led.book("S01L").equity == pytest.approx(1000 - 254.5)
    for b in BOT_IDS[1:]:
        assert led.book(b).equity == 1000.0  # no automatic transfer, no shared losses


def test_free_capital_excludes_reserved_margin_and_short_proceeds():
    led = Ledger(Store(":memory:"))
    led.initialize(FP, 22000.0, "USD", 1 / 22, BOT_IDS)
    b = led.book("S02L", unrealized=0.0, reserved_margin=400.0, pending_reserved=100.0)
    assert b.free_capital == 500.0  # opening a short adds margin usage, never notional cash


def test_allocation_change_requires_explicit_funded_action():
    st = Store(":memory:")
    led = Ledger(st)
    led.initialize(FP, 22000.0, "USD", 1 / 22, BOT_IDS)
    with pytest.raises(ValueError):
        led.change_allocation(FP, "S01L", 100.0, "more", "op")  # pool is empty
    with pytest.raises(ValueError):
        led.change_allocation(FP, "S01L", -10.0, "", "op")      # reason required
    led.change_allocation(FP, "S02L", -200.0, "reduce", "op")
    led.change_allocation(FP, "S01L", 200.0, "move freed funds", "op")
    assert led.allocation("S01L") == 1200.0 and led.allocation("S02L") == 800.0
    assert len(st.q("SELECT * FROM config_audit WHERE action='ALLOCATION_CHANGE'")) == 2


@pytest.mark.parametrize("mode,state", [(C.ACCOUNT_TRADE_MODE_REAL, AccountState.LIVE),
                                        (C.ACCOUNT_TRADE_MODE_CONTEST, AccountState.CONTEST), (99, AccountState.UNKNOWN)])
def test_non_demo_accounts_rejected(mode, state):
    t = SimTerminal()
    t.add_account(SimAccount(login=5, server="X", trade_mode=mode))
    t.switch_account(5)
    chk = check_account(ReadOnlyGateway(t, is_simulated=True))
    assert chk.state == state and not chk.is_demo
    assert any(b.startswith("ACCOUNT_NOT_DEMO") for b in execution_blockers(chk, chk.fingerprint))


def test_disconnected_rejected():
    t = SimTerminal(connected=False)
    chk = check_account(ReadOnlyGateway(t, is_simulated=True))
    assert chk.state == AccountState.DISCONNECTED


def test_live_account_blocked_at_order_boundary():
    t = SimTerminal()
    gw = TradingGateway(t, is_simulated=True)
    t.accounts[t.current_login].trade_mode = C.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(LiveAccountBlocked):
        gw.order_send({"action": 1, "symbol": "XAUUSD", "volume": 0.01, "type": 0})
    assert t.sent_requests == []  # nothing reached the terminal


def test_real_backend_refused_during_tests():
    gw = TradingGateway(SimTerminal(), is_simulated=False)  # pretend it is the real package
    with pytest.raises(MT5Error):
        gw.order_send({"action": 1})


def test_fingerprint_hides_login_and_detects_switch():
    fp1, fp2 = fingerprint(123456, "Srv", "Co"), fingerprint(123457, "Srv", "Co")
    assert fp1 != fp2 and "123456" not in fp1
    t = SimTerminal()
    pub = check_account(ReadOnlyGateway(t, is_simulated=True)).public()
    assert str(t.current_login) not in str(pub)


def test_netting_account_blocks_automatic_execution():
    t = SimTerminal()
    t.add_account(SimAccount(login=9, server="N", margin_mode=C.ACCOUNT_MARGIN_MODE_RETAIL_NETTING))
    t.switch_account(9)
    chk = check_account(ReadOnlyGateway(t, is_simulated=True))
    assert not chk.hedging
    assert any("NOT_HEDGING" in b for b in execution_blockers(chk, chk.fingerprint))
