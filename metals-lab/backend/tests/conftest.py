"""Shared fixtures. Every test uses the SimTerminal - never a real MT5 terminal."""
from __future__ import annotations

import copy
import os
from datetime import timedelta

import pandas as pd
import pytest

os.environ["METALS_LAB_TEST_MODE"] = "1"

from metals_lab import config as config_mod  # noqa: E402
from metals_lab.data import bars as B  # noqa: E402
from metals_lab.data import synthetic  # noqa: E402
from metals_lab.domain import Intent, Signal  # noqa: E402
from metals_lab.execution.approvals import Proposal  # noqa: E402
from metals_lab.execution.coordinator import BOT_IDS, Coordinator, LiveMarketView  # noqa: E402
from metals_lab.instruments import spec_from_info  # noqa: E402
from metals_lab.mt5.gateway import TradingGateway  # noqa: E402
from metals_lab.mt5.sim import SimAccount, SimTerminal, default_symbols  # noqa: E402
from metals_lab.mt5.simfeed import SimReplay  # noqa: E402
from metals_lab.store import Store  # noqa: E402
from metals_lab.strategies import magic_for  # noqa: E402
from metals_lab.timeutil import set_provider, utc_now  # noqa: E402
import numpy as np  # noqa: E402


@pytest.fixture(scope="session")
def specs():
    s = default_symbols()
    return {"GOLD": spec_from_info("GOLD", s["XAUUSD"]), "SILVER": spec_from_info("SILVER", s["XAGUSD"])}


def _frames(raw, base):
    out = {}
    for role, df in raw.items():
        out[(role, base)] = df
        for tf in ("M15", "H1", "H4", "D1"):
            if tf != base:
                out[(role, tf)] = B.resample(df, tf)
    return out


@pytest.fixture(scope="session")
def intraday_frames():
    return _frames(synthetic.generate(days=90, seed=3), "M5")


_FRAME_CACHE: dict = {}


def frames_for_strategy(cls):
    """SYNTHETIC frames sized to the strategy's timeframe (cached per timeframe set)."""
    key = tuple(sorted(cls.timeframes))
    if key not in _FRAME_CACHE:
        p = cls.primary_timeframe
        days = 20 if p == "M1" else 90 if p in ("M5", "M15") else 1100
        _FRAME_CACHE[key] = synthetic.frames_for(cls.timeframes, days=days, seed=3 if days < 1000 else 5)
    return _FRAME_CACHE[key]


@pytest.fixture(scope="session")
def swing_frames():
    return _frames(synthetic.generate(days=1100, seed=5, tf_minutes=60), "H1")


@pytest.fixture
def cfg():
    c = copy.deepcopy(config_mod.DEFAULTS)
    c["news"]["unknown_policy"] = "allow_with_flag"
    c["execution"]["loop_interval_seconds"] = 0
    return c


@pytest.fixture
def sim():
    t = SimTerminal()
    t.add_account(SimAccount(login=1001, server="Sim-Demo", balance=100000.0))
    t.switch_account(1001)
    replay = SimReplay(t, days=120, start_fraction=0.7, seed=4)
    set_provider(lambda: t._now)
    yield t, replay
    set_provider(None)


@pytest.fixture
def coord(cfg, sim):
    t, _ = sim
    c = Coordinator(cfg, TradingGateway(t, is_simulated=True), Store(":memory:"))
    c.connect()
    return c


@pytest.fixture
def armed(coord):
    coord.arm("pytest")
    for b in BOT_IDS:
        coord.set_bot_status(b, "RUNNING")
    coord.cycle()
    return coord


def make_open_proposal(c: Coordinator, bot="S01", role="GOLD", intent=Intent.OPEN_LONG, volume=0.1, stop_dist=3.0, tp_dist=6.0,
                       group_id=None):
    spec = c.specs[role]
    tk = c.ro.symbol_info_tick(spec.symbol)
    px = tk["ask"] if intent == Intent.OPEN_LONG else tk["bid"]
    sgn = 1 if intent == Intent.OPEN_LONG else -1
    sig = Signal(bot, utc_now(), role, spec.symbol,
                 intent, "test", {"x": 1}, stop_price=px - sgn * stop_dist, target_price=px + sgn * tp_dist, ref_price=px,
                 group_id=group_id, max_hold_bars=10)
    return Proposal(bot, role, spec.symbol, intent, volume, sig, magic_for(bot), sl=sig.stop_price, tp=sig.target_price)


def approve(c: Coordinator, p: Proposal, owned=None, day_start=None):
    """Run a proposal through the real auditor and risk engine."""
    from metals_lab.mt5.guard import check_account
    acct = check_account(c.ro)
    view = LiveMarketView(c, {}, utc_now())
    audit = c.auditor.assess(view, p.role, p.intent.is_opening, p.proposal_hash, acct.fingerprint)
    book = c.bot_book(p.bot_id)

    class R:
        clean = True
    ctx = c._risk_ctx(p.bot_id, acct, book, day_start or book.equity, book.equity, R(), c.specs[p.role], c._tick(c.specs[p.role]), owned=owned)
    risk = c.risk.evaluate(p, audit.approval, ctx) if audit.approval else None
    return audit, risk


def scan_signals(strategy, frames, specs, want: set, stride: int = 1, max_steps: int = 200000, positions=None):
    """Walk decision times like the backtester and collect signals by intent (no positions)."""
    prepared = strategy.prepare({k: frames[k] for k in {(r, tf) for r in strategy.roles for tf in strategy.timeframes}})
    from metals_lab.domain import TIMEFRAME_MINUTES
    from metals_lab.strategies.base import StrategyContext
    ptf = strategy.primary_timeframe
    role0 = strategy.roles[0]
    times = prepared[(role0, ptf)]["time"]
    close = {k: (pd.to_datetime(df["time"], utc=True).dt.as_unit("ns") + pd.Timedelta(minutes=TIMEFRAME_MINUTES[k[1]])).astype("int64").to_numpy()
             for k, df in prepared.items()}
    found: dict = {}
    for i in range(strategy.min_history_bars, min(len(times), max_steps), stride):
        T = times.iloc[i] + pd.Timedelta(minutes=TIMEFRAME_MINUTES[ptf])
        fr = {k: df.iloc[: np.searchsorted(close[k], T.as_unit("ns").value, side="right")] for k, df in prepared.items()}
        ctx = StrategyContext(T.to_pydatetime(), fr, positions or [], specs)
        for s in strategy.generate_signals(ctx):
            found.setdefault(s.intent, s)
        if want <= set(found):
            break
    return found
