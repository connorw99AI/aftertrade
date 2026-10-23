"""Backtest lab endpoints: cached MT5 data status and download, run one or all bots, clear old results."""
from __future__ import annotations

import json
import threading
import traceback
from datetime import timedelta
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..backtest.engine import Backtester
from ..backtest.models import BacktestConfig, CostModel, RiskParams
from ..data import synthetic
from ..execution.coordinator import BOT_IDS
from ..instruments import InstrumentSpec, spec_from_info
from ..jsonutil import dumps as jdumps
from ..mt5.guard import check_account
from ..mt5.sim import default_symbols
from ..store import now_iso
from ..strategies import STRATEGY_CLASSES, build
from ..timeutil import utc_now

DATA_TFS = ("M1", "M5", "M15", "H1")


class BacktestBody(BaseModel):
    strategy_id: str
    params: dict[str, Any] = {}
    dataset: str = "mt5"               # "mt5" (latest downloaded MT5 history) or "synthetic" (pipeline check only)
    initial_capital: Optional[float] = None   # default: each bot's live allocation
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0
    synthetic_days: int = 180


class RunAllBody(BaseModel):
    dataset: str = "mt5"
    synthetic_days: int = 180
    initial_capital: Optional[float] = None
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0


class TestChangeBody(BaseModel):
    change: dict[str, Any]
    dataset: str = "mt5"


class FetchBody(BaseModel):
    days: int = 365
    timeframes: list[str] = list(DATA_TFS)


def fx_from_spec(spec: InstrumentSpec) -> float:
    """Account currency per 1 unit of profit currency, from the terminal's tick value (which is in the deposit currency)."""
    if spec.tick_size and spec.tick_value and spec.contract_size:
        return float(spec.tick_value) / (float(spec.tick_size) * float(spec.contract_size))
    return 1.0


def _load_frames(datasets, cls, body: BacktestBody):
    if body.dataset == "synthetic":
        days = min(body.synthetic_days, 90) if cls.primary_timeframe == "M1" else body.synthetic_days
        frames = synthetic.frames_for(cls.timeframes, days=days)
        syms = default_symbols()
        specs = {"GOLD": spec_from_info("GOLD", syms["XAUUSD"]), "SILVER": spec_from_info("SILVER", syms["XAGUSD"])}
        return frames, specs, {"all": {"source": "SYNTHETIC"}}, 1.0
    frames, meta = {}, {}
    missing = [f"{r} {tf}" for r in cls.roles for tf in cls.timeframes if datasets.latest("MT5_TERMINAL", r, tf) is None]
    if missing:
        raise RuntimeError("no downloaded MT5 history for " + ", ".join(missing) +
                           ". Use 'Download history from MT5' on the Backtest page first.")
    for r in cls.roles:
        for tf in cls.timeframes:
            v = datasets.latest("MT5_TERMINAL", r, tf)
            df, m = datasets.load(v)
            frames[(r, tf)] = df
            meta[v] = m["provenance"]
    specs = {}
    for r in cls.roles:
        snap = next(p for p in meta.values() if p["role"] == r)["spec_snapshot"]
        specs[r] = InstrumentSpec(**{k: v for k, v in snap.items() if k in InstrumentSpec.__dataclass_fields__})
    return frames, specs, meta, fx_from_spec(specs[cls.roles[0]])


