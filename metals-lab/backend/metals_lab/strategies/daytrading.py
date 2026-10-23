"""Day-trading hypotheses (M15/H1). All positions are flat by the end of the trading day."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from .base import Param, Strategy, StrategyContext, chk, fmt, level, spread_price


def _local_minutes(df: pd.DataFrame, tz: str) -> tuple[pd.Series, pd.Series]:
    loc = df["time"].dt.tz_convert(tz)
    return loc.dt.date, loc.dt.hour * 60 + loc.dt.minute


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class LondonOpeningRangeBreakout(Strategy):
    strategy_id = "S04"
    name = "London Opening Range Breakout"
    style = Style.DAY
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 200
    magic_offset = 4
    description = ("Builds the first hour range after the London open (Europe/London local time, so UK DST is handled) "
                   "and trades the first decisive close beyond it, flat by late afternoon London time.")
    long_rules = "First M5 close above OR high + buffer_atr x ATR(14) between OR end and entry cut-off, OR width within limits."
    short_rules = "First M5 close below OR low - buffer."
    exit_rules = ("Stop at the opposite side of the range (or range midpoint if the range is wider than wide_range_atr x ATR); "
                  "target tp_range_mult x OR width; time exit at exit_time London.")
    invalidation_rules = "Close back inside the opposite half of the range = stop; one breakout attempt per day."
    filters = "OR width between min_range_atr and max_range_atr x ATR(14, M5); spread guard."
    citations = (
        "Crabel, T. (1990) Day Trading with Short Term Price Patterns and Opening Range Breakout, Traders Press.",
        "Zarattini, C. & Aziz, A. (2023) 'Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits "
        "from Opening Range Breakout (ORB) Day Trading Strategy', SSRN 4416622 (equity index ETF evidence, not metals).",
        "Batten et al. (2017) PLoS ONE - intraday precious-metals volatility rises with the opening of major markets.",
    )
    limitations = ("Spot metals trade almost 24h; 'London open' is a liquidity event, not an exchange open, so ORB evidence "
                   "from equities does not transfer automatically. Breakouts fail regularly on rotational days.")
    PARAMS = {
        "tz": Param("Europe/London", "Session clock (DST-aware).", ()),
        "or_start": Param("08:00", "London open.", ()),
        "or_end": Param("09:00", "One-hour opening range.", ("08:30", "09:00")),
        "entry_cutoff": Param("14:00", "No new entries after early afternoon London.", ("12:00", "13:00", "14:00")),
        "exit_time": Param("16:30", "Flat before US afternoon.", ("15:00", "16:30")),
        "buffer_atr": Param(0.1, "Close must clear the range by a small margin.", (0.0, 0.1, 0.25)),
        "min_range_atr": Param(1.0, "Too-narrow ranges produce noise breaks (in M5 ATRs).", (1.0, 1.5, 2.0)),
        "max_range_atr": Param(10.0, "Too-wide ranges leave little room (in M5 ATRs).", (8.0, 10.0, 12.0)),
        "wide_range_atr": Param(6.0, "Use midpoint stop for wide ranges (in M5 ATRs).", (6.0,)),
        "tp_range_mult": Param(1.5, "Target as multiple of the range.", (1.0, 1.5, 2.0)),
        "max_hold_bars": Param(120, "Safety cap (10h of M5 bars).", ()),
        "max_spread_atr": Param(0.2, "Cost guard.", ()),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            df["atr"] = ind.atr(df, 14)
            d, mins = _local_minutes(df, self.p["tz"])
            df["ldate"], df["lmin"] = d, mins
            s, e, cut, ex = (_hhmm(self.p[k]) for k in ("or_start", "or_end", "entry_cutoff", "exit_time"))
            in_or = (mins >= s) & (mins < e)
            or_hi = df["high"].where(in_or).groupby(df["ldate"]).cummax()
            or_lo = df["low"].where(in_or).groupby(df["ldate"]).cummin()
            # Range is only known after its last bar completes: forward-fill within the day from bars >= OR end.
            after = mins >= e
            df["or_high"] = or_hi.groupby(df["ldate"]).ffill().where(after)
            df["or_low"] = or_lo.groupby(df["ldate"]).ffill().where(after)
            buf = self.p["buffer_atr"] * df["atr"]
            window = after & (mins < cut)
            up = window & (df["close"] > df["or_high"] + buf)
            dn = window & (df["close"] < df["or_low"] - buf)
            brk = (up | dn)
            first = brk & (brk.astype(int).groupby(df["ldate"]).cumsum() == 1)
            df["orb_dir"] = np.where(first & up, 1, np.where(first & dn, -1, 0))
            df["in_window"] = window
            df["brk_before"] = (brk.astype(int).groupby(df["ldate"]).cumsum() - brk.astype(int)) > 0
            df["time_exit"] = mins >= ex
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 20):
                continue
            r = ctx.last(role, "M5")
            if r["orb_dir"] == 0 or pd.isna(r["atr"]):
                continue
            width = r["or_high"] - r["or_low"]
            if not (self.p["min_range_atr"] * r["atr"] <= width <= self.p["max_range_atr"] * r["atr"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            mid = (r["or_high"] + r["or_low"]) / 2
            wide = width > self.p["wide_range_atr"] * r["atr"]
            vals = {"close": r["close"], "or_high": r["or_high"], "or_low": r["or_low"], "or_width": width, "atr": r["atr"]}
            if r["orb_dir"] == 1:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "first close above London opening range", vals,
                                        stop_price=mid if wide else r["or_low"],
                                        target_price=r["close"] + self.p["tp_range_mult"] * width,
                                        max_hold_bars=self.p["max_hold_bars"]))
            else:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "first close below London opening range", vals,
                                        stop_price=mid if wide else r["or_high"],
                                        target_price=r["close"] - self.p["tp_range_mult"] * width,
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            if r["time_exit"] or r["ldate"] != pd.Timestamp(pos.opened_at).tz_convert(self.p["tz"]).date():
                out.append(self.close_signal(ctx, pos, f"day-trade time exit ({self.p['exit_time']} London)", {"close": r["close"]}))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, 20):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r = ctx.last(role, tf)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            done = not pd.isna(r["or_high"])
            width = (r["or_high"] - r["or_low"]) if done else float("nan")
            buf = self.p["buffer_atr"] * r["atr"]
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("Opening range high", r["or_high"]), level("Opening range low", r["or_low"])) if x],
                "filters": [
                    chk(f"Opening range complete ({self.p['or_start']}-{self.p['or_end']} London)", done,
                        "range not built yet today" if not done else f"{fmt(r['or_low'], d)} - {fmt(r['or_high'], d)}"),
                    chk(f"Before entry cut-off ({self.p['entry_cutoff']} London)", bool(r["in_window"]), ""),
                    chk("No breakout taken yet today", not bool(r["brk_before"]), ""),
                    chk(f"Range width {self.p['min_range_atr']}-{self.p['max_range_atr']} ATR",
                        done and self.p["min_range_atr"] * r["atr"] <= width <= self.p["max_range_atr"] * r["atr"],
                        f"width {fmt(width, d)} = {fmt(width / r['atr'] if r['atr'] else float('nan'), 1)} ATR"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}"),
                ],
                "long": [chk("Closed above range high + buffer", done and r["close"] > r["or_high"] + buf,
                             f"close {fmt(r['close'], d)} vs {fmt(r['or_high'] + buf, d)}")],
                "short": [chk("Closed below range low - buffer", done and r["close"] < r["or_low"] - buf,
                              f"close {fmt(r['close'], d)} vs {fmt(r['or_low'] - buf, d)}")],
            }
        return out


class BreakoutRetest(Strategy):
    strategy_id = "S05"
    name = "London-Range Breakout & Retest"
    style = Style.DAY
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 200
    magic_offset = 5
    description = ("Marks the London-morning range (08:00-13:00 London). After a decisive break during the US "
                   "session handover, waits for price to come back and hold the broken level before entering.")
    long_rules = ("Breakout: close > range high + 0.2 ATR after 13:00 London. Retest within retest_bars: bar low <= level + "
                  "retest_tol_atr x ATR and close > level with bullish body -> OPEN_LONG.")
    short_rules = "Mirror for a break below range low and a failed retest from below."
    exit_rules = "Stop = min(retest low, level) - stop_atr x ATR; target reward_risk x risk; time exit 16:00 New York."
    invalidation_rules = "Close back inside the range beyond the level by more than stop distance = stop; setup expires after retest_bars."
    filters = "Range width 3-16 ATR(14, M5); spread guard; one setup per side per day."
    citations = (
        "Bulkowski, T. (2005) Encyclopedia of Chart Patterns, 2nd ed., Wiley - breakout pullback/throwback behaviour.",
        "Kaufman (2013) Trading Systems and Methods - breakout confirmation techniques.",
        "Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3):257-278 - intraday volatility "
        "patterns and US macro announcement effects (the reason the auditor guards 13:30/14:30 UK releases).",
    )
    limitations = ("Pattern literature is mostly equities/discretionary; retests are subjective and here are encoded with "
                   "fixed tolerances. Many US releases fall inside the setup window; the auditor will delay those.")
    PARAMS = {
        "range_start": Param("08:00", "London.", ()),
        "range_end": Param("13:00", "Before US data/NY open.", ()),
        "breakout_atr": Param(0.1, "Close beyond the level.", (0.05, 0.1, 0.2)),
        "retest_tol_atr": Param(0.4, "How close the retest must come.", (0.25, 0.4, 0.6)),
        "retest_bars": Param(36, "Setup expiry (3h of M5 bars).", (24, 36, 48)),
        "stop_atr": Param(0.5, "Below the retest structure.", (0.5, 0.75)),
        "reward_risk": Param(2.0, "Target multiple.", (1.5, 2.0, 2.5)),
        "max_hold_bars": Param(96, "Safety cap (8h of M5 bars).", ()),
        "max_spread_atr": Param(0.2, "Cost guard.", ()),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            df["atr"] = ind.atr(df, 14)
            d, mins = _local_minutes(df, "Europe/London")
            ny = df["time"].dt.tz_convert("America/New_York")
            df["ny_exit"] = (ny.dt.hour * 60 + ny.dt.minute) >= 16 * 60
            df["ldate"] = d
            s, e = _hhmm(self.p["range_start"]), _hhmm(self.p["range_end"])
            in_r = (mins >= s) & (mins < e)
            after = mins >= e
            df["lvl_hi"] = df["high"].where(in_r).groupby(d).cummax().groupby(d).ffill().where(after)
            df["lvl_lo"] = df["low"].where(in_r).groupby(d).cummin().groupby(d).ffill().where(after)
            # Sequential state machine (causal: only past bars of the same day).
            n = len(df)
            sig = np.zeros(n, dtype=int)
            up_since = np.full(n, np.nan)
            dn_since = np.full(n, np.nan)
            hi, lo, c, o, lw, hg, atr = (df[k].to_numpy() for k in ("lvl_hi", "lvl_lo", "close", "open", "low", "high", "atr"))
            dates = df["ldate"].to_numpy()
            cur_date, up_at, dn_at, used_up, used_dn = None, -1, -1, False, False
            for i in range(n):
                if dates[i] != cur_date:
                    cur_date, up_at, dn_at, used_up, used_dn = dates[i], -1, -1, False, False
                if np.isnan(hi[i]) or np.isnan(atr[i]):
                    continue
                if up_at < 0 and c[i] > hi[i] + self.p["breakout_atr"] * atr[i]:
                    up_at = i
                    up_since[i] = 0
                    continue
                if dn_at < 0 and c[i] < lo[i] - self.p["breakout_atr"] * atr[i]:
                    dn_at = i
                    dn_since[i] = 0
                    continue
                if up_at >= 0 and not used_up:
                    up_since[i] = i - up_at
                if dn_at >= 0 and not used_dn:
                    dn_since[i] = i - dn_at
                if up_at >= 0 and not used_up and i - up_at <= self.p["retest_bars"]:
                    if lw[i] <= hi[i] + self.p["retest_tol_atr"] * atr[i] and c[i] > hi[i] and c[i] > o[i]:
                        sig[i], used_up = 1, True
                if dn_at >= 0 and not used_dn and i - dn_at <= self.p["retest_bars"]:
                    if hg[i] >= lo[i] - self.p["retest_tol_atr"] * atr[i] and c[i] < lo[i] and c[i] < o[i]:
                        sig[i], used_dn = -1, True
            df["retest_dir"] = sig
            df["up_since"], df["dn_since"] = up_since, dn_since
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 20):
                continue
            r = ctx.last(role, "M5")
            if r["retest_dir"] == 0 or r["ny_exit"]:
                continue
            width = r["lvl_hi"] - r["lvl_lo"]
            if not (3 * r["atr"] <= width <= 16 * r["atr"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            vals = {"close": r["close"], "level_high": r["lvl_hi"], "level_low": r["lvl_lo"], "atr": r["atr"], "bar_low": r["low"], "bar_high": r["high"]}
            if r["retest_dir"] == 1:
                stop = min(r["low"], r["lvl_hi"]) - self.p["stop_atr"] * r["atr"]
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "broke London range high and held the retest", vals,
                                        stop_price=stop, target_price=r["close"] + self.p["reward_risk"] * (r["close"] - stop),
                                        max_hold_bars=self.p["max_hold_bars"]))
            else:
                stop = max(r["high"], r["lvl_lo"]) + self.p["stop_atr"] * r["atr"]
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "broke London range low and failed the retest from below", vals,
                                        stop_price=stop, target_price=r["close"] - self.p["reward_risk"] * (stop - r["close"]),
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            if r["ny_exit"]:
                out.append(self.close_signal(ctx, pos, "day-trade time exit (16:00 New York)", {"close": r["close"]}))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, 20):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r = ctx.last(role, tf)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            done = not pd.isna(r["lvl_hi"])
            width = (r["lvl_hi"] - r["lvl_lo"]) if done else float("nan")
            rb = self.p["retest_bars"]
            up, dn = r["up_since"], r["dn_since"]
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("London range high", r["lvl_hi"]), level("London range low", r["lvl_lo"])) if x],
                "filters": [
                    chk(f"London range complete ({self.p['range_start']}-{self.p['range_end']})", done, ""),
                    chk("Before 16:00 New York", not bool(r["ny_exit"]), ""),
                    chk("Range width 3-16 ATR", done and 3 * r["atr"] <= width <= 16 * r["atr"],
                        f"{fmt(width / r['atr'] if r['atr'] else float('nan'), 1)} ATR"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}"),
                ],
                "long": [
                    chk("Decisive break above range high", not pd.isna(up), "not yet" if pd.isna(up) else f"{int(up)} bars ago"),
                    chk(f"Retest window still open (<= {rb} bars)", not pd.isna(up) and up <= rb, ""),
                    chk("Retest touched the level and closed back above (bullish)", r["retest_dir"] == 1,
                        f"low {fmt(r['low'], d)}, close {fmt(r['close'], d)}"),
                ],
                "short": [
                    chk("Decisive break below range low", not pd.isna(dn), "not yet" if pd.isna(dn) else f"{int(dn)} bars ago"),
                    chk(f"Retest window still open (<= {rb} bars)", not pd.isna(dn) and dn <= rb, ""),
                    chk("Retest touched the level and closed back below (bearish)", r["retest_dir"] == -1,
                        f"high {fmt(r['high'], d)}, close {fmt(r['close'], d)}"),
                ],
            }
        return out


class VolatilityCompressionBreakout(Strategy):
    strategy_id = "S06"
    name = "Volatility Compression Breakout"
    style = Style.DAY
    timeframes = ("M15",)
    primary_timeframe = "M15"
    min_history_bars = 300
    magic_offset = 6
    description = ("Detects M15 volatility compression (Bollinger Bands inside Keltner Channels for several bars) and "
                   "trades the direction of the release beyond the compression range.")
    long_rules = ("Squeeze lasted >= min_squeeze bars and released within the last release_window bars; "
                  "close > highest high of the squeeze period.")
    short_rules = "Same release window; close < lowest low of the squeeze period."
    exit_rules = ("Stop stop_atr x ATR(20); target reward_risk x risk; strategy close when close crosses back through EMA20; "
                  "max hold 64 bars (16h); flatten Friday from 20:00 UTC (weekend gap risk).")
    invalidation_rules = "Close back through EMA20 against the position."
    filters = "Spread guard; no entries Friday after 18:00 UTC."
    citations = (
        "Carter, J. (2005) Mastering the Trade, McGraw-Hill - 'TTM Squeeze' (Bollinger inside Keltner) compression concept.",
        "Crabel (1990) - contraction/expansion (NR4/NR7) observations.",
        "Bollinger (2001) - 'The Squeeze' as low band-width preceding expansion.",
    )
    limitations = "Compression predicts expansion more reliably than direction; false releases are common. Indicator lengths are conventions."
    PARAMS = {
        "bb_k": Param(2.0, "Standard.", ()),
        "kc_k": Param(1.5, "Carter's setting.", (1.25, 1.5, 2.0)),
        "min_squeeze": Param(6, "Bars of compression required (1.5h of M15).", (4, 6, 12)),
        "stop_atr": Param(1.5, "Stop.", (1.0, 1.5, 2.0)),
        "reward_risk": Param(2.0, "Target multiple.", (1.5, 2.0, 3.0)),
        "max_hold_bars": Param(64, "16 hours of M15 bars.", (32, 64, 96)),
        "release_window": Param(4, "Bars after the release in which a breakout still counts (1 hour).", (0, 2, 4, 8)),
        "max_spread_atr": Param(0.15, "Cost guard (M15 ranges are smaller than H1).", ()),
    }

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            _, bu, bl = ind.bollinger(df["close"], 20, self.p["bb_k"])
            _, ku, kl = ind.keltner(df, 20, self.p["kc_k"])
            df["bb_u"], df["bb_l"] = bu, bl
            sq = (bu < ku) & (bl > kl)
            grp = (~sq).cumsum()
            df["sq_count"] = sq.astype(int).groupby(grp).cumsum()
            prev_count = df["sq_count"].shift(1).fillna(0)
            df["released"] = (~sq) & (prev_count >= self.p["min_squeeze"])
            # High/low of the squeeze run that just ended (bars i-k..i-1).
            hh = np.full(len(df), np.nan)
            ll = np.full(len(df), np.nan)
            rel, pc = df["released"].to_numpy(), prev_count.to_numpy().astype(int)
            h, l = df["high"].to_numpy(), df["low"].to_numpy()
            for i in np.flatnonzero(rel):
                k = pc[i]
                hh[i], ll[i] = h[i - k:i].max(), l[i - k:i].min()
            # The release stays tradable for release_window bars, unless a new squeeze starts first.
            idx = np.arange(len(df))
            rel_at = pd.Series(np.where(rel, idx, np.nan)).ffill().to_numpy()
            sqc = sq.astype(int).cumsum().to_numpy()
            sqc_at_rel = pd.Series(np.where(rel, sqc, np.nan)).ffill().to_numpy()
            live = (idx - rel_at <= self.p["release_window"]) & (sqc == sqc_at_rel)
            df["release_live"] = live
            df["sq_len"] = np.where(live, pd.Series(np.where(rel, pc, np.nan)).ffill().to_numpy(), np.nan)
            df["sq_high"] = np.where(live, pd.Series(hh).ffill().to_numpy(), np.nan)
            df["sq_low"] = np.where(live, pd.Series(ll).ffill().to_numpy(), np.nan)
            df["ema20"] = ind.ema(df["close"], 20)
            df["atr"] = ind.atr(df, 20)
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        if ctx.now.weekday() == 4 and ctx.now.hour >= 18:
            return sigs
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M15", 30):
                continue
            r = ctx.last(role, "M15")
            if not r["release_live"] or pd.isna(r["atr"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            sd = self.p["stop_atr"] * r["atr"]
            vals = {"close": r["close"], "squeeze_high": r["sq_high"], "squeeze_low": r["sq_low"], "atr": r["atr"]}
            if r["close"] > r["sq_high"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "volatility squeeze released upward", vals,
                                        stop_price=r["close"] - sd, target_price=r["close"] + self.p["reward_risk"] * sd,
                                        max_hold_bars=self.p["max_hold_bars"]))
            elif r["close"] < r["sq_low"]:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "volatility squeeze released downward", vals,
                                        stop_price=r["close"] + sd, target_price=r["close"] - self.p["reward_risk"] * sd,
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M15")
            vals = {"close": r["close"], "ema20": r["ema20"]}
            if ctx.now.weekday() == 4 and ctx.now.hour >= 20:
                out.append(self.close_signal(ctx, pos, "flatten before weekend", vals))
            elif pos.side == "LONG" and r["close"] < r["ema20"]:
                out.append(self.close_signal(ctx, pos, "closed back below EMA20", vals))
            elif pos.side == "SHORT" and r["close"] > r["ema20"]:
                out.append(self.close_signal(ctx, pos, "closed back above EMA20", vals))
        return out


    def diagnose(self, ctx):
        out = {}
        tf = self.primary_timeframe
        for role in self.roles:
            if not ctx.has(role, tf, 30):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r, pv = ctx.last(role, tf), ctx.last(role, tf, 2)
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            late_fri = ctx.now.weekday() == 4 and ctx.now.hour >= 18
            sq_now = int(r["sq_count"]) if not pd.isna(r["sq_count"]) else 0
            sq_prev = int(r["sq_len"]) if not pd.isna(r["sq_len"]) else (int(pv["sq_count"]) if not pd.isna(pv["sq_count"]) else 0)
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("EMA20", r["ema20"]), level("Bollinger upper", r["bb_u"]), level("Bollinger lower", r["bb_l"]),
                                       level("Squeeze high", r["sq_high"]), level("Squeeze low", r["sq_low"])) if x],
                "filters": [
                    chk("Not Friday after 18:00 UTC", not late_fri, ""),
                    chk(f"Squeeze lasted >= {self.p['min_squeeze']} bars", max(sq_now, sq_prev) >= self.p["min_squeeze"],
                        f"currently squeezed for {sq_now} bars" if sq_now else f"last squeeze {sq_prev} bars"),
                    chk(f"Squeeze released within the last {self.p['release_window']} bars", bool(r["release_live"]),
                        "still compressed" if sq_now else ""),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"], f"spread {fmt(spr, d)}"),
                ],
                "long": [chk("Closed above the squeeze high", bool(r["release_live"]) and r["close"] > r["sq_high"],
                             f"close {fmt(r['close'], d)} vs {fmt(r['sq_high'], d)}")],
                "short": [chk("Closed below the squeeze low", bool(r["release_live"]) and r["close"] < r["sq_low"],
                              f"close {fmt(r['close'], d)} vs {fmt(r['sq_low'], d)}")],
            }
        return out

