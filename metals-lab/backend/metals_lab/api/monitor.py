"""Read models for the monitoring pages: account summary, open positions, activity, history and the live price stream.

Everything here is read-only except the manual-close endpoint, which routes every close through the same
auditor -> risk -> executor pipeline as the bots' own exits.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..execution.coordinator import BOT_IDS
from ..store import now_iso
from ..strategies import bot_class, bot_name, is_bot
from ..timeutil import utc_now


class CloseBody(BaseModel):
    scope: str = Field(pattern="^(position|bot|all)$")
    target: Optional[str] = None          # position_key for "position", bot id for "bot"
    operator: str = Field(min_length=1)
    reason: str = "manual close from dashboard"


def _today() -> str:
    return utc_now().strftime("%Y-%m-%d")


def _planned_risk_by_key(db) -> dict[str, float]:
    return {r["position_key"]: float(r["planned_risk"] or 0) for r in db.q("SELECT position_key, planned_risk FROM positions")}


def trade_r(t: dict, risk_by_key: dict[str, float]) -> Optional[float]:
    """Result in R: net P&L divided by what the trade planned to risk at its stop."""
    keys = json.loads(t.get("position_keys_json") or "[]")
    planned = sum(risk_by_key.get(k, 0.0) for k in keys)
    return round(t["net"] / planned, 2) if planned > 0 and t.get("net") is not None else None


def register(app: FastAPI, state, auth) -> None:
    db = state.db

    def coord():
        if state.coord is None:
            raise HTTPException(503, "MT5 terminal not connected (research-only mode)")
        return state.coord

    # ------------------------------------------------------------- summary
    @app.get("/api/summary")
    def summary():
        """Account strip plus one compact row per bot for the command centre."""
        c = state.coord
        info = (c.ro.account_info() or {}) if c else {}
        day = _today()
        bots, today_total = [], 0.0
        thinking = c.thinking() if c else {}
        for b in BOT_IDS:
            cls = bot_class(b)
            row = db.one("SELECT status, pause_reason FROM bots WHERE bot_id=?", (b,))
            book = c.bot_book(b) if c else None
            ds = db.one("SELECT equity FROM bot_day_start WHERE bot_id=? AND day=?", (b, day))
            eq = book.equity if book else 0.0
            today = round(eq - ds["equity"], 2) if ds and book else 0.0
            today_total += today
            today_pct = (today / ds["equity"]) if ds and ds["equity"] else 0.0
            rk = state.cfg["risk"]
            stopped_for_day = None
            if rk.get("daily_loss_pause") and today_pct <= -rk["daily_loss_pause"]:
                stopped_for_day = "daily loss stop"
            elif rk.get("daily_profit_stop") and today_pct >= rk["daily_profit_stop"]:
                stopped_for_day = "daily profit stop"
            th = thinking.get(b) or {}
            verdicts = {role: {"verdict": r.get("verdict"), "summary": r.get("summary")} for role, r in (th.get("roles") or {}).items()}
            opens = db.q("SELECT position_key, symbol, role, side, volume_open, entry_price, sl, tp, opened_at FROM positions "
                         "WHERE bot_id=? AND status='OPEN'", (b,))
            for o in opens:  # the checklist is per completed bar; an open trade is known immediately
                verdicts[o["role"]] = {"verdict": f"IN_TRADE_{o['side']}", "summary": f"in a {o['side'].lower()} trade"}
            bots.append({
                "bot_id": b, "name": bot_name(b), "side": b[3], "timeframe": cls.primary_timeframe, "style": cls.style.value,
                "status": row["status"], "pause_reason": row["pause_reason"],
                "equity": round(eq, 2), "allocation": round(book.allocation, 2) if book else 0.0,
                "unrealized": round(book.unrealized, 2) if book else 0.0, "shadow": bool(book.shadow) if book else False,
                "today_pnl": today, "today_pct": round(today_pct * 100, 2), "stopped_for_day": stopped_for_day,
                "entries_today": c.entries_today(b) if c else 0,
                "max_entries_per_day": int(rk.get("max_entries_per_bot_per_day", 0) or 0),
                "open_positions": opens, "verdicts": verdicts, "thinking_as_of": th.get("bar_time"),
                "metals": (c.strategy(b).p.get("metals") or "both") if c else "both",
            })
        alerts = db.q("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY id DESC")
        bots.sort(key=lambda x: x["status"] == "RETIRED")  # retired halves go last (stable: otherwise in bot order)
        return {
            "account": {k: info.get(k) for k in ("balance", "equity", "margin", "margin_free", "margin_level", "currency")},
            "armed": db.active_activation() is not None,
            "kill": db.get_control("kill") == "1", "global_pause": db.get_control("global_pause") == "1",
            "today_pnl": round(today_total, 2),
            "open_trades": sum(len(b["open_positions"]) for b in bots),
            "counts": {s: sum(1 for b in bots if b["status"] == s) for s in ("RUNNING", "PAUSED", "PAUSED_RISK", "STOPPED", "RETIRED")},
            "stopped_for_day": sum(1 for b in bots if b["stopped_for_day"]),
            "alerts": alerts, "bots": bots, "server_time": now_iso(),
        }

    # ----------------------------------------------------------- positions
    def live_positions() -> list[dict]:
        c = coord()
        rows = db.q("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at")
        out = []
        with c._lock:
            live = {p.get("ticket"): p for p in c.ro.positions()}
            ticks = {s.symbol: c.ro.symbol_info_tick(s.symbol) for s in c.specs.values()}
            for r in rows:
                spec = next((s for s in c.specs.values() if s.symbol == r["symbol"]), None)
                lp = live.get(r["ticket"]) or {}
                tk = ticks.get(r["symbol"]) or {}
                long = r["side"] == "LONG"
                price = tk.get("bid") if long else tk.get("ask")
                d = {k: r[k] for k in ("position_key", "bot_id", "ticket", "symbol", "role", "side", "volume_open", "entry_price",
                                       "sl", "tp", "opened_at", "group_id", "planned_risk")}
                d["bot_name"] = bot_name(r["bot_id"])
                d["price"] = price
                d["profit"] = lp.get("profit")
                d["swap"] = lp.get("swap")
                d["in_terminal"] = bool(lp)
                d["terminal_sl"], d["terminal_tp"] = lp.get("sl"), lp.get("tp")
                tol = (spec.tick_size * 1.5) if spec and spec.tick_size else 1e-9
                d["protection_ok"] = bool(lp) and (not r["sl"] or abs(float(lp.get("sl") or 0) - float(r["sl"])) <= tol) and \
                    (not r["tp"] or abs(float(lp.get("tp") or 0) - float(r["tp"])) <= tol)
                if spec and price is not None:
                    otype = c.ro.const("ORDER_TYPE_BUY") if long else c.ro.const("ORDER_TYPE_SELL")
                    for lvl, key in ((r["sl"], "to_sl"), (r["tp"], "to_tp")):
                        if lvl:
                            v = c.ro.order_calc_profit(otype, spec.symbol, float(r["volume_open"]), float(price), float(lvl))
                            d[key] = {"price_distance": round(abs(price - lvl), spec.digits), "money": round(v, 2) if v is not None else None}
                opened = datetime.fromisoformat(r["opened_at"])
                d["minutes_open"] = int((utc_now() - opened).total_seconds() // 60)
                out.append(d)
        return out

    @app.get("/api/positions")
    def positions():
        return {"positions": live_positions(), "server_time": now_iso()}

    @app.post("/api/positions/close", dependencies=[Depends(auth)])
    def close_positions(body: CloseBody):
        c = coord()
        if body.scope == "position":
            rows = db.q("SELECT * FROM positions WHERE status='OPEN' AND position_key=?", (body.target,))
        elif body.scope == "bot":
            if body.target not in BOT_IDS:
                raise HTTPException(404, "unknown bot")
            rows = db.q("SELECT * FROM positions WHERE status='OPEN' AND bot_id=?", (body.target,))
        else:
            rows = db.q("SELECT * FROM positions WHERE status='OPEN'")
        if not rows:
            raise HTTPException(404, "no matching open positions")
        return {"results": c.close_positions(rows, body.reason, body.operator)}

    # ------------------------------------------------------------ activity
    @app.get("/api/activity")
    def activity(bot: Optional[str] = None, limit: int = 200):
        """Decision feed: every signal with its outcome, plus system events, newest first."""
        lim = max(10, min(limit, 1000))
        where, args = ("WHERE bot_id=?", (bot,)) if bot else ("", ())
        sigs = db.q(f"SELECT id, ts, bot_id, mode, outcome, signal_json, explanation FROM signals_log {where} ORDER BY id DESC LIMIT ?",
                    args + (lim,))
        feed = []
        for s in sigs:
            sig = json.loads(s["signal_json"] or "{}")
            feed.append({"kind": "decision", "ts": s["ts"], "bot_id": s["bot_id"], "mode": s["mode"], "outcome": s["outcome"],
                         "intent": sig.get("intent"), "role": sig.get("role"), "rationale": sig.get("rationale"),
                         "stage": _stage(s["outcome"])})
        evs = db.q(f"SELECT id, ts, kind, bot_id, payload_json FROM events {where} ORDER BY id DESC LIMIT ?", args + (lim,))
        for e in evs:
            feed.append({"kind": "event", "ts": e["ts"], "bot_id": e["bot_id"], "event": e["kind"],
                         "payload": json.loads(e["payload_json"] or "{}")})
        feed.sort(key=lambda x: x["ts"] or "", reverse=True)
        return {"feed": feed[:lim], "funnel": funnel(days=1), "funnel_7d": funnel(days=7),
                "alerts": db.q("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY id DESC")}

    def funnel(days: int = 1) -> list[dict]:
        """Per bot: entry signals -> skipped at sizing -> held by auditor -> rejected by risk -> approved -> filled."""
        since = (utc_now() - timedelta(days=days)).isoformat()
        out = []
        for b in BOT_IDS:
            rows = db.q("SELECT outcome, signal_json FROM signals_log WHERE bot_id=? AND ts>=?", (b, since))
            opens = [r for r in rows if str(json.loads(r["signal_json"] or "{}").get("intent", "")).startswith("OPEN")]
            stages = {"signals": len(opens), "skipped_sizing": 0, "auditor_held": 0, "risk_rejected": 0, "approved": 0}
            for r in opens:
                st = _stage(r["outcome"])
                if st in stages:
                    stages[st] += 1
            stages["filled"] = db.one("SELECT COUNT(*) n FROM intents WHERE bot_id=? AND intent LIKE 'OPEN%' AND state IN "
                                      "('FILLED','PARTIAL') AND created_at>=?", (b, since))["n"]
            reasons: dict[str, int] = {}
            for r in db.q("SELECT reasons_json FROM risk_decisions WHERE bot_id=? AND ts>=? AND decision='REJECTED' "
                          "AND intent LIKE 'OPEN%'", (b, since)):
                for x in json.loads(r["reasons_json"] or "[]"):
                    k = str(x).split(" ")[0].split(":")[0]
                    reasons[k] = reasons.get(k, 0) + 1
            for r in db.q("SELECT triggered_json FROM audit_decisions WHERE bot_id=? AND ts>=? AND decision!='APPROVED' "
                          "AND intent LIKE 'OPEN%'", (b, since)):
                for x in json.loads(r["triggered_json"] or "[]"):
                    reasons[x] = reasons.get(x, 0) + 1
            for r in opens:
                if _stage(r["outcome"]) == "skipped_sizing":
                    k = r["outcome"].replace("SKIPPED: ", "").split(" ")[0]
                    reasons[k] = reasons.get(k, 0) + 1
            top = sorted(reasons.items(), key=lambda kv: -kv[1])[:5]
            out.append({"bot_id": b, "name": bot_name(b), **stages, "top_reasons": top})
        return out

    # ------------------------------------------------------------- history
    def history_rows(bot: Optional[str], symbol: Optional[str], since: Optional[str], until: Optional[str]) -> list[dict]:
        q, args = "SELECT * FROM trades WHERE 1=1", []
        for col, op, v in (("bot_id", "=", bot), ("symbol", "=", symbol), ("exit_time", ">=", since), ("exit_time", "<=", until)):
            if v:
                q += f" AND {col}{op}?"
                args.append(v)
        rows = db.q(q + " ORDER BY exit_time DESC LIMIT 5000", tuple(args))
        rk = _planned_risk_by_key(db)
        pos = {p["position_key"]: p for p in db.q("SELECT * FROM positions")}
        for r in rows:
            r["r_multiple"] = trade_r(r, rk)
            legs = []
            for key in json.loads(r.get("position_keys_json") or "[]"):
                p = pos.get(key)
                if not p:
                    continue
                ex = db.one("SELECT SUM(price*volume)/SUM(volume) px FROM deals WHERE position_id=? AND entry IN (1,3)", (p["ticket"],))
                exit_px = ex["px"] if ex and ex["px"] is not None else None
                move = None
                if exit_px is not None and p["entry_price"] is not None:
                    move = (exit_px - p["entry_price"]) if p["side"] == "LONG" else (p["entry_price"] - exit_px)
                legs.append({"symbol": p["symbol"], "side": p["side"], "volume": p["volume_initial"], "entry_price": p["entry_price"],
                             "exit_price": exit_px, "sl": p["sl"], "tp": p["tp"], "opened_at": p["opened_at"], "closed_at": p["closed_at"],
                             "price_move": round(move, 5) if move is not None else None, "planned_risk": p["planned_risk"]})
            r["legs"] = legs
            r["bot_name"] = bot_name(r["bot_id"]) if is_bot(r["bot_id"]) else r["bot_id"]
            if r.get("entry_time") and r.get("exit_time"):
                r["minutes_held"] = int((datetime.fromisoformat(r["exit_time"]) - datetime.fromisoformat(r["entry_time"])).total_seconds() // 60)
        return rows

    @app.get("/api/history")
    def history(bot: Optional[str] = None, symbol: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None):
        rows = history_rows(bot, symbol, since, until)
        nets = [r["net"] or 0.0 for r in rows]
        return {"trades": rows, "totals": {"count": len(rows), "net": round(sum(nets), 2),
                                           "wins": sum(1 for n in nets if n > 0), "losses": sum(1 for n in nets if n < 0)}}

    @app.get("/api/history.csv")
    def history_csv(bot: Optional[str] = None, symbol: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None):
        rows = history_rows(bot, symbol, since, until)
        cols = ["trade_id", "bot_id", "bot_name", "symbol", "side", "volume", "entry_time", "exit_time", "minutes_held",
                "entry_price", "exit_price", "gross", "commission", "swap", "fee", "net", "r_multiple", "provisional",
                "entry_rationale", "exit_rationale"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=metals_lab_trades.csv"})

    # ---------------------------------------------------------- live stream
    @app.get("/api/stream")
    async def stream(interval_ms: int = 1000):
        """Server-sent events: one message per interval with each metal's bid/ask. Read-only; skips a beat
        instead of waiting when the trading loop is using the terminal."""
        c = coord()
        wait = max(0.25, min(interval_ms / 1000, 5.0))

        def snapshot() -> Optional[dict]:
            if not c._lock.acquire(timeout=0.2):
                return None
            try:
                out = {}
                for role, s in c.specs.items():
                    t = c.ro.symbol_info_tick(s.symbol)
                    if t:
                        out[role] = {"bid": t.get("bid"), "ask": t.get("ask"), "symbol": s.symbol}
                return {"ticks": out, "server_time": now_iso()}
            finally:
                c._lock.release()

        async def gen():
            while True:
                snap = await asyncio.to_thread(snapshot)
                if snap is not None:
                    yield f"data: {json.dumps(snap, default=str)}\n\n"
                await asyncio.sleep(wait)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _stage(outcome: Optional[str]) -> str:
    o = outcome or ""
    if o.startswith("SKIPPED"):
        return "skipped_sizing"
    if o.startswith("AUDIT_"):
        return "auditor_held"
    if o.startswith("RISK_"):
        return "risk_rejected"
    if o in ("APPROVED", "SHADOW_APPROVED") or o.startswith("EXEC_"):
        return "approved"
    return "other"
