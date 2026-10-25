"""Command line interface.

Read-only by default. The ONLY way to enable demo execution is ``metals-lab arm``,
which runs the demo checks and requires a typed confirmation bound to the
connected account's fingerprint. Installation, setup checks and tests never arm.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as config_mod
from .mt5.gateway import TradingGateway, load_real_backend
from .mt5.guard import check_account
from .jsonutil import dumps as jdumps
from .store import Store, now_iso
from .strategies import BOT_IDS


def _gateway(sim: bool):
    if sim:
        from .mt5.sim import SimTerminal
        from .mt5.simfeed import SimReplay
        from .timeutil import set_provider
        t = SimTerminal()
        replay = SimReplay(t)
        set_provider(lambda: t._now)  # simulated time drives every timestamp in sim mode
        return TradingGateway(t, is_simulated=True), replay
    return TradingGateway(load_real_backend()), None


def _store(cfg: dict, sim: bool) -> Store:
    p = Path(cfg["storage"]["db_path"])
    if sim:
        p = p.with_name("sim_" + p.name)  # simulated activity never mixes with forward demo records
    return Store(p)


def _coordinator(cfg, sim):
    from .execution.coordinator import Coordinator
    gw, replay = _gateway(sim)
    c = Coordinator(cfg, gw, _store(cfg, sim))
    c.connect()
    return c, replay


def cmd_check(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    acct = check_account(c.ro)
    disc = dict(c.discovery or {})
    cands = disc.pop("candidates", [])
    equities = [x["symbol"] for x in cands if x["kind"] == "EXCHANGE_STOCK"]
    disc["other_metal_contracts_rejected"] = {x["symbol"]: "; ".join(x["reasons"]) for x in cands
                                              if not x["accepted"] and x["kind"] != "EXCHANGE_STOCK"}
    disc["stocks_and_etfs_rejected"] = f"{len(equities)} symbols (e.g. {', '.join(equities[:6])})" if equities else 0
    # What a 1.00 price move on 1 lot is worth in the ACCOUNT currency, straight from the terminal.
    values = {}
    for role, spec in c.specs.items():
        tk = c.ro.symbol_info_tick(spec.symbol)
        if tk:
            v = c.ro.order_calc_profit(c.ro.const("ORDER_TYPE_BUY"), spec.symbol, 1.0, tk["ask"], tk["ask"] + 1.0)
            m = c.ro.order_calc_margin(c.ro.const("ORDER_TYPE_BUY"), spec.symbol, spec.volume_min, tk["ask"])
            values[role] = {"symbol": spec.symbol, "spread_points": round((tk["ask"] - tk["bid"]) / spec.point),
                            f"profit_per_1.00_move_per_lot_{acct.currency}": v,
                            f"margin_for_min_lot_{acct.currency}": m}
    off = None
    for spec in c.specs.values():
        tk = c.ro.symbol_info_tick(spec.symbol)
        if tk:
            from .sessions import ServerClock
            from .timeutil import utc_now
            off = ServerClock.measure_offset_hours(int(tk["time"]), utc_now())
            break
    out = {"terminal": "SIMULATED" if a.sim else "MT5", "account": acct.public(), "instruments": disc,
           "value_check": values, "clock": c.clock.source, "measured_server_offset_hours": off,
           "sessions": {r: cal.source for r, cal in c.sessions.items()} or "UNKNOWN (no export/config)",
           "execution_blockers_if_armed": c.preflight(), "armed": c.is_armed(),
           "note": "read-only check: no orders were sent"}
    print(json.dumps(out, indent=2, default=str))
    return 0 if acct.is_demo else 2


def cmd_arm(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    acct = check_account(c.ro)
    print(json.dumps(acct.public(), indent=2))
    problems = c.preflight()
    if problems:
        print("REFUSING TO ARM:", "; ".join(problems))
        return 2
    fp6 = acct.fingerprint[:6]
    bal = acct.balance
    print(f"\nDemo account verified (trade_mode == ACCOUNT_TRADE_MODE_DEMO), hedging, server {acct.server}.")
    print(f"Starting balance snapshot (only taken on first activation): {bal:.2f} {acct.currency}")
    print(f"Each of {len(BOT_IDS)} bots receives {cfg['capital']['bot_fraction']:.2%} = {bal * cfg['capital']['bot_fraction']:.2f} {acct.currency}")
    print("Automatic DEMO trading will start for bots whose status is RUNNING.")
    phrase = f"ARM DEMO {fp6}"
    typed = a.confirm if a.confirm is not None else input(f'Type "{phrase}" to arm: ').strip()
    if typed != phrase:
        print("Confirmation did not match. Not armed.")
        return 1
    res = c.arm(a.operator)
    print(json.dumps(res, indent=2))
    return 0


def cmd_disarm(cfg, a):
    st = _store(cfg, a.sim)
    st.disarm(a.reason)
    st.x("INSERT INTO config_audit(bot_id,action,old_json,new_json,operator,created_at) VALUES(?,?,?,?,?,?)",
         (None, "DISARM", None, json.dumps({"reason": a.reason}), a.operator, now_iso()))
    print("disarmed; outstanding approvals revoked")
    return 0


def cmd_kill(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    print(json.dumps(c.kill(a.reason, a.operator), indent=2, default=str))
    return 0


def cmd_clear_kill(cfg, a):
    st = _store(cfg, a.sim)
    st.set_control("kill", "0")
    st.resolve_alert("KILL_SWITCH")
    print("kill switch cleared. The system stays DISARMED until you run 'arm' again.")
    return 0


def cmd_allocate(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    acct = check_account(c.ro)
    print(json.dumps(c.ledger.change_allocation(acct.fingerprint, a.bot, a.delta, a.reason, a.operator), indent=2))
    return 0


def cmd_unretire_all(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    print(json.dumps(c.unretire_all(a.operator), indent=2))
    return 0


def cmd_bot(cfg, a):
    c, _ = _coordinator(cfg, a.sim)
    c.set_bot_status(a.bot, {"start": "RUNNING", "pause": "PAUSED", "stop": "STOPPED"}[a.action], a.reason, a.operator)
    print(f"{a.bot} -> {a.action}")
    return 0


def cmd_fetch(cfg, a):
    from .data.history import download_and_cache
    from .data.store import DatasetStore
    c, _ = _coordinator(cfg, a.sim)
    acct = check_account(c.ro)
    if not acct.is_demo:
        print("connected account is not a verified demo account; refusing")
        return 2
    ds = DatasetStore(Path(cfg["storage"]["cache_dir"]))
    from .timeutil import utc_now
    end = utc_now()
    start = end - timedelta(days=a.days)
    for role, spec in c.specs.items():
        for tf in a.tf:
            try:
                v = download_and_cache(c.ro, c.clock, ds, spec, tf, start, end, acct.server, acct.fingerprint[:12], a.ticks)
                print(f"{role} {spec.symbol} {tf}: cached {v}")
            except Exception as e:
                print(f"{role} {spec.symbol} {tf}: FAILED {e}")
    return 0


def _research_inputs(cfg, a):
    from .data import bars as B
    from .data import synthetic
    from .data.store import DatasetStore
    from .instruments import InstrumentSpec, spec_from_info
    from .mt5.sim import default_symbols
    from .strategies import bot_class
    cls = bot_class(a.strategy)
    if a.synthetic:
        days = min(a.days, 90) if cls.primary_timeframe == "M1" else a.days
        frames = synthetic.frames_for(cls.timeframes, days=days, seed=a.seed)
        syms = default_symbols()
        specs = {"GOLD": spec_from_info("GOLD", syms["XAUUSD"]), "SILVER": spec_from_info("SILVER", syms["XAGUSD"])}
        return frames, specs, {"synthetic": {"source": "SYNTHETIC", "seed": a.seed, "days": a.days}}, f"SYNTHETIC-{a.seed}-{a.days}"
    ds = DatasetStore(Path(cfg["storage"]["cache_dir"]))
    frames, meta, specs = {}, {}, {}
    for r in cls.roles:
        for tf in cls.timeframes:
            v = ds.latest("MT5_TERMINAL", r, tf)
            if not v:
                raise SystemExit(f"no cached MT5 data for {r} {tf}; run fetch-history first")
            df, m = ds.load(v)
            frames[(r, tf)] = df
            meta[v] = m["provenance"]
            snap = m["provenance"]["spec_snapshot"]
            specs[r] = InstrumentSpec(**{k: snap[k] for k in InstrumentSpec.__dataclass_fields__ if k in snap})
    return frames, specs, meta, "|".join(sorted(meta))


def cmd_backtest(cfg, a):
    from .backtest.engine import Backtester
    from .backtest.models import BacktestConfig, RiskParams
    from .strategies import build
    from .api.lab import fx_from_spec
    from .backtest.models import CostModel
    frames, specs, meta, key = _research_inputs(cfg, a)
    fx = 1.0 if a.synthetic else fx_from_spec(next(iter(specs.values())))
    res = Backtester(build(a.strategy, json.loads(a.params)), frames, specs,
                     BacktestConfig(initial_capital=a.capital, risk=RiskParams.from_live(cfg), costs=CostModel(fx_to_account=fx)),
                     data_meta=meta).run()
    st = _store(cfg, False)
    st.x("INSERT INTO backtests(created_at,strategy_id,dataset_key,params_json,config_json,metrics_json,diagnostics_json,reliability_json) "
         "VALUES(?,?,?,?,?,?,?,?)", (now_iso(), a.strategy, key, a.params, jdumps(res.assumptions),
                                     jdumps(res.metrics), jdumps(res.diagnostics), json.dumps(res.reliability)))
    st.x("INSERT INTO experiments(created_at,strategy_id,dataset_key,kind,params_json,period,metrics_json,status) VALUES(?,?,?,?,?,?,?,?)",
         (now_iso(), a.strategy, key, "backtest", a.params, "full", jdumps(res.metrics), "OK"))
    print(json.dumps({"metrics": res.metrics, "diagnostics": res.diagnostics, "reliability": res.reliability}, indent=2, default=str))
    return 0


def cmd_research(cfg, a):
    from .backtest.models import BacktestConfig, RiskParams
    from .research.runner import HoldoutLocked, Research
    frames, specs, meta, key = _research_inputs(cfg, a)
    st = _store(cfg, False)

    def holdout_check(sid, params):
        cur = st.x("INSERT OR IGNORE INTO holdout_usage(strategy_id,dataset_key,params_json,used_at) VALUES(?,?,?,?)",
                   (sid, key, json.dumps(params), now_iso()))
        if cur.rowcount == 0:
            raise HoldoutLocked(f"final holdout for {sid} on this dataset was already used; results would be contaminated")

    r = Research(a.strategy, frames, specs, BacktestConfig(initial_capital=a.capital, risk=RiskParams.from_live(cfg)), meta, holdout_check)
    rep = r.full_study(max_trials=a.trials, wf_train_days=a.wf_train, wf_test_days=a.wf_test, touch_holdout=a.holdout)
    for t in rep["all_trials"]:
        st.x("INSERT INTO experiments(created_at,strategy_id,dataset_key,kind,params_json,period,metrics_json,status) VALUES(?,?,?,?,?,?,?,?)",
             (now_iso(), a.strategy, key, t["kind"], json.dumps(t["params"]), f"{t['start']}..{t['end']}",
              jdumps(t["metrics"]), t["status"]))
    st.x("INSERT INTO experiments(created_at,strategy_id,dataset_key,kind,params_json,period,metrics_json,status,report_json) VALUES(?,?,?,?,?,?,?,?,?)",
         (now_iso(), a.strategy, key, "study", json.dumps(rep["chosen_params"]), json.dumps(rep["split"]),
          jdumps(rep["test_metrics"]), "OK", jdumps({k: v for k, v in rep.items() if k != "all_trials"})))
    rep.pop("all_trials")
    out = Path(a.out or f"reports/research_{a.strategy}_{datetime.now():%Y%m%d_%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps({"chosen_params": rep["chosen_params"], "test_metrics": rep["test_metrics"],
                      "deflated_sharpe": rep["deflated_sharpe_test"], "holdout": rep["holdout_metrics"],
                      "report": str(out)}, indent=2, default=str))
    return 0


def cmd_apply_settings(cfg, a):
    """Apply agreed settings to config/config.yaml once each (backup kept). Run by the desktop launcher."""
    from .settings_update import apply
    p = Path(cfg.get("_path") or a.config or "config/config.yaml")
    try:
        done = apply(p)
    except Exception as e:  # never stop the lab from starting over this
        print(f"Settings update skipped: {e}")
        return 0
    if done:
        print(f"Updated {p} with: {', '.join(done)} (backup saved next to it)")
    return 0


def cmd_new_lab(cfg, a):
    """Archive the current database (history kept) so the next start creates a fresh lab with new allocations."""
    import shutil
    p = Path(cfg["storage"]["db_path"])
    if a.sim:
        p = p.with_name("sim_" + p.name)
    if not p.exists():
        print(f"no database at {p}; nothing to archive")
        return 0
    st = Store(p)
    open_n = st.one("SELECT COUNT(*) n FROM positions WHERE status='OPEN'")["n"]
    st.conn.close()
    if open_n and not a.force:
        print(f"{open_n} bot positions are still open. Close them first (dashboard Trades page, Open tab), or pass --force.")
        return 2
    if a.confirm != "NEW LAB":
        print('This archives the database and starts a fresh lab: new balance snapshot and allocations on the next arm.')
        print('Run again with --confirm "NEW LAB" to proceed.')
        return 2
    arch = p.parent / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(p) + suffix)
        if f.exists():
            shutil.move(str(f), str(arch / f"{p.stem}-{stamp}{p.suffix}{suffix}"))
    print(f"archived to {arch}; start the lab and arm again (metals-lab arm) to create the 22-bot allocations")
    return 0


def cmd_export_backtests(cfg, a):
    from .report import build_report
    md, data = build_report(_store(cfg, a.sim), cfg)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(jdumps(data), encoding="utf-8")
    print(f"wrote {out} and {out.with_suffix('.json')} (no account identifiers inside)")
    return 0


def cmd_serve(cfg, a):
    import uvicorn
    from .api.app import State, create_app
    try:
        c, replay = _coordinator(cfg, a.sim)
        kind = "SIMULATED" if a.sim else "MT5"
    except Exception as e:
        print(f"MT5 unavailable ({e}); starting in research-only mode")
        c, replay, kind = None, None, "NONE"
    st = c.db if c else _store(cfg, False)
    if cfg["api"]["host"] not in ("127.0.0.1", "localhost") and not __import__("os").environ.get(cfg["api"]["token_env"]):
        print("Refusing to bind to a non-local address without an API token")
        return 2
    if c and not a.no_loop:
        def loop():
            import time
            while True:
                try:
                    if replay:
                        replay.step()
                    c.cycle()
                    c.last_error = None
                except Exception as e:
                    logging.exception("cycle")
                    c.last_error = str(e)
                time.sleep(cfg["execution"]["loop_interval_seconds"] if not replay else a.sim_speed)
        threading.Thread(target=loop, daemon=True).start()
    uvicorn.run(create_app(State(cfg, st, c, kind)), host=cfg["api"]["host"], port=cfg["api"]["port"], log_level="info")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="metals-lab")
    p.add_argument("--config", default=None)
    p.add_argument("--sim", action="store_true", help="use the built-in SIMULATED terminal (no MT5, separate database)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="read-only connection, demo, instrument and session checks")
    s = sub.add_parser("arm", help="MANUAL: arm demo execution (typed confirmation)")
    s.add_argument("--operator", required=True)
    s.add_argument("--confirm", default=None, help=argparse.SUPPRESS)
    s = sub.add_parser("disarm")
    s.add_argument("--reason", default="manual disarm")
    s.add_argument("--operator", default="cli")
    s = sub.add_parser("kill")
    s.add_argument("--reason", required=True)
    s.add_argument("--operator", default="cli")
    sub.add_parser("clear-kill")
    s = sub.add_parser("allocate", help="explicit allocation change with audit record")
    s.add_argument("--bot", required=True)
    s.add_argument("--delta", type=float, required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--operator", required=True)
    s = sub.add_parser("unretire-all", help="un-retire every retired half and give each its starting allocation back")
    s.add_argument("--operator", required=True)
    s = sub.add_parser("bot")
    s.add_argument("bot")
    s.add_argument("action", choices=["start", "pause", "stop"])
    s.add_argument("--reason", default="")
    s.add_argument("--operator", default="cli")
    s = sub.add_parser("fetch-history")
    s.add_argument("--tf", nargs="+", default=["M5", "M15", "H1"])
    s.add_argument("--days", type=int, default=365)
    s.add_argument("--ticks", action="store_true", help="build bars from ticks (real per-tick spreads)")
    for name in ("backtest", "research"):
        s = sub.add_parser(name)
        s.add_argument("--strategy", required=True)
        s.add_argument("--synthetic", action="store_true", help="use SYNTHETIC data (pipeline check only)")
        s.add_argument("--days", type=int, default=365)
        s.add_argument("--seed", type=int, default=7)
        s.add_argument("--capital", type=float, default=1000.0)
        s.add_argument("--params", default="{}")
        if name == "research":
            s.add_argument("--trials", type=int, default=20)
            s.add_argument("--wf-train", type=int, default=120)
            s.add_argument("--wf-test", type=int, default=30)
            s.add_argument("--holdout", action="store_true", help="evaluate the final holdout ONCE")
            s.add_argument("--out", default=None)
    s = sub.add_parser("new-lab", help="archive the database and start a fresh lab (needed after the long/short split)")
    s.add_argument("--confirm", default=None)
    s.add_argument("--force", action="store_true")
    sub.add_parser("apply-settings", help="apply agreed settings to config/config.yaml once (backup kept)")
    s = sub.add_parser("export-backtests", help="write the latest backtest per bot + trade review to a shareable file")
    s.add_argument("--out", default="reports/backtest_review.md")
    s = sub.add_parser("serve")
    s.add_argument("--no-loop", action="store_true")
    s.add_argument("--sim-speed", type=float, default=1.0, help="seconds per simulated 5-minute bar")
    a = p.parse_args(argv)
    cfg = config_mod.load(a.config)
    fn = {"check": cmd_check, "arm": cmd_arm, "disarm": cmd_disarm, "kill": cmd_kill, "clear-kill": cmd_clear_kill,
          "allocate": cmd_allocate, "unretire-all": cmd_unretire_all, "bot": cmd_bot, "fetch-history": cmd_fetch, "backtest": cmd_backtest,
          "research": cmd_research, "serve": cmd_serve, "export-backtests": cmd_export_backtests, "new-lab": cmd_new_lab,
          "apply-settings": cmd_apply_settings}[a.cmd]
    return fn(cfg, a)


if __name__ == "__main__":
    sys.exit(main())
