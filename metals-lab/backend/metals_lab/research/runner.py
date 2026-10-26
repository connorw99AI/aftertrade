"""Research workflow: tune on train, select on validation, confirm on test,
touch the final holdout exactly once. Every trial is returned for recording,
including failures, so multiple-testing corrections use the true trial count."""
from __future__ import annotations

import itertools
import math
from dataclasses import replace
from typing import Callable, Optional

import numpy as np
import pandas as pd

from ..backtest.engine import AuditFn, Backtester, BacktestResult
from ..backtest.metrics import daily_returns
from ..backtest.models import BacktestConfig
from ..instruments import InstrumentSpec
from ..strategies import STRATEGY_CLASSES
from .validation import (chronological_split, deflated_sharpe, regime_labels, stationary_bootstrap_ci,
                         walk_forward_windows)


class HoldoutLocked(RuntimeError):
    pass


def param_grid(strategy_id: str, max_trials: int = 40, seed: int = 0) -> list[dict]:
    cls = STRATEGY_CLASSES[strategy_id]
    keys = [k for k, p in cls.PARAMS.items() if len(p.grid) > 1]
    if not keys:
        return [{}]
    combos = list(itertools.product(*[cls.PARAMS[k].grid for k in keys]))
    rng = np.random.default_rng(seed)
    if len(combos) > max_trials:
        pick = rng.choice(len(combos), size=max_trials - 1, replace=False)
        combos = [tuple(cls.PARAMS[k].default for k in keys)] + [combos[i] for i in sorted(pick)]
    return [dict(zip(keys, c)) for c in combos]


def objective(m: dict, min_trades: int = 20) -> float:
    if m["n_trades"] < min_trades or m.get("sharpe_daily_ann") is None or not np.isfinite(m["sharpe_daily_ann"]):
        return -math.inf
    return float(m["sharpe_daily_ann"])


