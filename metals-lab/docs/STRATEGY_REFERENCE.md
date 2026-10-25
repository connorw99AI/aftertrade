# Strategy reference (generated from code)
Generated from `Strategy.describe()`; regenerate with `python scripts/gen_strategy_doc.py` after changing rules.

## S01 Trend Pullback Scalper
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5, M15 (decisions on completed M5 bars) | min history: 700 bars | magic: 7710001

Trades short M5 pullbacks to the 20-EMA in the direction of the M15 trend (M15 EMA50 vs EMA200 with a rising or falling EMA50), only during London/New York liquidity. Trend from the slower chart, entry on the faster one.

**Long (OPEN_LONG / CLOSE_LONG).** M15 uptrend (EMA50 > EMA200 and EMA50 rising over 4 M15 bars); previous M5 bar's low touched/pierced the M5 EMA20 with RSI(7) < pullback level; current M5 bar closes back above EMA20, bullish body, RSI rising.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror image: M15 downtrend, M5 rally to EMA20 with RSI(7) > 100-pullback level, bearish close back below EMA20.

**Exits, stops, targets, max holding.** Server-side stop at entry -/+ stop_atr x ATR(14, M5); target at reward_risk x stop distance; close early if the M5 close crosses the M5 EMA50 against the trade or the M15 trend flips; max hold 12 bars (1 hour).

**Invalidation.** M5 close through EMA50 against the position, or the M15 trend turning, invalidates the premise.

**Spread, liquidity and volatility filters.** Any hour the market is open (07:00-20:00 UTC only when use_session is on); spread <= max_spread_atr x ATR; ATR percentile (500 bars) between 20 and 90 to avoid dead and disorderly markets.

**Limitations.** Generic trend-pullback logic, not a published metals-specific edge. On M5 OHLC the order of stop/target hits inside a bar is unknown; costs are still a real share of the target. Moved from one-minute to five-minute entries so years of history can be backtested (MT5 keeps far less one-minute history).

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `ema_fast` | 20 | 15, 20, 30 | M5 pullback reference; common short EMA. |
| `ema_mid` | 50 | 50 | M5 invalidation line (early exit). |
| `trend_fast` | 50 | 34, 50 | M15 trend EMA (50 x 15 min = 12.5 hours). |
| `ema_slow` | 200 | 150, 200 | M15 slow trend EMA (200 x 15 min = 50 hours). |
| `rsi_n` | 7 | 5, 7 | Short RSI to measure pullback depth. |
| `rsi_pull` | 40 | 35, 40, 45 | Pullback must push RSI below this (long). |
| `stop_atr` | 1.2 | 1.0, 1.2, 1.5 | Stop beyond normal M5 noise. |
| `reward_risk` | 2.0 | 1.5, 2.0, 2.5 | Target a multiple of the stop so it clears costs. |
| `max_hold_bars` | 12 | 6, 12, 18 | Scalps that do not work quickly are closed (12 M5 bars = 1 hour). |
| `max_spread_atr` | 0.2 | 0.15, 0.2, 0.3 | Skip when the spread exceeds 20% of ATR(14,M5), like the other M5 bots. |
| `use_session` | off | – | on = only trade inside this strategy's session window; off = any hour the market is open (operator choice for now). |
| `start_hour` | 7 | – | UTC start of liquid hours (used when use_session is on). |
| `end_hour` | 20 | – | UTC end of liquid hours. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. No assessor change; trades every side, metal and hour. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Kaufman, P. (2013) Trading Systems and Methods, 5th ed., Wiley - trend filters and moving-average pullbacks.
- Wilder, J.W. (1978) New Concepts in Technical Trading Systems - ATR and RSI definitions.
- Batten, Lucey, McGroarty, Peat, Urquhart (2017) 'Stylized facts of intraday precious metals', PLoS ONE 12(4) - liquidity/volatility concentrate in European/US hours; spreads tightest while Europe is open.

