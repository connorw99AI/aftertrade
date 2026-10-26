"""Day-trading hypotheses (M15/H1). All positions are flat by the end of the trading day."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..domain import Intent, Style
from .base import Param, Strategy, chk, fmt, level, local_window, spread_price


def _local_minutes(df: pd.DataFrame, tz: str) -> tuple[pd.Series, pd.Series]:
    loc = df["time"].dt.tz_convert(tz)
    return loc.dt.date, loc.dt.hour * 60 + loc.dt.minute


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _range_day(dates: pd.Series, mins: pd.Series, start: int, sessions: bool) -> pd.Series:
    """Which day's range a bar belongs to. With sessions on, its own local date; with them off, a range lasts until
    the next day's range starts, so bars before ``start`` belong to the previous day's range."""
    if sessions:
        return dates
    prev = (pd.to_datetime(dates) - pd.Timedelta(days=1)).dt.date
    return pd.Series(np.where(mins >= start, dates, prev), index=dates.index)


class NewYorkOpeningRangeBreakout(Strategy):
    strategy_id = "S04"
    # The London-open version lost in the London hours and only made money 12-16 UTC (assessor, in-sample), so the range
    # now opens with New York. The old side/metal/hour evidence came from the London version and is not carried over.
    ASSESSOR_FILTERS = {}
    name = "New York Opening Range Breakout"
    style = Style.DAY
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 200
    magic_offset = 4
    description = ("Builds the first half hour of the New York session (08:30-09:00 New York time, when US data lands and "
                   "COMEX gold is open; DST handled) and trades the first decisive M5 close beyond it, flat by mid-afternoon New York.")
    long_rules = "First M5 close above the opening-range high + buffer_atr x ATR(14) between the range end and the entry cut-off."
    short_rules = "First M5 close below the opening-range low - buffer."
    exit_rules = ("Stop at the opposite side of the range (or range midpoint if the range is wider than wide_range_atr x ATR); "
                  "target tp_range_mult x range width; time exit at exit_time New York.")
    invalidation_rules = "Close back inside the opposite half of the range = stop; one breakout attempt per day."
    filters = ("Range width between min_range_atr and max_range_atr x ATR(14, M5); spread guard. With use_session off (default for now) "
               "entries are allowed at any hour after the range is built, until the next day's range starts; on = only until entry_cutoff.")
    citations = (
        "Crabel, T. (1990) Day Trading with Short Term Price Patterns and Opening Range Breakout, Traders Press.",
        "Zarattini, C. & Aziz, A. (2023) 'Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits "
        "from Opening Range Breakout (ORB) Day Trading Strategy', SSRN 4416622 (equity index ETF evidence, not metals).",
        "Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3) - US macro releases move gold intraday.",
        "Assessor review of the London-open version (MT5 2025-10-23..2026-10-23, in-sample): 07-12 UTC -118, 12-16 UTC +91.",
    )
    limitations = ("Spot metals trade almost 24h; the New York open is a liquidity and data event, not an exchange open, so ORB "
                   "evidence from equities does not transfer automatically. The 08:30 data release can make the range unusually wide.")
    PARAMS = {
        "tz": Param("America/New_York", "Session clock (follows US daylight saving).", ()),
        "use_session": Param("off", "on = entries only in the session window and flat by the time exit; off = after the range is built, entries at any hour until the next range starts, exits by stop, target or max hold.", ()),
        "or_start": Param("08:30", "US data releases and the active New York gold session.", ()),
        "or_end": Param("09:00", "Half-hour opening range (6 M5 bars).", ("08:45", "09:00", "09:30")),
        "entry_cutoff": Param("11:30", "No new entries after late morning New York.", ("11:00", "11:30", "12:30")),
        "exit_time": Param("15:00", "Flat by mid-afternoon New York.", ("13:30", "15:00")),
        "buffer_atr": Param(0.1, "Close must clear the range by a small margin.", (0.0, 0.1, 0.25)),
        "min_range_atr": Param(1.0, "Too-narrow ranges produce noise breaks (in M5 ATRs).", (1.0, 1.5, 2.0)),
        "max_range_atr": Param(10.0, "Too-wide ranges leave little room (in M5 ATRs).", (8.0, 10.0, 12.0)),
        "wide_range_atr": Param(6.0, "Use midpoint stop for wide ranges (in M5 ATRs).", (6.0,)),
        "tp_range_mult": Param(1.5, "Target as multiple of the range.", (1.0, 1.5, 2.0)),
        "max_hold_bars": Param(84, "Safety cap (7h of M5 bars).", ()),
        "max_spread_atr": Param(0.2, "Cost guard.", ()),
    }

    @property
    def session_text(self):
        if self.p["use_session"] == "on":
            return f"{self.p['or_end']}-{self.p['entry_cutoff']} New York"
        return f"any time except while the range is built ({self.p['or_start']}-{self.p['or_end']} New York)"

    def session_windows(self, day):
        if self.p["use_session"] == "on":
            return [local_window(day, self.p["tz"], self.p["or_end"], self.p["entry_cutoff"])]
        return [local_window(day, self.p["tz"], "00:00", self.p["or_start"]), local_window(day, self.p["tz"], self.p["or_end"], "00:00")]

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            df["atr"] = ind.atr(df, 14)
            d, mins = _local_minutes(df, self.p["tz"])
            sessions = self.p["use_session"] == "on"
            s, e, cut, ex = (_hhmm(self.p[k]) for k in ("or_start", "or_end", "entry_cutoff", "exit_time"))
            rday = _range_day(d, mins, s, sessions)
            df["ldate"], df["lmin"] = rday, mins
            in_or = (mins >= s) & (mins < e)
            or_hi = df["high"].where(in_or).groupby(rday).cummax()
            or_lo = df["low"].where(in_or).groupby(rday).cummin()
            # Range is only known after its last bar completes: forward-fill from bars >= OR end (and, with sessions off,
            # through the night until the next day's range starts).
            after = (mins >= e) | (pd.Series(d.values, index=d.index) != rday)
            df["or_high"] = or_hi.groupby(rday).ffill().where(after)
            df["or_low"] = or_lo.groupby(rday).ffill().where(after)
            buf = self.p["buffer_atr"] * df["atr"]
            window = after & (mins < cut) if sessions else after
            up = window & (df["close"] > df["or_high"] + buf)
            dn = window & (df["close"] < df["or_low"] - buf)
            brk = (up | dn)
            first = brk & (brk.astype(int).groupby(rday).cumsum() == 1)
            df["orb_dir"] = np.where(first & up, 1, np.where(first & dn, -1, 0))
            df["in_window"] = window
            df["brk_before"] = (brk.astype(int).groupby(rday).cumsum() - brk.astype(int)) > 0
            df["time_exit"] = (mins >= ex) if sessions else False
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
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "first close above the New York opening range", vals,
                                        stop_price=mid if wide else r["or_low"],
                                        target_price=r["close"] + self.p["tp_range_mult"] * width,
                                        max_hold_bars=self.p["max_hold_bars"]))
            else:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, "first close below the New York opening range", vals,
                                        stop_price=mid if wide else r["or_high"],
                                        target_price=r["close"] - self.p["tp_range_mult"] * width,
                                        max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            if self.p["use_session"] != "on":
                continue  # exits by stop, target or max hold only
            if r["time_exit"] or r["ldate"] != pd.Timestamp(pos.opened_at).tz_convert(self.p["tz"]).date():
                out.append(self.close_signal(ctx, pos, f"day-trade time exit ({self.p['exit_time']} New York)", {"close": r["close"]}))
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
                    chk(f"Opening range complete ({self.p['or_start']}-{self.p['or_end']} New York)", done,
                        "range not built yet today" if not done else f"{fmt(r['or_low'], d)} - {fmt(r['or_high'], d)}"),
                    *([chk(f"Before entry cut-off ({self.p['entry_cutoff']} New York)", bool(r["in_window"]), "")]
                      if self.p["use_session"] == "on" else []),
                    chk("No breakout taken from this range yet", not bool(r["brk_before"]), ""),
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
    ASSESSOR_FILTERS = {"sides": "short", "evidence": "Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): longs -81 vs shorts +190."}
    name = "London-Range Breakout & Retest"
    style = Style.DAY
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 200
    magic_offset = 5
    description = ("Marks the London-morning range (08:00-13:00 London). After a decisive break during the US "
                   "session handover, waits for price to come back and hold the broken level before entering.")
    long_rules = ("Breakout: close > range high + breakout_atr x ATR (0.1 by default) after 13:00 London. Retest within retest_bars: bar low <= level + "
                  "retest_tol_atr x ATR and close > level with bullish body -> OPEN_LONG.")
    short_rules = "Mirror for a break below range low and a failed retest from below."
    exit_rules = "Stop = min(retest low, level) - stop_atr x ATR; target reward_risk x risk; time exit 16:00 New York."
    invalidation_rules = "Close back inside the range beyond the level by more than stop distance = stop; setup expires after retest_bars."
    filters = ("Range width 3-16 ATR(14, M5); spread guard; one setup per side per range. With use_session off (default for now) "
               "a range stays tradable at any hour until the next range starts; on = only until 16:00 New York.")
    citations = (
        "Bulkowski, T. (2005) Encyclopedia of Chart Patterns, 2nd ed., Wiley - breakout pullback/throwback behaviour.",
        "Kaufman (2013) Trading Systems and Methods - breakout confirmation techniques.",
        "Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3):257-278 - intraday volatility "
        "patterns and US macro announcement effects (the reason the auditor guards 13:30/14:30 UK releases).",
    )
    limitations = ("Pattern literature is mostly equities/discretionary; retests are subjective and here are encoded with "
                   "fixed tolerances. Many US releases fall inside the setup window; the auditor will delay those.")
    PARAMS = {
        "use_session": Param("off", "on = entries only in the session window and flat by the time exit; off = after the range is built, entries at any hour until the next range starts, exits by stop, target or max hold.", ()),
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

    @property
    def session_text(self):
        if self.p["use_session"] == "on":
            return f"{self.p['range_end']} London - 16:00 New York"
        return f"any time except while the range is built ({self.p['range_start']}-{self.p['range_end']} London)"

    def session_windows(self, day):
        if self.p["use_session"] != "on":
            return [local_window(day, "Europe/London", "00:00", self.p["range_start"]),
                    local_window(day, "Europe/London", self.p["range_end"], "00:00")]
        start = local_window(day, "Europe/London", self.p["range_end"], "23:59")[0]
        end = local_window(day, "America/New_York", "16:00", "16:01")[0]
        return [(start, end)] if end > start else []

    def prepare(self, frames):
        out = {}
        for key, df in frames.items():
            df = df.copy()
            df["atr"] = ind.atr(df, 14)
            d, mins = _local_minutes(df, "Europe/London")
            ny = df["time"].dt.tz_convert("America/New_York")
            sessions = self.p["use_session"] == "on"
            df["ny_exit"] = ((ny.dt.hour * 60 + ny.dt.minute) >= 16 * 60) if sessions else False
            s, e = _hhmm(self.p["range_start"]), _hhmm(self.p["range_end"])
            rday = _range_day(d, mins, s, sessions)
            df["ldate"] = rday
            in_r = (mins >= s) & (mins < e)
            after = (mins >= e) | (pd.Series(d.values, index=d.index) != rday)
            df["lvl_hi"] = df["high"].where(in_r).groupby(rday).cummax().groupby(rday).ffill().where(after)
            df["lvl_lo"] = df["low"].where(in_r).groupby(rday).cummin().groupby(rday).ffill().where(after)
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
                    *([chk("Before 16:00 New York", not bool(r["ny_exit"]), "")] if self.p["use_session"] == "on" else []),
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
    ASSESSOR_FILTERS = {"sides": "long", "evidence": "All hours trade for now (operator choice; the backtest preferred 0-7,16-24 UTC). Both metals trade for now (operator choice). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): shorts -269 vs longs +276; silver -27 vs gold +35; 07-16 UTC lost, Asia +125."}
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
            r, pv = ctx.last(role, "M15"), ctx.last(role, "M15", 2)
            if not r["release_live"] or pd.isna(r["atr"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            sd = self.p["stop_atr"] * r["atr"]
            vals = {"close": r["close"], "squeeze_high": r["sq_high"], "squeeze_low": r["sq_low"], "atr": r["atr"]}
            # only the first close beyond the squeeze range: a trade that closes does not re-enter the same breakout
            first_up = not (pv["release_live"] and pv["sq_high"] == r["sq_high"] and pv["close"] > pv["sq_high"])
            first_dn = not (pv["release_live"] and pv["sq_low"] == r["sq_low"] and pv["close"] < pv["sq_low"])
            if r["close"] > r["sq_high"] and first_up:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, "volatility squeeze released upward", vals,
                                        stop_price=r["close"] - sd, target_price=r["close"] + self.p["reward_risk"] * sd,
                                        max_hold_bars=self.p["max_hold_bars"]))
            elif r["close"] < r["sq_low"] and first_dn:
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



class SessionMomentum(Strategy):
    strategy_id = "S11"
    ASSESSOR_FILTERS = {}
    name = "Session Momentum (first half hour -> last half hour)"
    style = Style.DAY
    timeframes = ("M5",)
    primary_timeframe = "M5"
    min_history_bars = 400  # enough M5 bars to hold the previous session's close
    magic_offset = 11
    description = ("Intraday momentum: the move from the previous session's close to the end of the first half hour of the "
                   "New York metals session tends to continue into the session's last half hour. One trade per metal per "
                   "day, opened at the start of the last half hour and closed at the session close.")
    long_rules = ("At session_close - 30 min (New York time): if the first-half-hour return (previous session close -> "
                  "session_open + 30 min) is above +min_move, OPEN_LONG.")
    short_rules = "Mirror: first-half-hour return below -min_move -> OPEN_SHORT."
    exit_rules = ("Close at session_close (New York). Stop stop_atr x ATR(14, M5) from entry; no fixed target. "
                  "max_hold_bars is a safety cap.")
    invalidation_rules = "Stop hit, or the session close."
    filters = "Spread guard; the first half hour must be complete and the previous session's close known; one entry per day."
    citations = (
        "Gao, L., Han, Y., Li, S. Z. & Zhou, G. (2018) 'Market intraday momentum', Journal of Financial Economics 129(2):394-414 "
        "- the first half-hour return predicts the last half-hour return (S&P 500 ETF 1993-2013 and other ETFs and index futures).",
        "A commodity-futures study (University of Hull repository) reports the same predictability in four commodity futures; "
        "whether gold or silver were among them is not confirmed here.",
        "Studies of Chinese metal futures found the day-session effect disappeared there (Pacific-Basin Finance Journal 80, 2023).",
    )
    limitations = ("Evidence is strongest for US equities; for spot gold and silver it is unproven and must be tested on your "
                   "own MT5 history. The session hours follow COMEX (08:20-13:30 New York); spot metals trade around the clock, "
                   "so the 'open' here is a convention. Few trades (one per metal per day) means results take months to judge.")
    PARAMS = {
        "tz": Param("America/New_York", "Session clock (follows US summer time).", ()),
        "session_open": Param("08:20", "New York: COMEX metals open.", ()),
        "session_close": Param("13:30", "New York: COMEX metals close.", ()),
        "min_move": Param(0.0, "Smallest first-half-hour return (as a fraction, 0.001 = 0.1%) that counts as a direction.",
                          (0.0, 0.001, 0.002)),
        "stop_atr": Param(2.0, "Stop distance in ATR(14) of M5 bars.", (1.5, 2.0, 3.0)),
        "max_hold_bars": Param(8, "Safety cap (40 min of M5 bars).", ()),
        "max_spread_atr": Param(0.25, "Cost guard.", ()),
    }

    @property
    def session_text(self):
        close = _hhmm(self.p["session_close"]) - 30
        return f"{close // 60:02d}:{close % 60:02d}-{self.p['session_close']} New York (the session's last half hour)"

    def session_windows(self, day):
        close = _hhmm(self.p["session_close"])
        start = f"{(close - 30) // 60:02d}:{(close - 30) % 60:02d}"
        return [local_window(day, self.p["tz"], start, self.p["session_close"])]

    def prepare(self, frames):
        out = {}
        o, c = _hhmm(self.p["session_open"]), _hhmm(self.p["session_close"])
        for key, df in frames.items():
            df = df.copy()
            df["atr"] = ind.atr(df, 14)
            d, mins = _local_minutes(df, self.p["tz"])
            day = pd.Series(pd.to_datetime(d).values, index=df.index)
            in_sess = (mins >= o) & (mins < c)
            # the session's close price: close of its last bar; the previous session's is carried to the next day
            last_close = df["close"].where(in_sess).groupby(day).last().dropna()  # days with a session only (no weekends)
            prev_close = last_close.shift(1)
            df["prev_close"] = day.map(prev_close)
            # price at the end of the first half hour: close of the bar that ends at open + 30 min
            first = df["close"].where(mins == o + 25).groupby(day).transform("max")
            df["first_close"] = first.where(mins >= o + 30)
            df["r_first"] = df["first_close"] / df["prev_close"] - 1.0
            df["entry_bar"] = mins == c - 35          # this bar closes at the start of the last half hour
            df["exit_bar"] = mins >= c - 5            # bars closing at or after the session close
            df["sday"] = day
            out[key] = df
        return out

    def entry_signals(self, ctx):
        sigs = []
        for role in self.roles:
            if ctx.position(role) or not ctx.has(role, "M5", 20):
                continue
            r = ctx.last(role, "M5")
            if not r["entry_bar"] or pd.isna(r["r_first"]) or pd.isna(r["atr"]):
                continue
            if spread_price(r, ctx.specs[role]) > self.p["max_spread_atr"] * r["atr"]:
                continue
            mv = float(r["r_first"])
            if abs(mv) <= self.p["min_move"] or mv == 0:
                continue
            vals = {"first_half_hour_return": mv, "prev_session_close": r["prev_close"], "first_half_hour_close": r["first_close"],
                    "close": r["close"], "atr": r["atr"]}
            dist = self.p["stop_atr"] * r["atr"]
            if mv > 0:
                sigs.append(self.signal(ctx, role, Intent.OPEN_LONG, f"first half hour up {mv:+.2%}: ride it into the close", vals,
                                        stop_price=r["close"] - dist, max_hold_bars=self.p["max_hold_bars"]))
            else:
                sigs.append(self.signal(ctx, role, Intent.OPEN_SHORT, f"first half hour down {mv:+.2%}: ride it into the close", vals,
                                        stop_price=r["close"] + dist, max_hold_bars=self.p["max_hold_bars"]))
        return sigs

    def exit_signals(self, ctx):
        out = []
        for pos in ctx.positions:
            r = ctx.last(pos.role, "M5")
            if r["exit_bar"] or r["sday"] != pd.Timestamp(pd.Timestamp(pos.opened_at).tz_convert(self.p["tz"]).date()):
                out.append(self.close_signal(ctx, pos, f"session close ({self.p['session_close']} New York)", {"close": r["close"]}))
        return out

    def diagnose(self, ctx):
        out = {}
        for role in self.roles:
            if not ctx.has(role, "M5", 20):
                out[role] = {"waiting": "not enough bars yet"}
                continue
            r = ctx.last(role, "M5")
            d = ctx.specs[role].digits
            spr = spread_price(r, ctx.specs[role])
            mv = None if pd.isna(r["r_first"]) else float(r["r_first"])
            known = mv is not None
            out[role] = {
                "price": float(r["close"]),
                "levels": [x for x in (level("Previous session close", r["prev_close"]),
                                       level("First half hour close", r["first_close"])) if x],
                "filters": [
                    chk(f"First half hour complete ({self.p['session_open']} + 30 min New York)", known,
                        f"return {mv:+.2%}" if known else "not yet today"),
                    chk(f"Session's last half hour starts ({self.session_text})", bool(r["entry_bar"]),
                        "entries only at the start of the last half hour"),
                    chk(f"Spread <= {self.p['max_spread_atr']:.0%} of ATR", spr <= self.p["max_spread_atr"] * r["atr"],
                        f"spread {fmt(spr, d)}"),
                ],
                "long": [chk(f"First half hour up more than {self.p['min_move']:.2%}", known and mv > self.p["min_move"],
                             f"{mv:+.2%}" if known else "")],
                "short": [chk(f"First half hour down more than {self.p['min_move']:.2%}", known and mv < -self.p["min_move"],
                              f"{mv:+.2%}" if known else "")],
            }
        return out
