# Strategy research notes

Ten explicit, rule-based hypotheses for spot/CFD gold and silver. None of them is a proven edge. The defaults are starting hypotheses taken from the cited sources or from common practice. Their job is to be tested on your broker's real data, not trusted.

Full rules, parameters and per-strategy citations: [STRATEGY_REFERENCE.md](STRATEGY_REFERENCE.md) (generated from code).

## Distribution and why

Timeframes were set by the operator: one bot on M1, four on M5, two on M15, and one each on H1, H4 and D1.

| Bot | Timeframe | Style | Mechanism |
|---|---|---|---|
| S01 Trend Pullback Scalper | M1 | scalp | trend continuation |
| S02 Asian Range Reversion | M5 | scalp | range mean reversion |
| S03 Failed Breakout Reversal | M5 | scalp | failed-break reversal |
| S04 London Opening Range Breakout | M5 | day | first break of the opening range |
| S05 London-Range Breakout & Retest | M5 | day | break plus retest |
| S06 Volatility Compression Breakout | M15 | day | compression release |
| S10 Gold/Silver Ratio Reversion | M15 | day | intraday relative value (two-leg group) |
| S09 Time-Series Momentum | H1 | swing | short-horizon momentum, evaluated daily |
| S07 Regime-Filtered RSI(2) Reversion | H4 (D1 regime filter) | swing | pullback reversion with trend filter |
| S08 Donchian Channel Breakout | D1 | swing | multiday channel breakout |

**Cost warning for M1:** on the connected demo, gold's spread was about $0.43. That is often 25-40% of a one-minute range, so S01 keeps a spread guard (spread ≤ 30% of ATR) and a 2:1 target, and will skip many signals. M1 backtests without tick data are the least reliable results in this lab.

The short-horizon versions of S09 (H1) and S10 (M15) are adaptations. The cited evidence for both is at monthly/daily horizons.

They are deliberately not ten renamings of one idea. They differ in mechanism (trend, mean reversion, failed break, volatility regime, cross-sectional relative value), horizon and entry trigger.

## Evidence base and how strong it is

- **Trend following / time-series momentum (S08, S09).** Moskowitz, Ooi & Pedersen (2012, *JFE* 104:228-250) report time-series momentum in 58 futures markets, including commodities; Hurst, Ooi & Pedersen (2017) extend it over a century. Caveats: these are diversified *portfolio* results. Kim, Tse & Wald (2016) attribute much of the effect to volatility scaling and report weakness after 2009. With two correlated metals, expect long flat periods and very few trades.
- **Macro announcements (auditor; S05 timing).** Cai, Cheung & Wong (2001, *J. Futures Markets* 21(3):257-278): among 23 US releases, employment, GDP, CPI and personal income move gold volatility the most. This is the basis for the news blackout, not a source of edge.
- **Intraday seasonality (S01-S06 time filters).** Batten, Lucey, McGroarty, Peat & Urquhart (2017, *PLoS ONE*): 5-minute data on gold, silver, platinum and palladium show intraday volatility and activity cycles linked to market opens and closes, with the tightest spreads during European hours.
- **Opening-range breakout (S04).** Crabel (1990); Zarattini & Aziz (2023, SSRN) for equity-index ETFs. Transfer to 24-hour OTC metals is **untested**.
- **Turtle Soup failed breakout (S03).** Connors & Raschke (1995) on daily bars; the M5 adaptation is a hypothesis.
- **RSI(2) with a 200-day filter (S07).** Connors & Alvarez (2008) on US equities, mostly long-side. Using it on metals, on H4 bars and on the short side are all adaptations.
- **Squeeze (S06).** Bollinger (2001); Carter (2005). Compression predicts expansion better than it predicts direction.
- **Gold/silver ratio (S10).** Gatev, Goetzmann & Rouwenhorst (2006) for the pairs methodology. Cointegration studies of gold and silver (TCD IIIS DP 55; MPRA 109830) give mixed findings. The ratio trended for years at a time (e.g. 2011-2020), so it can stop mean-reverting for long stretches.
- **Multiple testing.** Bailey & López de Prado (2014, *JPM* 40(5)): Deflated Sharpe Ratio, implemented in `research/validation.py`.

Sources I could not verify against primary full text (I relied on abstracts and well-known summaries): Crabel (out of print), Connors & Raschke, Carter. Treat their descriptions as practitioner rules, not peer-reviewed evidence.

## Explicit design decisions

- Every exposure change is labelled `OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT` or `COVER_SHORT`. A buy is never ambiguous.
- Decisions use completed bars only. Orders fill at the next bar's open in backtests, and on the next live tick after the bar closes in live trading.
- Each opening carries a protective stop that is sent to the server with the order.
- Sizing comes from the bot's own equity: risk budget ÷ (loss at stop per lot + estimated round-trip cost per lot), rounded down to the lot step. If the minimum lot would exceed the budget, the trade is skipped, never upsized.
- S10 opens two legs as one trade group. Both legs must pass the auditor and risk checks; if one leg fails after the other has filled, the filled leg is closed. The money popup fires only when the whole group has closed.
- Strategy rules are never rewritten automatically. Parameter changes are an operator action, allowed only while the bot is paused and flat, and each change is versioned and logged.

## A consequence you should know before arming

At 0.5% risk per trade, a $1,000 virtual bot has a $5 risk budget. On a typical gold CFD (100 oz contract, 0.01 minimum lot) a $5 stop at the minimum lot already risks $5, so many day-trading and swing setups get **skipped** with `MIN_LOT_EXCEEDS_RISK_BUDGET`. I saw exactly this in the simulated run. Options: fund the demo account so each bot has more capital (a $100k demo gives $10k bots), accept fewer trades, or raise the risk per trade deliberately. I have not changed the 0.5% default.
