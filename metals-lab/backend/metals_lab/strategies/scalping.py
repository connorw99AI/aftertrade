"""Scalping hypotheses (M5 entries). Results on OHLC bars are flagged low-reliability."""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from datetime import datetime, timedelta, timezone

from .base import Param, Strategy, StrategyContext, chk, fmt, in_local_window, level, local_window, spread_price


class TrendPullbackScalper(Strategy):
    strategy_id = "S01"
    name = "Trend Pullback Scalper"
    style = Style.SCALP
    timeframes = ("M5", "M15")
    primary_timeframe = "M5"
    min_history_bars = 700
    magic_offset = 1
    description = ("Trades short M5 pullbacks to the 20-EMA in the direction of the M15 trend (M15 EMA50 vs EMA200 with a "
                   "rising or falling EMA50), only during London/New York liquidity. Trend from the slower chart, entry on the faster one.")
    long_rules = ("M15 uptrend (EMA50 > EMA200 and EMA50 rising over 4 M15 bars); previous M5 bar's low touched/pierced the M5 EMA20 "
                  "with RSI(7) < pullback level; current M5 bar closes back above EMA20, bullish body, RSI rising.")
    short_rules = "Mirror image: M15 downtrend, M5 rally to EMA20 with RSI(7) > 100-pullback level, bearish close back below EMA20."
    exit_rules = ("Server-side stop at entry -/+ stop_atr x ATR(14, M5); target at reward_risk x stop distance; "
                  "close early if the M5 close crosses the M5 EMA50 against the trade or the M15 trend flips; max hold 12 bars (1 hour).")
    invalidation_rules = "M5 close through EMA50 against the position, or the M15 trend turning, invalidates the premise."
    filters = ("Any hour the market is open (07:00-20:00 UTC only when use_session is on); spread <= max_spread_atr x ATR; ATR percentile (500 bars) between 20 and 90 "
               "to avoid dead and disorderly markets.")
    citations = (
        "Kaufman, P. (2013) Trading Systems and Methods, 5th ed., Wiley - trend filters and moving-average pullbacks.",
        "Wilder, J.W. (1978) New Concepts in Technical Trading Systems - ATR and RSI definitions.",
        "Batten, Lucey, McGroarty, Peat, Urquhart (2017) 'Stylized facts of intraday precious metals', PLoS ONE 12(4) - "
        "liquidity/volatility concentrate in European/US hours; spreads tightest while Europe is open.",
    )
    limitations = ("Generic trend-pullback logic, not a published metals-specific edge. On M5 OHLC the order of "
                   "stop/target hits inside a bar is unknown; costs are still a real share of the target. Moved from one-minute "
                   "to five-minute entries so years of history can be backtested (MT5 keeps far less one-minute history).")
    PARAMS = {
        "ema_fast": Param(20, "M5 pullback reference; common short EMA.", (15, 20, 30)),
        "ema_mid": Param(50, "M5 invalidation line (early exit).", (50,)),
        "trend_fast": Param(50, "M15 trend EMA (50 x 15 min = 12.5 hours).", (34, 50)),
        "ema_slow": Param(200, "M15 slow trend EMA (200 x 15 min = 50 hours).", (150, 200)),
        "rsi_n": Param(7, "Short RSI to measure pullback depth.", (5, 7)),
        "rsi_pull": Param(40, "Pullback must push RSI below this (long).", (35, 40, 45)),
        "stop_atr": Param(1.2, "Stop beyond normal M5 noise.", (1.0, 1.2, 1.5)),
        "reward_risk": Param(2.0, "Target a multiple of the stop so it clears costs.", (1.5, 2.0, 2.5)),
        "max_hold_bars": Param(12, "Scalps that do not work quickly are closed (12 M5 bars = 1 hour).", (6, 12, 18)),
        "max_spread_atr": Param(0.2, "Skip when the spread exceeds 20% of ATR(14,M5), like the other M5 bots.", (0.15, 0.2, 0.3)),
        "use_session": Param("off", "on = only trade inside this strategy's session window; off = any hour the market is open (operator choice for now).", ()),
        "start_hour": Param(7, "UTC start of liquid hours (used when use_session is on).", ()),
        "end_hour": Param(20, "UTC end of liquid hours.", ()),
    }

    def prepare(self, frames):
        out = {}
        for (role, tf), df in frames.items():
            df = df.copy()
            c = df["close"]
            if tf == "M15":
                df["t_fast"] = ind.ema(c, self.p["trend_fast"])
                df["t_slow"] = ind.ema(c, self.p["ema_slow"])
                df["t_slope"] = df["t_fast"] - df["t_fast"].shift(4)
            else:
                df["ema_f"] = ind.ema(c, self.p["ema_fast"])
                df["ema_m"] = ind.ema(c, self.p["ema_mid"])
                df["rsi"] = ind.rsi(c, self.p["rsi_n"])
                df["atr"] = ind.atr(df, 14)
                df["atr_pct"] = ind.rolling_percentile(df["atr"], 500)
            out[(role, tf)] = df
        return out

    def _trend(self, ctx, role) -> tuple[int, pd.Series]:
        """+1 M15 uptrend, -1 downtrend, 0 neither (last completed M15 bar)."""
        t = ctx.last(role, "M15")
        if pd.isna(t["t_slow"]) or pd.isna(t["t_slope"]):
            return 0, t
        if t["t_fast"] > t["t_slow"] and t["t_slope"] > 0:
            return 1, t
        if t["t_fast"] < t["t_slow"] and t["t_slope"] < 0:
            return -1, t
        return 0, t

    @property
    def session_text(self):
        return f"{self.p['start_hour']:02d}:00-{self.p['end_hour']:02d}:00 UTC" if self.p["use_session"] == "on" else ""

    def session_windows(self, day):
        if self.p["use_session"] != "on":
            return None
        d0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return [(d0 + timedelta(hours=self.p["start_hour"]), d0 + timedelta(hours=self.p["end_hour"]))]

    def entry_signals(self, ctx: StrategyContext):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 3) or not ctx.has(role, "M15", self.p["ema_slow"]):
                continue
            r, pv = ctx.last(role, "M5"), ctx.last(role, "M5", 2)
            trend, t = self._trend(ctx, role)
            spec = ctx.specs[role]
            hour = ctx.now.hour
            if self.p["use_session"] == "on" and not (self.p["start_hour"] <= hour < self.p["end_hour"]):
                continue
            if pd.isna(r["atr_pct"]) or not (20 <= r["atr_pct"] <= 90):
                continue
            if spread_price(r, spec) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "ema20": r["ema_f"], "ema50": r["ema_m"], "m15_ema_fast": t["t_fast"], "m15_ema_slow": t["t_slow"],
                    "rsi": r["rsi"], "prev_rsi": pv["rsi"], "atr": r["atr"], "atr_pct": r["atr_pct"]}
            stop_d = self.p["stop_atr"] * r["atr"]
            if (trend == 1 and pv["low"] <= pv["ema_f"] and r["close"] > r["ema_f"]
                    and r["close"] > r["open"] and pv["rsi"] < self.p["rsi_pull"] and r["rsi"] > pv["rsi"]):
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "M15 uptrend; M5 pullback to EMA20 rejected with bullish close",
                                        vals, stop_price=r["close"] - stop_d,
                                        target_price=r["close"] + self.p["reward_risk"] * stop_d,
                                        max_hold_bars=self.p["max_hold_bars"]))
            elif (trend == -1 and pv["high"] >= pv["ema_f"] and r["close"] < r["ema_f"]
                  and r["close"] < r["open"] and pv["rsi"] > 100 - self.p["rsi_pull"] and r["rsi"] < pv["rsi"]):
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "M15 downtrend; M5 rally to EMA20 rejected with bearish close",
                                        vals, stop_price=r["close"] + stop_d,
                                        target_price=r["close"] - self.p["reward_risk"] * stop_d,
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            trend = self._trend(ctx, pos.role)[0] if ctx.has(pos.role, "M15", 2) else 0
            vals = {"close": r["close"], "ema50": r["ema_m"], "m15_trend": trend}
            if pos.side == "LONG" and (r["close"] < r["ema_m"] or trend == -1):
                out.append(self.close_signal(ctx, pos, "M15 trend turned down" if trend == -1 else "closed below M5 EMA50: pullback failed", vals))
            elif pos.side == "SHORT" and (r["close"] > r["ema_m"] or trend == 1):
                out.append(self.close_signal(ctx, pos, "M15 trend turned up" if trend == 1 else "closed above M5 EMA50: rally held", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, 3) or not ctx.has(role, "M15", self.p["ema_slow"]):
                out[role] = {"waiting": f"needs {self.p['ema_slow']} M15 bars for the trend"}
                continue
            r, pv = ctx.last(role, tf), ctx.last(role, tf, 2)
            trend, t = self._trend(ctx, role)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("M5 EMA20", r["ema_f"]), level("M5 EMA50", r["ema_m"]), level("M15 EMA50", t["t_fast"]),
                                       level(f"M15 EMA{self.p['ema_slow']}", t["t_slow"])) if x],
                "filters": [
                    *([chk(f"Liquid hours ({self.p['start_hour']:02d}-{self.p['end_hour']:02d} UTC)",
                           self.p["start_hour"] <= ctx.now.hour < self.p["end_hour"], f"now {ctx.now:%H:%M} UTC")]
                      if self.p["use_session"] == "on" else []),
                    chk("Volatility normal (ATR percentile 20-90)", 20 <= (r["atr_pct"] if not pd.isna(r["atr_pct"]) else -1) <= 90,
                        f"percentile {fmt(r['atr_pct'], 0)}"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"],
                        f"spread {fmt(spr, d)} vs ATR {fmt(r['atr'], d)}"),
                ],
                "long": [
                    chk("M15 uptrend: EMA50 above EMA200 and rising", trend == 1, f"{fmt(t['t_fast'], d)} vs {fmt(t['t_slow'], d)}, 4-bar change {fmt(t['t_slope'], d)}"),
                    chk("Previous bar dipped to EMA20", pv["low"] <= pv["ema_f"], f"low {fmt(pv['low'], d)} vs EMA20 {fmt(pv['ema_f'], d)}"),
                    chk("Closed back above EMA20", r["close"] > r["ema_f"], f"close {fmt(r['close'], d)}"),
                    chk("Bullish candle", r["close"] > r["open"], ""),
                    chk(f"Pullback pushed RSI below {self.p['rsi_pull']}", pv["rsi"] < self.p["rsi_pull"], f"previous RSI {fmt(pv['rsi'], 1)}"),
                    chk("RSI turning up", r["rsi"] > pv["rsi"], f"RSI {fmt(r['rsi'], 1)}"),
                ],
                "short": [
                    chk("M15 downtrend: EMA50 below EMA200 and falling", trend == -1, f"{fmt(t['t_fast'], d)} vs {fmt(t['t_slow'], d)}, 4-bar change {fmt(t['t_slope'], d)}"),
                    chk("Previous bar rallied to EMA20", pv["high"] >= pv["ema_f"], f"high {fmt(pv['high'], d)} vs EMA20 {fmt(pv['ema_f'], d)}"),
                    chk("Closed back below EMA20", r["close"] < r["ema_f"], f"close {fmt(r['close'], d)}"),
                    chk("Bearish candle", r["close"] < r["open"], ""),
                    chk(f"Rally pushed RSI above {100 - self.p['rsi_pull']}", pv["rsi"] > 100 - self.p["rsi_pull"], f"previous RSI {fmt(pv['rsi'], 1)}"),
                    chk("RSI turning down", r["rsi"] < pv["rsi"], f"RSI {fmt(r['rsi'], 1)}"),
                ],
            }
        return out