def compute_backtest(body: dict, cfg: dict, cache_dir: str, capital: float, currency: Optional[str]) -> dict:
    """Runs in a worker process so a long backtest never stalls the dashboard or the trading loop."""
    from pathlib import Path
    from ..data.store import DatasetStore
    b = BacktestBody(**body)
    cls = STRATEGY_CLASSES[b.strategy_id]
    frames, specs, meta, fx = _load_frames(DatasetStore(Path(cache_dir)), cls, b)
    bcfg = BacktestConfig(initial_capital=b.initial_capital or capital, risk=RiskParams.from_live(cfg),
                          costs=CostModel(commission_per_lot_side=b.commission_per_lot_side, fx_to_account=fx,
                                          spread_multiplier=b.spread_multiplier, slippage_multiplier=b.slippage_multiplier))
    res = Backtester(build(b.strategy_id, b.params), frames, specs, bcfg, data_meta=meta).run()
    res.metrics["account_currency"] = currency if b.dataset != "synthetic" else "USD (synthetic)"
    res.metrics["fx_to_account"] = round(fx, 6)
    trades = res.trades.copy()
    for col in ("entry_time", "exit_time"):
        if col in trades:
            trades[col] = trades[col].astype(str)
    eq = res.equity.resample("1D").last().dropna() if len(res.equity) else res.equity
    return {"metrics": jdumps(res.metrics), "diagnostics": jdumps(res.diagnostics), "reliability": jdumps(res.reliability),
            "trades": jdumps(json.loads(trades.to_json(orient="records", default_handler=str))),
            "equity": jdumps([[str(t), round(float(v), 2)] for t, v in eq.items()]), "config": jdumps(res.assumptions),
            "dataset_key": "|".join(sorted(meta)) if b.dataset != "synthetic" else "synthetic"}


_POOL = None


def _pool():
    global _POOL
    if _POOL is None:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        _POOL = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
    return _POOL


