# Strategy reference (generated from code)
Generated from `Strategy.describe()`; regenerate with `python scripts/gen_strategy_doc.py` after changing rules.

## S01 Trend Pullback Scalper
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 700 bars | magic: 7710001

Trades short pullbacks to the 20-EMA in the direction of an established M5 trend (EMA50 vs EMA200 with positive slope), only during London/New York liquidity.

**Long (OPEN_LONG / CLOSE_LONG).** Uptrend (EMA50 > EMA200 and EMA50 rising over 10 bars); previous bar's low touched/pierced EMA20 with RSI(7) < pullback level; current bar closes back above EMA20, bullish body, RSI rising.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror image: downtrend, rally to EMA20 with RSI(7) > 100-pullback level, bearish close back below EMA20.

**Exits, stops, targets, max holding.** Server-side stop at entry -/+ stop_atr x ATR(14); target at reward_risk x stop distance; close early if price closes through EMA50 against the trade; max hold 12 bars (1 hour).

**Invalidation.** Close through EMA50 against the position invalidates the trend premise.

**Spread, liquidity and volatility filters.** Hours 07:00-20:00 UTC; spread <= max_spread_atr x ATR; ATR percentile (500 bars) between 20 and 90 to avoid dead and disorderly markets.

**Limitations.** Generic trend-pullback logic, not a published metals-specific edge. On M5 OHLC the order of stop/target hits inside a bar is unknown; costs are a large fraction of the target.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `ema_fast` | 20 | 15, 20, 30 | Pullback reference; common short EMA. |
| `ema_mid` | 50 | 50 | Trend/invalidation line. |
| `ema_slow` | 200 | 150, 200 | Long trend filter. |
| `rsi_n` | 7 | 5, 7 | Short RSI to measure pullback depth. |
| `rsi_pull` | 40 | 35, 40, 45 | Pullback must push RSI below this (long). |
| `stop_atr` | 1.2 | 1.0, 1.2, 1.5 | Stop beyond normal M5 noise. |
| `reward_risk` | 1.5 | 1.0, 1.5, 2.0 | Target as multiple of stop distance. |
| `max_hold_bars` | 12 | 8, 12, 18 | Scalps that do not work quickly are closed. |
| `max_spread_atr` | 0.15 | 0.1, 0.15, 0.2 | Skip when spread consumes >15% of ATR. |
| `start_hour` | 7 | – | UTC start of liquid hours. |
| `end_hour` | 20 | – | UTC end of liquid hours. |

**Sources.**

- Kaufman, P. (2013) Trading Systems and Methods, 5th ed., Wiley - trend filters and moving-average pullbacks.
- Wilder, J.W. (1978) New Concepts in Technical Trading Systems - ATR and RSI definitions.
- Batten, Lucey, McGroarty, Peat, Urquhart (2017) 'Stylized facts of intraday precious metals', PLoS ONE 12(4) - liquidity/volatility concentrate in European/US hours; spreads tightest while Europe is open.

## S02 Asian Range Reversion Scalper
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 400 bars | magic: 7710002

Fades 2-sigma Bollinger excursions back to the mean during the quieter Asian session, only when ADX and band width say the market is ranging.

**Long (OPEN_LONG / CLOSE_LONG).** Hours 00:00-06:00 UTC; ADX(14) < adx_max; band-width percentile < 50; close below lower band(20,2) and RSI(5) < rsi_low.

**Short (OPEN_SHORT / COVER_SHORT).** Same regime; close above upper band and RSI(5) > 100 - rsi_low.

**Exits, stops, targets, max holding.** Exit at the 20-period mean (strategy close when close crosses SMA20; server TP at the mean at entry time); stop stop_atr x ATR beyond entry; max hold 9 bars; flatten at flat_hour UTC before London opens.

**Invalidation.** ADX rising above adx_max+5 while in trade (range turning into trend) closes the position.

**Spread, liquidity and volatility filters.** Spread <= max_spread_atr x ATR; no new entries after entry_end_hour.

**Limitations.** Asian-session spreads can be wide relative to the small target; mean reversion fails abruptly on news (e.g. Chinese data, geopolitical headlines). OHLC intrabar ordering unknown.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `bb_n` | 20 | 20 | Standard band length. |
| `bb_k` | 2.0 | 1.8, 2.0, 2.3 | 2 standard deviations. |
| `rsi_low` | 20 | 15, 20, 25 | Short-term oversold threshold. |
| `adx_max` | 20 | 18, 20, 25 | Only trade when trend strength is low. |
| `stop_atr` | 1.0 | 0.8, 1.0, 1.3 | Tight stop; ranges that break tend to run. |
| `max_hold_bars` | 9 | 6, 9, 12 | 45 minutes. |
| `entry_start_hour` | 0 | – | UTC. |
| `entry_end_hour` | 6 | – | UTC. |
| `flat_hour` | 7 | – | UTC flatten before London open. |
| `max_spread_atr` | 0.2 | 0.15, 0.2 | Cost guard. |