class AsianRangeReversion(Strategy):
    strategy_id = "S02"
    name = "Asian Range Reversion Scalper"
    style = Style.SCALP
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 400
    magic_offset = 2
    description = ("Fades Bollinger-band excursions (bb_k sigma) back to the mean during the quieter Asian session, "
                   "only when ADX and band width say the market is ranging.")
    long_rules = "Entries 00:00-07:00 London time; ADX(14) < adx_max; band-width percentile < 50; close below lower band(20, bb_k) and RSI(5) < rsi_low."
    short_rules = "Same regime; close above upper band and RSI(5) > 100 - rsi_low."
    exit_rules = ("Exit at the 20-period mean (strategy close when close crosses SMA20; server TP at the mean at entry time); "
                  "stop stop_atr x ATR beyond entry; max hold 9 bars; flatten at flat_time London, before the 08:00 London open.")
    invalidation_rules = "ADX rising above adx_max+5 while in trade (range turning into trend) closes the position."
    filters = ("Spread <= max_spread_atr x ATR. Entries only in the Asian "
               "window (entry_start-entry_end London, following UK clock changes) and flat before the London open.")
    citations = (
        "Bollinger, J. (2001) Bollinger on Bollinger Bands, McGraw-Hill - band construction and mean-reversion use in ranges.",
        "Wilder (1978) - ADX as trend-strength filter.",
        "Batten et al. (2017) PLoS ONE - lower activity/volatility outside European and US hours.",
    )
    limitations = ("Asian-session spreads can be wide relative to the small target; mean reversion fails abruptly "
                   "on news (e.g. Chinese data, geopolitical headlines). OHLC intrabar ordering unknown.")
    PARAMS = {
        "bb_n": Param(20, "Standard band length.", (20,)),
        "bb_k": Param(1.8, "1.8 standard deviations (looser than 2.0 for more trades).", (1.5, 1.8, 2.0)),
        "rsi_low": Param(30, "Short-term oversold threshold (loosened from 20).", (20, 25, 30)),
        "adx_max": Param(25, "Only trade when trend strength is low.", (20, 25, 30)),
        "stop_atr": Param(1.0, "Tight stop; ranges that break tend to run.", (0.8, 1.0, 1.3)),
        "max_hold_bars": Param(9, "45 minutes.", (6, 9, 12)),
        "use_session": Param("on", "on (default) = entries only in the quiet Asian hours this bot is built for, flat before the London open; off = any hour the market is open.", ()),
        "tz": Param("Europe/London", "Session clock (follows UK summer time).", ()),
        "entry_start": Param("00:00", "London time: start of the quiet Asian hours.", ()),
        "entry_end": Param("07:00", "London time: no new entries after this.", ()),
        "flat_time": Param("07:45", "London time: flatten before the 08:00 London open.", ()),
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

    RETIRED_PARAMS = ("entry_start_hour", "entry_end_hour", "flat_hour")

    @property
    def session_text(self):
        return f"{self.p['entry_start']}-{self.p['entry_end']} London" if self.p["use_session"] == "on" else ""

    def session_windows(self, day):
        if self.p["use_session"] != "on":
            return None
        return [local_window(day, self.p["tz"], self.p["entry_start"], self.p["entry_end"])]

    def entry_signals(self, ctx):
        sigs = []
        if self.p["use_session"] == "on" and not in_local_window(ctx.now, self.p["tz"], self.p["entry_start"], self.p["entry_end"]):
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
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            vals = {"close": r["close"], "bb_mid": r["bb_m"], "adx": r["adx"], "time_utc": f"{ctx.now:%H:%M}"}
            if self.p["use_session"] == "on" and not in_local_window(ctx.now, self.p["tz"], self.p["entry_start"], self.p["flat_time"]):
                out.append(self.close_signal(ctx, pos, "session time stop before London open", vals))
            elif pos.side == "LONG" and r["close"] >= r["bb_m"]:
                out.append(self.close_signal(ctx, pos, "reverted to the mean", vals))
            elif pos.side == "SHORT" and r["close"] <= r["bb_m"]:
                out.append(self.close_signal(ctx, pos, "reverted to the mean", vals))
            elif r["adx"] > self.p["adx_max"] + 5:
                out.append(self.close_signal(ctx, pos, "ADX rising: range regime invalidated", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, 2):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r = ctx.last(role, tf)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("Upper band", r["bb_u"]), level("Mean (SMA20)", r["bb_m"]), level("Lower band", r["bb_l"])) if x],
                "filters": [
                    *([chk(f"Asian session ({self.p['entry_start']}-{self.p['entry_end']} London)",
                           in_local_window(ctx.now, self.p["tz"], self.p["entry_start"], self.p["entry_end"]), f"now {ctx.now:%H:%M} UTC")]
                      if self.p["use_session"] == "on" else []),
                    chk(f"Ranging: ADX below {self.p['adx_max']}", r["adx"] < self.p["adx_max"], f"ADX {fmt(r['adx'], 1)}"),
                    chk("Bands not wide (width percentile < 50)", r["bbw_pct"] < 50, f"percentile {fmt(r['bbw_pct'], 0)}"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"],
                        f"spread {fmt(spr, d)} vs ATR {fmt(r['atr'], d)}"),
                ],
                "long": [
                    chk("Closed below lower band", r["close"] < r["bb_l"], f"close {fmt(r['close'], d)} vs {fmt(r['bb_l'], d)}"),
                    chk(f"RSI(5) below {self.p['rsi_low']}", r["rsi"] < self.p["rsi_low"], f"RSI {fmt(r['rsi'], 1)}"),
                ],
                "short": [
                    chk("Closed above upper band", r["close"] > r["bb_u"], f"close {fmt(r['close'], d)} vs {fmt(r['bb_u'], d)}"),
                    chk(f"RSI(5) above {100 - self.p['rsi_low']}", r["rsi"] > 100 - self.p["rsi_low"], f"RSI {fmt(r['rsi'], 1)}"),
                ],
            }
        return out