## S02 Asian Range Reversion Scalper
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 400 bars | magic: 7710002

Fades Bollinger-band excursions (bb_k sigma) back to the mean during the quieter Asian session, only when ADX and band width say the market is ranging.

**Long (OPEN_LONG / CLOSE_LONG).** Entries 00:00-07:00 London time; ADX(14) < adx_max; band-width percentile < 50; close below lower band(20, bb_k) and RSI(5) < rsi_low.

**Short (OPEN_SHORT / COVER_SHORT).** Same regime; close above upper band and RSI(5) > 100 - rsi_low.

**Exits, stops, targets, max holding.** Exit at the 20-period mean (strategy close when close crosses SMA20; server TP at the mean at entry time); stop stop_atr x ATR beyond entry; max hold 9 bars; flatten at flat_time London, before the 08:00 London open.

**Invalidation.** ADX rising above adx_max+5 while in trade (range turning into trend) closes the position.

**Spread, liquidity and volatility filters.** Spread <= max_spread_atr x ATR. Any hour the market is open; with use_session on, entries only in the Asian window (entry_start-entry_end London, following UK clock changes) and flat before the London open.

**Limitations.** Asian-session spreads can be wide relative to the small target; mean reversion fails abruptly on news (e.g. Chinese data, geopolitical headlines). OHLC intrabar ordering unknown.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `bb_n` | 20 | 20 | Standard band length. |
| `bb_k` | 1.8 | 1.5, 1.8, 2.0 | 1.8 standard deviations (looser than 2.0 for more trades). |
| `rsi_low` | 30 | 20, 25, 30 | Short-term oversold threshold (loosened from 20). |
| `adx_max` | 25 | 20, 25, 30 | Only trade when trend strength is low. |
| `stop_atr` | 1.0 | 0.8, 1.0, 1.3 | Tight stop; ranges that break tend to run. |
| `max_hold_bars` | 9 | 6, 9, 12 | 45 minutes. |
| `use_session` | off | – | on = only trade inside this strategy's session window; off = any hour the market is open (operator choice for now). |
| `tz` | Europe/London | – | Session clock (follows UK summer time). |
| `entry_start` | 00:00 | – | London time: start of the quiet Asian hours. |
| `entry_end` | 07:00 | – | London time: no new entries after this. |
| `flat_time` | 07:45 | – | London time: flatten before the 08:00 London open. |
| `max_spread_atr` | 0.2 | 0.15, 0.2 | Cost guard. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. No assessor change; trades every side, metal and hour. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Bollinger, J. (2001) Bollinger on Bollinger Bands, McGraw-Hill - band construction and mean-reversion use in ranges.
- Wilder (1978) - ADX as trend-strength filter.
- Batten et al. (2017) PLoS ONE - lower activity/volatility outside European and US hours.

## S03 Failed Breakout Reversal
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 300 bars | magic: 7710003

Explicitly defined failed breakout: price takes out the prior N-bar extreme, then closes back inside the range on the same or next bar; trade the reversal (an intraday adaptation of 'Turtle Soup').

**Long (OPEN_LONG / CLOSE_LONG).** Low of the last two bars < prior N-bar low (excluding those two bars); current close back above that low with a bullish body; breakout bar range < 2.5 ATR; ADX(14) < adx_max.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror: high of last two bars > prior N-bar high, close back below it with bearish body.

**Exits, stops, targets, max holding.** Stop beyond the failed-breakout extreme + 0.2 ATR; target reward_risk x risk; max hold 10 bars (50 minutes).

**Invalidation.** A new extreme beyond the breakout high/low (= the stop) invalidates the failure.

**Spread, liquidity and volatility filters.** Any hour the market is open (08:00-21:00 London only when use_session is on); skip expansion bars (>2.5 ATR, likely news); skip strong trends (ADX >= adx_max); spread guard.

