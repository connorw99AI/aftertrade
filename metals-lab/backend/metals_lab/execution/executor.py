"""Demo execution. The only component holding a TradingGateway.

Order of operations for every submission:
  1. verify AUDIT and RISK approvals (signature, binding, expiry, single use);
  2. fresh account check: DEMO + same fingerprint as activation + hedging;
  3. duplicate prevention (same signal, or another order in flight for the bot/symbol);
  4. durable intent row written (state SUBMITTING) BEFORE order_send;
  5. order_check, then order_send (the gateway re-asserts DEMO immediately before sending);
  6. result handling: DONE / DONE_PARTIAL / rejection / no reply (UNKNOWN -> reconciliation);
  7. for openings: confirm the server-side stop exists; if it cannot be set the
     position is closed immediately - we do not keep unprotected positions.
"""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..domain import Intent
from ..instruments import InstrumentSpec
from ..mt5.gateway import LiveAccountBlocked, TradingGateway
from ..mt5.guard import check_account, execution_blockers
from ..store import Store, now_iso
from .approvals import ApprovalAuthority, ApprovalError, Proposal


@dataclass
class ExecResult:
    ok: bool
    state: str
    intent_id: Optional[str]
    message: str
    retcode: Optional[int] = None
    position_ticket: Optional[int] = None
    filled_volume: float = 0.0
    price: Optional[float] = None


def comment_tag(bot_id: str, intent_id: str) -> str:
    return f"ML{bot_id}-{intent_id[:10]}"  # <= 31 chars (MT5 comment limit)


