"""FastAPI backend for the creature dashboard.

Security posture: binds to 127.0.0.1 by default. If an API token env var is set,
every mutating request must send it as ``X-API-Token``. There is deliberately NO
endpoint that arms execution - arming is a separate manual CLI command.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..data.store import DatasetStore
from ..execution.coordinator import BOT_IDS, Coordinator
from ..mt5.guard import check_account
from ..store import Store, now_iso
from ..strategies import BOT_IDS as _ALL_BOTS, bot_class, bot_name, build, magic_for  # noqa: F401


class CleanJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        from ..jsonutil import clean
        return super().render(clean(content))


class State:
    def __init__(self, cfg: dict, store: Store, coord: Optional[Coordinator], terminal_kind: str):
        self.cfg, self.db, self.coord, self.terminal_kind = cfg, store, coord, terminal_kind
        self.datasets = DatasetStore(Path(cfg["storage"]["cache_dir"]))
        self.jobs: dict[int, str] = {}


class StatusBody(BaseModel):
    reason: str = ""
    operator: str = "dashboard"


class ParamsBody(BaseModel):
    params: dict[str, Any]
    operator: str = Field(min_length=1)


class ArmBody(BaseModel):
    phrase: str
    operator: str = Field(min_length=1)


class DisarmBody(BaseModel):
    reason: str = Field(min_length=3)
    operator: str = Field(min_length=1)


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class AlertBody(BaseModel):
    message: str = Field(min_length=3)
    operator: str = "dashboard"


def create_app(state: State) -> FastAPI:
    app = FastAPI(title="Metals Creature Lab", version="0.1.0", default_response_class=CleanJSONResponse)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["*"], allow_headers=["*"])
    token = os.environ.get(state.cfg["api"]["token_env"])

    def auth(x_api_token: Optional[str] = Header(default=None)):
        if token and x_api_token != token:
            raise HTTPException(401, "missing or wrong X-API-Token")

    db = state.db

    def coord() -> Coordinator:
        if state.coord is None:
            raise HTTPException(503, "MT5 terminal not connected (research-only mode)")
        return state.coord

    # ------------------------------------------------------------------ status
    # ------------------------------------------------------------------ arming
    def arm_preview_data() -> dict:
        c = coord()
        acct = check_account(c.ro)
        snap = c.ledger.snapshot(acct.fingerprint) if acct.fingerprint else None
        bal = float(snap["starting_balance"]) if snap else float(acct.balance or 0)
        frac = float(snap["bot_fraction"]) if snap else float(state.cfg["capital"]["bot_fraction"])
        problems = c.preflight()
        if db.get_control("kill") == "1":
            problems.append("KILL_SWITCH_ON (clear it from the command line: metals-lab clear-kill)")
        return {"armed": c.is_armed(), "account": acct.public(), "problems": problems,
                "phrase": f"ARM DEMO {(acct.fingerprint or '')[:6]}", "snapshot_exists": bool(snap),
                "starting_balance": round(bal, 2), "per_bot": round(bal * frac, 2), "bots": len(BOT_IDS),
                "running": db.one("SELECT COUNT(*) n FROM bots WHERE status='RUNNING'")["n"]}

    @app.get("/api/arm/preview")
    def arm_preview():
        """What arming would do, and anything that would stop it. Changes nothing."""
        return arm_preview_data()

    @app.post("/api/arm", dependencies=[Depends(auth)])
    def arm(body: ArmBody, request: Request):
        """Arm automatic DEMO trading from the dashboard: only from this PC, only a verified demo account, only with the
        typed phrase (the same as the command line), never while the kill switch is on. Logged with the operator's name."""
        host = request.client.host if request.client else ""
        if host not in LOCAL_HOSTS:
            raise HTTPException(403, "arming is only allowed from the PC running Metal Lab")
        c = coord()
        pv = arm_preview_data()
        if pv["problems"]:
            raise HTTPException(409, "refusing to arm: " + "; ".join(pv["problems"]))
        if body.phrase.strip() != pv["phrase"]:
            raise HTTPException(409, "the confirmation phrase does not match; not armed")
        try:
            out = c.arm(body.operator, source="dashboard")
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {**out, "armed": True}

    @app.post("/api/disarm", dependencies=[Depends(auth)])
    def disarm(body: DisarmBody):
        """Stop all new automatic trading (open trades stay managed until you close them). Always allowed."""
        c = coord()
        c.disarm(body.reason, body.operator)
        return {"armed": c.is_armed()}

    @app.get("/api/status")
    def status():
        c = state.coord
        acct = check_account(c.ro).public() if c else {"state": "DISCONNECTED", "reason": "no terminal backend"}
        act = db.active_activation()
        return {
            "terminal_kind": state.terminal_kind,
            "account": acct,
            "armed": act is not None,
            "armed_since": act["armed_at"] if act else None,
            "armed_fingerprint": act["fingerprint"][:12] if act else None,
            "kill": db.get_control("kill") == "1",
            "global_pause": db.get_control("global_pause") == "1",
            "blockers": c.preflight() if c else ["NO_TERMINAL"],
            "last_cycle": c.last_cycle if c else None,
            "last_error": c.last_error if c else None,
            "reconciliation": c.last_reconcile if c else None,
            "clock": c.clock.source if c else None,
            "alerts": db.q("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY id DESC"),
            "server_time": now_iso(),
        }

    @app.get("/api/instruments")
    def instruments():
        c = state.coord
        return {"discovery": c.discovery if c else None,
                "sessions": {r: cal.public() for r, cal in (c.sessions.items() if c else [])}}

    # ----------------------------------------------------------------- thinking
    @app.get("/api/thinking")
    def thinking():
        """Each bot's latest entry checklist (what it sees on its last completed bar)."""
        c = state.coord
        return c.thinking() if c else {}

    # ------------------------------------------------------------------- charts
    @app.get("/api/chart")
    def chart(role: str = "GOLD", tf: str = "M15", bars: int = 150):
        """Read-only candles for one metal plus every bot's open positions on it.

        The last candle is the forming bar (marked ``forming``)."""
        from ..data.history import TF_CONST, fetch_recent
        c = coord()
        role, tf = role.upper(), tf.upper()
        if role not in c.specs:
            raise HTTPException(404, f"{role} not resolved in the terminal")
        if tf not in TF_CONST:
            raise HTTPException(400, f"timeframe must be one of {sorted(TF_CONST)}")
        spec = c.specs[role]
        n = max(20, min(int(bars), 500))
        with c._lock:  # serialise terminal access with the trading loop
            try:
                df = fetch_recent(c.ro, c.clock, spec, tf, n)
            except ValueError as e:
                raise HTTPException(503, f"server clock not set: {e}")
            tick = c.ro.symbol_info_tick(spec.symbol)
        candles = [{"t": r.time.isoformat(), "o": r.open, "h": r.high, "l": r.low, "c": r.close}
                   for r in df.itertuples(index=False)]
        if candles:
            candles[-1]["forming"] = True
        positions = [{"bot_id": p["bot_id"], "side": p["side"], "volume": p["volume_open"], "entry": p["entry_price"],
                      "sl": p["sl"], "tp": p["tp"], "opened_at": p["opened_at"]}
                     for p in db.q("SELECT * FROM positions WHERE status='OPEN' AND symbol=?", (spec.symbol,))]
        return {"role": role, "symbol": spec.symbol, "timeframe": tf, "digits": spec.digits, "candles": candles,
                "bid": tick["bid"] if tick else None, "ask": tick["ask"] if tick else None, "positions": positions}

    # -------------------------------------------------------------------- bots
    def bot_summary(bot: str) -> dict:
        cls = bot_class(bot)
        row = db.one("SELECT * FROM bots WHERE bot_id=?", (bot,))
        book = state.coord.bot_book(bot) if state.coord else None
        trades = db.q("SELECT * FROM trades WHERE bot_id=? ORDER BY exit_time", (bot,))
        nets = [t["net"] for t in trades]
        alloc = book.allocation if book else 0.0
        eq_series = [alloc]
        for n in nets:
            eq_series.append(eq_series[-1] + n)
        peak, mdd = alloc, 0.0
        for e in eq_series:
            peak = max(peak, e)
            if peak > 0:
                mdd = min(mdd, (e - peak) / peak)
        last_sig = db.one("SELECT * FROM signals_log WHERE bot_id=? ORDER BY id DESC LIMIT 1", (bot,))
        last_audit = db.one("SELECT * FROM audit_decisions WHERE bot_id=? ORDER BY id DESC LIMIT 1", (bot,))
        last_risk = db.one("SELECT * FROM risk_decisions WHERE bot_id=? ORDER BY id DESC LIMIT 1", (bot,))
        open_pos = db.q("SELECT * FROM positions WHERE bot_id=? AND status='OPEN'", (bot,))
        from ..risk import risk_pct_for
        risk_pct = risk_pct_for(state.cfg, bot)
        return {
            "bot_id": bot, "name": cls.name, "style": cls.style.value, "roles": list(cls.roles),
            "timeframe": cls.primary_timeframe,
            "symbols": [state.coord.specs[r].symbol for r in cls.roles if state.coord and r in state.coord.specs],
            "status": row["status"], "pause_reason": row["pause_reason"], "config_version": row["config_version"],
            "magic": magic_for(bot),
            "book": book.public() if book else None,
            "risk_budget_per_trade": round(book.equity * risk_pct, 2) if book else None,
            "risk_pct": risk_pct,
            "min_lot": state.cfg["risk"].get("min_lot", 0.0) or 0.0,
            "per_trade_limit_enabled": bool(state.cfg["risk"].get("per_trade_limit_enabled", True)),
            "entries_today": state.coord.entries_today(bot) if state.coord else None,
            "max_entries_per_day": int(state.cfg["risk"].get("max_entries_per_bot_per_day", 0) or 0),
            "open_positions": open_pos,
            "closed_trades": len(trades),
            "closed_net_pnl": round(sum(nets), 2),
            "provisional_trades": sum(1 for t in trades if t["provisional"]),
            "return_pct": round((book.equity / alloc - 1) * 100, 3) if book and alloc else None,
            "max_drawdown_closed_pct": round(mdd * 100, 3),
            "sample_size_note": None if len(trades) >= 30 else f"{len(trades)} closed trade{'' if len(trades) == 1 else 's'}: too few to judge",
            "last_signal": {**last_sig, "signal": json.loads(last_sig["signal_json"])} if last_sig else None,
            "last_audit": last_audit, "last_risk": last_risk,
        }

    @app.get("/api/bots")
    def bots():
        return [bot_summary(b) for b in BOT_IDS]

    @app.get("/api/bots/{bot}")
    def bot(bot: str):
        if bot not in BOT_IDS:
            raise HTTPException(404)
        d = bot_summary(bot)
        row = db.one("SELECT params_json FROM bots WHERE bot_id=?", (bot,))
        d["strategy"] = build(bot, json.loads(row["params_json"] or "{}")).describe()
        d["thinking"] = state.coord.thinking().get(bot) if state.coord else None
        # each parameter save is one new rule version; version 1 is the bot as first created
        changes = db.q("SELECT operator, created_at FROM config_audit WHERE bot_id=? AND action='PARAMS' ORDER BY id", (bot,))
        d["rule_changes"] = [{"version": i + 2, "at": r["created_at"], "operator": r["operator"]} for i, r in enumerate(changes)]
        return d

    @app.get("/api/bots/{bot}/trades")
    def bot_trades(bot: str):
        return db.q("SELECT * FROM trades WHERE bot_id=? ORDER BY exit_time DESC", (bot,))

    @app.get("/api/bots/{bot}/signals")
    def bot_signals(bot: str, limit: int = 50):
        return db.q("SELECT * FROM signals_log WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot, limit))

    @app.get("/api/bots/{bot}/decisions")
    def bot_decisions(bot: str, limit: int = 50):
        return {"audit": db.q("SELECT * FROM audit_decisions WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot, limit)),
                "risk": db.q("SELECT * FROM risk_decisions WHERE bot_id=? ORDER BY id DESC LIMIT ?", (bot, limit))}

    @app.get("/api/bots/{bot}/ledger")
    def bot_ledger(bot: str):
        return db.q("SELECT * FROM ledger WHERE bot_id=? ORDER BY id DESC LIMIT 500", (bot,))

    @app.post("/api/bots-all/{action}", dependencies=[Depends(auth)])
    def bots_all(action: str, body: StatusBody):
        """Start all / pause all (every non-retired half) and un-retire all, from the Overview."""
        if action == "unretire":
            return {"unretired": coord().unretire_all(body.operator)}
        if action not in ("start", "pause"):
            raise HTTPException(404)
        if action == "start" and db.get_control("kill") == "1":
            raise HTTPException(409, "kill switch is on; clear it first (metals-lab clear-kill), then start the bots")
        changed = []
        for b in BOT_IDS:
            st = db.one("SELECT status FROM bots WHERE bot_id=?", (b,))["status"]
            if action == "start" and st not in ("RUNNING", "RETIRED"):
                coord().set_bot_status(b, "RUNNING", body.reason or "start all", body.operator)
                changed.append(b)
            elif action == "pause" and st == "RUNNING":
                coord().set_bot_status(b, "PAUSED", body.reason or "pause all", body.operator)
                changed.append(b)
        if action == "start":
            db.set_control("global_pause", "0")
        return {"changed": changed}

    @app.post("/api/bots/{bot}/{action}", dependencies=[Depends(auth)])
    def bot_action(bot: str, action: str, body: StatusBody):
        if bot not in BOT_IDS or action not in ("start", "pause", "stop", "retire", "unretire"):
            raise HTTPException(404)
        if action in ("retire", "unretire"):
            try:
                return coord().retire(bot, body.reason, body.operator) if action == "retire" else coord().unretire(bot, body.operator)
            except RuntimeError as e:
                raise HTTPException(409, str(e))
        if db.one("SELECT status FROM bots WHERE bot_id=?", (bot,))["status"] == "RETIRED" and action == "start":
            raise HTTPException(409, "this half is retired; un-retire it first")
        st = {"start": "RUNNING", "pause": "PAUSED", "stop": "STOPPED"}[action]
        if action == "stop" and db.one("SELECT 1 FROM positions WHERE bot_id=? AND status='OPEN'", (bot,)):
            raise HTTPException(409, "bot has open positions; pause it (exits keep running) or close them first")
        coord().set_bot_status(bot, st, body.reason, body.operator)
        return bot_summary(bot)

    @app.put("/api/bots/{bot}/params", dependencies=[Depends(auth)])
    def bot_params(bot: str, body: ParamsBody):
        try:
            return coord().update_params(bot, body.params, body.operator)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(409, str(e))

    # ------------------------------------------------------------------ events
    @app.get("/api/events")
    def events(after: int = 0):
        return db.events_after(after)

    # ----------------------------------------------------------------- auditor
    @app.get("/api/auditor/decisions")
    def audit_decisions(limit: int = 100):
        rows = db.q("SELECT * FROM audit_decisions ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            for k in ("triggered_json", "reasons_json", "evidence_json"):
                r[k.replace("_json", "")] = json.loads(r.pop(k) or "null")
        return {"ruleset_version": state.cfg["auditor"]["ruleset_version"], "decisions": rows,
                "news": {"provider": state.cfg["news"]["provider"], "unknown_policy": state.cfg["news"]["unknown_policy"]}}

    @app.post("/api/auditor/market-alert", dependencies=[Depends(auth)])
    def market_alert(body: AlertBody):
        code = f"MARKET_ALERT_{int(pd.Timestamp.utcnow().timestamp())}"
        db.alert("HIGH", code, f"{body.message} (by {body.operator})", blocks_entries=False)
        return {"code": code}

    @app.post("/api/alerts/{code}/resolve", dependencies=[Depends(auth)])
    def resolve(code: str, body: StatusBody):
        a = db.one("SELECT * FROM alerts WHERE code=? AND resolved_at IS NULL", (code,))
        if not a:
            raise HTTPException(404)
        if code.startswith("BALANCE_OP_"):
            db.x("UPDATE unallocated_events SET acknowledged=1 WHERE deal_ticket=?", (int(code.split("_")[-1]),))
        if code in ("NOT_DEMO", "ACCOUNT_SWITCHED", "LIVE_ACCOUNT_AT_BOUNDARY") and state.coord:
            if not check_account(state.coord.ro).is_demo:
                raise HTTPException(409, "cannot resolve: connected account is still not a verified demo")
        db.resolve_alert(code)
        db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
             (None, "RESOLVE_ALERT", json.dumps(a), json.dumps({"reason": body.reason}), body.operator, now_iso()))
        return {"resolved": code}

    # -------------------------------------------------------------------- risk
    @app.get("/api/risk")
    def risk():
        c = state.coord
        info = c.ro.account_info() if c else None
        act = db.active_activation()
        fp = act["fingerprint"] if act else (check_account(c.ro).fingerprint if c else None)
        return {"limits": state.cfg["risk"],
                "account": ({k: info.get(k) for k in ("balance", "equity", "margin", "margin_free", "margin_level", "currency")}
                            if info else None),
                "capital_snapshot": (lambda s: {k: s[k] for k in ("starting_balance", "currency", "bot_fraction", "created_at")} if s else None)(
                    c.ledger.snapshot(fp) if (c and fp) else None),
                "unallocated_pool": c.ledger.unallocated_pool(fp) if (c and fp) else None,
                "unallocated_events": db.q("SELECT * FROM unallocated_events ORDER BY id DESC"),
                "alerts": db.q("SELECT * FROM alerts ORDER BY id DESC LIMIT 200"),
                "kill": db.get_control("kill") == "1", "global_pause": db.get_control("global_pause") == "1",
                "kill_switch_behaviour": "disarm + block all new entries + close every bot-owned position via the guarded "
                                         "pipeline; positions that cannot close keep their server-side stops and raise alerts",
                "audit_log": db.q("SELECT * FROM config_audit ORDER BY id DESC LIMIT 100")}

    @app.post("/api/risk/kill", dependencies=[Depends(auth)])
    def kill(body: StatusBody):
        return coord().kill(body.reason or "dashboard kill", body.operator)


    @app.post("/api/risk/pause-all", dependencies=[Depends(auth)])
    def pause_all(body: StatusBody):
        db.set_control("global_pause", "1")
        return {"global_pause": True}

    @app.post("/api/risk/resume-all", dependencies=[Depends(auth)])
    def resume_all(body: StatusBody):
        if db.get_control("kill") == "1":
            raise HTTPException(409, "kill switch active; clear it from the CLI (metals-lab clear-kill)")
        db.set_control("global_pause", "0")
        return {"global_pause": False}

    # -------------------------------------------------------------- research
    @app.get("/api/strategies")
    def strategies():
        return [bot_class(s)().describe() for s in BOT_IDS]

    @app.get("/api/datasets")
    def datasets():
        return state.datasets.manifests()

    @app.get("/api/research/experiments")
    def experiments(limit: int = 200):
        rows = db.q("SELECT id, created_at, strategy_id, dataset_key, kind, params_json, period, metrics_json, status, notes "
                    "FROM experiments ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = json.loads(r.pop("params_json") or "null")
            r["metrics"] = json.loads(r.pop("metrics_json") or "null")
        return {"experiments": rows, "holdout_usage": db.q("SELECT * FROM holdout_usage")}

    @app.get("/api/ranking")
    def ranking():
        from ..ranking import rank
        per = {}
        for b in BOT_IDS:
            book = state.coord.bot_book(b) if state.coord else None
            per[b] = {"name": bot_name(b), "allocation": book.allocation if book else 0.0,
                      "nets": [t["net"] for t in db.q("SELECT net FROM trades WHERE bot_id=? ORDER BY exit_time", (b,))]}
        n = int(state.cfg.get("selection", {}).get("min_trades", 50))
        return {"min_trades": n, "rule": f"at least {n} closed trades, profitable after costs, then smallest max drawdown",
                "rows": rank(per, n)}

    @app.get("/api/compare")
    def compare():
        out = []
        for b in BOT_IDS:
            bt = db.one("SELECT id, dataset_key, metrics_json, reliability_json FROM backtests WHERE strategy_id=? AND metrics_json IS NOT NULL "
                        "ORDER BY id DESC LIMIT 1", (b,))
            fwd = db.q("SELECT net FROM trades WHERE bot_id=?", (b,))
            out.append({"bot_id": b, "name": bot_name(b), "style": bot_class(b).style.value,
                        "latest_backtest": {"id": bt["id"], "dataset": bt["dataset_key"], "metrics": json.loads(bt["metrics_json"]),
                                            "reliability": json.loads(bt["reliability_json"] or "[]")} if bt else None,
                        "forward_demo": {"closed_trades": len(fwd), "net": round(sum(t["net"] for t in fwd), 2)}})
        return out

    from . import lab, monitor
    monitor.register(app, state, auth)
    lab.register(app, state, auth)

    # ----------------------------------------------------------------- static
    dist = Path(os.environ.get("METALS_LAB_DASHBOARD_DIR", Path(__file__).resolve().parents[3] / "dashboard" / "out"))
    if dist.exists():
        app.mount("/_next", StaticFiles(directory=dist / "_next"), name="next")

        @app.get("/{path:path}")
        def spa(path: str):
            f = dist / path
            if path and f.is_file():
                return FileResponse(f)
            if path and (dist / f"{path}.html").is_file():
                return FileResponse(dist / f"{path}.html")
            return FileResponse(dist / "index.html")

    return app