**Limitations.** Source pattern is daily and not metals-specific. M5 adaptation is a hypothesis; stop placement on OHLC is coarse.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 20 | 15, 20, 30 | Prior range length (Turtle Soup uses 20). |
| `reward_risk` | 1.5 | 1.0, 1.5, 2.0 | Target multiple. |
| `adx_max` | 30 | 25, 30 | Avoid fading strong trends. |
| `max_hold_bars` | 10 | 6, 10, 15 | Reversal should work quickly (50 min). |
| `max_spread_atr` | 0.15 | 0.15, 0.2 | Cost guard: spread <= 15% of ATR(14,M5). |
| `tz` | Europe/London | – | Session clock (follows UK summer time). |
| `use_session` | off | – | on = only trade inside this strategy's session window; off = any hour the market is open (operator choice for now). |
| `session_start` | 08:00 | – | London time: London open. |
| `session_end` | 21:00 | – | London time: end of the New York afternoon. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | long | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 12-21 UTC). Both metals trade for now (operator choice). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): shorts net -76 vs longs +96; silver -185 vs gold +204; 07-12 UTC -39 vs later +58. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Connors, L. & Raschke, L.B. (1995) Street Smarts: High Probability Short-Term Trading Strategies, M. Gordon Publishing - 'Turtle Soup' failed 20-day breakout pattern (daily bars; adapted here to M5, which is NOT validated by the source).

## S04 New York Opening Range Breakout
- Style: day | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 200 bars | magic: 7710004

Builds the first half hour of the New York session (08:30-09:00 New York time, when US data lands and COMEX gold is open; DST handled) and trades the first decisive M5 close beyond it, flat by mid-afternoon New York.

**Long (OPEN_LONG / CLOSE_LONG).** First M5 close above the opening-range high + buffer_atr x ATR(14) between the range end and the entry cut-off.

**Short (OPEN_SHORT / COVER_SHORT).** First M5 close below the opening-range low - buffer.

**Exits, stops, targets, max holding.** Stop at the opposite side of the range (or range midpoint if the range is wider than wide_range_atr x ATR); target tp_range_mult x range width; time exit at exit_time New York.

**Invalidation.** Close back inside the opposite half of the range = stop; one breakout attempt per day.

**Spread, liquidity and volatility filters.** Range width between min_range_atr and max_range_atr x ATR(14, M5); spread guard. With use_session off (default for now) entries are allowed at any hour after the range is built, until the next day's range starts; on = only until entry_cutoff.

**Limitations.** Spot metals trade almost 24h; the New York open is a liquidity and data event, not an exchange open, so ORB evidence from equities does not transfer automatically. The 08:30 data release can make the range unusually wide.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `tz` | America/New_York | – | Session clock (follows US daylight saving). |
| `use_session` | off | – | on = entries only in the session window and flat by the time exit; off = after the range is built, entries at any hour until the next range starts, exits by stop, target or max hold. |
| `or_start` | 08:30 | – | US data releases and the active New York gold session. |
| `or_end` | 09:00 | 08:45, 09:00, 09:30 | Half-hour opening range (6 M5 bars). |
| `entry_cutoff` | 11:30 | 11:00, 11:30, 12:30 | No new entries after late morning New York. |
| `exit_time` | 15:00 | 13:30, 15:00 | Flat by mid-afternoon New York. |
| `buffer_atr` | 0.1 | 0.0, 0.1, 0.25 | Close must clear the range by a small margin. |
| `min_range_atr` | 1.0 | 1.0, 1.5, 2.0 | Too-narrow ranges produce noise breaks (in M5 ATRs). |
| `max_range_atr` | 10.0 | 8.0, 10.0, 12.0 | Too-wide ranges leave little room (in M5 ATRs). |
| `wide_range_atr` | 6.0 | 6.0 | Use midpoint stop for wide ranges (in M5 ATRs). |
| `tp_range_mult` | 1.5 | 1.0, 1.5, 2.0 | Target as multiple of the range. |
| `max_hold_bars` | 84 | – | Safety cap (7h of M5 bars). |
| `max_spread_atr` | 0.2 | – | Cost guard. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. No assessor change; trades every side, metal and hour. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Crabel, T. (1990) Day Trading with Short Term Price Patterns and Opening Range Breakout, Traders Press.
- Zarattini, C. & Aziz, A. (2023) 'Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading Strategy', SSRN 4416622 (equity index ETF evidence, not metals).
- Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3) - US macro releases move gold intraday.
- Assessor review of the London-open version (MT5 2025-10-23..2026-10-23, in-sample): 07-12 UTC -118, 12-16 UTC +91.

