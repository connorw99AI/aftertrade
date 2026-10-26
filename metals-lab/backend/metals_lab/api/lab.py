"""Backtest lab endpoints: cached MT5 data status and download, run one or all bots, clear old results."""
from __future__ import annotations

import json
import threading
import traceback
from datetime import timedelta
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..backtest.engine import Backtester
from ..backtest.models import BacktestConfig, CostModel, RiskParams
from ..data import synthetic
from ..execution.coordinator import BOT_IDS
from ..instruments import InstrumentSpec, spec_from_info
from ..jsonutil import dumps as jdumps
from ..mt5.guard import check_account
from ..mt5.sim import default_symbols
from ..store import now_iso
from ..strategies import BOT_IDS as _BOTS, bot_class, bot_name, build  # noqa: F401
from ..timeutil import utc_now

DATA_TFS = ("M5", "M15", "H1")  # every bot enters on M5 or M15; H1 is only a trend filter


class BacktestBody(BaseModel):
    strategy_id: str
    params: dict[str, Any] = {}
    dataset: str = "mt5"               # "mt5" (latest downloaded MT5 history) or "synthetic" (pipeline check only)
    initial_capital: Optional[float] = None   # default: each bot's live allocation
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0
    synthetic_days: int = 180
    start: Optional[str] = None        # YYYY-MM-DD (UTC); None = from the start of the downloaded history
    end: Optional[str] = None          # YYYY-MM-DD (UTC, inclusive); None = to its end


class RunAllBody(BaseModel):
    dataset: str = "mt5"
    synthetic_days: int = 180
    start: Optional[str] = None
    end: Optional[str] = None
    initial_capital: Optional[float] = None
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_lot_side: float = 0.0


class ApplySuggestionBody(BaseModel):
    suggestion_id: str = Field(min_length=1)
    change: dict[str, Any]
    operator: str = Field(min_length=1)
    review_id: Optional[int] = None
    source: Optional[str] = None


class OperatorBody(BaseModel):
    operator: str = Field(min_length=1)


class SendToEngineerBody(BaseModel):
    bot_id: str
    title: str = Field(min_length=3)
    rule: str = Field(min_length=3)
    evidence: str = ""
    operator: str = Field(min_length=1)


class RequestStatusBody(BaseModel):
    status: str                        # DONE | DISMISSED | OPEN
    operator: str = Field(min_length=1)


class OptimiseBody(BaseModel):
    dataset: Optional[str] = None      # default: the dataset of the bot's latest finished backtest
    start: Optional[str] = None        # default: that backtest's period
    end: Optional[str] = None


class TestChangeBody(BaseModel):
    change: dict[str, Any]
    dataset: str = "mt5"
    start: Optional[str] = None
    end: Optional[str] = None


class DeleteDataBody(BaseModel):
    versions: list[str] = []
    all: bool = False
    operator: str = Field(min_length=1)


class FetchBody(BaseModel):
    days: int = 365
    start: Optional[str] = None        # YYYY-MM-DD; when given, overrides days
    end: Optional[str] = None          # YYYY-MM-DD inclusive; default today
    timeframes: list[str] = list(DATA_TFS)