class FailedBreakoutReversal(Strategy):
    strategy_id = "S03"
    ASSESSOR_FILTERS = {"sides": "long", "evidence": "All hours trade for now (operator choice; the backtest preferred 12-21 UTC). Both metals trade for now (operator choice). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): shorts net -76 vs longs +96; silver -185 vs gold +204; 07-12 UTC -39 vs later +58."}
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
    exit_rules = "Stop beyond the failed-breakout extreme + 0.2 ATR; target reward_risk x risk; max hold 10 bars (50 minutes)."
    invalidation_rules = "A new extreme beyond the breakout high/low (= the stop) invalidates the failure."
    filters = ("Any hour the market is open (08:00-21:00 London only when use_session is on); skip expansion bars (>2.5 ATR, likely news); "
               "skip strong trends (ADX >= adx_max); spread guard.")
    citations = (
        "Connors, L. & Raschke, L.B. (1995) Street Smarts: High Probability Short-Term Trading Strategies, M. Gordon Publishing "
        "- 'Turtle Soup' failed 20-day breakout pattern (daily bars; adapted here to M5, which is NOT validated by the source).",
    )
    limitations = "Source pattern is daily and not metals-specific. M5 adaptation is a hypothesis; stop placement on OHLC is coarse."
    PARAMS = {
        "lookback": Param(20, "Prior range length (Turtle Soup uses 20).", (15, 20, 30)),
        "reward_risk": Param(1.5, "Target multiple.", (1.0, 1.5, 2.0)),
        "adx_max": Param(30, "Avoid fading strong trends.", (25, 30)),
        "max_hold_bars": Param(10, "Reversal should work quickly (50 min).", (6, 10, 15)),
        "max_spread_atr": Param(0.15, "Cost guard: spread <= 15% of ATR(14,M5).", (0.15, 0.2)),
        "tz": Param("Europe/London", "Session clock (follows UK summer time).", ()),
        "use_session": Param("off", "on = only trade inside this strategy's session window; off = any hour the market is open (operator choice for now).", ()),
        "session_start": Param("08:00", "London time: London open.", ()),
        "session_end": Param("21:00", "London time: end of the New York afternoon.", ()),
    }

    @property
    def session_text(self):
        return f"{self.p['session_start']}-{self.p['session_end']} London" if self.p["use_session"] == "on" else ""

    def session_windows(self, day):
        if self.p["use_session"] != "on":
            return None
        return [local_window(day, self.p["tz"], self.p["session_start"], self.p["session_end"])]

    def _in_session(self, now) -> bool:
        return self.p["use_session"] != "on" or in_local_window(now, self.p["tz"], self.p["session_start"], self.p["session_end"])

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
        if not self._in_session(ctx.now):
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


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        n = self.p["lookback"]
        for role in self.roles:
            if not ctx.has(role, tf, 3):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r, b = ctx.last(role, tf), ctx.last(role, tf, 2)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            hi2, lo2 = max(r["high"], b["high"]), min(r["low"], b["low"])
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level(f"Prior {n}-bar high", r["ph"]), level(f"Prior {n}-bar low", r["pl"])) if x],
                "filters": [
                    *([chk(f"Session {self.session_text}", self._in_session(ctx.now), f"now {ctx.now:%H:%M} UTC")]
                      if self.p["use_session"] == "on" else []),
                    chk(f"Not a strong trend (ADX < {self.p['adx_max']})", r["adx"] < self.p["adx_max"], f"ADX {fmt(r['adx'], 1)}"),
                    chk("Breakout bar not a news spike (< 2.5 ATR)", (b["high"] - b["low"]) <= 2.5 * r["atr"],
                        f"range {fmt(b['high'] - b['low'], d)} vs ATR {fmt(r['atr'], d)}"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"],
                        f"spread {fmt(spr, d)}"),
                ],
                "long": [
                    chk(f"Price broke below the {n}-bar low", lo2 < r["pl"], f"low {fmt(lo2, d)} vs {fmt(r['pl'], d)}"),
                    chk("Closed back above it (failed breakdown)", r["close"] > r["pl"], f"close {fmt(r['close'], d)}"),
                    chk("Bullish candle", r["close"] > r["open"], ""),
                ],
                "short": [
                    chk(f"Price broke above the {n}-bar high", hi2 > r["ph"], f"high {fmt(hi2, d)} vs {fmt(r['ph'], d)}"),
                    chk("Closed back below it (failed breakout)", r["close"] < r["ph"], f"close {fmt(r['close'], d)}"),
                    chk("Bearish candle", r["close"] < r["open"], ""),
                ],
            }
        return out