## S05 London-Range Breakout & Retest
- Style: day | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 200 bars | magic: 7710005

Marks the London-morning range (08:00-13:00 London). After a decisive break during the US session handover, waits for price to come back and hold the broken level before entering.

**Long (OPEN_LONG / CLOSE_LONG).** Breakout: close > range high + breakout_atr x ATR (0.1 by default) after 13:00 London. Retest within retest_bars: bar low <= level + retest_tol_atr x ATR and close > level with bullish body -> OPEN_LONG.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror for a break below range low and a failed retest from below.

**Exits, stops, targets, max holding.** Stop = min(retest low, level) - stop_atr x ATR; target reward_risk x risk; time exit 16:00 New York.

**Invalidation.** Close back inside the range beyond the level by more than stop distance = stop; setup expires after retest_bars.

**Spread, liquidity and volatility filters.** Range width 3-16 ATR(14, M5); spread guard; one setup per side per range. With use_session off (default for now) a range stays tradable at any hour until the next range starts; on = only until 16:00 New York.

**Limitations.** Pattern literature is mostly equities/discretionary; retests are subjective and here are encoded with fixed tolerances. Many US releases fall inside the setup window; the auditor will delay those.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `use_session` | off | – | on = entries only in the session window and flat by the time exit; off = after the range is built, entries at any hour until the next range starts, exits by stop, target or max hold. |
| `range_start` | 08:00 | – | London. |
| `range_end` | 13:00 | – | Before US data/NY open. |
| `breakout_atr` | 0.1 | 0.05, 0.1, 0.2 | Close beyond the level. |
| `retest_tol_atr` | 0.4 | 0.25, 0.4, 0.6 | How close the retest must come. |
| `retest_bars` | 36 | 24, 36, 48 | Setup expiry (3h of M5 bars). |
| `stop_atr` | 0.5 | 0.5, 0.75 | Below the retest structure. |
| `reward_risk` | 2.0 | 1.5, 2.0, 2.5 | Target multiple. |
| `max_hold_bars` | 96 | – | Safety cap (8h of M5 bars). |
| `max_spread_atr` | 0.2 | – | Cost guard. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | short | – | Which sides may open: both, long or short. Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): longs -81 vs shorts +190. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Bulkowski, T. (2005) Encyclopedia of Chart Patterns, 2nd ed., Wiley - breakout pullback/throwback behaviour.
- Kaufman (2013) Trading Systems and Methods - breakout confirmation techniques.
- Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3):257-278 - intraday volatility patterns and US macro announcement effects (the reason the auditor guards 13:30/14:30 UK releases).

## S06 Volatility Compression Breakout
- Style: day | instruments: GOLD, SILVER | timeframes: M15 (decisions on completed M15 bars) | min history: 300 bars | magic: 7710006

Detects M15 volatility compression (Bollinger Bands inside Keltner Channels for several bars) and trades the direction of the release beyond the compression range.

**Long (OPEN_LONG / CLOSE_LONG).** Squeeze lasted >= min_squeeze bars and released within the last release_window bars; close > highest high of the squeeze period.

**Short (OPEN_SHORT / COVER_SHORT).** Same release window; close < lowest low of the squeeze period.

