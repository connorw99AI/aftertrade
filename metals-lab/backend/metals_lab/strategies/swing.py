"""Swing hypotheses (H4/D1), including the two-legged gold/silver relative-value strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from .base import Param, Strategy, StrategyContext, spread_price


class RegimeRSI2Reversion(Strategy):
    strategy_id = "S07"
    name = "Regime-Filtered RSI(2) Reversion"
    style = Style.SWING
    timeframes = ("H4", "D1")
    primary_timeframe = "H4"
    min_history_bars = 260
    magic_offset = 7
    description = ("Buys sharp short-term pullbacks only when the daily regime is up (close > SMA200 D1), and sells "
                   "short-term spikes only when the daily regime is down. Exits on reversion to a short mean.")
    long_rules = "Last completed D1 close > D1 SMA(200) and H4 RSI(2) < rsi_entry."
    short_rules = "Last completed D1 close < D1 SMA(200) and H4 RSI(2) > 100 - rsi_entry."
    exit_rules = "Close long when H4 close > SMA(5); cover short when close < SMA(5); stop stop_atr x ATR(14,H4); max hold 30 bars (~5 days)."
    invalidation_rules = "Daily regime flipping against the position closes it."
    filters = "Spread guard; D1 ADX(14) < adx_max (very strong trends make counter-moves shallow and fast)."
    citations = (
        "Connors, L. & Alvarez, C. (2008) Short Term Trading Strategies That Work, TradingMarkets - RSI(2) with 200-day "
        "filter and 5-day MA exit (documented on US equities/indices; metals and H4 adaptation are untested hypotheses).",
    )
    limitations = "Source evidence is equity-index long-side; short side and metals are lower-evidence. No fixed profit target."
    PARAMS = {
        "rsi_entry": Param(10, "Connors' extreme threshold.", (5, 10, 15)),
        "sma_exit": Param(5, "Short mean for exit.", (3, 5, 8)),
        "regime_sma": Param(200, "Classic regime filter.", (100, 200)),
        "stop_atr": Param(2.5, "Wide stop: reversion needs room.", (2.0, 2.5, 3.0)),
        "adx_max": Param(40, "Skip runaway trends.", (35, 40, 50)),
        "max_hold_bars": Param(30, "About a week.", (20, 30)),
        "max_spread_atr": Param(0.05, "Cost guard.", ()),
    }

    def prepare(self, frames):
        out = {}
        for (role, tf), df in frames.items():
            df = df.copy()
            if tf == "H4":
                df["rsi2"] = ind.rsi(df["close"], 2)
                df["sma_x"] = ind.sma(df["close"], self.p["sma_exit"])
                df["atr"] = ind.atr(df, 14)
            else:
                df["sma_r"] = ind.sma(df["close"], self.p["regime_sma"])
                df["adx"] = ind.adx(df, 14)
            out[(role, tf)] = df
        return out

    def _regime(self, ctx, role):
        d = ctx.last(role, "D1")
        if pd.isna(d["sma_r"]):
            return 0, d
        return (1 if d["close"] > d["sma_r"] else -1), d

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "H4", 20) or not ctx.has(role, "D1", self.p["regime_sma"]):
                continue
            reg, d = self._regime(ctx, role)
            r = ctx.last(role, "H4")
            if reg == 0 or pd.isna(r["rsi2"]) or (not pd.isna(d["adx"]) and d["adx"] >= self.p["adx_max"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "rsi2": r["rsi2"], "sma5": r["sma_x"], "d1_close": d["close"], "d1_sma200": d["sma_r"],
                    "d1_adx": d["adx"], "atr": r["atr"]}
            sd = self.p["stop_atr"] * r["atr"]
            if reg == 1 and r["rsi2"] < self.p["rsi_entry"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "daily uptrend + extreme H4 oversold", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif reg == -1 and r["rsi2"] > 100 - self.p["rsi_entry"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "daily downtrend + extreme H4 overbought", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "H4")
            reg, d = self._regime(ctx, pos.role)
            vals = {"close": r["close"], "sma5": r["sma_x"], "regime": reg}
            if pos.side == "LONG" and (r["close"] > r["sma_x"] or reg == -1):
                out.append(self.close_signal(ctx, pos, "reverted above SMA5" if reg != -1 else "daily regime turned down", vals))
            elif pos.side == "SHORT" and (r["close"] < r["sma_x"] or reg == 1):
                out.append(self.close_signal(ctx, pos, "reverted below SMA5" if reg != 1 else "daily regime turned up", vals))
        return out


class DonchianChannelBreakout(Strategy):
    strategy_id = "S08"
    name = "Donchian Channel Breakout"
    style = Style.SWING
    timeframes = ("D1",)
    primary_timeframe = "D1"
    min_history_bars = 120
    magic_offset = 8
    description = "Turtle-style multiday channel breakout: enter on a close beyond the prior 55-day extreme, exit on the 20-day opposite extreme."
    long_rules = "D1 close > highest high of the previous entry_n days."
    short_rules = "D1 close < lowest low of the previous entry_n days."
    exit_rules = "Close long on close < lowest low of previous exit_n days (cover short mirror). Server stop at stop_n x N (ATR 20). No fixed target."
    invalidation_rules = "The 2N stop."
    filters = "Spread guard. No pyramiding (simplification of original Turtle rules)."
    citations = (
        "Faith, C. (2007) Way of the Turtle, McGraw-Hill; and the public 'Original Turtle Trading Rules' (System 2: 55/20 day).",
        "Donchian, R. (1960s) channel rules as summarised in Kaufman (2013) Trading Systems and Methods.",
        "Hurst, Ooi & Pedersen (2017) 'A Century of Evidence on Trend-Following Investing', J. Portfolio Management 44(1).",
    )
    limitations = ("Trend-following evidence is for diversified futures portfolios; a 2-instrument version has long flat "
                   "or losing stretches and few trades, so statistical confidence will be low.")
    PARAMS = {
        "entry_n": Param(55, "Turtle System 2 entry.", (20, 40, 55)),
        "exit_n": Param(20, "Turtle System 2 exit.", (10, 20)),
        "stop_n": Param(2.0, "2N stop.", (2.0, 3.0)),
        "max_hold_bars": Param(250, "About one year (safety cap).", ()),
        "max_spread_atr": Param(0.05, "Cost guard.", ()),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            df["hi_e"], df["lo_e"] = ind.donchian(df, self.p["entry_n"])
            df["hi_x"], df["lo_x"] = ind.donchian(df, self.p["exit_n"])
            df["atr"] = ind.atr(df, 20)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "D1", self.p["entry_n"] + 2):
                continue
            r = ctx.last(role, "D1")
            if pd.isna(r["hi_e"]) or pd.isna(r["atr"]) or spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "upper": r["hi_e"], "lower": r["lo_e"], "N_atr20": r["atr"]}
            sd = self.p["stop_n"] * r["atr"]
            if r["close"] > r["hi_e"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, f"close above {self.p['entry_n']}-day high", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif r["close"] < r["lo_e"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, f"close below {self.p['entry_n']}-day low", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "D1")
            vals = {"close": r["close"], "exit_low": r["lo_x"], "exit_high": r["hi_x"]}
            if pos.side == "LONG" and r["close"] < r["lo_x"]:
                out.append(self.close_signal(ctx, pos, f"close below {self.p['exit_n']}-day low", vals))
            elif pos.side == "SHORT" and r["close"] > r["hi_x"]:
                out.append(self.close_signal(ctx, pos, f"close above {self.p['exit_n']}-day high", vals))
        return out


class TimeSeriesMomentum(Strategy):
    strategy_id = "S09"
    name = "Time-Series Momentum"
    style = Style.SWING
    timeframes = ("H1",)
    primary_timeframe = "H1"
    min_history_bars = 400
    magic_offset = 9
    description = ("Each metal is held long or short according to the sign of its own volatility-scaled past return, "
                   "re-evaluated once per day on H1 bars (short-horizon adaptation). Position size comes from the stop-based risk budget (a volatility scaling).")
    long_rules = "On the first H1 bar of each UTC day: lookback return / (daily vol x sqrt(lookback)) > entry_strength."
    short_rules = "Same, < -entry_strength."
    exit_rules = "On the daily evaluation close if strength sign flips or |strength| < exit_strength; catastrophic server stop stop_atr x ATR(20)."
    invalidation_rules = "Sign reversal of the trend measure."
    filters = "Spread guard; one evaluation per day (limits turnover)."
    citations = (
        "Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012) 'Time series momentum', J. Financial Economics 104(2):228-250 "
        "(58 futures incl. commodities; 1-12 month lookbacks).",
        "Kim, Tse & Wald (2016) 'Time series momentum and volatility scaling', J. Financial Markets 30 - critique: much of the "
        "effect comes from volatility scaling and weakens post-2009.",
    )
    limitations = "Published results are portfolio-level across 58 markets; single-metal TSMOM is noisy. The H1, ~5-day horizon is an untested adaptation of the monthly-horizon evidence."
    PARAMS = {
        "lookback": Param(120, "120 H1 bars (~5 trading days); far shorter than the 1-12 month horizons studied.", (48, 120, 240)),
        "entry_strength": Param(0.5, "Require a meaningful trend.", (0.25, 0.5, 1.0)),
        "exit_strength": Param(0.0, "Exit on sign flip.", (0.0, 0.25)),
        "stop_atr": Param(3.0, "Catastrophic stop only.", (3.0, 4.0)),
        "max_hold_bars": Param(480, "Safety cap (~4 weeks of H1 bars).", ()),
        "max_spread_atr": Param(0.05, "Cost guard.", ()),
    }

    def prepare(self, frames):
        out = {}
        lb = self.p["lookback"]
        for key, df in frames.items():
            df = df.copy()
            ret = df["close"] / df["close"].shift(lb) - 1
            vol = ind.realized_vol(df["close"], 60) * np.sqrt(lb)
            df["strength"] = ret / vol
            df["ret_lb"] = ret
            day_id = df["time"].dt.floor("1D")
            df["rebalance"] = day_id != day_id.shift(1)
            df["atr"] = ind.atr(df, 20)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "H1", self.p["lookback"] + 61):
                continue
            r = ctx.last(role, "H1")
            if not r["rebalance"] or pd.isna(r["strength"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "lookback_return": r["ret_lb"], "strength": r["strength"], "atr": r["atr"]}
            sd = self.p["stop_atr"] * r["atr"]
            if r["strength"] > self.p["entry_strength"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "positive vol-scaled trend at daily evaluation", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif r["strength"] < -self.p["entry_strength"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "negative vol-scaled trend at daily evaluation", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "H1")
            if not r["rebalance"] or pd.isna(r["strength"]):
                continue
            vals = {"strength": r["strength"]}
            if pos.side == "LONG" and r["strength"] <= self.p["exit_strength"]:
                out.append(self.close_signal(ctx, pos, "trend strength faded/flipped at daily evaluation", vals))
            elif pos.side == "SHORT" and r["strength"] >= -self.p["exit_strength"]:
                out.append(self.close_signal(ctx, pos, "trend strength faded/flipped at daily evaluation", vals))
        return out


class GoldSilverRatioReversion(Strategy):
    strategy_id = "S10"
    name = "Gold/Silver Ratio Reversion"
    style = Style.DAY
    timeframes = ("M15",)
    primary_timeframe = "M15"
    min_history_bars = 200
    magic_offset = 10
    description = ("Intraday relative-value strategy on the log gold/silver ratio (M15). When the ratio is stretched, buy the "
                   "cheap metal and sell the rich one as one explicitly defined two-leg trade group; exit both legs together.")
    long_rules = "z(ln(Gold/Silver), lookback) > entry_z: OPEN_LONG silver + OPEN_SHORT gold (group)."
    short_rules = "z < -entry_z: OPEN_SHORT silver + OPEN_LONG gold (group)."
    exit_rules = ("Close both legs when |z| < exit_z (reversion) or |z| > stop_z (divergence stop); max hold 96 bars (one day); "
                  "each leg also carries a server stop at leg_stop_atr x ATR(20, M15) so it is protected if the bot is offline. "
                  "If one leg is closed (e.g. by its stop) the other leg is closed ('group broken').")
    invalidation_rules = "Rolling 120-bar return correlation < min_corr (relationship breakdown) blocks entries."
    filters = "Both instruments must be tradable; spread guard on both legs."
    citations = (
        "Gatev, E., Goetzmann, W. & Rouwenhorst, K.G. (2006) 'Pairs Trading: Performance of a Relative-Value Arbitrage Rule', "
        "Review of Financial Studies 19(3):797-827 (equities; methodology reference).",
        "Lucey, B. & co-authors, Trinity College Dublin IIIS DP 55 - dynamic cointegration of gold and silver (weekly COMEX 1978-2002).",
        "MPRA paper 109830 - fractional cointegration between gold and silver.",
    )
    limitations = ("The ratio has trended for years at a time (e.g. 2011-2020); stationarity is not guaranteed. Legs are "
                   "risk-balanced via ATR stops, not dollar-neutral; contract sizes and min lots may make balance impossible "
                   "for a small virtual portfolio, in which case the trade is skipped.")
    roles = ("GOLD", "SILVER")
    PARAMS = {
        "lookback": Param(192, "192 M15 bars (~2 trading days) for the intraday mean.", (96, 192, 384)),
        "entry_z": Param(2.0, "Stretch threshold.", (1.5, 2.0, 2.5)),
        "exit_z": Param(0.5, "Reversion target.", (0.0, 0.5)),
        "stop_z": Param(3.5, "Divergence stop.", (3.0, 3.5, 4.0)),
        "min_corr": Param(0.5, "Relationship filter.", (0.3, 0.5)),
        "leg_stop_atr": Param(3.0, "Per-leg protective server stop.", (3.0, 4.0)),
        "max_hold_bars": Param(96, "One day of M15 bars.", (48, 96, 192)),
    }

    def risk_metadata(self) -> dict:
        m = super().risk_metadata()
        m["uses_trade_groups"] = True
        m["max_positions"] = 2
        return m

    def prepare(self, frames):
        out = {k: v.copy() for k, v in frames.items()}
        g, s = out.get(("GOLD", "M15")), out.get(("SILVER", "M15"))
        for df in (g, s):
            if df is not None:
                df["atr"] = ind.atr(df, 20)
        if g is None or s is None:
            return out
        m = g[["time", "close"]].merge(s[["time", "close"]], on="time", how="left", suffixes=("_g", "_s"))
        lr = np.log(m["close_g"] / m["close_s"])
        g["log_ratio"] = lr.to_numpy()
        g["z"] = ind.zscore(lr, self.p["lookback"]).to_numpy()
        rg, rs = np.log(m["close_g"]).diff(), np.log(m["close_s"]).diff()
        g["corr"] = rg.rolling(120, min_periods=60).corr(rs).to_numpy()
        g["silver_close"] = m["close_s"].to_numpy()
        return out

    def entry_signals(self, ctx):
        if ctx.positions or not ctx.has("GOLD", "M15", self.p["lookback"]) or not ctx.has("SILVER", "M15", 21):
            return []
        g, s = ctx.last("GOLD", "M15"), ctx.last("SILVER", "M15")
        if g["time"] != s["time"] or pd.isna(g["z"]) or pd.isna(g["corr"]) or g["corr"] < self.p["min_corr"]:
            return []
        vals = {"z": g["z"], "log_ratio": g["log_ratio"], "corr120": g["corr"], "gold": g["close"], "silver": s["close"]}
        gid = f"S10-{ctx.now:%Y%m%d%H%M}"
        ga, sa = self.p["leg_stop_atr"] * g["atr"], self.p["leg_stop_atr"] * s["atr"]
        mh = self.p["max_hold_bars"]
        if g["z"] > self.p["entry_z"]:
            why = "ratio stretched high: silver cheap vs gold"
            return [self.signal(ctx, "SILVER", Intent.OPEN_LONG, why, vals, stop_price=s["close"] - sa, group_id=gid, max_hold_bars=mh),
                    self.signal(ctx, "GOLD", Intent.OPEN_SHORT, why, vals, stop_price=g["close"] + ga, group_id=gid, max_hold_bars=mh)]
        if g["z"] < -self.p["entry_z"]:
            why = "ratio stretched low: gold cheap vs silver"
            return [self.signal(ctx, "SILVER", Intent.OPEN_SHORT, why, vals, stop_price=s["close"] + sa, group_id=gid, max_hold_bars=mh),
                    self.signal(ctx, "GOLD", Intent.OPEN_LONG, why, vals, stop_price=g["close"] - ga, group_id=gid, max_hold_bars=mh)]
        return []

    def exit_signals(self, ctx):
        if not ctx.positions:
            return []
        g = ctx.last("GOLD", "M15")
        vals = {"z": g["z"]}
        roles = {p.role for p in ctx.positions}
        if len(roles) < 2:
            why = "trade group broken (one leg closed); closing remaining leg"
        elif abs(g["z"]) < self.p["exit_z"]:
            why = "ratio reverted"
        elif abs(g["z"]) > self.p["stop_z"]:
            why = "ratio divergence stop"
        elif any(p.max_hold_bars and p.bars_held >= p.max_hold_bars for p in ctx.positions):
            why = "max holding period reached for the group"
        else:
            return []
        return [self.close_signal(ctx, p, why, vals) for p in ctx.positions]
