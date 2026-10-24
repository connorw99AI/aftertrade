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

    # ------------------------------------------------------------- backtests
    def run_one(job_id: int, body: BacktestBody):
        try:
            out = _pool().submit(compute_backtest, body.model_dump(), state.cfg, str(state.datasets.root), default_capital(),
                                 account_currency()).result()
            db.x("UPDATE backtests SET metrics_json=?, diagnostics_json=?, reliability_json=?, trades_json=?, equity_json=?, config_json=?, "
                 "dataset_key=? WHERE id=?", (out["metrics"], out["diagnostics"], out["reliability"], out["trades"], out["equity"],
                                             out["config"], out["dataset_key"], job_id))
            try:
                from .. import assessor
                assessor.store_review(db, body.strategy_id, f"backtest:{job_id}", "backtest finished",
                                      assessor.review_backtest(db, body.strategy_id, job_id, state.datasets))
            except Exception as e:  # a failed review never fails the backtest
                db.event("ASSESSOR_ERROR", body.strategy_id, {"backtest": job_id, "error": str(e)})
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
            return assessor.review_demo(db, bot, state.coord)
        if source.startswith("backtest:"):
            try:
                return assessor.review_backtest(db, bot, int(source.split(":", 1)[1]), state.datasets)
            except KeyError as e:
                raise HTTPException(404, str(e))
        raise HTTPException(400, "source must be 'demo' or 'backtest:<id>'")

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
            demo, bt = assessor.latest(db, b, "demo"), assessor.latest(db, b, "backtest")
            cards.append({"bot_id": b, "name": bot_name(b), "status": row["status"] if row else None,
                          "pause_reason": row["pause_reason"] if row else None,
                          "demo": {"grade": assessor.grade(demo), "stats": (demo or {}).get("stats"), "at": (demo or {}).get("created_at")},
                          "backtest": {"grade": assessor.grade(bt), "stats": (bt or {}).get("stats"), "source": (bt or {}).get("source"),
                                       "at": (bt or {}).get("created_at")}})
        last = db.one("SELECT created_at FROM reviews ORDER BY id DESC LIMIT 1")
        return {"suggestions": assessor.suggestions_ranked(db), "cards": cards,
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