**Exits, stops, targets, max holding.** Stop stop_atr x ATR(20); target reward_risk x risk; strategy close when close crosses back through EMA20; max hold 64 bars (16h); flatten Friday from 20:00 UTC (weekend gap risk).

**Invalidation.** Close back through EMA20 against the position.

**Spread, liquidity and volatility filters.** Spread guard; no entries Friday after 18:00 UTC.

**Limitations.** Compression predicts expansion more reliably than direction; false releases are common. Indicator lengths are conventions.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `bb_k` | 2.0 | – | Standard. |
| `kc_k` | 1.5 | 1.25, 1.5, 2.0 | Carter's setting. |
| `min_squeeze` | 6 | 4, 6, 12 | Bars of compression required (1.5h of M15). |
| `stop_atr` | 1.5 | 1.0, 1.5, 2.0 | Stop. |
| `reward_risk` | 2.0 | 1.5, 2.0, 3.0 | Target multiple. |
| `max_hold_bars` | 64 | 32, 64, 96 | 16 hours of M15 bars. |
| `release_window` | 4 | 0, 2, 4, 8 | Bars after the release in which a breakout still counts (1 hour). |
| `max_spread_atr` | 0.15 | – | Cost guard (M15 ranges are smaller than H1). |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | long | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 0-7,16-24 UTC). Both metals trade for now (operator choice). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): shorts -269 vs longs +276; silver -27 vs gold +35; 07-16 UTC lost, Asia +125. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Carter, J. (2005) Mastering the Trade, McGraw-Hill - 'TTM Squeeze' (Bollinger inside Keltner) compression concept.
- Crabel (1990) - contraction/expansion (NR4/NR7) observations.
- Bollinger (2001) - 'The Squeeze' as low band-width preceding expansion.

## S07 Regime-Filtered RSI(2) Reversion
- Style: day | instruments: GOLD, SILVER | timeframes: M15, H1 (decisions on completed M15 bars) | min history: 260 bars | magic: 7710007

Buys sharp M15 pullbacks only when the hourly regime is up (H1 close > H1 SMA200, about 8 trading days), and sells M15 spikes only when the hourly regime is down. Exits on reversion to a short mean.

**Long (OPEN_LONG / CLOSE_LONG).** Last completed H1 close > H1 SMA(regime_sma) and M15 RSI(2) < rsi_entry.

**Short (OPEN_SHORT / COVER_SHORT).** Last completed H1 close < H1 SMA(regime_sma) and M15 RSI(2) > 100 - rsi_entry.

**Exits, stops, targets, max holding.** Close long when M15 close > SMA(5); cover short when close < SMA(5); stop stop_atr x ATR(14,M15); max hold 32 bars (8 hours).

**Invalidation.** Hourly regime flipping against the position closes it.

**Spread, liquidity and volatility filters.** Spread guard; H1 ADX(14) < adx_max (very strong trends make counter-moves shallow and fast).

**Limitations.** Source evidence is daily equity-index long-side; the M15/H1 intraday version, the short side and metals are untested adaptations. Intraday costs are a much larger share of each move. No fixed profit target.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `rsi_entry` | 15 | 10, 15, 20 | Looser than Connors' 10 so it trades several times a day. |
| `sma_exit` | 5 | 3, 5, 8 | Short mean for exit. |
| `regime_sma` | 200 | 100, 200 | 200 H1 bars, about 8 trading days. |
| `stop_atr` | 2.5 | 2.0, 2.5, 3.0 | Wide stop: reversion needs room. |
| `adx_max` | 40 | 35, 40, 50 | Skip runaway trends. |
| `max_hold_bars` | 32 | 16, 32, 48 | 8 hours of M15 bars. |
| `max_spread_atr` | 0.15 | – | Cost guard (M15 ranges are small next to the spread). |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | long | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 12-24 UTC). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): shorts -135 vs longs +31; 00-12 UTC -160 vs later +56. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Connors, L. & Alvarez, C. (2008) Short Term Trading Strategies That Work, TradingMarkets - RSI(2) with 200-day filter and 5-day MA exit (documented on US equities/indices; metals and the M15/H1 adaptation are untested hypotheses).

