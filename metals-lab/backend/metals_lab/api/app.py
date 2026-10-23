"""FastAPI backend for the creature dashboard.

Security posture: binds to 127.0.0.1 by default. If an API token env var is set,
every mutating request must send it as ``X-API-Token``. There is deliberately NO
endpoint that arms execution - arming is a separate manual CLI command.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..backtest.engine import Backtester
from ..backtest.models import BacktestConfig, CostModel
from ..data import bars as B
from ..data import synthetic
from ..data.store import DatasetStore
from ..execution.coordinator import BOT_IDS, Coordinator
from ..instruments import spec_from_info
from ..jsonutil import dumps as jdumps
from ..mt5.guard import check_account
from ..mt5.sim import default_symbols
from ..store import Store, now_iso
from ..strategies import STRATEGY_CLASSES, build, magic_for


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


class AlertBody(BaseModel):
    message: str = Field(min_length=3)
    operator: str = "dashboard"


class BacktestBody(BaseModel):
    strategy_id: str
    params: dict[str, Any] = {}
    dataset: str = "synthetic"         # "synthetic" or "mt5" (latest cached MT5 datasets)
    initial_capital: float = 1000.0
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 3.5
    synthetic_days: int = 180


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
        cls = STRATEGY_CLASSES[bot]
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
        if bot not in STRATEGY_CLASSES:
            raise HTTPException(404)
        d = bot_summary(bot)
        row = db.one("SELECT params_json FROM bots WHERE bot_id=?", (bot,))
        d["strategy"] = build(bot, json.loads(row["params_json"] or "{}")).describe()
        d["thinking"] = state.coord.thinking().get(bot) if state.coord else None
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

    @app.post("/api/bots/{bot}/{action}", dependencies=[Depends(auth)])
    def bot_action(bot: str, action: str, body: StatusBody):
        if bot not in STRATEGY_CLASSES or action not in ("start", "pause", "stop"):
            raise HTTPException(404)
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

    @app.post("/api/risk/disarm", dependencies=[Depends(auth)])
    def disarm(body: StatusBody):
        coord().disarm(body.reason or "dashboard disarm", body.operator)
        return {"armed": False}

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
        return [STRATEGY_CLASSES[s]().describe() for s in BOT_IDS]

    @app.get("/api/datasets")
    def datasets():
        return state.datasets.manifests()

    def _run_backtest(job_id: int, body: BacktestBody):
        try:
            cls = STRATEGY_CLASSES[body.strategy_id]
            if body.dataset == "synthetic":
                days = min(body.synthetic_days, 90) if cls.primary_timeframe == "M1" else body.synthetic_days
                frames = synthetic.frames_for(cls.timeframes, days=days)
                syms = default_symbols()
                specs = {"GOLD": spec_from_info("GOLD", syms["XAUUSD"]), "SILVER": spec_from_info("SILVER", syms["XAGUSD"])}
                meta = {"all": {"source": "SYNTHETIC"}}
            else:
                frames, meta = {}, {}
                for r in cls.roles:
                    for tf in cls.timeframes:
                        v = state.datasets.latest("MT5_TERMINAL", r, tf)
                        if v is None:
                            raise RuntimeError(f"no cached MT5 dataset for {r} {tf}; run 'metals-lab fetch-history'")
                        df, m = state.datasets.load(v)
                        frames[(r, tf)] = df
                        meta[v] = m["provenance"]
                specs_src = {r: m for r, m in ((r, next(v for k, v in meta.items() if v["role"] == r)) for r in cls.roles)}
                from ..instruments import InstrumentSpec
                specs = {r: InstrumentSpec(**{k: v for k, v in p["spec_snapshot"].items() if k in InstrumentSpec.__dataclass_fields__})
                         for r, p in specs_src.items()}
            cfg = BacktestConfig(initial_capital=body.initial_capital,
                                 costs=CostModel(commission_per_lot_side=body.commission_per_lot_side,
                                                 spread_multiplier=body.spread_multiplier, slippage_multiplier=body.slippage_multiplier))
            res = Backtester(build(body.strategy_id, body.params), frames, specs, cfg, data_meta=meta).run()
            trades = res.trades.copy()
            for c in ("entry_time", "exit_time"):
                if c in trades:
                    trades[c] = trades[c].astype(str)
            eq = res.equity.resample("1D").last().dropna() if len(res.equity) else res.equity
            db.x("UPDATE backtests SET metrics_json=?, diagnostics_json=?, reliability_json=?, trades_json=?, equity_json=?, config_json=? WHERE id=?",
                 (jdumps(res.metrics), jdumps(res.diagnostics), jdumps(res.reliability),
                  jdumps(json.loads(trades.to_json(orient="records", default_handler=str))),
                  jdumps([[str(t), round(float(v), 2)] for t, v in eq.items()]),
                  jdumps(res.assumptions), job_id))
            state.jobs[job_id] = "DONE"
        except Exception as e:
            db.x("UPDATE backtests SET diagnostics_json=? WHERE id=?", (json.dumps({"error": str(e), "trace": traceback.format_exc()[-2000:]}), job_id))
            state.jobs[job_id] = "FAILED"

    @app.post("/api/backtests", dependencies=[Depends(auth)])
    def start_backtest(body: BacktestBody):
        if body.strategy_id not in STRATEGY_CLASSES:
            raise HTTPException(404)
        cur = db.x("INSERT INTO backtests(created_at,strategy_id,dataset_key,params_json) VALUES(?,?,?,?)",
                   (now_iso(), body.strategy_id, body.dataset, json.dumps(body.params)))
        jid = cur.lastrowid
        state.jobs[jid] = "RUNNING"
        threading.Thread(target=_run_backtest, args=(jid, body), daemon=True).start()
        return {"id": jid, "status": "RUNNING"}

    @app.get("/api/backtests")
    def list_backtests():
        rows = db.q("SELECT id, created_at, strategy_id, dataset_key, params_json, metrics_json, reliability_json, diagnostics_json "
                    "FROM backtests ORDER BY id DESC LIMIT 100")
        for r in rows:
            r["status"] = state.jobs.get(r["id"], "DONE" if r["metrics_json"] else ("FAILED" if r["diagnostics_json"] else "UNKNOWN"))
            for k in ("params_json", "metrics_json", "reliability_json", "diagnostics_json"):
                r[k.replace("_json", "")] = json.loads(r.pop(k) or "null")
        return rows

    @app.get("/api/backtests/{bid}")
    def get_backtest(bid: int):
        r = db.one("SELECT * FROM backtests WHERE id=?", (bid,))
        if not r:
            raise HTTPException(404)
        for k in list(r):
            if k.endswith("_json"):
                r[k.replace("_json", "")] = json.loads(r.pop(k) or "null")
        r["status"] = state.jobs.get(bid, "DONE" if r.get("metrics") else "FAILED")
        r["label"] = "HISTORICAL REPLAY - not forward demo activity"
        return r

    @app.get("/api/research/experiments")
    def experiments(limit: int = 200):
        rows = db.q("SELECT id, created_at, strategy_id, dataset_key, kind, params_json, period, metrics_json, status, notes "
                    "FROM experiments ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = json.loads(r.pop("params_json") or "null")
            r["metrics"] = json.loads(r.pop("metrics_json") or "null")
        return {"experiments": rows, "holdout_usage": db.q("SELECT * FROM holdout_usage")}

    @app.get("/api/compare")
    def compare():
        out = []
        for b in BOT_IDS:
            bt = db.one("SELECT id, dataset_key, metrics_json, reliability_json FROM backtests WHERE strategy_id=? AND metrics_json IS NOT NULL "
                        "ORDER BY id DESC LIMIT 1", (b,))
            fwd = db.q("SELECT net FROM trades WHERE bot_id=?", (b,))
            out.append({"bot_id": b, "name": STRATEGY_CLASSES[b].name, "style": STRATEGY_CLASSES[b].style.value,
                        "latest_backtest": {"id": bt["id"], "dataset": bt["dataset_key"], "metrics": json.loads(bt["metrics_json"]),
                                            "reliability": json.loads(bt["reliability_json"] or "[]")} if bt else None,
                        "forward_demo": {"closed_trades": len(fwd), "net": round(sum(t["net"] for t in fwd), 2)}})
        return out

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
