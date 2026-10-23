"""Former swing hypotheses, now run intraday on M15 (operator rule: nothing slower than M15), plus the gold/silver pair."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from .base import Param, Strategy, StrategyContext, chk, fmt, level, spread_price


class RegimeRSI2Reversion(Strategy):
    strategy_id = "S07"
    name = "Regime-Filtered RSI(2) Reversion"
    style = Style.DAY
    timeframes = ("M15", "H1")
    primary_timeframe = "M15"
    min_history_bars = 260
    magic_offset = 7
    description = ("Buys sharp M15 pullbacks only when the hourly regime is up (H1 close > H1 SMA200, about 8 trading days), and sells "
                   "M15 spikes only when the hourly regime is down. Exits on reversion to a short mean.")
    long_rules = "Last completed H1 close > H1 SMA(regime_sma) and M15 RSI(2) < rsi_entry."
    short_rules = "Last completed H1 close < H1 SMA(regime_sma) and M15 RSI(2) > 100 - rsi_entry."
    exit_rules = "Close long when M15 close > SMA(5); cover short when close < SMA(5); stop stop_atr x ATR(14,M15); max hold 32 bars (8 hours)."
    invalidation_rules = "Hourly regime flipping against the position closes it."
    filters = "Spread guard; H1 ADX(14) < adx_max (very strong trends make counter-moves shallow and fast)."
    citations = (
        "Connors, L. & Alvarez, C. (2008) Short Term Trading Strategies That Work, TradingMarkets - RSI(2) with 200-day "
        "filter and 5-day MA exit (documented on US equities/indices; metals and the M15/H1 adaptation are untested hypotheses).",
    )
    limitations = ("Source evidence is daily equity-index long-side; the M15/H1 intraday version, the short side and metals are untested "
                   "adaptations. Intraday costs are a much larger share of each move. No fixed profit target.")
    PARAMS = {
        "rsi_entry": Param(15, "Looser than Connors' 10 so it trades several times a day.", (10, 15, 20)),
        "sma_exit": Param(5, "Short mean for exit.", (3, 5, 8)),
        "regime_sma": Param(200, "200 H1 bars, about 8 trading days.", (100, 200)),
        "stop_atr": Param(2.5, "Wide stop: reversion needs room.", (2.0, 2.5, 3.0)),
        "adx_max": Param(40, "Skip runaway trends.", (35, 40, 50)),
        "max_hold_bars": Param(32, "8 hours of M15 bars.", (16, 32, 48)),
        "max_spread_atr": Param(0.15, "Cost guard (M15 ranges are small next to the spread).", ()),
    }

    def prepare(self, frames):
        out = {}
        for (role, tf), df in frames.items():
            df = df.copy()
            if tf == "M15":
                df["rsi2"] = ind.rsi(df["close"], 2)
                df["sma_x"] = ind.sma(df["close"], self.p["sma_exit"])
                df["atr"] = ind.atr(df, 14)
            else:
                df["sma_r"] = ind.sma(df["close"], self.p["regime_sma"])
                df["adx"] = ind.adx(df, 14)
            out[(role, tf)] = df
        return out

    def _regime(self, ctx, role):
        d = ctx.last(role, "H1")
        if pd.isna(d["sma_r"]):
            return 0, d
        return (1 if d["close"] > d["sma_r"] else -1), d

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M15", 20) or not ctx.has(role, "H1", self.p["regime_sma"]):
                continue
            reg, d = self._regime(ctx, role)
            r = ctx.last(role, "M15")
            if reg == 0 or pd.isna(r["rsi2"]) or (not pd.isna(d["adx"]) and d["adx"] >= self.p["adx_max"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "rsi2": r["rsi2"], "sma5": r["sma_x"], "h1_close": d["close"], "h1_sma": d["sma_r"],
                    "h1_adx": d["adx"], "atr": r["atr"]}
            sd = self.p["stop_atr"] * r["atr"]
            if reg == 1 and r["rsi2"] < self.p["rsi_entry"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "hourly uptrend + sharp M15 dip", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif reg == -1 and r["rsi2"] > 100 - self.p["rsi_entry"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "hourly downtrend + sharp M15 spike", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M15")
            reg, d = self._regime(ctx, pos.role)
            vals = {"close": r["close"], "sma5": r["sma_x"], "regime": reg}
            if pos.side == "LONG" and (r["close"] > r["sma_x"] or reg == -1):
                out.append(self.close_signal(ctx, pos, "reverted above SMA5" if reg != -1 else "hourly regime turned down", vals))
            elif pos.side == "SHORT" and (r["close"] < r["sma_x"] or reg == 1):
                out.append(self.close_signal(ctx, pos, "reverted below SMA5" if reg != 1 else "hourly regime turned up", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        for role in self.roles:
            if not ctx.has(role, "M15", 20) or not ctx.has(role, "H1", self.p["regime_sma"]):
                out[role] = {"waiting": f"needs {self.p['regime_sma']} hourly bars for the trend filter"}
                continue
            r = ctx.last(role, "M15")
            reg, dd = self._regime(ctx, role)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            e = self.p["rsi_entry"]
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("SMA5 (exit)", r["sma_x"]), level(f"Hourly SMA{self.p['regime_sma']}", dd["sma_r"])) if x],
                "filters": [
                    chk(f"Hourly trend not runaway (ADX < {self.p['adx_max']})", pd.isna(dd["adx"]) or dd["adx"] < self.p["adx_max"],
                        f"hourly ADX {fmt(dd['adx'], 1)}"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}"),
                ],
                "long": [
                    chk("Hourly uptrend (close above SMA)", reg == 1, f"hourly close {fmt(dd['close'], d)} vs {fmt(dd['sma_r'], d)}"),
                    chk(f"M15 RSI(2) below {e} (sharp dip)", r["rsi2"] < e, f"RSI(2) {fmt(r['rsi2'], 1)}"),
                ],
                "short": [
                    chk("Hourly downtrend (close below SMA)", reg == -1, f"hourly close {fmt(dd['close'], d)} vs {fmt(dd['sma_r'], d)}"),
                    chk(f"M15 RSI(2) above {100 - e} (sharp spike)", r["rsi2"] > 100 - e, f"RSI(2) {fmt(r['rsi2'], 1)}"),
                ],
            }
        return out


class DonchianChannelBreakout(Strategy):
    strategy_id = "S08"
    name = "Donchian Channel Breakout"
    style = Style.DAY
    timeframes = ("M15",)
    primary_timeframe = "M15"
    min_history_bars = 120
    magic_offset = 8
    description = ("Turtle-style channel breakout on M15: enter on a close beyond the prior entry_n-bar extreme (8 hours by default), "
                   "exit on the exit_n-bar opposite extreme.")
    long_rules = "M15 close > highest high of the previous entry_n bars."
    short_rules = "M15 close < lowest low of the previous entry_n bars."
    exit_rules = "Close long on close < lowest low of previous exit_n bars (cover short mirror). Server stop at stop_n x N (ATR 20). Max hold 96 bars (one day). No fixed target."
    invalidation_rules = "The 2N stop."
    filters = "Spread guard. No pyramiding (simplification of original Turtle rules)."
    citations = (
        "Faith, C. (2007) Way of the Turtle, McGraw-Hill; and the public 'Original Turtle Trading Rules' (System 2: 55/20 day).",
        "Donchian, R. (1960s) channel rules as summarised in Kaufman (2013) Trading Systems and Methods.",
        "Hurst, Ooi & Pedersen (2017) 'A Century of Evidence on Trend-Following Investing', J. Portfolio Management 44(1).",
    )
    limitations = ("Trend-following evidence is for daily bars on diversified futures portfolios. This M15 version is an untested "
                   "intraday adaptation: many more false breakouts, and costs are a much larger share of each move.")
    PARAMS = {
        "entry_n": Param(24, "24 M15 bars = 6 hours (Turtle System 2 used 55 days).", (16, 24, 32)),
        "exit_n": Param(12, "12 M15 bars = 3 hours.", (8, 12, 16)),
        "stop_n": Param(2.0, "2N stop.", (2.0, 3.0)),
        "max_hold_bars": Param(96, "One day of M15 bars (safety cap).", (48, 96)),
        "max_spread_atr": Param(0.15, "Cost guard (M15 ranges are small next to the spread).", ()),
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
            if ctx.position(role) or not ctx.has(role, "M15", self.p["entry_n"] + 2):
                continue
            r = ctx.last(role, "M15")
            if pd.isna(r["hi_e"]) or pd.isna(r["atr"]) or spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "upper": r["hi_e"], "lower": r["lo_e"], "N_atr20": r["atr"]}
            sd = self.p["stop_n"] * r["atr"]
            if r["close"] > r["hi_e"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, f"close above {self.p['entry_n']}-bar high", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif r["close"] < r["lo_e"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, f"close below {self.p['entry_n']}-bar low", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M15")
            vals = {"close": r["close"], "exit_low": r["lo_x"], "exit_high": r["hi_x"]}
            if pos.side == "LONG" and r["close"] < r["lo_x"]:
                out.append(self.close_signal(ctx, pos, f"close below {self.p['exit_n']}-bar low", vals))
            elif pos.side == "SHORT" and r["close"] > r["hi_x"]:
                out.append(self.close_signal(ctx, pos, f"close above {self.p['exit_n']}-bar high", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, self.p["entry_n"] + 2):
                out[role] = {"waiting": f"needs {self.p['entry_n'] + 2} M15 bars"}
                continue
            r = ctx.last(role, tf)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            n, x = self.p["entry_n"], self.p["exit_n"]
            out[role] = {
                "price": float(r["close"]),
                "levels": [v for v in (level(f"{n}-bar high (long entry)", r["hi_e"]), level(f"{n}-bar low (short entry)", r["lo_e"]),
                                       level(f"{x}-bar low (long exit)", r["lo_x"]), level(f"{x}-bar high (short exit)", r["hi_x"])) if v],
                "filters": [chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}")],
                "long": [chk(f"M15 close above the {n}-bar high", r["close"] > r["hi_e"],
                             f"close {fmt(r['close'], d)} vs {fmt(r['hi_e'], d)} ({fmt(r['hi_e'] - r['close'], d)} away)")],
                "short": [chk(f"M15 close below the {n}-bar low", r["close"] < r["lo_e"],
                              f"close {fmt(r['close'], d)} vs {fmt(r['lo_e'], d)} ({fmt(r['close'] - r['lo_e'], d)} away)")],
            }
        return out


class TimeSeriesMomentum(Strategy):
    strategy_id = "S09"
    name = "Time-Series Momentum"
    style = Style.DAY
    timeframes = ("M15",)
    primary_timeframe = "M15"
    min_history_bars = 200
    magic_offset = 9
    description = ("Each metal is held long or short according to the sign of its own volatility-scaled past return over the last "
                   "lookback M15 bars, re-evaluated on the first M15 bar of every hour (short-horizon adaptation).")
    long_rules = "On the first M15 bar of each hour: lookback return / (bar vol x sqrt(lookback)) > entry_strength."
    short_rules = "Same, < -entry_strength."
    exit_rules = "At an hourly evaluation, close if strength fades to exit_strength or flips; server stop stop_atr x ATR(20); max hold 48 bars (12 hours)."
    invalidation_rules = "Sign reversal of the trend measure."
    filters = "Spread guard; one evaluation per hour (limits turnover)."
    citations = (
        "Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012) 'Time series momentum', J. Financial Economics 104(2):228-250 "
        "(58 futures incl. commodities; 1-12 month lookbacks).",
        "Kim, Tse & Wald (2016) 'Time series momentum and volatility scaling', J. Financial Markets 30 - critique: much of the "
        "effect comes from volatility scaling and weakens post-2009.",
    )
    limitations = ("Published results are portfolio-level across 58 markets at 1-12 month horizons. Single-metal momentum over one day "
                   "of M15 bars is an untested adaptation and mostly measures intraday noise.")
    PARAMS = {
        "lookback": Param(96, "96 M15 bars (one trading day); far shorter than the 1-12 month horizons studied.", (48, 96, 192)),
        "entry_strength": Param(0.3, "Require some trend (loosened from 0.5).", (0.2, 0.3, 0.5)),
        "exit_strength": Param(0.0, "Exit on sign flip.", (0.0, 0.25)),
        "stop_atr": Param(3.0, "Wide stop: momentum needs room.", (2.0, 3.0, 4.0)),
        "max_hold_bars": Param(48, "12 hours of M15 bars.", (24, 48, 96)),
        "max_spread_atr": Param(0.15, "Cost guard (M15 ranges are small next to the spread).", ()),
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
            hour_id = df["time"].dt.floor("1h")
            df["rebalance"] = hour_id != hour_id.shift(1)
            df["atr"] = ind.atr(df, 20)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M15", self.p["lookback"] + 61):
                continue
            r = ctx.last(role, "M15")
            if not r["rebalance"] or pd.isna(r["strength"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "lookback_return": r["ret_lb"], "strength": r["strength"], "atr": r["atr"]}
            sd = self.p["stop_atr"] * r["atr"]
            if r["strength"] > self.p["entry_strength"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "positive vol-scaled trend at hourly evaluation", vals,
                                        stop_price=r["close"] - sd, max_hold_bars=self.p["max_hold_bars"]))
            elif r["strength"] < -self.p["entry_strength"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "negative vol-scaled trend at hourly evaluation", vals,
                                        stop_price=r["close"] + sd, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M15")
            if not r["rebalance"] or pd.isna(r["strength"]):
                continue
            vals = {"strength": r["strength"]}
            if pos.side == "LONG" and r["strength"] <= self.p["exit_strength"]:
                out.append(self.close_signal(ctx, pos, "trend strength faded/flipped at hourly evaluation", vals))
            elif pos.side == "SHORT" and r["strength"] >= -self.p["exit_strength"]:
                out.append(self.close_signal(ctx, pos, "trend strength faded/flipped at hourly evaluation", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, self.p["lookback"] + 61):
                out[role] = {"waiting": f"needs {self.p['lookback'] + 61} M15 bars"}
                continue
            r = ctx.last(role, tf)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            th = self.p["entry_strength"]
            out[role] = {
                "price": float(r["close"]),
                "levels": [],
                "filters": [
                    chk("Hourly evaluation bar (first M15 bar of the hour)", bool(r["rebalance"]),
                        "evaluates once an hour; waiting for the next hour's first bar" if not r["rebalance"] else ""),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}"),
                ],
                "long": [chk(f"Trend strength above +{th}", r["strength"] > th,
                             f"strength {fmt(r['strength'], 2)} ({fmt(100 * r['ret_lb'], 2)}% over {self.p['lookback']} bars)")],
                "short": [chk(f"Trend strength below -{th}", r["strength"] < -th, f"strength {fmt(r['strength'], 2)}")],
            }
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
        "lookback": Param(96, "96 M15 bars (one trading day) for the intraday mean.", (64, 96, 192)),
        "entry_z": Param(1.5, "Stretch threshold (loosened from 2.0).", (1.25, 1.5, 2.0)),
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


    def diagnose(self, ctx):
        tf = self.primary_timeframe
        if not ctx.has("GOLD", tf, self.p["lookback"]) or not ctx.has("SILVER", tf, 21):
            w = {"waiting": f"needs {self.p['lookback']} M15 bars of both metals"}
            return {"GOLD": w, "SILVER": dict(w)}
        g, s = ctx.last("GOLD", tf), ctx.last("SILVER", tf)
        z, e = g["z"], self.p["entry_z"]
        common = [
            chk("Gold and silver bars aligned", g["time"] == s["time"], ""),
            chk(f"Metals still moving together (correlation >= {self.p['min_corr']})", g["corr"] >= self.p["min_corr"],
                f"correlation {fmt(g['corr'], 2)}"),
        ]
        zt = f"ratio z-score {fmt(z, 2)}"
        high = chk(f"Ratio stretched high (z > +{e})", z > e, zt)
        low = chk(f"Ratio stretched low (z < -{e})", z < -e, zt)
        return {
            "GOLD": {"price": float(g["close"]), "levels": [], "filters": common,
                     "long": [chk("Gold cheap vs silver: " + low["label"], low["ok"], zt)],
                     "short": [chk("Gold rich vs silver: " + high["label"], high["ok"], zt)]},
            "SILVER": {"price": float(s["close"]), "levels": [], "filters": common,
                       "long": [chk("Silver cheap vs gold: " + high["label"], high["ok"], zt)],
                       "short": [chk("Silver rich vs gold: " + low["label"], low["ok"], zt)]},
        }