## S08 Donchian Channel Breakout
- Style: day | instruments: GOLD, SILVER | timeframes: M15, H1 (decisions on completed M15 bars) | min history: 120 bars | magic: 7710008

Turtle-style channel breakout on M15: enter on a close beyond the prior entry_n-bar extreme (6 hours by default) in the direction of the hourly trend, exit on the exit_n-bar opposite extreme.

**Long (OPEN_LONG / CLOSE_LONG).** Hourly uptrend (last completed H1 close > H1 SMA(trend_sma)) and M15 close > highest high of the previous entry_n bars.

**Short (OPEN_SHORT / COVER_SHORT).** Hourly downtrend (H1 close < H1 SMA(trend_sma)) and M15 close < lowest low of the previous entry_n bars.

**Exits, stops, targets, max holding.** Close long on close < lowest low of previous exit_n bars (cover short mirror). Server stop at stop_n x N (ATR 20). Max hold 96 bars (one day). No fixed target.

**Invalidation.** The 2N stop.

**Spread, liquidity and volatility filters.** Spread guard. No pyramiding (simplification of original Turtle rules).

**Limitations.** Trend-following evidence is for daily bars on diversified futures portfolios. This M15 version is an untested intraday adaptation: many more false breakouts, and costs are a much larger share of each move.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `entry_n` | 24 | 16, 24, 32 | 24 M15 bars = 6 hours (Turtle System 2 used 55 days). |
| `exit_n` | 12 | 8, 12, 16 | 12 M15 bars = 3 hours. |
| `stop_n` | 2.0 | 2.0, 3.0 | 2N stop. |
| `max_hold_bars` | 96 | 48, 96 | One day of M15 bars (safety cap). |
| `max_spread_atr` | 0.15 | – | Cost guard (M15 ranges are small next to the spread). |
| `trend_sma` | 200 | 0, 100, 200 | Hourly trend filter: 200 H1 bars, about 8 trading days (0 = off). |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 7-12,16-24 UTC). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): Asia -61 and 12-16 UTC -27; only 07-12 was positive (+4). |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Faith, C. (2007) Way of the Turtle, McGraw-Hill; and the public 'Original Turtle Trading Rules' (System 2: 55/20 day).
- Donchian, R. (1960s) channel rules as summarised in Kaufman (2013) Trading Systems and Methods.
- Hurst, Ooi & Pedersen (2017) 'A Century of Evidence on Trend-Following Investing', J. Portfolio Management 44(1).

## S09 Time-Series Momentum
- Style: day | instruments: GOLD, SILVER | timeframes: M15, H1 (decisions on completed M15 bars) | min history: 200 bars | magic: 7710009

Each metal is held long or short according to the sign of its own volatility-scaled past return over the last lookback M15 bars, re-evaluated on the first M15 bar of every hour (short-horizon adaptation).

**Long (OPEN_LONG / CLOSE_LONG).** On the first M15 bar of each hour: lookback return / (bar vol x sqrt(lookback)) > entry_strength, and the hourly trend is up (H1 close > H1 SMA(trend_sma)).

**Short (OPEN_SHORT / COVER_SHORT).** Same, < -entry_strength, and the hourly trend is down.

**Exits, stops, targets, max holding.** At an hourly evaluation, close if strength fades to exit_strength or flips; server stop stop_atr x ATR(20); max hold 48 bars (12 hours).

**Invalidation.** Sign reversal of the trend measure.

**Spread, liquidity and volatility filters.** Spread guard; one evaluation per hour (limits turnover).