def register(app: FastAPI, state, auth) -> None:
    db = state.db
    fetch_job: dict = {"state": "IDLE"}

    def default_capital() -> float:
        c = state.coord
        if c:
            try:
                return round(c.bot_book(BOT_IDS[0]).allocation, 2) or 1000.0
            except Exception:
                pass
        return 1000.0

    def account_currency() -> Optional[str]:
        c = state.coord
        return (c.ro.account_info() or {}).get("currency") if c else None

    # ----------------------------------------------------------------- data
    @app.get("/api/data/status")
    def data_status():
        rows = []
        for role in ("GOLD", "SILVER"):
            for tf in DATA_TFS:
                v = state.datasets.latest("MT5_TERMINAL", role, tf)
                m = next((x for x in state.datasets.manifests() if x.get("version") == v), None) if v else None
                rows.append({"role": role, "timeframe": tf, "version": v,
                             "rows": (m or {}).get("quality", {}).get("rows"),
                             "start": (m or {}).get("quality", {}).get("start"), "end": (m or {}).get("quality", {}).get("end"),
                             "retrieved_at": (m or {}).get("provenance", {}).get("retrieved_at")})
        have = {(r["role"], r["timeframe"]) for r in rows if r["version"]}
        bots = []
        for b in BOT_IDS:
            cls = STRATEGY_CLASSES[b]
            missing = [f"{r} {tf}" for r in cls.roles for tf in cls.timeframes if (r, tf) not in have]
            bots.append({"bot_id": b, "timeframes": list(cls.timeframes), "ready": not missing, "missing": missing})
        return {"datasets": rows, "bots": bots, "fetch": fetch_job}

    @app.post("/api/data/fetch", dependencies=[Depends(auth)])
    def data_fetch(body: FetchBody):
        from ..data.history import download_and_cache
        c = state.coord
        if c is None:
            raise HTTPException(503, "MT5 terminal not connected")
        if fetch_job.get("state") == "RUNNING":
            raise HTTPException(409, "a download is already running")
        acct = check_account(c.ro)
        if not acct.is_demo:
            raise HTTPException(409, "connected account is not a verified demo account")
        tfs = [t for t in body.timeframes if t in DATA_TFS]
        days = max(7, min(int(body.days), 3650))
        fetch_job.clear()
        fetch_job.update({"state": "RUNNING", "started_at": now_iso(), "days": days, "done": [], "failed": [],
                          "total": len(tfs) * len(c.specs)})

        def run():
            end = utc_now()
            start = end - timedelta(days=days)
            for role, spec in c.specs.items():
                for tf in tfs:
                    try:
                        with c._lock:  # one download at a time, never while the trading loop uses the terminal
                            v = download_and_cache(c.ro, c.clock, state.datasets, spec, tf, start, end, acct.server, (acct.fingerprint or "")[:12])
                        fetch_job["done"].append(f"{role} {tf}: {v}")
                    except Exception as e:
                        fetch_job["failed"].append(f"{role} {tf}: {e}")
            fetch_job["state"] = "DONE"
            fetch_job["finished_at"] = now_iso()

        threading.Thread(target=run, daemon=True).start()
        return fetch_job

    # ------------------------------------------------------------- backtests
    def run_one(job_id: int, body: BacktestBody):
        try:
            out = _pool().submit(compute_backtest, body.model_dump(), state.cfg, str(state.datasets.root), default_capital(),
                                 account_currency()).result()
            db.x("UPDATE backtests SET metrics_json=?, diagnostics_json=?, reliability_json=?, trades_json=?, equity_json=?, config_json=?, "
                 "dataset_key=? WHERE id=?", (out["metrics"], out["diagnostics"], out["reliability"], out["trades"], out["equity"],
                                             out["config"], out["dataset_key"], job_id))
            state.jobs[job_id] = "DONE"
        except Exception as e:
            db.x("UPDATE backtests SET diagnostics_json=? WHERE id=?",
                 (json.dumps({"error": str(e), "trace": traceback.format_exc()[-2000:]}), job_id))
            state.jobs[job_id] = "FAILED"

    def create_job(body: BacktestBody) -> int:
        cur = db.x("INSERT INTO backtests(created_at,strategy_id,dataset_key,params_json) VALUES(?,?,?,?)",
                   (now_iso(), body.strategy_id, body.dataset, json.dumps(body.params)))
        state.jobs[cur.lastrowid] = "QUEUED"
        return cur.lastrowid

    @app.post("/api/backtests", dependencies=[Depends(auth)])
    def start_backtest(body: BacktestBody):
        if body.strategy_id not in STRATEGY_CLASSES:
            raise HTTPException(404)
        jid = create_job(body)
        state.jobs[jid] = "RUNNING"
        threading.Thread(target=run_one, args=(jid, body), daemon=True).start()
        return {"id": jid, "status": "RUNNING"}

    @app.post("/api/backtests/run-all", dependencies=[Depends(auth)])
    def run_all(body: RunAllBody):
        jobs = []
        for b in BOT_IDS:
            jb = BacktestBody(strategy_id=b, dataset=body.dataset, initial_capital=body.initial_capital, synthetic_days=body.synthetic_days,
                              spread_multiplier=body.spread_multiplier, slippage_multiplier=body.slippage_multiplier,
                              commission_per_lot_side=body.commission_per_lot_side)
            jobs.append((create_job(jb), jb))

        def run():
            for jid, jb in jobs:
                state.jobs[jid] = "RUNNING"
                run_one(jid, jb)

        threading.Thread(target=run, daemon=True).start()
        return {"ids": [j for j, _ in jobs], "status": "QUEUED"}

    @app.post("/api/backtests/clear", dependencies=[Depends(auth)])
    def clear_backtests():
        if any(v in ("RUNNING", "QUEUED") for v in state.jobs.values()):
            raise HTTPException(409, "backtests are still running")
        n = db.one("SELECT COUNT(*) n FROM backtests")["n"]
        db.x("DELETE FROM backtests")
        e = db.x("DELETE FROM experiments WHERE kind='backtest'").rowcount
        state.jobs.clear()
        db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
             (None, "CLEAR_BACKTESTS", json.dumps({"backtests": n, "experiments": e}), "{}", "dashboard", now_iso()))
        return {"deleted_backtests": n, "deleted_experiments": e,
                "kept": "walk-forward research and holdout usage records (they protect against reusing the final test period)"}

    # ------------------------------------------------------------- review
    def current_params(bot: str) -> dict:
        row = db.one("SELECT params_json FROM bots WHERE bot_id=?", (bot,))
        return build(bot, json.loads((row or {}).get("params_json") or "{}")).params

    @app.get("/api/review/{bot}")
    def review_bot(bot: str, source: str = "demo"):
        from .. import review as R
        if bot not in STRATEGY_CLASSES:
            raise HTTPException(404)
        cls = STRATEGY_CLASSES[bot]
        bars: dict = {}
        if source == "demo":
            trades = db.q("SELECT * FROM trades WHERE bot_id=? ORDER BY entry_time", (bot,))
            positions = {r["position_key"]: r for r in db.q("SELECT * FROM positions WHERE bot_id=?", (bot,))}
            df = R.from_demo(trades, positions)
            c = state.coord
            if c and not df.empty:
                from ..data.history import fetch_bars
                for role in set(df["role"]):
                    if role in c.specs:
                        start = df["entry_time"].min().to_pydatetime() - timedelta(hours=1)
                        end = df["exit_time"].max().to_pydatetime() + timedelta(hours=1)
                        try:
                            with c._lock:
                                bars[role] = fetch_bars(c.ro, c.clock, c.specs[role], cls.primary_timeframe, start, end)
                        except Exception:
                            pass
            label = "forward demo trades"
        elif source.startswith("backtest:"):
            bid = int(source.split(":", 1)[1])
            row = db.one("SELECT strategy_id, dataset_key, trades_json FROM backtests WHERE id=?", (bid,))
            if not row or row["strategy_id"] != bot:
                raise HTTPException(404, "no such backtest for this bot")
            df = R.from_backtest(json.loads(row["trades_json"] or "[]"))
            if row["dataset_key"] != "synthetic":
                for role in cls.roles:
                    v = state.datasets.latest("MT5_TERMINAL", role, cls.primary_timeframe)
                    if v:
                        bars[role], _ = state.datasets.load(v)
            label = f"backtest #{bid} ({'synthetic data' if row['dataset_key'] == 'synthetic' else 'MT5 history'})"
        else:
            raise HTTPException(400, "source must be 'demo' or 'backtest:<id>'")
        out = R.review(bot, df, current_params(bot), bars or None)
        out["source"] = label
        return out

    @app.post("/api/review/{bot}/test", dependencies=[Depends(auth)])
    def test_change(bot: str, body: TestChangeBody):
        """Backtest the bot as it is now and with the suggested change, on the same data, for a side-by-side comparison."""
        if bot not in STRATEGY_CLASSES:
            raise HTTPException(404)
        base = current_params(bot)
        try:
            build(bot, {**base, **body.change})
        except Exception as e:
            raise HTTPException(400, f"invalid change: {e}")
        jobs = []
        for params in (base, {**base, **body.change}):
            jb = BacktestBody(strategy_id=bot, params=params, dataset=body.dataset)
            jobs.append((create_job(jb), jb))

        def run():
            for jid, jb in jobs:
                state.jobs[jid] = "RUNNING"
                run_one(jid, jb)

        threading.Thread(target=run, daemon=True).start()
        return {"baseline_id": jobs[0][0], "candidate_id": jobs[1][0], "change": body.change,
                "note": "same data, same settings; only the change differs. Results on data the suggestion was derived from are optimistic."}

    @app.get("/api/backtests")
    def list_backtests():
        rows = db.q("SELECT id, created_at, strategy_id, dataset_key, params_json, metrics_json, reliability_json, diagnostics_json "
                    "FROM backtests ORDER BY id DESC LIMIT 200")
        for r in rows:
            r["status"] = state.jobs.get(r["id"], "DONE" if r["metrics_json"] else ("FAILED" if r["diagnostics_json"] else "UNKNOWN"))
            for k in ("params_json", "metrics_json", "reliability_json", "diagnostics_json"):
                r[k.replace("_json", "")] = json.loads(r.pop(k) or "null")
            r["error"] = (r.get("diagnostics") or {}).get("error") if r["status"] == "FAILED" else None
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
        r["error"] = (r.get("diagnostics") or {}).get("error") if r["status"] == "FAILED" else None
        r["label"] = "HISTORICAL REPLAY - not forward demo activity"
        return r
