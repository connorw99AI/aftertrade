"""Statistical validation helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    # Acklam's rational approximation (sufficient for thresholds)
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def probabilistic_sharpe(returns: pd.Series, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & Lopez de Prado 2012), per-period Sharpe, non-annualised."""
    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 10 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    skew, kurt = float(r.skew()), float(r.kurt()) + 3
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return _norm_cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom)


def deflated_sharpe(returns: pd.Series, n_trials: int, trial_sr_std: float) -> dict:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014, J. Portfolio Mgmt 40(5)).

    The benchmark SR* is the expected maximum Sharpe of ``n_trials`` unskilled
    strategies given the cross-sectional std of the trial Sharpes.
    """
    n_trials = max(1, int(n_trials))
    if n_trials == 1 or not np.isfinite(trial_sr_std) or trial_sr_std <= 0:
        sr_star = 0.0
    else:
        sr_star = trial_sr_std * ((1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n_trials) +
                                  EULER_GAMMA * _norm_ppf(1 - 1 / (n_trials * math.e)))
    return {"n_trials": n_trials, "sr_star_per_period": sr_star, "dsr": probabilistic_sharpe(returns, sr_star)}


def stationary_bootstrap_ci(x: np.ndarray, stat=np.mean, n_boot: int = 2000, mean_block: int = 5,
                            alpha: float = 0.05, seed: int = 0) -> dict:
    """Politis & Romano (1994) stationary bootstrap CI for a statistic of a series."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 5:
        return {"n": n, "estimate": float(stat(x)) if n else None, "lo": None, "hi": None}
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    out = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        new_block = rng.random(n) < p
        starts = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = starts[i] if new_block[i] else (idx[i - 1] + 1) % n
        out[b] = stat(x[idx])
    return {"n": n, "estimate": float(stat(x)), "lo": float(np.quantile(out, alpha / 2)),
            "hi": float(np.quantile(out, 1 - alpha / 2)), "level": 1 - alpha}


@dataclass
class Split:
    train: tuple[pd.Timestamp, pd.Timestamp]
    validation: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]
    holdout: tuple[pd.Timestamp, pd.Timestamp]

    def public(self) -> dict:
        return {k: [str(v[0]), str(v[1])] for k, v in self.__dict__.items()}


def chronological_split(start: pd.Timestamp, end: pd.Timestamp, fractions=(0.5, 0.15, 0.15, 0.2)) -> Split:
    assert abs(sum(fractions) - 1) < 1e-9
    total = end - start
    cuts = [start]
    acc = 0.0
    for f in fractions:
        acc += f
        cuts.append(start + total * acc)
    cuts[-1] = end
    return Split((cuts[0], cuts[1]), (cuts[1], cuts[2]), (cuts[2], cuts[3]), (cuts[3], cuts[4]))


def walk_forward_windows(start: pd.Timestamp, end: pd.Timestamp, train_days: int, test_days: int,
                         anchored: bool) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    out = []
    tr0 = start
    tr1 = start + pd.Timedelta(days=train_days)
    while tr1 + pd.Timedelta(days=test_days) <= end + pd.Timedelta(days=1):
        te1 = min(end, tr1 + pd.Timedelta(days=test_days))
        out.append((tr0, tr1, tr1, te1))
        tr1 = te1
        if not anchored:
            tr0 = tr1 - pd.Timedelta(days=train_days)
    return out


def regime_labels(daily_close: pd.Series) -> pd.DataFrame:
    """Per-day volatility tercile and trend state using only past data."""
    r = np.log(daily_close).diff()
    vol = r.rolling(20, min_periods=10).std()
    q1, q2 = vol.expanding(60).quantile(1 / 3), vol.expanding(60).quantile(2 / 3)
    vstate = np.where(vol <= q1, "low_vol", np.where(vol <= q2, "mid_vol", "high_vol"))
    sma = daily_close.rolling(100, min_periods=50).mean()
    tstate = np.where(daily_close > sma, "up_trend", "down_trend")
    df = pd.DataFrame({"vol_regime": vstate, "trend_regime": tstate}, index=daily_close.index)
    df.loc[vol.isna() | q1.isna(), "vol_regime"] = "unknown"
    df.loc[sma.isna(), "trend_regime"] = "unknown"
    return df