**Limitations.** Published results are portfolio-level across 58 markets at 1-12 month horizons. Single-metal momentum over one day of M15 bars is an untested adaptation and mostly measures intraday noise.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 96 | 48, 96, 192 | 96 M15 bars (one trading day); far shorter than the 1-12 month horizons studied. |
| `entry_strength` | 0.3 | 0.2, 0.3, 0.5 | Require some trend (loosened from 0.5). |
| `exit_strength` | 0.0 | 0.0, 0.25 | Exit on sign flip. |
| `stop_atr` | 3.0 | 2.0, 3.0, 4.0 | Wide stop: momentum needs room. |
| `max_hold_bars` | 48 | 24, 48, 96 | 12 hours of M15 bars. |
| `max_spread_atr` | 0.15 | – | Cost guard (M15 ranges are small next to the spread). |
| `trend_sma` | 200 | 0, 100, 200 | Hourly trend filter: 200 H1 bars, about 8 trading days (0 = off). |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 0-7,21-24 UTC). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): Asia +467 and 21-24 UTC +367; London and New York hours lost -320. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012) 'Time series momentum', J. Financial Economics 104(2):228-250 (58 futures incl. commodities; 1-12 month lookbacks).
- Kim, Tse & Wald (2016) 'Time series momentum and volatility scaling', J. Financial Markets 30 - critique: much of the effect comes from volatility scaling and weakens post-2009.

## S10 Gold/Silver Ratio Reversion
- Style: day | instruments: GOLD, SILVER | timeframes: M15 (decisions on completed M15 bars) | min history: 200 bars | magic: 7710010

Intraday relative-value strategy on the log gold/silver ratio (M15). When the ratio is stretched, buy the cheap metal and sell the rich one as one explicitly defined two-leg trade group; exit both legs together.

**Long (OPEN_LONG / CLOSE_LONG).** z(ln(Gold/Silver), lookback) > entry_z: OPEN_LONG silver + OPEN_SHORT gold (group).

**Short (OPEN_SHORT / COVER_SHORT).** z < -entry_z: OPEN_SHORT silver + OPEN_LONG gold (group).

**Exits, stops, targets, max holding.** Close both legs when |z| < exit_z (reversion) or |z| > stop_z (divergence stop); max hold 96 bars (one day); each leg also carries a server stop at leg_stop_atr x ATR(20, M15) so it is protected if the bot is offline. If one leg is closed (e.g. by its stop) the other leg is closed ('group broken').

**Invalidation.** Rolling 120-bar return correlation < min_corr (relationship breakdown) blocks entries.

**Spread, liquidity and volatility filters.** Both instruments must be tradable; spread guard on both legs.

**Limitations.** The ratio has trended for years at a time (e.g. 2011-2020); stationarity is not guaranteed. Legs are risk-balanced via ATR stops, not dollar-neutral; contract sizes and min lots may make balance impossible for a small virtual portfolio, in which case the trade is skipped.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 96 | 64, 96, 192 | 96 M15 bars (one trading day) for the intraday mean. |
| `entry_z` | 1.5 | 1.25, 1.5, 2.0 | Stretch threshold (loosened from 2.0). |
| `exit_z` | 0.5 | 0.0, 0.5 | Reversion target. |
| `stop_z` | 3.5 | 3.0, 3.5, 4.0 | Divergence stop. |
| `min_corr` | 0.5 | 0.3, 0.5 | Relationship filter. |
| `leg_stop_atr` | 3.0 | 3.0, 4.0 | Per-leg protective server stop. |
| `max_hold_bars` | 96 | 48, 96, 192 | One day of M15 bars. |
| `sides` | both | – | Which sides may open: both, long or short. All hours trade for now (operator choice; the backtest preferred 21-24 UTC). Assessor, MT5 backtest 2025-10-23..2026-10-23 (in-sample): every session lost except 21-24 UTC (+24 over 28 trades, weak). Side/metal suggestions do not apply to a pair. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Gatev, E., Goetzmann, W. & Rouwenhorst, K.G. (2006) 'Pairs Trading: Performance of a Relative-Value Arbitrage Rule', Review of Financial Studies 19(3):797-827 (equities; methodology reference).
- Lucey, B. & co-authors, Trinity College Dublin IIIS DP 55 - dynamic cointegration of gold and silver (weekly COMEX 1978-2002).
- MPRA paper 109830 - fractional cointegration between gold and silver.

