"""Scalping hypotheses (M5). Results on OHLC bars are flagged low-reliability."""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from .base import Param, Strategy, StrategyContext, spread_price


class TrendPullbackScalper(Strategy):
    strategy_id = "S01"
    name = "Trend Pullback Scalper"
    style = Style.SCALP
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 700
    magic_offset = 1
    description = ("Trades short pullbacks to the 20-EMA in the direction of an established M5 trend "
                   "(EMA50 vs EMA200 with positive slope), only during London/New York liquidity.")
    long_rules = ("Uptrend (EMA50 > EMA200 and EMA50 rising over 10 bars); previous bar's low touched/pierced EMA20 "
                  "with RSI(7) < pullback level; current bar closes back above EMA20, bullish body, RSI rising.")
    short_rules = "Mirror image: downtrend, rally to EMA20 with RSI(7) > 100-pullback level, bearish close back below EMA20."
    exit_rules = ("Server-side stop at entry -/+ stop_atr x ATR(14); target at reward_risk x stop distance; "
                  "close early if price closes through EMA50 against the trade; max hold 12 bars (1 hour).")
    invalidation_rules = "Close through EMA50 against the position invalidates the trend premise."
    filters = ("Hours 07:00-20:00 UTC; spread <= max_spread_atr x ATR; ATR percentile (500 bars) between 20 and 90 "
               "to avoid dead and disorderly markets.")
    citations = (
        "Kaufman, P. (2013) Trading Systems and Methods, 5th ed., Wiley - trend filters and moving-average pullbacks.",
        "Wilder, J.W. (1978) New Concepts in Technical Trading Systems - ATR and RSI definitions.",
        "Batten, Lucey, McGroarty, Peat, Urquhart (2017) 'Stylized facts of intraday precious metals', PLoS ONE 12(4) - "
        "liquidity/volatility concentrate in European/US hours; spreads tightest while Europe is open.",
    )
    limitations = ("Generic trend-pullback logic, not a published metals-specific edge. On M5 OHLC the order of "
                   "stop/target hits inside a bar is unknown; costs are a large fraction of the target.")
    PARAMS = {
        "ema_fast": Param(20, "Pullback reference; common short EMA.", (15, 20, 30)),
        "ema_mid": Param(50, "Trend/invalidation line.", (50,)),
        "ema_slow": Param(200, "Long trend filter.", (150, 200)),
        "rsi_n": Param(7, "Short RSI to measure pullback depth.", (5, 7)),
        "rsi_pull": Param(40, "Pullback must push RSI below this (long).", (35, 40, 45)),
        "stop_atr": Param(1.2, "Stop beyond normal M5 noise.", (1.0, 1.2, 1.5)),
        "reward_risk": Param(1.5, "Target as multiple of stop distance.", (1.0, 1.5, 2.0)),
        "max_hold_bars": Param(12, "Scalps that do not work quickly are closed.", (8, 12, 18)),
        "max_spread_atr": Param(0.15, "Skip when spread consumes >15% of ATR.", (0.1, 0.15, 0.2)),
        "start_hour": Param(7, "UTC start of liquid hours.", ()),
        "end_hour": Param(20, "UTC end of liquid hours.", ()),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            c = df["close"]
            df["ema_f"] = ind.ema(c, self.p["ema_fast"])
            df["ema_m"] = ind.ema(c, self.p["ema_mid"])
            df["ema_s"] = ind.ema(c, self.p["ema_slow"])
            df["slope"] = df["ema_m"] - df["ema_m"].shift(10)
            df["rsi"] = ind.rsi(c, self.p["rsi_n"])
            df["atr"] = ind.atr(df, 14)
            df["atr_pct"] = ind.rolling_percentile(df["atr"], 500)
            out[key] = df
        return out

    def entry_signals(self, ctx: StrategyContext):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 3):
                continue
            r, pv = ctx.last(role, "M5"), ctx.last(role, "M5", 2)
            spec = ctx.specs[role]
            hour = ctx.now.hour
            if not (self.p["start_hour"] <= hour < self.p["end_hour"]):
                continue
            if pd.isna(r["atr_pct"]) or not (20 <= r["atr_pct"] <= 90):
                continue
            if spread_price(r, spec) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "ema20": r["ema_f"], "ema50": r["ema_m"], "ema200": r["ema_s"],
                    "rsi": r["rsi"], "prev_rsi": pv["rsi"], "atr": r["atr"], "atr_pct": r["atr_pct"]}
            stop_d = self.p["stop_atr"] * r["atr"]
            if (r["ema_m"] > r["ema_s"] and r["slope"] > 0 and pv["low"] <= pv["ema_f"] and r["close"] > r["ema_f"]
                    and r["close"] > r["open"] and pv["rsi"] < self.p["rsi_pull"] and r["rsi"] > pv["rsi"]):
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "uptrend pullback to EMA20 rejected with bullish close",
                                        vals, stop_price=r["close"] - stop_d,
                                        target_price=r["close"] + self.p["reward_risk"] * stop_d,
                                        max_hold_bars=self.p["max_hold_bars"]))
            elif (r["ema_m"] < r["ema_s"] and r["slope"] < 0 and pv["high"] >= pv["ema_f"] and r["close"] < r["ema_f"]
                  and r["close"] < r["open"] and pv["rsi"] > 100 - self.p["rsi_pull"] and r["rsi"] < pv["rsi"]):
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "downtrend rally to EMA20 rejected with bearish close",
                                        vals, stop_price=r["close"] + stop_d,
                                        target_price=r["close"] - self.p["reward_risk"] * stop_d,
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            if pos.side == "LONG" and r["close"] < r["ema_m"]:
                out.append(self.close_signal(ctx, pos, "closed below EMA50: trend premise invalidated", {"close": r["close"], "ema50": r["ema_m"]}))
            elif pos.side == "SHORT" and r["close"] > r["ema_m"]:
                out.append(self.close_signal(ctx, pos, "closed above EMA50: trend premise invalidated", {"close": r["close"], "ema50": r["ema_m"]}))
        return out