**Sources.**

- Bollinger, J. (2001) Bollinger on Bollinger Bands, McGraw-Hill - band construction and mean-reversion use in ranges.
- Wilder (1978) - ADX as trend-strength filter.
- Batten et al. (2017) PLoS ONE - lower activity/volatility outside European and US hours.

## S03 Failed Breakout Reversal
- Style: scalp | instruments: GOLD, SILVER | timeframes: M5 (decisions on completed M5 bars) | min history: 300 bars | magic: 7710003

Explicitly defined failed breakout: price takes out the prior N-bar extreme, then closes back inside the range on the same or next bar; trade the reversal (an intraday adaptation of 'Turtle Soup').

**Long (OPEN_LONG / CLOSE_LONG).** Low of the last two bars < prior N-bar low (excluding those two bars); current close back above that low with a bullish body; breakout bar range < 2.5 ATR; ADX(14) < adx_max.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror: high of last two bars > prior N-bar high, close back below it with bearish body.

**Exits, stops, targets, max holding.** Stop beyond the failed-breakout extreme + 0.2 ATR; target reward_risk x risk; max hold 10 bars.

**Invalidation.** A new extreme beyond the breakout high/low (= the stop) invalidates the failure.

**Spread, liquidity and volatility filters.** 07:00-20:00 UTC; skip expansion bars (>2.5 ATR, likely news); skip strong trends (ADX >= adx_max); spread guard.

**Limitations.** Source pattern is daily and not metals-specific. M5 adaptation is a hypothesis; stop placement on OHLC is coarse.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 20 | 15, 20, 30 | Prior range length (Turtle Soup uses 20). |
| `reward_risk` | 1.5 | 1.0, 1.5, 2.0 | Target multiple. |
| `adx_max` | 30 | 25, 30 | Avoid fading strong trends. |
| `max_hold_bars` | 10 | 6, 10, 15 | Reversal should work quickly. |
| `max_spread_atr` | 0.15 | 0.15 | Cost guard. |

**Sources.**

- Connors, L. & Raschke, L.B. (1995) Street Smarts: High Probability Short-Term Trading Strategies, M. Gordon Publishing - 'Turtle Soup' failed 20-day breakout pattern (daily bars; adapted here to M5, which is NOT validated by the source).

## S04 London Opening Range Breakout
- Style: day | instruments: GOLD, SILVER | timeframes: M15 (decisions on completed M15 bars) | min history: 200 bars | magic: 7710004

Builds the first hour range after the London open (Europe/London local time, so UK DST is handled) and trades the first decisive close beyond it, flat by late afternoon London time.

**Long (OPEN_LONG / CLOSE_LONG).** First M15 close above OR high + buffer_atr x ATR(14) between OR end and entry cut-off, OR width within limits.

**Short (OPEN_SHORT / COVER_SHORT).** First M15 close below OR low - buffer.

**Exits, stops, targets, max holding.** Stop at the opposite side of the range (or range midpoint if the range is wider than wide_range_atr x ATR); target tp_range_mult x OR width; time exit at exit_time London.

**Invalidation.** Close back inside the opposite half of the range = stop; one breakout attempt per day.

**Spread, liquidity and volatility filters.** OR width between min_range_atr and max_range_atr x ATR(14, M15); spread guard.

**Limitations.** Spot metals trade almost 24h; 'London open' is a liquidity event, not an exchange open, so ORB evidence from equities does not transfer automatically. Breakouts fail regularly on rotational days.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `tz` | Europe/London | – | Session clock (DST-aware). |
| `or_start` | 08:00 | – | London open. |
| `or_end` | 09:00 | 08:30, 09:00 | One-hour opening range. |
| `entry_cutoff` | 12:00 | 11:00, 12:00, 13:00 | No new entries after midday London. |
| `exit_time` | 16:30 | 15:00, 16:30 | Flat before US afternoon. |
| `buffer_atr` | 0.1 | 0.0, 0.1, 0.25 | Close must clear the range by a small margin. |
| `min_range_atr` | 1.0 | 0.5, 1.0 | Too-narrow ranges produce noise breaks. |
| `max_range_atr` | 5.0 | 4.0, 5.0, 6.0 | Too-wide ranges leave little room. |
| `wide_range_atr` | 3.0 | 3.0 | Use midpoint stop for wide ranges. |
| `tp_range_mult` | 1.5 | 1.0, 1.5, 2.0 | Target as multiple of the range. |
| `max_hold_bars` | 40 | – | Safety cap (10h). |
| `max_spread_atr` | 0.2 | – | Cost guard. |