## S11 Session Momentum (first half hour -> last half hour)
- Style: day | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 400 bars | magic: 7710011

Intraday momentum: the move from the previous session's close to the end of the first half hour of the New York metals session tends to continue into the session's last half hour. One trade per metal per day, opened at the start of the last half hour and closed at the session close.

**Long (OPEN_LONG / CLOSE_LONG).** At session_close - 30 min (New York time): if the first-half-hour return (previous session close -> session_open + 30 min) is above +min_move, OPEN_LONG.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror: first-half-hour return below -min_move -> OPEN_SHORT.

**Exits, stops, targets, max holding.** Close at session_close (New York). Stop stop_atr x ATR(14, M5) from entry; no fixed target. max_hold_bars is a safety cap.

**Invalidation.** Stop hit, or the session close.

**Spread, liquidity and volatility filters.** Spread guard; the first half hour must be complete and the previous session's close known; one entry per day.

**Limitations.** Evidence is strongest for US equities; for spot gold and silver it is unproven and must be tested on your own MT5 history. The session hours follow COMEX (08:20-13:30 New York); spot metals trade around the clock, so the 'open' here is a convention. Few trades (one per metal per day) means results take months to judge.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `tz` | America/New_York | – | Session clock (follows US summer time). |
| `session_open` | 08:20 | – | New York: COMEX metals open. |
| `session_close` | 13:30 | – | New York: COMEX metals close. |
| `min_move` | 0.0 | 0.0, 0.001, 0.002 | Smallest first-half-hour return (as a fraction, 0.001 = 0.1%) that counts as a direction. |
| `stop_atr` | 2.0 | 1.5, 2.0, 3.0 | Stop distance in ATR(14) of M5 bars. |
| `max_hold_bars` | 8 | – | Safety cap (40 min of M5 bars). |
| `max_spread_atr` | 0.25 | – | Cost guard. |
| `breakeven_r` | 0.0 | 0.0, 0.5, 1.0, 1.5 | Move the stop to the entry price once a trade is this many R in profit (R = the distance to the original stop). 0 = off. |
| `trail_atr` | 0.0 | 0.0, 2.0, 3.0, 4.0 | Trailing stop: keep the stop this many ATR(14) behind the best price since entry; it only ever tightens. When on, the fixed target is dropped so winners can run. 0 = off. |
| `sides` | both | – | Which sides may open: both, long or short. No assessor change; trades every side, metal and hour. |
| `trade_gold` | on | – | on: this bot may open gold trades; off: it does not trade gold. |
| `trade_silver` | on | – | on: this bot may open silver trades; off: it does not trade silver. |
| `gold_windows` | 00:00-24:00 | – | Gold: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |
| `silver_windows` | 00:00-24:00 | – | Silver: UK time windows new entries are allowed in, e.g. 06:00-11:00, 13:00-17:00 (00:00-24:00 = all day). |

**Sources.**

- Gao, L., Han, Y., Li, S. Z. & Zhou, G. (2018) 'Market intraday momentum', Journal of Financial Economics 129(2):394-414 - the first half-hour return predicts the last half-hour return (S&P 500 ETF 1993-2013 and other ETFs and index futures).
- A commodity-futures study (University of Hull repository) reports the same predictability in four commodity futures; whether gold or silver were among them is not confirmed here.
- Studies of Chinese metal futures found the day-session effect disappeared there (Pacific-Basin Finance Journal 80, 2023).