class AsianRangeReversion(Strategy):
    strategy_id = "S02"
    name = "Asian Range Reversion Scalper"
    style = Style.SCALP
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 400
    magic_offset = 2
    description = ("Fades 2-sigma Bollinger excursions back to the mean during the quieter Asian session, "
                   "only when ADX and band width say the market is ranging.")
    long_rules = "Hours 00:00-06:00 UTC; ADX(14) < adx_max; band-width percentile < 50; close below lower band(20,2) and RSI(5) < rsi_low."
    short_rules = "Same regime; close above upper band and RSI(5) > 100 - rsi_low."
    exit_rules = ("Exit at the 20-period mean (strategy close when close crosses SMA20; server TP at the mean at entry time); "
                  "stop stop_atr x ATR beyond entry; max hold 9 bars; flatten at flat_hour UTC before London opens.")
    invalidation_rules = "ADX rising above adx_max+5 while in trade (range turning into trend) closes the position."
    filters = "Spread <= max_spread_atr x ATR; no new entries after entry_end_hour."
    citations = (
        "Bollinger, J. (2001) Bollinger on Bollinger Bands, McGraw-Hill - band construction and mean-reversion use in ranges.",
        "Wilder (1978) - ADX as trend-strength filter.",
        "Batten et al. (2017) PLoS ONE - lower activity/volatility outside European and US hours.",
    )
    limitations = ("Asian-session spreads can be wide relative to the small target; mean reversion fails abruptly "
                   "on news (e.g. Chinese data, geopolitical headlines). OHLC intrabar ordering unknown.")
    PARAMS = {
        "bb_n": Param(20, "Standard band length.", (20,)),
        "bb_k": Param(2.0, "2 standard deviations.", (1.8, 2.0, 2.3)),
        "rsi_low": Param(20, "Short-term oversold threshold.", (15, 20, 25)),
        "adx_max": Param(20, "Only trade when trend strength is low.", (18, 20, 25)),
        "stop_atr": Param(1.0, "Tight stop; ranges that break tend to run.", (0.8, 1.0, 1.3)),
        "max_hold_bars": Param(9, "45 minutes.", (6, 9, 12)),
        "entry_start_hour": Param(0, "UTC.", ()),
        "entry_end_hour": Param(6, "UTC.", ()),
        "flat_hour": Param(7, "UTC flatten before London open.", ()),
        "max_spread_atr": Param(0.2, "Cost guard.", (0.15, 0.2)),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            m, u, l = ind.bollinger(df["close"], self.p["bb_n"], self.p["bb_k"])
            df["bb_m"], df["bb_u"], df["bb_l"] = m, u, l
            df["bbw"] = (u - l) / m
            df["bbw_pct"] = ind.rolling_percentile(df["bbw"], 288)
            df["rsi"] = ind.rsi(df["close"], 5)
            df["adx"] = ind.adx(df, 14)
            df["atr"] = ind.atr(df, 14)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        h = ctx.now.hour
        if not (self.p["entry_start_hour"] <= h < self.p["entry_end_hour"]):
            return sigs
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 2):
                continue
            r = ctx.last(role, "M5")
            spec = ctx.specs[role]
            if pd.isna(r["adx"]) or pd.isna(r["bbw_pct"]) or r["adx"] >= self.p["adx_max"] or r["bbw_pct"] >= 50:
                continue
            if spread_price(r, spec) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "bb_lower": r["bb_l"], "bb_upper": r["bb_u"], "bb_mid": r["bb_m"],
                    "rsi5": r["rsi"], "adx": r["adx"], "bbw_pct": r["bbw_pct"], "atr": r["atr"]}
            sd = self.p["stop_atr"] * r["atr"]
            if r["close"] < r["bb_l"] and r["rsi"] < self.p["rsi_low"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "ranging Asian session, close below lower band, RSI oversold",
                                        vals, stop_price=r["close"] - sd, target_price=r["bb_m"], max_hold_bars=self.p["max_hold_bars"]))
            elif r["close"] > r["bb_u"] and r["rsi"] > 100 - self.p["rsi_low"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "ranging Asian session, close above upper band, RSI overbought",
                                        vals, stop_price=r["close"] + sd, target_price=r["bb_m"], max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        h = ctx.now.hour
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            vals = {"close": r["close"], "bb_mid": r["bb_m"], "adx": r["adx"], "hour_utc": h}
            if h >= self.p["flat_hour"] or h < self.p["entry_start_hour"]:
                out.append(self.close_signal(ctx, pos, "session time stop before London open", vals))
            elif pos.side == "LONG" and r["close"] >= r["bb_m"]:
                out.append(self.close_signal(ctx, pos, "reverted to the mean", vals))
            elif pos.side == "SHORT" and r["close"] <= r["bb_m"]:
                out.append(self.close_signal(ctx, pos, "reverted to the mean", vals))
            elif r["adx"] > self.p["adx_max"] + 5:
                out.append(self.close_signal(ctx, pos, "ADX rising: range regime invalidated", vals))
        return out


class FailedBreakoutReversal(Strategy):
    strategy_id = "S03"
    name = "Failed Breakout Reversal"
    style = Style.SCALP
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 300
    magic_offset = 3
    description = ("Explicitly defined failed breakout: price takes out the prior N-bar extreme, then closes back "
                   "inside the range on the same or next bar; trade the reversal (an intraday adaptation of 'Turtle Soup').")
    long_rules = ("Low of the last two bars < prior N-bar low (excluding those two bars); current close back above that "
                  "low with a bullish body; breakout bar range < 2.5 ATR; ADX(14) < adx_max.")
    short_rules = "Mirror: high of last two bars > prior N-bar high, close back below it with bearish body."
    exit_rules = "Stop beyond the failed-breakout extreme + 0.2 ATR; target reward_risk x risk; max hold 10 bars."
    invalidation_rules = "A new extreme beyond the breakout high/low (= the stop) invalidates the failure."
    filters = "07:00-20:00 UTC; skip expansion bars (>2.5 ATR, likely news); skip strong trends (ADX >= adx_max); spread guard."
    citations = (
        "Connors, L. & Raschke, L.B. (1995) Street Smarts: High Probability Short-Term Trading Strategies, M. Gordon Publishing "
        "- 'Turtle Soup' failed 20-day breakout pattern (daily bars; adapted here to M5, which is NOT validated by the source).",
    )
    limitations = "Source pattern is daily and not metals-specific. M5 adaptation is a hypothesis; stop placement on OHLC is coarse."
    PARAMS = {
        "lookback": Param(20, "Prior range length (Turtle Soup uses 20).", (15, 20, 30)),
        "reward_risk": Param(1.5, "Target multiple.", (1.0, 1.5, 2.0)),
        "adx_max": Param(30, "Avoid fading strong trends.", (25, 30)),
        "max_hold_bars": Param(10, "Reversal should work quickly.", (6, 10, 15)),
        "max_spread_atr": Param(0.15, "Cost guard.", (0.15,)),
    }

    def prepare(self, frames):
        out = {}
        n = self.p["lookback"]
        for key, df in frames.items():
            df = df.copy()
            df["ph"] = df["high"].rolling(n, min_periods=n).max().shift(2)
            df["pl"] = df["low"].rolling(n, min_periods=n).min().shift(2)
            df["atr"] = ind.atr(df, 14)
            df["adx"] = ind.adx(df, 14)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        if not (7 <= ctx.now.hour < 20):
            return sigs
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 3):
                continue
            r, b = ctx.last(role, "M5"), ctx.last(role, "M5", 2)
            spec = ctx.specs[role]
            if pd.isna(r["ph"]) or pd.isna(r["adx"]) or r["adx"] >= self.p["adx_max"]:
                continue
            if (b["high"] - b["low"]) > 2.5 * r["atr"] or spread_price(r, spec) > self.p["max_spread_atr"] * r["atr"]:
                continue
            hi2, lo2 = max(r["high"], b["high"]), min(r["low"], b["low"])
            vals = {"close": r["close"], "prior_high": r["ph"], "prior_low": r["pl"], "atr": r["atr"], "adx": r["adx"],
                    "excursion_high": hi2, "excursion_low": lo2}
            if hi2 > r["ph"] and r["close"] < r["ph"] and r["close"] < r["open"]:
                stop = hi2 + 0.2 * r["atr"]
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, f"failed breakout above {self.p['lookback']}-bar high, closed back inside",
                                        vals, stop_price=stop, target_price=r["close"] - self.p["reward_risk"] * (stop - r["close"]),
                                        max_hold_bars=self.p["max_hold_bars"]))
            elif lo2 < r["pl"] and r["close"] > r["pl"] and r["close"] > r["open"]:
                stop = lo2 - 0.2 * r["atr"]
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, f"failed breakdown below {self.p['lookback']}-bar low, closed back inside",
                                        vals, stop_price=stop, target_price=r["close"] + self.p["reward_risk"] * (r["close"] - stop),
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs
