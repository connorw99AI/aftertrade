"""Idempotent reconciliation of internal state against actual MT5 records.

Run every cycle and at startup (restart recovery). It:
  * mirrors new MT5 deals (unique by deal ticket) and attributes them to bots
    by position ticket first, magic number second;
  * books each attributed deal's profit/commission/swap/fee to the owning bot;
  * updates open volumes from partial closes and detects full closes;
  * emits TRADE_CLOSED once per fully closed position / trade group (the only
    source of creature money popups) and TRADE_FINALIZED after a settle window;
  * resolves SUBMITTING/UNKNOWN intents from deals/positions (comment tag,
    then order ticket);
  * flags unknown positions/deals, manual trades and balance operations as
    blocking alerts until an operator acknowledges them.
"""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from ..ledger import Ledger
from ..mt5.gateway import ReadOnlyGateway
from ..store import Store, now_iso


@dataclass
class ReconcileReport:
    clean: bool = True
    issues: list[str] = field(default_factory=list)
    new_deals: int = 0
    closed_trades: list[str] = field(default_factory=list)
    resolved_intents: list[str] = field(default_factory=list)
    unrealized: dict = field(default_factory=dict)        # bot -> float
    reserved_margin: dict = field(default_factory=dict)   # bot -> float
    terminal_positions: list[dict] = field(default_factory=list)


class Reconciler:
    def __init__(self, gw: ReadOnlyGateway, store: Store, ledger: Ledger, bot_for_magic: Callable[[int], Optional[str]],
                 cfg: dict):
        self.gw = gw
        self.db = store
        self.ledger = ledger
        self.bot_for_magic = bot_for_magic
        self.cfg = cfg["execution"]

    def run(self, now: Optional[datetime] = None) -> ReconcileReport:
        now = now or _tnow()
        rep = ReconcileReport()
        positions = self.gw.positions()
        deals = self.gw.history_deals(now - timedelta(days=self.cfg["history_lookback_days"]), now + timedelta(days=2))
        if positions is None or deals is None:
            rep.clean = False
            rep.issues.append("terminal did not return positions/deals")
            return rep
        rep.terminal_positions = positions
        known = {r["ticket"]: r for r in self.db.q("SELECT * FROM positions")}
        # 1) intents awaiting resolution (crash/timeout recovery)
        self._resolve_intents(positions, deals, rep, now)
        known = {r["ticket"]: r for r in self.db.q("SELECT * FROM positions")}
        # 2) deals
        for d in sorted(deals, key=lambda x: (x.get("time", 0), x.get("ticket", 0))):
            if self.db.one("SELECT 1 FROM deals WHERE ticket=?", (d["ticket"],)):
                continue
            rep.new_deals += 1
            bot = None
            dtype = d.get("type")
            if dtype in (self.gw.const("DEAL_TYPE_BUY"), self.gw.const("DEAL_TYPE_SELL")):
                pos = known.get(d.get("position_id"))
                bot = pos["bot_id"] if pos else self.bot_for_magic(int(d.get("magic") or 0))
                if bot is None:
                    self.db.alert("HIGH", f"UNKNOWN_DEAL_{d['ticket']}",
                                  f"deal on {d.get('symbol')} not owned by any bot (manual trade or other EA)", blocks_entries=True)
                elif pos is None and d.get("entry") == self.gw.const("DEAL_ENTRY_IN"):
                    # Known magic but untracked position: adopt only if a matching intent exists.
                    if not self._adopt_from_intent(d, bot):
                        self.db.alert("HIGH", f"UNTRACKED_POSITION_{d.get('position_id')}",
                                      f"bot-magic deal for untracked position on {d.get('symbol')}", blocks_entries=True)
            else:
                # balance / credit / charge / correction / bonus: never auto-distributed.
                self.db.x("INSERT OR IGNORE INTO unallocated_events(ts,amount,deal_ticket,kind) VALUES(?,?,?,?)",
                          (now_iso(), float(d.get("profit") or 0.0), d["ticket"], f"DEAL_TYPE_{dtype}"))
                self.db.alert("MEDIUM", f"BALANCE_OP_{d['ticket']}",
                              f"account balance operation {float(d.get('profit') or 0):+.2f} detected; not allocated to any bot. "
                              "Acknowledge in the Risk centre.", blocks_entries=True)
            self.db.x("INSERT OR IGNORE INTO deals(ticket,order_ticket,position_id,bot_id,symbol,type,entry,volume,price,profit,"
                      "commission,swap,fee,magic,time,comment,attributed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (d["ticket"], d.get("order"), d.get("position_id"), bot, d.get("symbol"), dtype, d.get("entry"),
                       d.get("volume"), d.get("price"), d.get("profit"), d.get("commission"), d.get("swap"), d.get("fee"),
                       d.get("magic"), d.get("time"), d.get("comment"), int(bot is not None)))
            if bot:
                self.ledger.post_deal(bot, d)
                if d.get("entry") in (self.gw.const("DEAL_ENTRY_OUT"), self.gw.const("DEAL_ENTRY_OUT_BY"),
                                      self.gw.const("DEAL_ENTRY_INOUT")):
                    self.db.x("UPDATE positions SET volume_open=MAX(0, volume_open-?) WHERE ticket=?",
                              (float(d.get("volume") or 0), d.get("position_id")))
        # 3) positions present in terminal
        live_tickets = set()
        for p in positions:
            t = p["ticket"]
            live_tickets.add(t)
            row = self.db.one("SELECT * FROM positions WHERE ticket=?", (t,))
            if row is None:
                bot = self.bot_for_magic(int(p.get("magic") or 0))
                code = f"UNKNOWN_POSITION_{t}"
                self.db.alert("HIGH", code, f"open position on {p.get('symbol')} not owned by any bot"
                              + ("" if bot is None else f" (magic of {bot}, no intent)"), blocks_entries=True)
                rep.clean = False
                rep.issues.append(code)
                continue
            if row["status"] != "OPEN":
                rep.clean = False
                rep.issues.append(f"position {t} closed internally but open at terminal")
                continue
            self.db.x("UPDATE positions SET volume_open=?, sl=?, tp=? WHERE ticket=?", (p["volume"], p.get("sl"), p.get("tp"), t))
            b = row["bot_id"]
            rep.unrealized[b] = rep.unrealized.get(b, 0.0) + float(p.get("profit") or 0.0) + float(p.get("swap") or 0.0)
            rep.reserved_margin[b] = rep.reserved_margin.get(b, 0.0) + float(row["reserved_margin"] or 0.0)
        # 4) internally open, no longer at the terminal -> closed
        for row in self.db.q("SELECT * FROM positions WHERE status='OPEN'"):
            if row["ticket"] in live_tickets:
                continue
            outs = self.db.q("SELECT * FROM deals WHERE position_id=? AND entry IN (1,2,3)", (row["ticket"],))
            if not outs:
                rep.clean = False
                rep.issues.append(f"position {row['ticket']} missing at terminal with no closing deal yet")
                self.db.alert("HIGH", f"MISSING_CLOSE_{row['ticket']}", "position vanished without a closing deal", blocks_entries=True)
                continue
            self.db.x("UPDATE positions SET status='CLOSED', closed_at=?, volume_open=0 WHERE position_key=?",
                      (now_iso(), row["position_key"]))
            self.db.resolve_alert(f"MISSING_CLOSE_{row['ticket']}")
            self._maybe_close_group(row["group_id"], row["bot_id"], rep)
        # 5) finalise provisional trades after the settle window
        self._finalize(now)
        if self.db.blocking_alerts():
            rep.clean = False
            rep.issues.extend(a["code"] for a in self.db.blocking_alerts())
        return rep

    # ------------------------------------------------------------------ parts
    def _resolve_intents(self, positions, deals, rep, now):
        pending = self.db.q("SELECT * FROM intents WHERE state IN ('SUBMITTING','UNKNOWN')")
        for it in pending:
            match = [d for d in deals if (d.get("comment") or "") == it["comment_tag"]] or \
                    ([d for d in deals if it["mt5_order"] and d.get("order") == it["mt5_order"]])
            if match:
                d = match[0]
                vol = sum(float(x.get("volume") or 0) for x in match)
                state = "FILLED" if vol + 1e-9 >= it["volume"] else "PARTIAL"
                self.db.x("UPDATE intents SET state=?, position_ticket=?, filled_volume=?, fill_price=?, updated_at=? WHERE intent_id=?",
                          (state, d.get("position_id"), vol, d.get("price"), now_iso(), it["intent_id"]))
                if it["intent"] in ("OPEN_LONG", "OPEN_SHORT"):
                    self._insert_position_from_intent(it, d, vol)
                rep.resolved_intents.append(it["intent_id"])
                continue
            age = (now - datetime.fromisoformat(it["created_at"])).total_seconds()
            if age > self.cfg["intent_timeout_seconds"]:
                self.db.x("UPDATE intents SET state='REJECTED', error=COALESCE(error,'')||' | no trace at terminal after timeout', "
                          "updated_at=? WHERE intent_id=?", (now_iso(), it["intent_id"]))
                rep.resolved_intents.append(it["intent_id"])
            else:
                rep.clean = False
                rep.issues.append(f"intent {it['intent_id'][:8]} unresolved")

    def _insert_position_from_intent(self, it: dict, deal: dict, vol: float):
        prop = json.loads(it["proposal_json"] or "{}")
        sig = prop.get("signal", {})
        key = f"{it['bot_id']}-{deal.get('position_id')}"
        self.db.x("INSERT OR IGNORE INTO positions(position_key,bot_id,ticket,symbol,role,side,volume_initial,volume_open,entry_price,"
                  "sl,tp,opened_at,status,group_id,reserved_margin,planned_risk,max_hold_bars,signal_json,intent_id) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (key, it["bot_id"], deal.get("position_id"), it["symbol"], prop.get("role"),
                   "LONG" if it["intent"] == "OPEN_LONG" else "SHORT", vol, vol, deal.get("price"), prop.get("sl"), prop.get("tp"),
                   now_iso(), "OPEN", sig.get("group_id") or key, prop.get("est_margin"), prop.get("planned_risk"),
                   sig.get("max_hold_bars"), json.dumps(sig), it["intent_id"]))

    def _adopt_from_intent(self, d: dict, bot: str) -> bool:
        it = self.db.one("SELECT * FROM intents WHERE bot_id=? AND (comment_tag=? OR mt5_order=?)",
                         (bot, d.get("comment"), d.get("order")))
        if not it:
            return False
        self._insert_position_from_intent(it, d, float(d.get("volume") or 0))
        return True

    def _maybe_close_group(self, group_id: str, bot_id: str, rep: ReconcileReport):
        rows = self.db.q("SELECT * FROM positions WHERE group_id=? AND bot_id=?", (group_id, bot_id))
        if any(r["status"] == "OPEN" for r in rows):
            return  # trade group not complete yet: no popup
        if self.db.one("SELECT 1 FROM trades WHERE trade_id=?", (group_id,)):
            return
        tickets = [r["ticket"] for r in rows]
        ds = self.db.q(f"SELECT * FROM deals WHERE position_id IN ({','.join('?' * len(tickets))})", tickets)
        gross = sum(float(x["profit"] or 0) for x in ds)
        com = sum(float(x["commission"] or 0) for x in ds)
        swp = sum(float(x["swap"] or 0) for x in ds)
        fee = sum(float(x["fee"] or 0) for x in ds)
        net = gross + com + swp + fee
        first = rows[0]
        outs = [x for x in ds if x["entry"] in (1, 2, 3)]
        exit_price = outs[-1]["price"] if outs else None
        sig = json.loads(first["signal_json"] or "{}")
        reasons = set()
        for x in outs:
            c = x["comment"] or ""
            it = self.db.one("SELECT proposal_json FROM intents WHERE comment_tag=?", (c,)) if c.startswith("ML") else None
            if it:
                reasons.add(json.loads(it["proposal_json"] or "{}").get("signal", {}).get("rationale") or "strategy exit")
            elif "sl" in c.lower() or "tp" in c.lower():
                reasons.add(f"server-side stop/target ({c})")
            else:
                reasons.add(c or "closed outside the bot (no comment)")
        exit_reason = "; ".join(sorted(reasons)) or "closed"
        self.db.x("INSERT INTO trades(trade_id,bot_id,group_id,position_keys_json,symbol,side,volume,entry_time,exit_time,entry_price,"
                  "exit_price,gross,commission,swap,fee,net,provisional,entry_rationale,exit_rationale) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                  (group_id, bot_id, group_id, json.dumps([r["position_key"] for r in rows]),
                   ",".join(sorted({r["symbol"] for r in rows})), ",".join(r["side"] for r in rows),
                   sum(float(r["volume_initial"] or 0) for r in rows), first["opened_at"], now_iso(), first["entry_price"],
                   exit_price, gross, com, swp, fee, net, sig.get("rationale"), exit_reason))
        self.db.event("TRADE_CLOSED", bot_id, {"trade_id": group_id, "net": round(net, 2), "gross": round(gross, 2),
                                               "commission": round(com, 2), "swap": round(swp, 2), "fee": round(fee, 2),
                                               "legs": len(rows), "provisional": True,
                                               "note": "provisional until settle window passes with no further deals"})
        rep.closed_trades.append(group_id)

    def _finalize(self, now: datetime):
        settle = timedelta(seconds=self.cfg["settle_seconds"])
        for t in self.db.q("SELECT * FROM trades WHERE provisional=1"):
            if now - datetime.fromisoformat(t["exit_time"]) < settle:
                continue
            keys = json.loads(t["position_keys_json"])
            tickets = [r["ticket"] for r in self.db.q(f"SELECT ticket FROM positions WHERE position_key IN ({','.join('?' * len(keys))})", keys)]
            ds = self.db.q(f"SELECT * FROM deals WHERE position_id IN ({','.join('?' * len(tickets))})", tickets)
            net = sum(float(x["profit"] or 0) + float(x["commission"] or 0) + float(x["swap"] or 0) + float(x["fee"] or 0) for x in ds)
            changed = abs(net - float(t["net"])) > 1e-6
            self.db.x("UPDATE trades SET provisional=0, finalized_at=?, net=? WHERE trade_id=?", (now_iso(), net, t["trade_id"]))
            self.db.event("TRADE_FINALIZED", t["bot_id"], {"trade_id": t["trade_id"], "net": round(net, 2), "adjusted": changed})