**Sources.**

- Crabel, T. (1990) Day Trading with Short Term Price Patterns and Opening Range Breakout, Traders Press.
- Zarattini, C. & Aziz, A. (2023) 'Can Day Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading Strategy', SSRN 4416622 (equity index ETF evidence, not metals).
- Batten et al. (2017) PLoS ONE - intraday precious-metals volatility rises with the opening of major markets.

## S05 London-Range Breakout & Retest
- Style: day | instruments: GOLD, SILVER | timeframes: M15 (decisions on completed M15 bars) | min history: 200 bars | magic: 7710005

Marks the London-morning range (08:00-13:00 London). After a decisive break during the US session handover, waits for price to come back and hold the broken level before entering.

**Long (OPEN_LONG / CLOSE_LONG).** Breakout: close > range high + 0.2 ATR after 13:00 London. Retest within retest_bars: bar low <= level + retest_tol_atr x ATR and close > level with bullish body -> OPEN_LONG.

**Short (OPEN_SHORT / COVER_SHORT).** Mirror for a break below range low and a failed retest from below.

**Exits, stops, targets, max holding.** Stop = min(retest low, level) - stop_atr x ATR; target reward_risk x risk; time exit 16:00 New York.

**Invalidation.** Close back inside the range beyond the level by more than stop distance = stop; setup expires after retest_bars.

**Spread, liquidity and volatility filters.** Range width 1.5-8 ATR(M15); spread guard; one setup per side per day.

**Limitations.** Pattern literature is mostly equities/discretionary; retests are subjective and here are encoded with fixed tolerances. Many US releases fall inside the setup window; the auditor will delay those.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `range_start` | 08:00 | – | London. |
| `range_end` | 13:00 | – | Before US data/NY open. |
| `breakout_atr` | 0.2 | 0.1, 0.2, 0.4 | Decisive close beyond level. |
| `retest_tol_atr` | 0.25 | 0.15, 0.25, 0.4 | How close the retest must come. |
| `retest_bars` | 8 | 6, 8, 12 | Setup expiry (2h). |
| `stop_atr` | 0.5 | 0.5, 0.75 | Below the retest structure. |
| `reward_risk` | 2.0 | 1.5, 2.0, 2.5 | Target multiple. |
| `max_hold_bars` | 32 | – | Safety cap. |
| `max_spread_atr` | 0.2 | – | Cost guard. |

**Sources.**

- Bulkowski, T. (2005) Encyclopedia of Chart Patterns, 2nd ed., Wiley - breakout pullback/throwback behaviour.
- Kaufman (2013) Trading Systems and Methods - breakout confirmation techniques.
- Cai, Cheung & Wong (2001) 'What moves the gold market?', J. Futures Markets 21(3):257-278 - intraday volatility patterns and US macro announcement effects (the reason the auditor guards 13:30/14:30 UK releases).

## S06 Volatility Compression Breakout
- Style: day | instruments: GOLD, SILVER | timeframes: H1 (decisions on completed H1 bars) | min history: 300 bars | magic: 7710006

Detects H1 volatility compression (Bollinger Bands inside Keltner Channels for several bars) and trades the direction of the release beyond the compression range.

**Long (OPEN_LONG / CLOSE_LONG).** Squeeze lasted >= min_squeeze bars and has just released; close > highest high of the squeeze period.

**Short (OPEN_SHORT / COVER_SHORT).** Same release; close < lowest low of the squeeze period.

**Exits, stops, targets, max holding.** Stop stop_atr x ATR(20); target reward_risk x risk; strategy close when close crosses back through EMA20; max hold 24 bars; flatten Friday from 20:00 UTC (weekend gap risk).

**Invalidation.** Close back through EMA20 against the position.

**Spread, liquidity and volatility filters.** Spread guard; no entries Friday after 18:00 UTC.

**Limitations.** Compression predicts expansion more reliably than direction; false releases are common. Indicator lengths are conventions.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `bb_k` | 2.0 | – | Standard. |
| `kc_k` | 1.5 | 1.25, 1.5, 2.0 | Carter's setting. |
| `min_squeeze` | 6 | 4, 6, 10 | Bars of compression required. |
| `stop_atr` | 1.5 | 1.0, 1.5, 2.0 | Stop. |
| `reward_risk` | 2.0 | 1.5, 2.0, 3.0 | Target multiple. |
| `max_hold_bars` | 24 | 12, 24, 48 | One day. |
| `max_spread_atr` | 0.1 | – | Cost guard. |

