"""Performance metrics. Sample size is always reported next to every ratio."""
from __future__ import annotations

import math

import pandas as pd


def daily_returns(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return pd.Series(dtype=float)
    d = equity.resample("1D").last().dropna()
    return d.pct_change().dropna()


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min())


def sharpe(r: pd.Series, periods: int = 252) -> float:
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods))


def sortino(r: pd.Series, periods: int = 252) -> float:
    dn = r[r < 0]
    if len(r) < 2 or len(dn) < 2 or dn.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / dn.std(ddof=1) * math.sqrt(periods))


def summarize(trades: pd.DataFrame, equity: pd.Series, initial: float) -> dict:
    out: dict = {"n_trades": int(len(trades))}
    final = float(equity.iloc[-1]) if len(equity) else initial
    out["initial_capital"] = initial
    out["final_equity"] = round(final, 2)
    out["total_return"] = round(final / initial - 1, 6)
    out["max_drawdown"] = round(max_drawdown(equity), 6)
    r = daily_returns(equity)
    out["n_days"] = int(len(r))
    out["sharpe_daily_ann"] = round(sharpe(r), 4) if len(r) > 1 else None
    out["sortino_daily_ann"] = round(sortino(r), 4) if len(r) > 1 else None
    if len(equity) > 1:
        yrs = (equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400)
        out["cagr"] = round((final / initial) ** (1 / yrs) - 1, 6) if yrs > 0.05 and final > 0 else None
    if len(trades):
        net = trades["net_pnl"]
        wins, losses = net[net > 0], net[net <= 0]
        out["win_rate"] = round(len(wins) / len(trades), 4)
        out["profit_factor"] = round(wins.sum() / -losses.sum(), 4) if losses.sum() < 0 else None
        out["avg_net_pnl"] = round(net.mean(), 4)
        out["avg_r"] = round(trades["r_multiple"].mean(), 4) if "r_multiple" in trades else None
        out["gross_pnl"] = round(trades["gross_pnl"].sum(), 2)
        out["total_commission"] = round(trades["commission"].sum(), 2)
        out["total_swap"] = round(trades["swap"].sum(), 2)
        out["total_spread_slippage_cost"] = round(trades["spread_slip_cost"].sum(), 2)
        out["net_pnl"] = round(net.sum(), 2)
        out["avg_bars_held"] = round(trades["bars_held"].mean(), 2)
        out["long_trades"] = int((trades["side"] == "LONG").sum())
        out["short_trades"] = int((trades["side"] == "SHORT").sum())
        out["long_net"] = round(trades.loc[trades["side"] == "LONG", "net_pnl"].sum(), 2)
        out["short_net"] = round(trades.loc[trades["side"] == "SHORT", "net_pnl"].sum(), 2)
        out["ambiguous_exits"] = int(trades["ambiguous_exit"].sum())
    out["sample_warning"] = (None if len(trades) >= 100 else
                             f"only {len(trades)} trades: ratios are statistically weak")
    return out