class Executor:
    def __init__(self, gw: TradingGateway, store: Store, audit_auth: ApprovalAuthority, risk_auth: ApprovalAuthority,
                 cfg: dict):
        self.gw = gw
        self.db = store
        self.audit_auth = audit_auth
        self.risk_auth = risk_auth
        self.cfg = cfg["execution"]

    def _activation_fp(self) -> Optional[str]:
        a = self.db.active_activation()
        return a["fingerprint"] if a else None

    def submit(self, p: Proposal, spec: InstrumentSpec, audit_appr, risk_appr) -> ExecResult:
        now = _tnow()
        act_fp = self._activation_fp()
        acct = check_account(self.gw)
        if p.intent.is_risk_reducing and act_fp is None:
            # Exits of positions opened under an earlier activation stay possible after a
            # disarm/kill, but only on that same account.
            last = self.db.one("SELECT fingerprint FROM activation ORDER BY id DESC LIMIT 1")
            act_fp = last["fingerprint"] if last else None
        blockers = execution_blockers(acct, act_fp)
        if p.intent.is_risk_reducing:
            blockers = [b for b in blockers if not b.startswith(("ACCOUNT_NOT_HEDGING", "NOT_ARMED"))]
        if self.db.get_control("kill") == "1" and p.intent.is_opening:
            blockers.append("KILL_SWITCH_ACTIVE")
        if blockers:
            return ExecResult(False, "BLOCKED", None, "; ".join(blockers))
        try:
            self.audit_auth.verify(audit_appr, p.proposal_hash, act_fp, now)
            self.risk_auth.verify(risk_appr, p.proposal_hash, act_fp, now)
        except ApprovalError as e:
            return ExecResult(False, "BLOCKED", None, f"approval check failed: {e}")
        # duplicates
        if self.db.one("SELECT 1 FROM intents WHERE bot_id=? AND signal_id=?", (p.bot_id, p.signal.signal_id + (p.position_key or ""))):
            return ExecResult(False, "DUPLICATE", None, "signal already has an intent")
        inflight = self.db.one("SELECT intent_id FROM intents WHERE bot_id=? AND symbol=? AND state IN ('CREATED','SUBMITTING','UNKNOWN')",
                               (p.bot_id, p.symbol))
        if inflight:
            return ExecResult(False, "DUPLICATE", None, f"intent {inflight['intent_id']} still in flight")
        tick = self.gw.symbol_info_tick(spec.symbol)
        if tick is None:
            return ExecResult(False, "BLOCKED", None, "no tick")
        buy = p.intent.order_side == "BUY"
        price = tick["ask"] if buy else tick["bid"]
        intent_id = uuid.uuid4().hex
        tag = comment_tag(p.bot_id, intent_id)
        req = {
            "action": self.gw.const("TRADE_ACTION_DEAL"),
            "symbol": spec.symbol,
            "volume": float(p.volume),
            "type": self.gw.const("ORDER_TYPE_BUY") if buy else self.gw.const("ORDER_TYPE_SELL"),
            "price": price,
            "deviation": int(self.cfg["deviation_points"]),
            "magic": int(p.magic),
            "comment": tag,
            "type_time": self.gw.const("ORDER_TIME_GTC"),
            "type_filling": spec.preferred_filling(),
        }
        if p.intent.is_opening:
            req["sl"] = spec.round_price(p.sl) if p.sl else 0.0
            req["tp"] = spec.round_price(p.tp) if p.tp else 0.0
        else:
            req["position"] = int(p.position_ticket)
        # durable intent before submission
        with self.db.tx():
            # consuming inside the same transaction as the intent row makes reuse impossible
            self.db.conn.execute(
                "INSERT INTO intents(intent_id,bot_id,signal_id,intent,symbol,side,volume,position_key,group_id,state,comment_tag,"
                "proposal_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, p.bot_id, p.signal.signal_id + (p.position_key or ""), p.intent.value, p.symbol,
                 p.intent.order_side, p.volume, p.position_key, p.signal.group_id, "SUBMITTING", tag,
                 json.dumps(p.public(), default=str), now_iso(), now_iso()))
            for a in (audit_appr, risk_appr):
                cur = self.db.conn.execute("UPDATE approvals SET used_at=? WHERE approval_id=? AND used_at IS NULL AND revoked=0",
                                           (now_iso(), a.approval_id))
                if cur.rowcount != 1:
                    raise ApprovalError("approval consumed concurrently")
        try:
            chk = self.gw.order_check(req)
            if chk is not None and chk.get("retcode") not in (0, self.gw.const("TRADE_RETCODE_DONE")):
                self._set(intent_id, "REJECTED", retcode=chk.get("retcode"), error=f"order_check: {chk.get('comment')}")
                return ExecResult(False, "REJECTED", intent_id, f"order_check failed: {chk.get('comment')}", chk.get("retcode"))
            res = self.gw.order_send(req)
        except LiveAccountBlocked as e:
            self._set(intent_id, "REJECTED", error=f"LIVE_ACCOUNT_BLOCKED: {e}")
            self.db.disarm("live/unknown account detected at order boundary")
            self.db.alert("CRITICAL", "LIVE_ACCOUNT_AT_BOUNDARY", str(e), blocks_entries=True)
            return ExecResult(False, "BLOCKED", intent_id, str(e))
        except Exception as e:  # transport failure: the order may or may not exist
            self._set(intent_id, "UNKNOWN", error=f"exception during send: {e}")
            return ExecResult(False, "UNKNOWN", intent_id, f"send failed; reconciliation will resolve: {e}")
        if res is None:
            self._set(intent_id, "UNKNOWN", error=f"no reply: {self.gw.last_error()}")
            return ExecResult(False, "UNKNOWN", intent_id, "no reply from terminal; reconciliation will resolve")
        rc = res.get("retcode")
        done, partial = self.gw.const("TRADE_RETCODE_DONE"), self.gw.const("TRADE_RETCODE_DONE_PARTIAL")
        if rc not in (done, partial):
            self._set(intent_id, "REJECTED", retcode=rc, error=res.get("comment"))
            return ExecResult(False, "REJECTED", intent_id, f"retcode {rc}: {res.get('comment')}", rc)
        filled = float(res.get("volume") or 0.0)
        pos_ticket = self._position_for_deal(res.get("deal"), res.get("order")) if p.intent.is_opening else p.position_ticket
        state = "FILLED" if rc == done and filled + 1e-9 >= p.volume else "PARTIAL"
        self._set(intent_id, state, retcode=rc, mt5_order=res.get("order"), mt5_deal=res.get("deal"),
                  position_ticket=pos_ticket, filled_volume=filled, fill_price=res.get("price"))
        if p.intent.is_opening:
            self._record_open_position(p, spec, intent_id, pos_ticket, filled, res.get("price"))
            prot = self._ensure_protection(p, spec, pos_ticket)
            if not prot:
                return ExecResult(False, "UNPROTECTED_CLOSED", intent_id, "server-side stop could not be set; position closed",
                                  rc, pos_ticket, filled, res.get("price"))
        return ExecResult(True, state, intent_id, "ok", rc, pos_ticket, filled, res.get("price"))

    # ---------------------------------------------------------------- helpers
    def _set(self, intent_id: str, state: str, **kw) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.x(f"UPDATE intents SET state=?, updated_at=?{', ' + cols if cols else ''} WHERE intent_id=?",
                  (state, now_iso(), *kw.values(), intent_id))

    def _position_for_deal(self, deal_ticket, order_ticket) -> Optional[int]:
        now = _tnow()
        for d in self.gw.history_deals(now - timedelta(days=2), now + timedelta(days=2)):
            if d.get("ticket") == deal_ticket:
                return d.get("position_id")
        return order_ticket  # MT5: a position's identifier equals the ticket of the order that opened it

    def _record_open_position(self, p: Proposal, spec: InstrumentSpec, intent_id: str, ticket: int, vol: float, price) -> None:
        key = f"{p.bot_id}-{ticket}"
        self.db.x("INSERT OR IGNORE INTO positions(position_key,bot_id,ticket,symbol,role,side,volume_initial,volume_open,entry_price,"
                  "sl,tp,opened_at,status,group_id,reserved_margin,planned_risk,max_hold_bars,bars_timeframe,signal_json,intent_id) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (key, p.bot_id, ticket, spec.symbol, p.role, p.intent.position_side, vol, vol, price, p.sl, p.tp, now_iso(),
                   "OPEN", p.signal.group_id or key, p.est_margin, p.planned_risk, p.signal.max_hold_bars,
                   None, json.dumps(p.signal.to_dict(), default=str), intent_id))

    def verify_protection(self, rows: list[dict], specs: dict) -> list[dict]:
        """Every cycle: each open bot position must carry the stop-loss and take-profit it was opened with.
        A missing or changed level is put back; if that fails, entries are blocked until it is fixed."""
        out = []
        by_symbol: dict[str, dict] = {}
        for r in rows:
            spec = next((s for s in specs.values() if s.symbol == r["symbol"]), None)
            if spec is None:
                continue
            if r["symbol"] not in by_symbol:
                by_symbol[r["symbol"]] = {x.get("ticket"): x for x in self.gw.positions(r["symbol"])}
            live = by_symbol[r["symbol"]].get(r["ticket"])
            if live is None:
                continue  # closed in the terminal; reconciliation records it
            tol = spec.tick_size * 1.5 if spec.tick_size else 1e-9
            want_sl = spec.round_price(r["sl"]) if r["sl"] else 0.0
            want_tp = spec.round_price(r["tp"]) if r["tp"] else 0.0
            ok_sl = abs(float(live.get("sl") or 0.0) - want_sl) <= tol
            ok_tp = abs(float(live.get("tp") or 0.0) - want_tp) <= tol
            code = f"PROTECTION_{r['ticket']}"
            if ok_sl and ok_tp:
                self.db.resolve_alert(code)
                continue
            res = self.gw.order_send({"action": self.gw.const("TRADE_ACTION_SLTP"), "symbol": spec.symbol, "position": r["ticket"],
                                      "sl": want_sl, "tp": want_tp})
            done = bool(res and res.get("retcode") == self.gw.const("TRADE_RETCODE_DONE"))
            info = {"ticket": r["ticket"], "symbol": r["symbol"], "had_sl": live.get("sl"), "had_tp": live.get("tp"),
                    "set_sl": want_sl, "set_tp": want_tp, "restored": done}
            self.db.event("PROTECTION_RESTORED" if done else "PROTECTION_FAILED", r["bot_id"], info)
            if done:
                self.db.resolve_alert(code)
            else:
                self.db.alert("HIGH", code, f"{r['symbol']} trade #{r['ticket']} is missing its stop-loss or take-profit and the lab could not put it back. "
                              "Set it in MT5 or close the trade; new trades are blocked until then", blocks_entries=True)
            out.append(info)
        return out

    def _ensure_protection(self, p: Proposal, spec: InstrumentSpec, ticket: int) -> bool:
        if not p.sl:
            return True
        pos = [x for x in self.gw.positions(spec.symbol) if x.get("ticket") == ticket]
        if pos and pos[0].get("sl"):
            return True
        if pos:
            r = self.gw.order_send({"action": self.gw.const("TRADE_ACTION_SLTP"), "symbol": spec.symbol, "position": ticket,
                                    "sl": spec.round_price(p.sl), "tp": spec.round_price(p.tp) if p.tp else 0.0,
                                    "magic": int(p.magic)})
            if r and r.get("retcode") == self.gw.const("TRADE_RETCODE_DONE"):
                return True
            # Cannot protect -> close immediately (risk-reducing, still demo-guarded by the gateway).
            tick = self.gw.symbol_info_tick(spec.symbol)
            buy_close = p.intent == Intent.OPEN_SHORT
            self.gw.order_send({"action": self.gw.const("TRADE_ACTION_DEAL"), "symbol": spec.symbol, "volume": float(pos[0]["volume"]),
                                "type": self.gw.const("ORDER_TYPE_BUY") if buy_close else self.gw.const("ORDER_TYPE_SELL"),
                                "price": tick["ask"] if buy_close else tick["bid"], "position": ticket,
                                "deviation": int(self.cfg["deviation_points"]), "magic": int(p.magic),
                                "comment": "ML-unprotected-close", "type_time": self.gw.const("ORDER_TIME_GTC"),
                                "type_filling": spec.preferred_filling()})
            self.db.alert("HIGH", f"UNPROTECTED_{ticket}", f"The broker would not accept a stop-loss on the new {spec.symbol} trade (#{ticket}), so the lab closed it", blocks_entries=False)
            return False
        return True
