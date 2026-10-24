"""Allocation/execution coordinator: the live loop.

Modes
* SHADOW (not armed): strategies, auditor and risk all run on live data and
  their decisions are recorded, but nothing is sent. This is the default.
* ARMED: same pipeline, and approved proposals go to the executor.

Pause stops new entries for a bot (or all bots); open positions keep their
server-side stops and strategy exits keep running.
KILL: disarms the system, blocks every new entry, and closes every bot-owned
position through the normal (demo-guarded) pipeline. Positions that cannot be
closed (e.g. market closed) keep their server-side stops and raise alerts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from ..auditor.auditor import MarketAuditor, spread_baseline_from_bars
from ..auditor.news import build_provider
from ..data.bars import completed_only
from ..data.history import fetch_recent
from ..domain import TIMEFRAME_MINUTES, Decision, Intent, PositionView, Signal
from ..instruments import ROLES, InstrumentSpec, discover
from ..ledger import Ledger
from ..mt5.gateway import TradingGateway, utc_now
from ..mt5.guard import AccountCheck, AccountState, check_account, execution_blockers
from ..risk import RiskContext, RiskEngine, risk_pct_for
from ..sessions import ServerClock, calendar_from_config, calendar_from_mql5_export
from ..store import Store, now_iso
from ..risk.daily import TEXT as DAILY_TEXT, daily_stop
from ..strategies import ASSESSOR_RETIRED, BOT_IDS, STRATEGY_CLASSES, Strategy, build, magic_for, strategy_for_magic  # noqa: F401
from ..strategies.base import StrategyContext
from .approvals import ApprovalAuthority, Proposal
from .executor import Executor
from .reconciliation import Reconciler

log = logging.getLogger("metals_lab")


class LiveMarketView:
    def __init__(self, coord: "Coordinator", frames: dict, now: datetime):
        self.c, self.frames, self._now = coord, frames, now

    def now(self):
        return self._now

    def spec(self, role):
        return self.c.specs[role]

    def tick(self, role):
        t = self.c.gw.symbol_info_tick(self.c.specs[role].symbol)
        if not t:
            return None
        ms = t.get("time_msc") or t["time"] * 1000
        return {"bid": t["bid"], "ask": t["ask"], "time_utc": self.c.clock.server_epoch_to_utc(int(ms // 1000))}

    def bars(self, role, tf, n):
        f = self.frames.get((role, tf))
        if f is None:
            f = self.c.get_frame(role, tf, max(n, 300), self._now)
        return f.iloc[-n:]

    def session_open(self, role, t):
        cal = self.c.sessions.get(role)
        if cal is None:
            return None
        return cal.is_open_server(self.c.clock.utc_to_server_naive(t))

    def spread_baseline(self, role, hour):
        return self.c.spread_baselines.get(role, {}).get(hour)

    def reference_bars(self):
        return self.c.reference_frames(self._now)


class Coordinator:
    def __init__(self, cfg: dict, gw: TradingGateway, store: Store):
        self.cfg = cfg
        self.gw = gw
        self.ro = gw.read_only()
        self.db = store
        self.ledger = Ledger(store)
        self.audit_auth = ApprovalAuthority("AUDIT", store, cfg["execution"]["approval_ttl_seconds"], cfg["auditor"]["ruleset_version"])
        self.risk_auth = ApprovalAuthority("RISK", store, cfg["execution"]["approval_ttl_seconds"], "risk-1.0")
        self.auditor = MarketAuditor(cfg, build_provider(cfg), self.audit_auth, self._operator_alerts)
        self.risk = RiskEngine(cfg, store, self.audit_auth, self.risk_auth, self.ro)
        self.executor = Executor(gw, store, self.audit_auth, self.risk_auth, cfg)
        self.reconciler = Reconciler(self.ro, store, self.ledger, strategy_for_magic, cfg)
        self.specs: dict[str, InstrumentSpec] = {}
        self.discovery: dict = {}
        self.sessions: dict = {}
        self.spread_baselines: dict = {}
        self.clock = ServerClock(cfg["mt5"]["server_timezone"]) if cfg["mt5"]["server_timezone"] not in ("auto", None) else ServerClock()
        self.account: Optional[AccountCheck] = None
        self.last_reconcile = None
        self._frame_cache: dict = {}
        self._prep_cache: dict = {}
        self._last_tick_epoch: dict = {}
        self._cache_time = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.connected = False
        self.last_cycle: Optional[str] = None
        self.last_error: Optional[str] = None
        self._init_bots()

    # ------------------------------------------------------------------ setup
    def _init_bots(self):
        legacy = [r["bot_id"] for r in self.db.q("SELECT bot_id FROM bots") if r["bot_id"] not in BOT_IDS]
        if legacy and self.db.one("SELECT 1 FROM capital_snapshot"):
            self.db.alert("CRITICAL", "LAB_UPGRADE_NEEDED",
                          "this database holds the old 10-bot layout; run 'metals-lab new-lab' to archive it and start the "
                          "20-bot (long/short) lab", blocks_entries=True)
        for sid in BOT_IDS:
            params = (self.cfg.get("strategies", {}).get(sid, {}) or {}).get("params", {})
            build(sid, params)  # validates
            retired = ASSESSOR_RETIRED.get(sid) if self.cfg["capital"].get("apply_assessor_retirements", False) else None
            self.db.x("INSERT OR IGNORE INTO bots(bot_id,enabled,status,pause_reason,params_json,updated_at) VALUES(?,?,?,?,?,?)",
                      (sid, int(bool((self.cfg.get("strategies", {}).get(sid, {}) or {}).get("enabled", False))),
                       "RETIRED" if retired else "STOPPED", f"assessor: {retired}" if retired else None, json.dumps(params), now_iso()))

    def strategy(self, bot_id: str) -> Strategy:
        row = self.db.one("SELECT params_json FROM bots WHERE bot_id=?", (bot_id,))
        return build(bot_id, json.loads(row["params_json"] or "{}"))

    def connect(self) -> AccountCheck:
        ok = self.gw.initialize(self.cfg["mt5"]["terminal_path"], self.cfg["mt5"]["timeout_ms"])
        self.connected = ok
        if not ok:
            self.last_error = f"initialize failed: {self.gw.last_error()}"
            self.account = check_account(self.ro)
            return self.account
        self.account = check_account(self.ro)
        if self.account.state != AccountState.DEMO:
            self.db.alert("CRITICAL", "NOT_DEMO", f"Trading blocked: the connected MT5 account is not a demo account ({self.account.reason})", blocks_entries=True)
            if self.db.active_activation():
                self.db.disarm("non-demo account at startup")
        else:
            self.db.resolve_alert("NOT_DEMO")
        sel = {"GOLD": self.cfg["instruments"].get("gold"), "SILVER": self.cfg["instruments"].get("silver")}
        res = discover(self.ro, sel)
        self.specs = res.resolved
        self.discovery = res.public()
        for role, why in res.unresolved.items():
            self.db.alert("HIGH", f"INSTRUMENT_{role}", f"{role.title()} could not be found in MT5: {why}. Check the symbol names in config.yaml", blocks_entries=True)
        for role in res.resolved:
            self.db.resolve_alert(f"INSTRUMENT_{role}")
        self._setup_clock()
        self._load_sessions()
        self._check_activation_binding()
        return self.account

    def _setup_clock(self):
        if self.clock.tz is not None:
            return
        for role, spec in self.specs.items():
            t = self.ro.symbol_info_tick(spec.symbol)
            if t:
                off = ServerClock.measure_offset_hours(int(t["time"]), utc_now())
                if abs(off) <= 14:
                    self.clock = ServerClock(fixed_offset_hours=off)
                    self.db.set_control("server_offset_hours", str(off))
                    self.db.resolve_alert("CLOCK_UNVERIFIED")
                    return
        saved = self.db.get_control("server_offset_hours")
        if saved is not None:
            self.clock = ServerClock(fixed_offset_hours=float(saved))
            return
        self.db.alert("HIGH", "CLOCK_UNVERIFIED", "The broker's clock offset is unknown (often because the market is closed). Set mt5.server_timezone in config.yaml", blocks_entries=True)

    def _load_sessions(self):
        exp = self.cfg["mt5"].get("sessions_export")
        for role, spec in self.specs.items():
            cal = None
            if exp:
                cal = calendar_from_mql5_export(Path(exp), spec.symbol)
            if cal is None and self.cfg.get("sessions", {}).get(role.lower()):
                cal = calendar_from_config(spec.symbol, self.cfg["sessions"][role.lower()])
            if cal is not None:
                self.sessions[role] = cal

    def _check_activation_binding(self):
        act = self.db.active_activation()
        if act and self.account and self.account.fingerprint != act["fingerprint"]:
            self.db.disarm("account changed since activation")
            self.db.alert("CRITICAL", "ACCOUNT_SWITCHED", "A different MT5 account is connected than the one you armed, so trading was disarmed. Arm again if this is intended",
                          blocks_entries=True)

    # ------------------------------------------------------------- activation
    def preflight(self) -> list[str]:
        acct = check_account(self.ro)
        problems = [b for b in execution_blockers(acct, acct.fingerprint) if b != "NOT_ARMED"]
        if set(ROLES) - set(self.specs):
            problems.append("INSTRUMENTS_UNRESOLVED")
        if self.clock.source == "unset":
            problems.append("CLOCK_UNVERIFIED")
        return problems

    def arm(self, operator: str) -> dict:
        """Called only from the CLI 'arm' command after interactive confirmation."""
        acct = check_account(self.ro)
        problems = self.preflight()
        if problems:
            raise RuntimeError("cannot arm: " + "; ".join(problems))
        self.db.arm(acct.fingerprint, acct.server)
        snap = self.ledger.initialize(acct.fingerprint, float(acct.balance), acct.currency or "", self.cfg["capital"]["bot_fraction"], BOT_IDS)
        self.db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                  (None, "ARM", None, json.dumps({"fingerprint": acct.fingerprint[:12]}), operator, now_iso()))
        self.db.set_control("kill", "0")
        self.db.event("ARMED", None, {"fingerprint": acct.fingerprint[:12], "server": acct.server})
        return {"fingerprint": acct.fingerprint[:12], "server": acct.server, "starting_balance": snap["starting_balance"],
                "per_bot_allocation": round(snap["starting_balance"] * snap["bot_fraction"], 2)}

    def disarm(self, reason: str, operator: str = "operator") -> None:
        self.db.disarm(reason)
        self.db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                  (None, "DISARM", None, json.dumps({"reason": reason}), operator, now_iso()))
        self.db.event("DISARMED", None, {"reason": reason})

    def kill(self, reason: str, operator: str = "operator") -> dict:
        self.db.set_control("kill", "1")
        self.db.alert("CRITICAL", "KILL_SWITCH", f"Kill switch on: {reason}. All trades were closed and trading is off until you clear it", blocks_entries=True)
        results = []
        with self._lock:
            for row in self.db.q("SELECT * FROM positions WHERE status='OPEN'"):
                results.append(self._close_position_row(row, f"KILL SWITCH: {reason}"))
        self.disarm(f"kill: {reason}", operator)
        return {"closed_attempts": results}

    def close_positions(self, rows: list[dict], reason: str, operator: str) -> list[dict]:
        """Operator-initiated closes. Each goes through the auditor, risk engine and executor like any exit;
        a two-leg group is closed together when either leg is selected."""
        keys = {r["position_key"] for r in rows}
        groups = {r["group_id"] for r in rows if r.get("group_id")}
        for g in groups:
            keys |= {x["position_key"] for x in self.db.q("SELECT position_key FROM positions WHERE status='OPEN' AND group_id=?", (g,))}
        results = []
        with self._lock:
            for key in sorted(keys):
                row = self.db.one("SELECT * FROM positions WHERE status='OPEN' AND position_key=?", (key,))
                if row:
                    results.append(self._close_position_row(row, f"MANUAL CLOSE by {operator}: {reason}"))
        self.db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                  (None, "MANUAL_CLOSE", json.dumps(sorted(keys)), json.dumps({"reason": reason, "results": results}, default=str),
                   operator, now_iso()))
        self.db.event("MANUAL_CLOSE", None, {"operator": operator, "reason": reason, "results": results})
        return results

    def is_armed(self) -> bool:
        return self.db.active_activation() is not None

    # ------------------------------------------------------------- bot control
    def set_bot_status(self, bot_id: str, status: str, reason: str = "", operator: str = "operator") -> None:
        assert status in ("RUNNING", "PAUSED", "STOPPED", "RETIRED")
        old = self.db.one("SELECT status FROM bots WHERE bot_id=?", (bot_id,))
        if old and old["status"] == "PAUSED_RISK" and status == "RUNNING":
            # a restart after the drawdown pause measures the drawdown from now on; otherwise the next cycle re-pauses it
            self.db.x("DELETE FROM bot_peak WHERE bot_id=?", (bot_id,))
        self.db.x("UPDATE bots SET status=?, pause_reason=?, enabled=?, updated_at=? WHERE bot_id=?",
                  (status, reason, int(status == "RUNNING"), now_iso(), bot_id))
        self.db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                  (bot_id, "STATUS", json.dumps(old), json.dumps({"status": status, "reason": reason}), operator, now_iso()))

    def _assess_closed(self, trade_ids: list[str]) -> None:
        """The assessor re-reviews every bot that just closed a trade. Never allowed to break the loop."""
        from .. import assessor
        bots = {r["bot_id"] for t in trade_ids for r in self.db.q("SELECT bot_id FROM trades WHERE trade_id=?", (t,))}
        for b in sorted(bots):
            try:
                assessor.store_review(self.db, b, "demo", "trade closed", assessor.review_demo(self.db, b, self))
            except Exception as e:  # pragma: no cover - defensive
                log.warning("assessor review for %s failed: %s", b, e)

    @staticmethod
    def sibling(bot_id: str) -> str:
        return bot_id[:3] + ("S" if bot_id[3] == "L" else "L")

    def retire(self, bot_id: str, reason: str, operator: str) -> dict:
        """Retire one half: no new entries until un-retired. The halves are independent bots, so its balance stays on
        its own book (it does not move to the other half). The half must be flat first."""
        if self.db.one("SELECT 1 FROM positions WHERE bot_id=? AND status='OPEN'", (bot_id,)):
            raise RuntimeError("close this bot's open trades first (Positions page), then retire it")
        self.set_bot_status(bot_id, "RETIRED", reason or "retired by operator", operator)
        return {"bot_id": bot_id, "equity": round(self.ledger.allocation(bot_id) + self.ledger.realized(bot_id), 2)}

    def unretire(self, bot_id: str, operator: str) -> dict:
        """Back to STOPPED. Labs from before the halves were made independent moved a retired half's balance to the
        other half; that amount is moved back (from the other half, or the unallocated pool if it no longer has it).
        Every move is an audited ledger entry."""
        self.set_bot_status(bot_id, "STOPPED", "un-retired", operator)
        restored = {"from_other_half": 0.0, "from_pool": 0.0}
        q = lambda sql, *a: float(self.db.one(sql, a)["s"])
        moved_out = -q("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind='ALLOCATION' AND note LIKE 'retired:%'", bot_id)
        back = q("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind='ALLOCATION' AND amount>0 AND note LIKE '%un-retire%'", bot_id)
        need = round(moved_out - back, 2)
        if need <= 0:
            return {"bot_id": bot_id, "allocation": self.ledger.allocation(bot_id), **restored}
        sib = self.sibling(bot_id)
        got = q("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind='ALLOCATION' AND note LIKE ?", sib, f"from retired {bot_id}:%")
        returned = -q("SELECT COALESCE(SUM(amount),0) s FROM ledger WHERE bot_id=? AND kind='ALLOCATION' AND note LIKE ?", sib, f"to {bot_id}: un-retire%")
        amt = round(min(need, got - returned, self.ledger.allocation(sib)), 2)
        if amt > 0:
            self.ledger.move_allocation(sib, bot_id, amt, "un-retire: return the retired half's balance", operator)
            restored["from_other_half"], need = amt, round(need - amt, 2)
        acct = check_account(self.ro)
        if need > 0 and acct.fingerprint and self.ledger.snapshot(acct.fingerprint):
            amt = min(need, self.ledger.unallocated_pool(acct.fingerprint))
            if amt > 0:
                self.ledger.change_allocation(acct.fingerprint, bot_id, amt, "un-retire: restore the retired half's balance", operator)
                restored["from_pool"] = amt
        return {"bot_id": bot_id, "allocation": self.ledger.allocation(bot_id), **restored}

    def unretire_all(self, operator: str) -> list[dict]:
        return [self.unretire(b, operator) for b in BOT_IDS
                if self.db.one("SELECT status FROM bots WHERE bot_id=?", (b,))["status"] == "RETIRED"]

    def update_params(self, bot_id: str, params: dict, operator: str) -> dict:
        row = self.db.one("SELECT * FROM bots WHERE bot_id=?", (bot_id,))
        if row["status"] == "RUNNING":
            raise RuntimeError("pause the bot before changing its parameters")
        if self.db.one("SELECT 1 FROM positions WHERE bot_id=? AND status='OPEN'", (bot_id,)):
            raise RuntimeError("bot has open positions; close them before changing rules")
        build(bot_id, params)  # validates names
        self.db.x("UPDATE bots SET params_json=?, config_version=config_version+1, updated_at=? WHERE bot_id=?",
                  (json.dumps(params), now_iso(), bot_id))
        self.db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
                  (bot_id, "PARAMS", row["params_json"], json.dumps(params), operator, now_iso()))
        return self.db.one("SELECT * FROM bots WHERE bot_id=?", (bot_id,))

    # ------------------------------------------------------------------- data
    def get_frame(self, role: str, tf: str, n: int, now: datetime) -> pd.DataFrame:
        key = (role, tf, n)
        if self._cache_time != now:
            self._frame_cache, self._prep_cache, self._cache_time = {}, {}, now
        if key not in self._frame_cache:
            df = fetch_recent(self.ro, self.clock, self.specs[role], tf, n + 1)
            self._frame_cache[key] = completed_only(df, tf, pd.Timestamp(now)).reset_index(drop=True)
        return self._frame_cache[key]

    def prepared(self, strat, frames: dict) -> dict:
        """strat.prepare(frames), shared within one cycle by both halves of a strategy (and by _think and _run_bot).
        The side/metal/hour filters only act in entries(), so they are left out of the key."""
        key = (type(strat).__name__, json.dumps({k: v for k, v in strat.p.items() if k not in ("sides", "metals", "hours_utc")},
                                                sort_keys=True, default=str), tuple(id(f) for f in frames.values()))
        if key not in self._prep_cache:
            self._prep_cache[key] = strat.prepare(frames)
        return self._prep_cache[key]

    def reference_frames(self, now) -> dict:
        out = {}
        for sym in self.cfg["instruments"].get("reference_symbols") or []:
            info = self.ro.symbol_info(sym)
            if not info:
                continue
            from ..instruments import spec_from_info
            out[sym] = completed_only(fetch_recent(self.ro, self.clock, spec_from_info("REF", info), "M5", 301), "M5", pd.Timestamp(now))
        return out

    def _operator_alerts(self) -> list[dict]:
        return [a for a in self.db.q("SELECT * FROM alerts WHERE resolved_at IS NULL AND code LIKE 'MARKET_ALERT_%'")]

    # ------------------------------------------------------------------ cycle
    def cycle(self, now: Optional[datetime] = None) -> dict:
        now = now or utc_now()
        with self._lock:
            summary = {"time": now.isoformat(), "bots": {}}
            acct = check_account(self.ro)
            if acct.state == AccountState.DISCONNECTED:
                self.db.alert("HIGH", "DISCONNECTED", "MT5 is disconnected. Check the terminal is open and logged in", blocks_entries=True)
                if not self.gw.initialize(self.cfg["mt5"]["terminal_path"], self.cfg["mt5"]["timeout_ms"]):
                    self.account = acct
                    summary["error"] = "disconnected"
                    return summary
                acct = check_account(self.ro)  # re-verify demo + identity after reconnection
            self.db.resolve_alert("DISCONNECTED")
            self.account = acct
            if acct.state != AccountState.DEMO:
                if self.is_armed():
                    self.db.disarm(f"account state {acct.state.value}")
                self.db.alert("CRITICAL", "NOT_DEMO", f"Trading blocked: the connected MT5 account is not a demo account ({acct.reason})", blocks_entries=True)
                summary["error"] = "not demo"
                return summary
            self._check_activation_binding()
            if not self.specs:
                self.connect()
            self._verify_clock(now)
            rep = self.reconciler.run(now)
            if rep.closed_trades:
                self._assess_closed(rep.closed_trades)
            self.last_reconcile = {"clean": rep.clean, "issues": rep.issues, "time": now.isoformat(), "new_deals": rep.new_deals,
                                   "closed_trades": rep.closed_trades}
            if not self.spread_baselines:
                for role in self.specs:
                    try:
                        self.spread_baselines[role] = spread_baseline_from_bars(self.get_frame(role, "M5", 2000, now))
                    except Exception:
                        pass
            armed = self.is_armed()
            if armed:
                summary["protection"] = self.executor.verify_protection(
                    self.db.q("SELECT * FROM positions WHERE status='OPEN' AND ticket IS NOT NULL"), self.specs)
            for bot in BOT_IDS:
                try:
                    self._think(bot, now)
                except Exception as e:  # explanations must never block trading
                    log.warning("thinking for %s failed: %s", bot, e)
                try:
                    summary["bots"][bot] = self._run_bot(bot, now, rep, acct, armed)
                except Exception as e:  # one bot's failure must not stop the others
                    log.exception("bot %s failed", bot)
                    summary["bots"][bot] = {"error": str(e)}
            self.last_cycle = now.isoformat()
            return summary

    def _verify_clock(self, now):
        for spec in self.specs.values():
            t = self.ro.symbol_info_tick(spec.symbol)
            if not t:
                continue
            epoch = int(t["time"])
            # Only a live price can measure the clock: the tick must have moved since the last cycle, and its time must sit
            # within 2 minutes of a whole half-hour offset. A stale tick (market closed, e.g. Friday night) would otherwise
            # read as a wrong offset, such as +2.5h when the last price is 30 minutes old.
            prev = self._last_tick_epoch.get(spec.symbol)
            self._last_tick_epoch[spec.symbol] = epoch
            diff = epoch - now.timestamp()
            if prev is None or epoch <= prev or abs(diff - round(diff / 1800.0) * 1800.0) > 120:
                continue
            measured = ServerClock.measure_offset_hours(epoch, now)
            if abs(measured) > 14:
                return
            ok, msg = self.clock.verify_against_tick(int(t["time"]), now)
            if ok:
                self.db.resolve_alert("CLOCK_MISMATCH")
            elif self.clock.tz is None:
                # measured-offset mode: follow server DST shifts, but tell the operator
                self.clock = ServerClock(fixed_offset_hours=measured)
                self.db.set_control("server_offset_hours", str(measured))
                self.db.event("CLOCK_OFFSET_CHANGED", None, {"detail": msg})
            else:
                self.db.alert("HIGH", "CLOCK_MISMATCH", f"mt5.server_timezone in config.yaml does not match the broker's clock: {msg}", blocks_entries=True)
            return

    def bot_book(self, bot: str, rep=None):
        unreal = (rep.unrealized.get(bot, 0.0) if rep else 0.0)
        reserved = (rep.reserved_margin.get(bot, 0.0) if rep else
                    self.db.one("SELECT COALESCE(SUM(reserved_margin),0) s FROM positions WHERE bot_id=? AND status='OPEN'", (bot,))["s"])
        acct = self.account
        snap = self.ledger.snapshot(acct.fingerprint) if acct and acct.fingerprint else None
        if snap is None and acct is not None and acct.balance:
            # Not armed yet on this account: size shadow decisions on the allocation the bot WOULD receive,
            # instead of a zero budget that would make every shadow entry look unaffordable.
            from ..ledger import BotBook
            return BotBook(bot, round(float(acct.balance) * self.cfg["capital"]["bot_fraction"], 2), 0.0, unreal, reserved, 0.0,
                           shadow=True)
        return self.ledger.book(bot, unreal, reserved, 0.0)

    def _day_and_peak(self, bot: str, equity: float, now: datetime) -> tuple[float, float]:
        day = now.strftime("%Y-%m-%d")
        self.db.x("INSERT OR IGNORE INTO bot_day_start(bot_id,day,equity,high) VALUES(?,?,?,?)", (bot, day, equity, equity))
        self.db.x("UPDATE bot_day_start SET high=MAX(COALESCE(high, equity), ?) WHERE bot_id=? AND day=?", (equity, bot, day))
        row = self.db.one("SELECT equity, high, stopped FROM bot_day_start WHERE bot_id=? AND day=?", (bot, day))
        ds = row["equity"]
        if not row["stopped"]:
            stop = daily_stop(ds, row["high"], equity, self.cfg["risk"])
            if stop:  # holds for the rest of the UTC day, even if equity recovers
                self.db.x("UPDATE bot_day_start SET stopped=? WHERE bot_id=? AND day=?", (stop, bot, day))
                self.db.event("DAILY_STOP", bot, {"reason": DAILY_TEXT[stop], "day_start": round(ds, 2), "equity": round(equity, 2)})
        self.db.x("INSERT INTO bot_peak(bot_id,peak) VALUES(?,?) ON CONFLICT(bot_id) DO UPDATE SET peak=MAX(peak, excluded.peak)", (bot, equity))
        pk = self.db.one("SELECT peak FROM bot_peak WHERE bot_id=?", (bot,))["peak"]
        return ds, pk

    def _day_row(self, bot: str) -> dict:
        return self.db.one("SELECT high, stopped FROM bot_day_start WHERE bot_id=? ORDER BY day DESC LIMIT 1", (bot,)) or {}

    def _positions_view(self, bot: str, strat: Strategy, frames: dict) -> list[PositionView]:
        out = []
        for r in self.db.q("SELECT * FROM positions WHERE bot_id=? AND status='OPEN'", (bot,)):
            opened = datetime.fromisoformat(r["opened_at"])
            f = frames.get((r["role"], strat.primary_timeframe))
            held = int((f["time"] >= pd.Timestamp(opened).floor("min")).sum()) if f is not None else 0
            out.append(PositionView(r["position_key"], r["role"], r["symbol"], r["side"], r["volume_open"], r["entry_price"], opened,
                                    held, r["sl"], r["tp"], r["max_hold_bars"], r["group_id"]))
        return out

    def _think(self, bot: str, now: datetime) -> None:
        """Record what the bot currently sees (its entry checklist) whenever a new bar completes."""
        strat = self.strategy(bot)
        if set(strat.roles) - set(self.specs):
            return
        ptf = strat.primary_timeframe
        frames = {(r, tf): self.get_frame(r, tf, max(strat.min_history_bars + 50, 300), now)
                  for r in strat.roles for tf in strat.timeframes}
        if any(len(frames[(r, ptf)]) == 0 for r in strat.roles):
            return
        last_bar = max(str(frames[(r, ptf)]["time"].iloc[-1]) for r in strat.roles)
        status = self.db.one("SELECT status FROM bots WHERE bot_id=?", (bot,))["status"]
        key = f"thinkbar:{bot}"
        if self.db.get_control(key) == f"{last_bar}|{status}":
            return
        prepared = self.prepared(strat, frames)
        th = strat.think(StrategyContext(now, prepared, self._positions_view(bot, strat, prepared), self.specs))
        th["bar_time"] = last_bar
        th["bot_status"] = status
        th["note"] = ("rules only: an entry still has to pass sizing, the market auditor and the risk engine"
                      if status == "RUNNING" else "bot is not running: shown for information only")
        self.db.set_control(f"thinking:{bot}", json.dumps(th, default=str))
        self.db.set_control(key, f"{last_bar}|{status}")

    def thinking(self) -> dict:
        out = {}
        for bot in BOT_IDS:
            raw = self.db.get_control(f"thinking:{bot}")
            out[bot] = json.loads(raw) if raw else None
        return out

    def _run_bot(self, bot: str, now: datetime, rep, acct: AccountCheck, armed: bool) -> dict:
        row = self.db.one("SELECT * FROM bots WHERE bot_id=?", (bot,))
        status = row["status"]
        has_pos = self.db.one("SELECT 1 FROM positions WHERE bot_id=? AND status='OPEN'", (bot,)) is not None
        if status in ("STOPPED", "RETIRED") and not has_pos:
            return {"status": status}
        strat = self.strategy(bot)
        if set(strat.roles) - set(self.specs):
            return {"status": status, "skipped": "instruments unresolved"}
        snap = self.ledger.snapshot(acct.fingerprint) if acct.fingerprint else None
        book = self.bot_book(bot, rep)
        ds, pk = self._day_and_peak(bot, book.equity, now) if snap else (book.equity, book.equity)
        ddp = float(self.cfg["risk"].get("max_drawdown_pause") or 0)
        if ddp > 0 and snap and pk and (book.equity - pk) / pk <= -ddp and status == "RUNNING":
            self.db.x("UPDATE bots SET status='PAUSED_RISK', pause_reason='max drawdown reached' WHERE bot_id=?", (bot,))
            self.db.event("BOT_PAUSED", bot, {"reason": "max drawdown"})
            status = "PAUSED_RISK"
        # only act when a new primary bar completed, or a delayed proposal is due for reassessment
        ptf = strat.primary_timeframe
        frames = {(r, tf): self.get_frame(r, tf, max(strat.min_history_bars + 50, 300), now)
                  for r in strat.roles for tf in strat.timeframes}
        last_bar = max(str(frames[(r, ptf)]["time"].iloc[-1]) for r in strat.roles if len(frames[(r, ptf)]))
        prev = self.db.get_control(f"lastbar:{bot}")
        due = self.db.get_control(f"reassess:{bot}")
        reassess_due = due is not None and datetime.fromisoformat(due) <= now
        if prev == last_bar and not reassess_due:
            return {"status": status, "waiting_for_bar": True}
        self.db.set_control(f"lastbar:{bot}", last_bar)
        if reassess_due:
            self.db.x("DELETE FROM control WHERE key=?", (f"reassess:{bot}",))
        prepared = self.prepared(strat, frames)
        ctx = StrategyContext(now, prepared, self._positions_view(bot, strat, prepared), self.specs,
                              weekend_flat_from=self.cfg["risk"].get("weekend_flat_from"))
        sigs = strat.generate_signals(ctx)
        if status != "RUNNING":
            sigs = [s for s in sigs if s.intent.is_risk_reducing]  # paused: manage exits only
        mode = "LIVE" if armed and status == "RUNNING" else ("LIVE_EXIT" if armed else "SHADOW")
        out = {"status": status, "signals": len(sigs), "mode": mode, "results": []}
        if not sigs:
            return out
        view = LiveMarketView(self, prepared, now)
        exits = [s for s in sigs if s.intent.is_risk_reducing]
        entries = [s for s in sigs if s.intent.is_opening]
        for s in exits:
            pr = self.db.one("SELECT * FROM positions WHERE position_key=? AND status='OPEN'", (s.position_key,))
            if pr:
                out["results"].append(self._pipeline_close(bot, s, pr, view, acct, armed, strat, book, ds, pk, rep))
        if entries:
            out["results"].extend(self._pipeline_entries(bot, entries, view, acct, armed and status == "RUNNING", strat, book, ds, pk, rep))
        return out

    # --------------------------------------------------------------- pipeline
    def _risk_ctx(self, bot, acct, book, ds, pk, rep, spec, tick, owned=None) -> RiskContext:
        info = self.ro.account_info() or {}
        gross, same = 0.0, {"LONG": 0.0, "SHORT": 0.0}
        bgross, bsame = 0.0, {"LONG": 0.0, "SHORT": 0.0}
        for r in self.db.q("SELECT * FROM positions WHERE status='OPEN'"):
            s = next((x for x in self.specs.values() if x.symbol == r["symbol"]), None)
            if s:
                n = self.risk.notional(s, float(r["volume_open"] or 0), float(r["entry_price"] or 0))
                gross += n
                same[r["side"]] += n
                if r["bot_id"] == bot:
                    bgross += n
                    bsame[r["side"]] += n
        inflight = {r["symbol"] for r in self.db.q("SELECT symbol FROM intents WHERE bot_id=? AND state IN ('CREATED','SUBMITTING','UNKNOWN')", (bot,))}
        act = self.db.active_activation()
        return RiskContext(
            account=acct, activation_fp=act["fingerprint"] if act else None, armed=act is not None,
            killed=self.db.get_control("kill") == "1", global_paused=self.db.get_control("global_pause") == "1",
            bot_status=self.db.one("SELECT status FROM bots WHERE bot_id=?", (bot,))["status"], book=book,
            day_start_equity=ds, peak_equity=pk, day_high_equity=self._day_row(bot).get("high") or ds, day_stopped=self._day_row(bot).get("stopped"),
            bot_open_positions=self.db.one("SELECT COUNT(*) n FROM positions WHERE bot_id=? AND status='OPEN'", (bot,))["n"],
            account_equity=float(info.get("equity") or 0), account_margin=float(info.get("margin") or 0),
            account_margin_level=float(info.get("margin_level") or 0), gross_exposure_notional=gross, same_direction_notional=same,
            bot_gross_notional=bgross, bot_same_direction_notional=bsame,
            reconciliation_clean=rep.clean, blocking_alerts=self.db.blocking_alerts(), in_flight_symbols=inflight,
            tick=tick, spec=spec, owned_position=owned, entries_today=self.entries_today(bot))

    def entries_today(self, bot: str) -> int:
        """Trades this bot started this UTC day: live intents that reached the terminal, plus shadow approvals.
        The legs of a trade group count as one trade."""
        day = utc_now().strftime("%Y-%m-%d") + "%"
        keys = {r["k"] for r in self.db.q(
            "SELECT COALESCE(group_id, intent_id) k FROM intents WHERE bot_id=? AND intent LIKE 'OPEN%' "
            "AND state NOT IN ('REJECTED','CANCELLED') AND created_at LIKE ?", (bot, day))}
        for r in self.db.q("SELECT signal_json FROM signals_log WHERE bot_id=? AND mode='SHADOW' AND outcome='SHADOW_APPROVED' "
                           "AND ts LIKE ?", (bot, day)):
            sig = json.loads(r["signal_json"])
            if str(sig.get("intent", "")).startswith("OPEN"):
                keys.add(sig.get("group_id") or sig.get("signal_id"))
        return len(keys)

    def _log_decisions(self, bot, p: Proposal, audit, risk, mode, outcome, strat):
        s = p.signal
        self.db.x("INSERT INTO audit_decisions(ts,bot_id,symbol,intent,decision,triggered_json,reasons_json,evidence_json,data_quality,"
                  "news_status,ruleset_version,reassess_at,proposal_hash,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (now_iso(), bot, p.symbol, p.intent.value, audit.decision.value, json.dumps(audit.triggered), json.dumps(audit.reasons),
                   json.dumps(audit.evidence, default=str), audit.data_quality, audit.news_status, audit.ruleset_version,
                   audit.reassess_at.isoformat() if audit.reassess_at else None, p.proposal_hash, mode))
        if risk is not None:
            self.db.x("INSERT INTO risk_decisions(ts,bot_id,symbol,intent,decision,reasons_json,metrics_json,proposal_hash,mode) VALUES(?,?,?,?,?,?,?,?,?)",
                      (now_iso(), bot, p.symbol, p.intent.value, risk.decision.value, json.dumps(risk.reasons),
                       json.dumps(risk.metrics, default=str), p.proposal_hash, mode))
        self.db.x("INSERT INTO signals_log(ts,bot_id,mode,outcome,signal_json,explanation) VALUES(?,?,?,?,?,?)",
                  (now_iso(), bot, mode, outcome, json.dumps(s.to_dict(), default=str), strat.explain_signal(s)))

    def _tick(self, spec):
        t = self.ro.symbol_info_tick(spec.symbol)
        return {"bid": t["bid"], "ask": t["ask"]} if t else None

    def _pipeline_close(self, bot, s: Signal, pr: dict, view, acct, armed, strat, book, ds, pk, rep) -> dict:
        spec = self.specs[pr["role"]]
        p = Proposal(bot, pr["role"], spec.symbol, s.intent, float(pr["volume_open"]), s, magic_for(bot),
                     position_key=pr["position_key"], position_ticket=pr["ticket"])
        audit = self.auditor.assess(view, pr["role"], False, p.proposal_hash, acct.fingerprint or "", primary_tf=strat.primary_timeframe,
                                    proposal=p)
        risk = None
        outcome = f"AUDIT_{audit.decision.value}"
        if audit.decision == Decision.APPROVED:
            risk = self.risk.evaluate(p, audit.approval, self._risk_ctx(bot, acct, book, ds, pk, rep, spec, self._tick(spec),
                                                                           owned={"volume": pr["volume_open"]}))
            outcome = f"RISK_{risk.decision.value}"
            if risk.decision == Decision.APPROVED:
                if armed or self._last_fp_matches(acct):
                    r = self.executor.submit(p, spec, audit.approval, risk.approval)
                    outcome = f"EXEC_{r.state}: {r.message}"
                else:
                    outcome = "SHADOW_APPROVED"
        elif audit.decision == Decision.DELAYED and audit.reassess_at:
            self.db.set_control(f"reassess:{bot}", audit.reassess_at.isoformat())
        self._log_decisions(bot, p, audit, risk, "LIVE" if armed else "SHADOW", outcome, strat)
        return {"signal": s.intent.value, "outcome": outcome}

    def _last_fp_matches(self, acct) -> bool:
        r = self.db.one("SELECT fingerprint FROM activation ORDER BY id DESC LIMIT 1")
        return bool(r and acct.fingerprint == r["fingerprint"])

    def _pipeline_entries(self, bot, entries, view, acct, live, strat, book, ds, pk, rep) -> list[dict]:
        out = []
        ticks = {s.role: self._tick(self.specs[s.role]) for s in entries}
        for s in entries:  # fresh reference price for sizing
            if ticks.get(s.role):
                s.ref_price = ticks[s.role]["ask"] if s.intent == Intent.OPEN_LONG else ticks[s.role]["bid"]
        commission = float(self.cfg.get("risk", {}).get("commission_per_lot_side_estimate", 0.0))
        tps = strat.calculate_target_positions(
            entries, book.equity, self.specs, risk_pct_for(self.cfg, bot), self.risk.loss_at_stop,
            lambda spec: self.risk.est_cost_per_lot(spec, ticks.get(spec.role), commission),
            min_lot=self.cfg["risk"].get("min_lot", 0.0) or 0.0, max_risk_pct=float(self.cfg["risk"].get("max_trade_risk") or 0))
        approved: list[tuple] = []
        group = any(s.group_id for s in entries)
        for tp in tps:
            s = tp.signal
            spec = self.specs[s.role]
            p = Proposal(bot, s.role, spec.symbol, s.intent, tp.volume, s, magic_for(bot), sl=s.stop_price, tp=s.target_price,
                         planned_risk=tp.planned_risk, risk_budget=tp.risk_budget)
            if tp.skipped:
                self.db.x("INSERT INTO signals_log(ts,bot_id,mode,outcome,signal_json,explanation) VALUES(?,?,?,?,?,?)",
                          (now_iso(), bot, "LIVE" if live else "SHADOW", f"SKIPPED: {tp.skip_reason}", json.dumps(s.to_dict(), default=str),
                           strat.explain_signal(s)))
                out.append({"signal": s.intent.value, "outcome": f"SKIPPED: {tp.skip_reason}"})
                if group:
                    return out
                continue
            audit = self.auditor.assess(view, s.role, True, p.proposal_hash, acct.fingerprint or "", primary_tf=strat.primary_timeframe,
                                        proposal=p)
            risk = None
            outcome = f"AUDIT_{audit.decision.value}"
            if audit.decision == Decision.APPROVED:
                risk = self.risk.evaluate(p, audit.approval, self._risk_ctx(bot, acct, book, ds, pk, rep, spec, ticks.get(s.role)))
                outcome = f"RISK_{risk.decision.value}"
                if risk.decision == Decision.APPROVED:
                    approved.append((p, spec, audit, risk))
                    outcome = "APPROVED" if live else "SHADOW_APPROVED"
            elif audit.decision == Decision.DELAYED and audit.reassess_at:
                # Nothing is queued: at reassess time the strategy reruns on fresh data with fresh sizing.
                self.db.set_control(f"reassess:{bot}", audit.reassess_at.isoformat())
            elif audit.decision == Decision.MANUAL_REVIEW_REQUIRED:
                self.db.event("MANUAL_REVIEW", bot, {"symbol": spec.symbol, "reasons": audit.reasons})
            self._log_decisions(bot, p, audit, risk, "LIVE" if live else "SHADOW", outcome, strat)
            out.append({"signal": s.intent.value, "outcome": outcome, "audit": audit.decision.value,
                        "risk": risk.decision.value if risk else None})
        if not live or not approved:
            return out
        if group and len(approved) != len(tps):
            out.append({"group": "not all legs approved; nothing submitted"})
            return out
        done = []
        for p, spec, audit, risk in approved:
            r = self.executor.submit(p, spec, audit.approval, risk.approval)
            out.append({"signal": p.intent.value, "outcome": f"EXEC_{r.state}: {r.message}", "ticket": r.position_ticket})
            if r.ok:
                done.append(r)
            elif group:
                for row in self.db.q("SELECT * FROM positions WHERE group_id=? AND status='OPEN'", (p.signal.group_id,)):
                    out.append(self._close_position_row(row, "group leg failed; closing filled leg"))
                break
        return out

    def _close_position_row(self, row: dict, why: str) -> dict:
        """Operator/system-initiated risk-reducing close through auditor + risk + executor."""
        acct = check_account(self.ro)
        spec = next((s for s in self.specs.values() if s.symbol == row["symbol"]), None)
        if spec is None:
            return {"position": row["position_key"], "outcome": "unknown symbol"}
        intent = Intent.CLOSE_LONG if row["side"] == "LONG" else Intent.COVER_SHORT
        s = Signal(row["bot_id"], utc_now(), row["role"], spec.symbol, intent, why, {}, position_key=row["position_key"],
                   group_id=row["group_id"])
        p = Proposal(row["bot_id"], row["role"], spec.symbol, intent, float(row["volume_open"]), s, magic_for(row["bot_id"]),
                     position_key=row["position_key"], position_ticket=row["ticket"], kill=True)
        view = LiveMarketView(self, {}, utc_now())
        audit = self.auditor.assess(view, row["role"], False, p.proposal_hash, acct.fingerprint or "")
        if audit.decision != Decision.APPROVED:
            self.db.alert("HIGH", f"CLOSE_DELAYED_{row['ticket']}", f"Closing {row['symbol']} trade #{row['ticket']} was held back: {'; '.join(map(str, audit.reasons))}", blocks_entries=True)
            return {"position": row["position_key"], "outcome": f"AUDIT_{audit.decision.value}"}
        book = self.bot_book(row["bot_id"])
        class _R:  # minimal report stand-in for risk context
            clean = True
        risk = self.risk.evaluate(p, audit.approval, self._risk_ctx(row["bot_id"], acct, book, book.equity, book.equity, _R(), spec,
                                                                  self._tick(spec), owned={"volume": row["volume_open"]}))
        if risk.decision != Decision.APPROVED:
            return {"position": row["position_key"], "outcome": f"RISK_{risk.decision.value}: {risk.reasons}"}
        r = self.executor.submit(p, spec, audit.approval, risk.approval)
        return {"position": row["position_key"], "outcome": f"EXEC_{r.state}: {r.message}"}

    # ------------------------------------------------------------------- loop
    def run_forever(self):
        interval = self.cfg["execution"]["loop_interval_seconds"]
        while not self._stop.is_set():
            try:
                self.cycle()
                self.last_error = None
            except Exception as e:
                log.exception("cycle failed")
                self.last_error = str(e)
            self._stop.wait(interval)

    def stop(self):
        self._stop.set()