**Sources.**

- Carter, J. (2005) Mastering the Trade, McGraw-Hill - 'TTM Squeeze' (Bollinger inside Keltner) compression concept.
- Crabel (1990) - contraction/expansion (NR4/NR7) observations.
- Bollinger (2001) - 'The Squeeze' as low band-width preceding expansion.

## S07 Regime-Filtered RSI(2) Reversion
- Style: swing | instruments: GOLD, SILVER | timeframes: H4, D1 (decisions on completed H4 bars) | min history: 260 bars | magic: 7710007

Buys sharp short-term pullbacks only when the daily regime is up (close > SMA200 D1), and sells short-term spikes only when the daily regime is down. Exits on reversion to a short mean.

**Long (OPEN_LONG / CLOSE_LONG).** Last completed D1 close > D1 SMA(200) and H4 RSI(2) < rsi_entry.

**Short (OPEN_SHORT / COVER_SHORT).** Last completed D1 close < D1 SMA(200) and H4 RSI(2) > 100 - rsi_entry.

**Exits, stops, targets, max holding.** Close long when H4 close > SMA(5); cover short when close < SMA(5); stop stop_atr x ATR(14,H4); max hold 30 bars (~5 days).

**Invalidation.** Daily regime flipping against the position closes it.

**Spread, liquidity and volatility filters.** Spread guard; D1 ADX(14) < adx_max (very strong trends make counter-moves shallow and fast).

**Limitations.** Source evidence is equity-index long-side; short side and metals are lower-evidence. No fixed profit target.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `rsi_entry` | 10 | 5, 10, 15 | Connors' extreme threshold. |
| `sma_exit` | 5 | 3, 5, 8 | Short mean for exit. |
| `regime_sma` | 200 | 100, 200 | Classic regime filter. |
| `stop_atr` | 2.5 | 2.0, 2.5, 3.0 | Wide stop: reversion needs room. |
| `adx_max` | 40 | 35, 40, 50 | Skip runaway trends. |
| `max_hold_bars` | 30 | 20, 30 | About a week. |
| `max_spread_atr` | 0.05 | – | Cost guard. |

**Sources.**

- Connors, L. & Alvarez, C. (2008) Short Term Trading Strategies That Work, TradingMarkets - RSI(2) with 200-day filter and 5-day MA exit (documented on US equities/indices; metals and H4 adaptation are untested hypotheses).

## S08 Donchian Channel Breakout
- Style: swing | instruments: GOLD, SILVER | timeframes: D1 (decisions on completed D1 bars) | min history: 120 bars | magic: 7710008

Turtle-style multiday channel breakout: enter on a close beyond the prior 55-day extreme, exit on the 20-day opposite extreme.

**Long (OPEN_LONG / CLOSE_LONG).** D1 close > highest high of the previous entry_n days.

**Short (OPEN_SHORT / COVER_SHORT).** D1 close < lowest low of the previous entry_n days.

**Exits, stops, targets, max holding.** Close long on close < lowest low of previous exit_n days (cover short mirror). Server stop at stop_n x N (ATR 20). No fixed target.

**Invalidation.** The 2N stop.

**Spread, liquidity and volatility filters.** Spread guard. No pyramiding (simplification of original Turtle rules).

**Limitations.** Trend-following evidence is for diversified futures portfolios; a 2-instrument version has long flat or losing stretches and few trades, so statistical confidence will be low.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `entry_n` | 55 | 20, 40, 55 | Turtle System 2 entry. |
| `exit_n` | 20 | 10, 20 | Turtle System 2 exit. |
| `stop_n` | 2.0 | 2.0, 3.0 | 2N stop. |
| `max_hold_bars` | 250 | – | About one year (safety cap). |
| `max_spread_atr` | 0.05 | – | Cost guard. |

**Sources.**

- Faith, C. (2007) Way of the Turtle, McGraw-Hill; and the public 'Original Turtle Trading Rules' (System 2: 55/20 day).
- Donchian, R. (1960s) channel rules as summarised in Kaufman (2013) Trading Systems and Methods.
- Hurst, Ooi & Pedersen (2017) 'A Century of Evidence on Trend-Following Investing', J. Portfolio Management 44(1).