class Research:
    def __init__(self, strategy_id: str, frames: dict, specs: dict[str, InstrumentSpec],
                 base_config: Optional[BacktestConfig] = None, data_meta: Optional[dict] = None,
                 holdout_check: Optional[Callable[[str, dict], None]] = None):
        self.sid = strategy_id
        self.frames = frames
        self.specs = specs
        self.base = base_config or BacktestConfig()
        self.meta = data_meta or {}
        self.holdout_check = holdout_check
        self.trials: list[dict] = []
        ptf = STRATEGY_CLASSES[strategy_id].primary_timeframe
        role = STRATEGY_CLASSES[strategy_id].roles[0]
        t = pd.to_datetime(frames[(role, ptf)]["time"], utc=True)
        self.start, self.end = t.iloc[0], t.iloc[-1]

    def run(self, params: dict, start, end, cfg: Optional[BacktestConfig] = None, audit_fn: Optional[AuditFn] = None,
            kind: str = "trial") -> BacktestResult:
        c = replace(cfg or self.base, start=str(start), end=str(end))
        try:
            res = Backtester(STRATEGY_CLASSES[self.sid](params), self.frames, self.specs, c, audit_fn=audit_fn,
                             data_meta=self.meta).run()
            self.trials.append({"kind": kind, "params": params, "start": str(start), "end": str(end),
                                "metrics": res.metrics, "status": "OK", "reliability": res.reliability})
            return res
        except Exception as e:  # failures are recorded, never hidden
            self.trials.append({"kind": kind, "params": params, "start": str(start), "end": str(end),
                                "metrics": {}, "status": f"FAILED: {e}"})
            raise

    # ------------------------------------------------------------------ steps
    def tune(self, start, end, grid: list[dict], min_trades: int = 20) -> list[dict]:
        rows = []
        for g in grid:
            r = self.run(g, start, end, kind="tune_train")
            rows.append({"params": g, "objective": objective(r.metrics, min_trades), "metrics": r.metrics})
        return sorted(rows, key=lambda x: x["objective"], reverse=True)

    def walk_forward(self, grid: list[dict], train_days: int, test_days: int, anchored: bool,
                     end: Optional[pd.Timestamp] = None, min_trades: int = 10) -> dict:
        """Optimise on each train window, apply chosen params to the following test window."""
        end = end or self.end
        folds = []
        oos_equity = []
        for tr0, tr1, te0, te1 in walk_forward_windows(self.start, end, train_days, test_days, anchored):
            ranked = self.tune(tr0, tr1, grid, min_trades)
            best = ranked[0]["params"] if ranked and np.isfinite(ranked[0]["objective"]) else {}
            oos = self.run(best, te0, te1, kind="walk_forward_oos")
            folds.append({"train": [str(tr0), str(tr1)], "test": [str(te0), str(te1)], "chosen_params": best,
                          "train_objective": ranked[0]["objective"] if ranked else None, "oos_metrics": oos.metrics})
            if len(oos.equity):
                oos_equity.append(daily_returns(oos.equity))
        r = pd.concat(oos_equity) if oos_equity else pd.Series(dtype=float)
        return {"scheme": "anchored" if anchored else "rolling", "folds": folds,
                "oos_daily_returns_n": int(len(r)),
                "oos_mean_daily_return_ci": stationary_bootstrap_ci(r.to_numpy()) if len(r) else None}

    def sensitivity(self, params: dict, start, end) -> list[dict]:
        cls = STRATEGY_CLASSES[self.sid]
        out = []
        base = {k: p.default for k, p in cls.PARAMS.items()} | params
        for k, p in cls.PARAMS.items():
            for v in p.grid:
                trial = dict(params) | {k: v}
                m = self.run(trial, start, end, kind="sensitivity").metrics
                out.append({"param": k, "value": v, "is_base": base.get(k) == v, "metrics": m})
        return out

    def cost_stress(self, params: dict, start, end) -> list[dict]:
        scenarios = {"baseline": {}, "spread_x1.5": {"spread_multiplier": 1.5}, "spread_x2": {"spread_multiplier": 2.0},
                     "slippage_x3": {"slippage_multiplier": 3.0}, "commission_x2": {"commission_multiplier": 2.0},
                     "all_costs_x2": {"spread_multiplier": 2.0, "slippage_multiplier": 2.0, "commission_multiplier": 2.0}}
        out = []
        for name, kw in scenarios.items():
            cfg = replace(self.base, costs=replace(self.base.costs, **kw))
            out.append({"scenario": name, "metrics": self.run(params, start, end, cfg, kind="cost_stress").metrics})
        return out

    def regimes(self, res: BacktestResult) -> dict:
        role = STRATEGY_CLASSES[self.sid].roles[0]
        key = (role, "D1") if (role, "D1") in self.frames else (role, STRATEGY_CLASSES[self.sid].primary_timeframe)
        df = self.frames[key]
        dc = pd.Series(df["close"].to_numpy(), index=pd.to_datetime(df["time"], utc=True)).resample("1D").last().dropna()
        lab = regime_labels(dc)
        if res.trades.empty or "entry_time" not in res.trades:
            return {}
        t = res.trades.copy()
        day = pd.to_datetime(t["entry_time"], utc=True).dt.floor("1D")
        t = t.join(lab.reindex(day.to_numpy()).reset_index(drop=True))
        out = {}
        for col in ("vol_regime", "trend_regime"):
            g = t.groupby(col)["net_pnl"]
            out[col] = {k: {"n": int(v.count()), "net": round(float(v.sum()), 2), "mean": round(float(v.mean()), 4)}
                        for k, v in g}
        return out

    def event_filter_comparison(self, params: dict, start, end, audit_fn: AuditFn) -> dict:
        """Same data, same params, with vs without the non-safety event filter."""
        raw = self.run(params, start, end, kind="event_filter_off")
        flt = self.run(params, start, end, audit_fn=audit_fn, kind="event_filter_on")
        if raw.trades.empty:
            return {"unfiltered": raw.metrics, "filtered": flt.metrics, "blocked_trades": 0, "blocked_profitable": 0,
                    "opportunity_cost": 0.0, "avoided_losses": 0.0, "note": "no trades in the unfiltered run"}
        kept = set(pd.to_datetime(flt.trades["entry_time"]).astype(str)) if not flt.trades.empty else set()
        rt = raw.trades.assign(_k=pd.to_datetime(raw.trades["entry_time"]).astype(str))
        blocked = rt[~rt["_k"].isin(kept)]
        return {
            "unfiltered": raw.metrics, "filtered": flt.metrics,
            "blocked_trades": int(len(blocked)),
            "blocked_profitable": int((blocked["net_pnl"] > 0).sum()),
            "opportunity_cost": round(float(blocked.loc[blocked["net_pnl"] > 0, "net_pnl"].sum()), 2),
            "avoided_losses": round(float(-blocked.loc[blocked["net_pnl"] <= 0, "net_pnl"].sum()), 2),
            "note": "trade paths diverge after the first blocked trade (capital/positions differ), so this is an approximation",
        }

    def full_study(self, max_trials: int = 20, wf_train_days: int = 120, wf_test_days: int = 30,
                   audit_fn: Optional[AuditFn] = None, touch_holdout: bool = False) -> dict:
        split = chronological_split(self.start, self.end)
        grid = param_grid(self.sid, max_trials)
        ranked = self.tune(*split.train, grid)
        val = []
        for row in ranked[: min(5, len(ranked))]:
            m = self.run(row["params"], *split.validation, kind="validation").metrics
            val.append({"params": row["params"], "train_objective": row["objective"], "validation_metrics": m,
                        "validation_objective": objective(m, 10)})
        chosen = max(val, key=lambda x: x["validation_objective"])["params"] if val else {}
        test = self.run(chosen, *split.test, kind="test")
        trial_srs = [t["metrics"].get("sharpe_daily_ann") for t in self.trials if t["kind"] == "tune_train"]
        trial_srs = [x / math.sqrt(252) for x in trial_srs if x is not None and np.isfinite(x)]
        dsr = deflated_sharpe(daily_returns(test.equity), n_trials=len(grid),
                              trial_sr_std=float(np.std(trial_srs)) if len(trial_srs) > 1 else 0.0)
        out = {
            "strategy_id": self.sid, "split": split.public(), "n_trials": len(grid), "chosen_params": chosen,
            "train_top": ranked[:5], "validation": val, "test_metrics": test.metrics, "test_reliability": test.reliability,
            "deflated_sharpe_test": dsr,
            "test_trade_bootstrap_mean_net": stationary_bootstrap_ci(test.trades["net_pnl"].to_numpy()) if len(test.trades) else None,
            "walk_forward_rolling": self.walk_forward(grid[: max(1, min(6, len(grid)))], wf_train_days, wf_test_days, anchored=False, end=split.test[1]),
            "walk_forward_anchored": self.walk_forward(grid[: max(1, min(6, len(grid)))], wf_train_days, wf_test_days, anchored=True, end=split.test[1]),
            "sensitivity": self.sensitivity(chosen, *split.validation),
            "cost_stress": self.cost_stress(chosen, *split.test),
            "regimes_test": self.regimes(test),
        }
        if audit_fn is not None:
            out["event_filter"] = self.event_filter_comparison(chosen, *split.test, audit_fn)
        if touch_holdout:
            if self.holdout_check is None:
                raise HoldoutLocked("holdout evaluation requires a holdout registry")
            self.holdout_check(self.sid, chosen)  # raises if already used for this strategy/dataset
            out["holdout_metrics"] = self.run(chosen, *split.holdout, kind="HOLDOUT").metrics
        else:
            out["holdout_metrics"] = "NOT EVALUATED (locked; run with --holdout once, after research is frozen)"
        out["all_trials"] = self.trials
        return out