def pd_ts(day: str):
    import pandas as pd
    return pd.Timestamp(day, tz="UTC")


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
    import pandas as pd
    for r in cls.roles:
        for tf in cls.timeframes:
            parts = []
            for m in datasets.manifests():  # every download of this metal/timeframe, merged; newest wins on overlap
                pv = m["provenance"]
                if pv["source"] == "MT5_TERMINAL" and pv["role"] == r and pv["timeframe"] == tf:
                    df, mm = datasets.load(m["version"])
                    parts.append((pv.get("retrieved_at", ""), df))
                    meta[m["version"]] = mm["provenance"]
            parts.sort(key=lambda x: x[0])
            frames[(r, tf)] = (pd.concat([p for _, p in parts]).drop_duplicates("time", keep="last")
                               .sort_values("time").reset_index(drop=True))
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
    cls = bot_class(b.strategy_id)
    frames, specs, meta, fx = _load_frames(DatasetStore(Path(cache_dir)), cls, b)
    end = (pd_ts(b.end) + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d") if b.end else None
    bcfg = BacktestConfig(initial_capital=b.initial_capital or capital, risk=RiskParams.from_live(cfg), start=b.start, end=end,
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
POOL_WORKERS = 2  # backtests run in separate processes, this many at a time (config assessor.parallel_backtests)


def _pool():
    global _POOL
    if _POOL is None:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        _POOL = ProcessPoolExecutor(max_workers=max(1, int(POOL_WORKERS)), mp_context=mp.get_context("spawn"))
    return _POOL


def register(app: FastAPI, state, auth) -> None:
    global POOL_WORKERS
    db = state.db
    POOL_WORKERS = int(state.cfg.get("assessor", {}).get("parallel_backtests", POOL_WORKERS) or 1)
    fetch_job: dict = {"state": "IDLE"}

    def default_capital() -> float:
        """What one bot starts with: the starting balance (or the balance now, before the first arm) x bot_fraction.
        Taken from the config, not a bot's ledger, so an old database's allocations can never leak into a backtest."""
        c = state.coord
        frac = float(state.cfg["capital"]["bot_fraction"])
        if c:
            try:
                acct = c.account
                snap = c.ledger.snapshot(acct.fingerprint) if acct and acct.fingerprint else None
                base = float(snap["starting_balance"]) if snap else float((c.ro.account_info() or {}).get("balance") or 0)
                if base > 0:
                    return round(base * frac, 2)
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
        mans = state.datasets.manifests()
        for role in ("GOLD", "SILVER"):
            for tf in DATA_TFS:
                ms = [m for m in mans if m["provenance"]["source"] == "MT5_TERMINAL" and m["provenance"]["role"] == role
                      and m["provenance"]["timeframe"] == tf]
                q = [m.get("quality", {}) for m in ms]
                rows.append({"role": role, "timeframe": tf, "version": ms[-1]["version"] if ms else None, "downloads": len(ms),
                             "rows": sum(x.get("rows", 0) for x in q) if q else None,
                             "start": min((x["start"] for x in q if x.get("start")), default=None),
                             "end": max((x["end"] for x in q if x.get("end")), default=None),
                             "retrieved_at": max((m["provenance"].get("retrieved_at", "") for m in ms), default=None)})
        have = {(r["role"], r["timeframe"]) for r in rows if r["version"]}
        bots = []
        for b in BOT_IDS:
            cls = bot_class(b)
            missing = [f"{r} {tf}" for r in cls.roles for tf in cls.timeframes if (r, tf) not in have]
            bots.append({"bot_id": b, "timeframes": list(cls.timeframes), "ready": not missing, "missing": missing})
        return {"datasets": rows, "bots": bots, "fetch": fetch_job}

    @app.get("/api/data/downloads")
    def data_downloads():
        """Every MT5 history download on disk, newest first, so you can see and delete them one by one."""
        out = []
        for m in state.datasets.manifests():
            pv, q = m["provenance"], m.get("quality", {})
            if pv.get("source") != "MT5_TERMINAL":
                continue
            out.append({"version": m["version"], "role": pv["role"], "timeframe": pv["timeframe"], "start": q.get("start"),
                        "end": q.get("end"), "rows": q.get("rows"), "retrieved_at": pv.get("retrieved_at"),
                        "bytes": state.datasets.size_of(m["version"])})
        out.sort(key=lambda d: d["retrieved_at"] or "", reverse=True)
        return {"downloads": out, "total_bytes": sum(d["bytes"] for d in out)}

    @app.post("/api/data/delete", dependencies=[Depends(auth)])
    def data_delete(body: DeleteDataBody):
        """Delete downloaded history: the listed downloads, or all of them. Live trading never reads these files;
        finished backtest results are kept (their numbers are stored separately)."""
        if fetch_job.get("state") == "RUNNING":
            raise HTTPException(409, "a download is running; wait for it to finish")
        if any(v in ("RUNNING", "QUEUED") for v in state.jobs.values()):
            raise HTTPException(409, "backtests are running; wait for them to finish")
        known = {m["version"] for m in state.datasets.manifests() if m["provenance"].get("source") == "MT5_TERMINAL"}
        targets = sorted(known) if body.all else [v for v in body.versions if v in known]
        if not targets:
            raise HTTPException(404, "nothing to delete")
        freed = sum(state.datasets.delete(v) for v in targets)
        db.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
             (None, "DELETE_HISTORY", json.dumps({"downloads": targets}), json.dumps({"deleted": len(targets), "bytes": freed}),
              body.operator, now_iso()))
        return {"deleted": len(targets), "bytes_freed": freed}

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
        try:
            rng_end = (pd_ts(body.end) + timedelta(days=1)).to_pydatetime() if body.end else None
            rng_start = pd_ts(body.start).to_pydatetime() if body.start else None
        except ValueError:
            raise HTTPException(400, "dates must be YYYY-MM-DD")
        if rng_start and rng_end and rng_start >= rng_end:
            raise HTTPException(400, "start must be before end")
        fetch_job.clear()
        fetch_job.update({"state": "RUNNING", "started_at": now_iso(), "days": days, "start": body.start, "end": body.end, "done": [], "failed": [],
                          "total": len(tfs) * len(c.specs)})

        def run():
            end = min(rng_end, utc_now()) if rng_end else utc_now()
            start = rng_start or (end - timedelta(days=days))
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

    # ------------------------------------------------- assessor change tests
    import queue as _queue
    test_q: "_queue.Queue[int]" = _queue.Queue()
    worker: dict = {"thread": None}

    def latest_backtest(bot: str) -> Optional[dict]:
        return db.one("SELECT id, dataset_key, equity_json FROM backtests WHERE strategy_id=? AND metrics_json IS NOT NULL "
                      "ORDER BY id DESC LIMIT 1", (bot,))

    def history_range(bot: str) -> Optional[tuple[str, str]]:
        """The span every chart this bot needs is downloaded for (MT5 history), as (first day, last day)."""
        cls = bot_class(bot)
        starts, ends = [], []
        mans = state.datasets.manifests()
        for r in cls.roles:
            for tf in cls.timeframes:
                q = [m.get("quality", {}) for m in mans if m["provenance"].get("source") == "MT5_TERMINAL"
                     and m["provenance"]["role"] == r and m["provenance"]["timeframe"] == tf]
                st = [x["start"] for x in q if x.get("start")]
                en = [x["end"] for x in q if x.get("end")]
                if not st or not en:
                    return None
                starts.append(min(st)); ends.append(max(en))
        a, b = str(max(starts))[:10], str(min(ends))[:10]
        return (a, b) if a < b else None

    def queue_test(bot: str, dataset: Optional[str], start: Optional[str], end: Optional[str], trigger: str) -> dict:
        """Queue one overall assessment of a bot: a full-history backtest, its review and key failures, then tuning."""
        from ..optimise import period_of_backtest
        busy = db.one("SELECT * FROM optimisations WHERE bot_id=? AND status IN ('QUEUED','RUNNING') ORDER BY id DESC LIMIT 1", (bot,))
        if busy:
            return busy
        if not (start and end and dataset):
            rng = history_range(bot) if dataset in (None, "mt5") else None
            if rng:
                dataset = "mt5"
            else:
                bt = latest_backtest(bot)
                rng = period_of_backtest(bt) if bt else None
                if not rng:
                    raise RuntimeError("download MT5 history (or run a backtest of this bot) first; the bot is assessed on that history")
                dataset = dataset or ("synthetic" if bt["dataset_key"] == "synthetic" else "mt5")
            start, end = start or rng[0], end or rng[1]
        row = db.one("SELECT config_version FROM bots WHERE bot_id=?", (bot,))
        cur = db.x("INSERT INTO optimisations(bot_id,created_at,status,progress,trigger,dataset,start,\"end\",config_version) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (bot, now_iso(), "QUEUED", "waiting", trigger, dataset, start, end,
                                                  row["config_version"] if row else None))
        test_q.put(cur.lastrowid)
        if worker["thread"] is None or not worker["thread"].is_alive():
            worker["thread"] = threading.Thread(target=test_worker, daemon=True)
            worker["thread"].start()
        return db.one("SELECT * FROM optimisations WHERE id=?", (cur.lastrowid,))

    def assess(oid: int) -> None:
        import pandas as pd
        from .. import assessor
        from ..optimise import evaluate
        from ..review import key_failures
        row = db.one("SELECT * FROM optimisations WHERE id=?", (oid,))
        bot = row["bot_id"]
        params = current_params(bot)
        db.x("UPDATE optimisations SET status='RUNNING', params_json=?, config_version=(SELECT config_version FROM bots "
             "WHERE bot_id=?) WHERE id=?", (json.dumps(params), bot, oid))
        say = lambda m: db.x("UPDATE optimisations SET progress=? WHERE id=?", (m, oid))
        # 1) the bot as it stands, over the whole history: its review and what is failing
        say("backtesting the whole history")
        jb = BacktestBody(strategy_id=bot, params=params, dataset=row["dataset"], start=row["start"], end=row["end"])
        jid = create_job(jb)
        state.jobs[jid] = "RUNNING"
        out = _pool().submit(compute_backtest, jb.model_dump(), state.cfg, str(state.datasets.root), default_capital(),
                             account_currency()).result()
        db.x("UPDATE backtests SET metrics_json=?, diagnostics_json=?, reliability_json=?, trades_json=?, equity_json=?, config_json=?, "
             "dataset_key=? WHERE id=?", (out["metrics"], out["diagnostics"], out["reliability"], out["trades"], out["equity"],
                                         out["config"], out["dataset_key"], jid))
        state.jobs[jid] = "DONE"
        rv = assessor.review_backtest(db, bot, jid, state.datasets)

        # 2) tuning, several backtests at a time
        def run_many(jobs):
            futs = [_pool().submit(compute_backtest, BacktestBody(strategy_id=bot, params=p, dataset=row["dataset"], start=a, end=b)
                                   .model_dump(), state.cfg, str(state.datasets.root), default_capital(), account_currency())
                    for p, a, b in jobs]
            return [json.loads(f.result()["metrics"]) for f in futs]

        res = evaluate(bot, params, rv.get("suggestions", []), row["start"], row["end"], progress=say, run_many=run_many)
        df = pd.DataFrame(rv.get("trades") or [])
        if not df.empty:
            df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, format="mixed")
        res["failures"] = key_failures(df, rv.get("stats") or {}, res)
        res["backtest_id"] = jid
        st, bm = rv.get("stats") or {}, json.loads(out["metrics"])
        res["summary"] = {"n": st.get("n", 0), "net": st.get("net"), "expectancy_r": st.get("expectancy_r"),
                          "win_rate": st.get("win_rate"), "max_drawdown": bm.get("max_drawdown"), "total_return": bm.get("total_return")}
        rv["failures"] = res["failures"]
        assessor.store_review(db, bot, f"assessment:{oid}", row["trigger"] or "assessment", rv)
        t = res.get("tuned")
        db.x("UPDATE optimisations SET status='DONE', progress=?, result_json=?, finished_at=? WHERE id=?",
             ((f"tuned in {len(t['steps'])} step(s): {t['verdict']}" if t else "no change beat the bot as it stands")
              + f"; {len(res['failures'])} key failure(s)", json.dumps(res, default=str), now_iso(), oid))
        db.event("ASSESSOR_TESTED", bot, {"assessment": oid, "verdict": t["verdict"] if t else None})

    def test_worker():
        while True:
            try:
                oid = test_q.get(timeout=5)
            except _queue.Empty:
                return
            try:
                assess(oid)
            except Exception as e:
                db.x("UPDATE optimisations SET status='FAILED', error=?, finished_at=? WHERE id=?", (str(e)[:500], now_iso(), oid))

    @app.post("/api/optimise/all", dependencies=[Depends(auth)])
    def optimise_all(body: OptimiseBody):
        """Assess every bot (queued; one at a time, several backtests in parallel)."""
        queued, skipped = [], []
        for b in BOT_IDS:
            if db.one("SELECT status FROM bots WHERE bot_id=?", (b,))["status"] == "RETIRED":
                skipped.append({"bot_id": b, "why": "retired"})
                continue
            try:
                queued.append(queue_test(b, body.dataset, body.start, body.end, "test all")["bot_id"])
            except RuntimeError as e:
                skipped.append({"bot_id": b, "why": str(e)})
        return {"queued": queued, "skipped": skipped}

    @app.post("/api/assessor/clear-suggestions", dependencies=[Depends(auth)])
    def assessor_clear_suggestions(body: OperatorBody):
        from .. import assessor
        return assessor.clear_suggestions(db, body.operator)

    @app.post("/api/assessor/clear-history", dependencies=[Depends(auth)])
    def assessor_clear_history(body: OperatorBody):
        from .. import assessor
        return assessor.clear_history(db, body.operator)

    @app.get("/api/workshop")
    def workshop(status: Optional[str] = None):
        """The Engineer's change requests: rule changes no setting can make, written up for a developer."""
        from .. import assessor
        return assessor.change_requests(db, status)

    @app.post("/api/workshop/send", dependencies=[Depends(auth)])
    def workshop_send(body: SendToEngineerBody):
        """You decide what goes to the Engineer: one failure or rule idea becomes a change request (not twice)."""
        from .. import assessor
        if body.bot_id not in BOT_IDS:
            raise HTTPException(404)
        t = assessor.latest_test(db, body.bot_id)
        rid = assessor.send_to_engineer(db, body.bot_id, body.title, body.rule, body.evidence, body.operator, t["id"] if t else None)
        return db.one("SELECT * FROM change_requests WHERE id=?", (rid,))

    @app.post("/api/workshop/{rid}/status", dependencies=[Depends(auth)])
    def workshop_status(rid: int, body: RequestStatusBody):
        if body.status not in ("DONE", "DISMISSED", "OPEN"):
            raise HTTPException(400, "status must be DONE, DISMISSED or OPEN")
        if not db.one("SELECT 1 FROM change_requests WHERE id=?", (rid,)):
            raise HTTPException(404)
        db.x("UPDATE change_requests SET status=?, closed_at=?, operator=? WHERE id=?",
             (body.status, None if body.status == "OPEN" else now_iso(), body.operator, rid))
        return db.one("SELECT * FROM change_requests WHERE id=?", (rid,))

    @app.post("/api/optimise/{bot}", dependencies=[Depends(auth)])
    def optimise_bot(bot: str, body: OptimiseBody):
        """Test candidate changes for one bot now (queued behind any running tests)."""
        if bot not in BOT_IDS:
            raise HTTPException(404)
        try:
            return queue_test(bot, body.dataset, body.start, body.end, "manual")
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/optimise/{bot}")
    def optimise_result(bot: str):
        from .. import assessor
        if bot not in BOT_IDS:
            raise HTTPException(404)
        return assessor.latest_test(db, bot)

    # ------------------------------------------------------------- backtests
    def run_one(job_id: int, body: BacktestBody):
        try:
            out = _pool().submit(compute_backtest, body.model_dump(), state.cfg, str(state.datasets.root), default_capital(),
                                 account_currency()).result()
            db.x("UPDATE backtests SET metrics_json=?, diagnostics_json=?, reliability_json=?, trades_json=?, equity_json=?, config_json=?, "
                 "dataset_key=? WHERE id=?", (out["metrics"], out["diagnostics"], out["reliability"], out["trades"], out["equity"],
                                             out["config"], out["dataset_key"], job_id))
            state.jobs[job_id] = "DONE"  # the Assessor judges the bot overall (Assess / Test all / after Run all), not each run
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
        if body.strategy_id not in BOT_IDS:
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
                              start=body.start, end=body.end,
                              spread_multiplier=body.spread_multiplier, slippage_multiplier=body.slippage_multiplier,
                              commission_per_lot_side=body.commission_per_lot_side)
            jobs.append((create_job(jb), jb))

        def run():
            for jid, jb in jobs:
                state.jobs[jid] = "RUNNING"
                run_one(jid, jb)
            if body.dataset != "synthetic" and state.cfg.get("assessor", {}).get("auto_test", True):
                for b in BOT_IDS:  # one overall assessment per bot once the whole run is done
                    try:
                        queue_test(b, "mt5", None, None, "run all finished")
                    except RuntimeError:
                        pass

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
        from .. import assessor
        if bot not in BOT_IDS:
            raise HTTPException(404)
        if source == "demo":
            rv = assessor.review_demo(db, bot, state.coord)
        elif source.startswith("backtest:"):
            try:
                rv = assessor.review_backtest(db, bot, int(source.split(":", 1)[1]), state.datasets)
            except KeyError as e:
                raise HTTPException(404, str(e))
        else:
            raise HTTPException(400, "source must be 'demo' or 'backtest:<id>'")
        sugg = [{**x, "untested": True} if x.get("change") else x for x in rv.get("suggestions", [])]
        tested = [x for x in assessor.suggestions_for(db, bot) if x.get("tested")]
        return {**rv, "suggestions": assessor.mark_applied(db, bot, sugg), "tested": assessor.mark_applied(db, bot, tested),
                "test": assessor.latest_test(db, bot)}

    @app.post("/api/review/{bot}/apply", dependencies=[Depends(auth)])
    def apply_suggestion(bot: str, body: ApplySuggestionBody):
        """Apply one assessor suggestion to the bot's settings and record it, so it shows as applied and folds away."""
        from .. import assessor
        if bot not in BOT_IDS:
            raise HTTPException(404)
        if state.coord is None:
            raise HTTPException(409, "the lab is not running")
        try:
            return assessor.apply_suggestion(db, state.coord, bot, body.suggestion_id, body.change, body.operator,
                                             body.review_id, body.source)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(409, str(e))

    @app.get("/api/assessor")
    def assessor_view():
        from .. import assessor
        since = (utc_now() - timedelta(days=7)).isoformat()
        blocks: dict[str, dict] = {}
        for r in db.q("SELECT bot_id, triggered_json, reasons_json FROM audit_decisions WHERE ts>=? AND decision!='APPROVED' "
                      "AND intent LIKE 'OPEN%'", (since,)):
            for code, why in zip(json.loads(r["triggered_json"] or "[]"), json.loads(r["reasons_json"] or "[]")):
                g = blocks.setdefault(code, {"code": code, "by": "auditor", "count": 0, "bots": set(), "example": why})
                g["count"] += 1
                g["bots"].add(r["bot_id"])
        for r in db.q("SELECT bot_id, reasons_json FROM risk_decisions WHERE ts>=? AND decision='REJECTED' AND intent LIKE 'OPEN%'", (since,)):
            for why in json.loads(r["reasons_json"] or "[]"):
                code = str(why).split(" ")[0].split(":")[0]
                g = blocks.setdefault(code, {"code": code, "by": "risk", "count": 0, "bots": set(), "example": why})
                g["count"] += 1
                g["bots"].add(r["bot_id"])
        cards = []
        for b in BOT_IDS:
            row = db.one("SELECT status, pause_reason FROM bots WHERE bot_id=?", (b,))
            demo, bt = assessor.latest(db, b, "demo"), assessor.latest(db, b, "assessment") or assessor.latest(db, b, "backtest")
            cards.append({"bot_id": b, "name": bot_name(b), "status": row["status"] if row else None,
                          "pause_reason": row["pause_reason"] if row else None,
                          "demo": {"grade": assessor.grade(demo), "stats": (demo or {}).get("stats"), "at": (demo or {}).get("created_at")},
                          "backtest": {"grade": assessor.grade(bt), "stats": (bt or {}).get("stats"), "source": (bt or {}).get("source"),
                                       "at": (bt or {}).get("created_at")}})
        last = db.one("SELECT created_at FROM reviews ORDER BY id DESC LIMIT 1")
        return {"suggestions": assessor.suggestions_ranked(db), "cards": cards,
                "tests": {b: assessor.latest_test(db, b) for b in BOT_IDS},
                "workshop_open": db.one("SELECT COUNT(*) n FROM change_requests WHERE status='OPEN'")["n"],
                "blocks": sorted(({**g, "bots": sorted(g["bots"])} for g in blocks.values()), key=lambda g: -g["count"]),
                "protection": db.q("SELECT ts, kind, bot_id, payload_json FROM events WHERE kind IN ('PROTECTION_RESTORED','PROTECTION_FAILED') "
                                   "ORDER BY id DESC LIMIT 30"),
                "recent_reviews": db.q("SELECT id, created_at, bot_id, source, trigger FROM reviews ORDER BY id DESC LIMIT 30"),
                "last_review_at": last["created_at"] if last else None}

    @app.post("/api/assessor/run", dependencies=[Depends(auth)])
    def assessor_run():
        """Re-review every bot now: demo trades plus its latest finished backtest."""
        from .. import assessor
        n = 0
        for b in BOT_IDS:
            assessor.store_review(db, b, "demo", "manual", assessor.review_demo(db, b, state.coord))
            n += 1
            bt = db.one("SELECT id FROM backtests WHERE strategy_id=? AND metrics_json IS NOT NULL ORDER BY id DESC LIMIT 1", (b,))
            if bt:
                assessor.store_review(db, b, f"backtest:{bt['id']}", "manual", assessor.review_backtest(db, b, bt["id"], state.datasets))
                n += 1
        return {"reviews": n}

    @app.post("/api/review/{bot}/test", dependencies=[Depends(auth)])
    def test_change(bot: str, body: TestChangeBody):
        """Backtest the bot as it is now and with the suggested change, on the same data, for a side-by-side comparison."""
        if bot not in BOT_IDS:
            raise HTTPException(404)
        base = current_params(bot)
        try:
            build(bot, {**base, **body.change})
        except Exception as e:
            raise HTTPException(400, f"invalid change: {e}")
        jobs = []
        for params in (base, {**base, **body.change}):
            jb = BacktestBody(strategy_id=bot, params=params, dataset=body.dataset, start=body.start, end=body.end)
            jobs.append((create_job(jb), jb))

        def run():
            for jid, jb in jobs:
                state.jobs[jid] = "RUNNING"
                run_one(jid, jb)

        threading.Thread(target=run, daemon=True).start()
        return {"baseline_id": jobs[0][0], "candidate_id": jobs[1][0], "change": body.change,
                "note": "same data, same settings; only the change differs. Results on data the suggestion was derived from are optimistic."}

    @app.get("/api/backtests/report.md")
    def backtest_report():
        from fastapi.responses import Response
        from ..report import build_report
        md, _ = build_report(db, state.cfg)
        return Response(md, media_type="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=backtest_review.md"})

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