## S09 Time-Series Momentum
- Style: swing | instruments: GOLD, SILVER | timeframes: D1 (decisions on completed D1 bars) | min history: 200 bars | magic: 7710009

Each metal is held long or short according to the sign of its own volatility-scaled past return, re-evaluated weekly. Position size comes from the stop-based risk budget (a volatility scaling).

**Long (OPEN_LONG / CLOSE_LONG).** On the first D1 bar of each week: lookback return / (daily vol x sqrt(lookback)) > entry_strength.

**Short (OPEN_SHORT / COVER_SHORT).** Same, < -entry_strength.

**Exits, stops, targets, max holding.** On weekly evaluation close if strength sign flips or |strength| < exit_strength; catastrophic server stop stop_atr x ATR(20).

**Invalidation.** Sign reversal of the trend measure.

**Spread, liquidity and volatility filters.** Spread guard; weekly rebalance only (limits turnover).

**Limitations.** Published results are portfolio-level across 58 markets; single-metal TSMOM is noisy. Weekly evaluation is a design choice.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 126 | 63, 126, 252 | ~6 months, inside the 1-12m range studied. |
| `entry_strength` | 0.5 | 0.25, 0.5, 1.0 | Require a meaningful trend. |
| `exit_strength` | 0.0 | 0.0, 0.25 | Exit on sign flip. |
| `stop_atr` | 3.0 | 3.0, 4.0 | Catastrophic stop only. |
| `max_hold_bars` | 400 | – | Safety cap. |
| `max_spread_atr` | 0.05 | – | Cost guard. |

**Sources.**

- Moskowitz, T., Ooi, Y.H. & Pedersen, L.H. (2012) 'Time series momentum', J. Financial Economics 104(2):228-250 (58 futures incl. commodities; 1-12 month lookbacks).
- Kim, Tse & Wald (2016) 'Time series momentum and volatility scaling', J. Financial Markets 30 - critique: much of the effect comes from volatility scaling and weakens post-2009.

## S10 Gold/Silver Ratio Reversion
- Style: swing | instruments: GOLD, SILVER | timeframes: D1 (decisions on completed D1 bars) | min history: 200 bars | magic: 7710010

Relative-value research strategy on the log gold/silver ratio. When the ratio is stretched, buy the cheap metal and sell the rich one as one explicitly defined two-leg trade group; exit both legs together.

**Long (OPEN_LONG / CLOSE_LONG).** z(ln(Gold/Silver), lookback) > entry_z: OPEN_LONG silver + OPEN_SHORT gold (group).

**Short (OPEN_SHORT / COVER_SHORT).** z < -entry_z: OPEN_SHORT silver + OPEN_LONG gold (group).

**Exits, stops, targets, max holding.** Close both legs when |z| < exit_z (reversion) or |z| > stop_z (divergence stop); max hold 40 days; each leg also carries a server stop at leg_stop_atr x ATR(20) so it is protected if the bot is offline. If one leg is closed (e.g. by its stop) the other leg is closed ('group broken').

**Invalidation.** Rolling 120-day return correlation < min_corr (relationship breakdown) blocks entries.

**Spread, liquidity and volatility filters.** Both instruments must be tradable; spread guard on both legs.

**Limitations.** The ratio has trended for years at a time (e.g. 2011-2020); stationarity is not guaranteed. Legs are risk-balanced via ATR stops, not dollar-neutral; contract sizes and min lots may make balance impossible for a small virtual portfolio, in which case the trade is skipped.

| Parameter | Default | Research grid | Rationale |
|---|---|---|---|
| `lookback` | 120 | 60, 120, 250 | ~6 months of trading days. |
| `entry_z` | 2.0 | 1.5, 2.0, 2.5 | Stretch threshold. |
| `exit_z` | 0.5 | 0.0, 0.5 | Reversion target. |
| `stop_z` | 3.5 | 3.0, 3.5, 4.0 | Divergence stop. |
| `min_corr` | 0.5 | 0.3, 0.5 | Relationship filter. |
| `leg_stop_atr` | 3.0 | 3.0, 4.0 | Per-leg protective server stop. |
| `max_hold_bars` | 40 | 20, 40, 60 | ~2 months. |

**Sources.**

- Gatev, E., Goetzmann, W. & Rouwenhorst, K.G. (2006) 'Pairs Trading: Performance of a Relative-Value Arbitrage Rule', Review of Financial Studies 19(3):797-827 (equities; methodology reference).
- Lucey, B. & co-authors, Trinity College Dublin IIIS DP 55 - dynamic cointegration of gold and silver (weekly COMEX 1978-2002).
- MPRA paper 109830 - fractional cointegration between gold and silver.
