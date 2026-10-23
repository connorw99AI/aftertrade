"""Deterministic end-to-end simulation through every gate on the SimTerminal."""
import copy

from metals_lab import config as config_mod
from metals_lab.execution.coordinator import BOT_IDS, Coordinator
from metals_lab.mt5.gateway import TradingGateway
from metals_lab.mt5.sim import SimAccount, SimTerminal
from metals_lab.mt5.simfeed import SimReplay
from metals_lab.store import Store
from metals_lab.timeutil import set_provider


def _run(steps=350):
    cfg = copy.deepcopy(config_mod.DEFAULTS)
    cfg["news"]["unknown_policy"] = "allow_with_flag"
    t = SimTerminal()
    t.add_account(SimAccount(login=7, server="Sim-Demo", balance=100000.0))
    t.switch_account(7)
    rp = SimReplay(t, days=200, start_fraction=0.5, seed=21)
    set_provider(lambda: t._now)
    try:
        c = Coordinator(cfg, TradingGateway(t, is_simulated=True), Store(":memory:"))
        c.connect()
        c.arm("e2e")
        for b in BOT_IDS:
            c.set_bot_status(b, "RUNNING")
        for _ in range(steps):
            rp.step()
            c.cycle()
        trades = [(r["bot_id"], round(r["net"], 2)) for r in c.db.q("SELECT bot_id, net FROM trades ORDER BY rowid")]
        return c, t, trades
    finally:
        set_provider(None)


def test_e2e_deterministic_and_reconciled():
    c1, t1, tr1 = _run()
    c2, t2, tr2 = _run()
    assert tr1 == tr2 and len(tr1) > 0
    # account balance change equals the sum of every bot's booked P&L: nothing leaks between books
    realized = sum(c1.ledger.realized(b) for b in BOT_IDS)
    assert abs((t1.account_info().balance - 100000.0) - realized) < 0.01
    closed = c1.db.q("SELECT * FROM events WHERE kind='TRADE_CLOSED'")
    assert len(closed) == len(tr1)
    assert all(r["mode"] == "LIVE" for r in c1.db.q("SELECT mode FROM audit_decisions WHERE intent LIKE 'OPEN%'"))
