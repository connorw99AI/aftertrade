# Strategy research notes

Ten explicit, rule-based hypotheses for spot/CFD gold and silver. None of them is a proven edge. The defaults are starting hypotheses taken from the cited sources or from common practice. Their job is to be tested on your broker's real data, not trusted.

Full rules, parameters and per-strategy citations: [STRATEGY_REFERENCE.md](STRATEGY_REFERENCE.md) (generated from code).

## Distribution and why

Timeframes were set by the operator: nothing slower than M15, only one bot on M1. That gives one bot on M1, four on M5 and five on M15. The former swing strategies (S07, S08, S09) now run intraday on M15, so there is no swing bot any more.

| Bot | Timeframe | Style | Mechanism |
|---|---|---|---|
| S01 Trend Pullback Scalper | M1 | scalp | trend continuation |
| S02 Asian Range Reversion | M5 | scalp | range mean reversion |
| S03 Failed Breakout Reversal | M5 | scalp | failed-break reversal |
| S04 London Opening Range Breakout | M5 | day | first break of the opening range |
| S05 London-Range Breakout & Retest | M5 | day | break plus retest |
| S06 Volatility Compression Breakout | M15 | day | compression release |
| S10 Gold/Silver Ratio Reversion | M15 | day | intraday relative value (two-leg group) |
| S07 Regime-Filtered RSI(2) Reversion | M15 (H1 regime filter) | day | pullback reversion with trend filter |
| S08 Donchian Channel Breakout | M15 | day | 6-hour channel breakout |
| S09 Time-Series Momentum | M15 | day | one-day momentum, evaluated hourly |

**Cost warning for M1:** on the connected demo, gold's spread was about $0.43. That is often 25-40% of a one-minute range, so S01 keeps a spread guard (spread ≤ 30% of ATR) and a 2:1 target, and will skip many signals. M1 backtests without tick data are the least reliable results in this lab.

S07, S08, S09 and S10 on M15 are adaptations. The cited evidence for all four is at daily or monthly horizons.

**Trade frequency.** The operator target is about 5 trades per bot per day, and the risk engine caps each bot at 5 new trades per UTC day (`risk.max_entries_per_bot_per_day`; a two-leg S10 group counts once). Filters were loosened to trade more often. On synthetic data, S01, S03 and S07 reach roughly 3-5 a day and S08, S09 and S10 roughly 2-3. The session strategies (S02 Asian range, S04 and S05 London range, S06 squeeze) are built around one setup per session, so expect about 1 a day from each. Real-market frequency will differ from the synthetic figures. Trading more often is not evidence of edge: each extra trade pays the spread again.

They are deliberately not ten renamings of one idea. They differ in mechanism (trend, mean reversion, failed break, volatility regime, cross-sectional relative value), horizon and entry trigger.

## Evidence base and how strong it is

- **Trend following / time-series momentum (S08, S09).** Moskowitz, Ooi & Pedersen (2012, *JFE* 104:228-250) report time-series momentum in 58 futures markets, including commodities; Hurst, Ooi & Pedersen (2017) extend it over a century. Caveats: these are diversified *portfolio* results. Kim, Tse & Wald (2016) attribute much of the effect to volatility scaling and report weakness after 2009. With two correlated metals and M15 bars, this is a long way from the published evidence.
- **Macro announcements (auditor; S05 timing).** Cai, Cheung & Wong (2001, *J. Futures Markets* 21(3):257-278): among 23 US releases, employment, GDP, CPI and personal income move gold volatility the most. This is the basis for the news blackout, not a source of edge.
- **Intraday seasonality (S01-S06 time filters).** Batten, Lucey, McGroarty, Peat & Urquhart (2017, *PLoS ONE*): 5-minute data on gold, silver, platinum and palladium show intraday volatility and activity cycles linked to market opens and closes, with the tightest spreads during European hours.
- **Opening-range breakout (S04).** Crabel (1990); Zarattini & Aziz (2023, SSRN) for equity-index ETFs. Transfer to 24-hour OTC metals is **untested**.
- **Turtle Soup failed breakout (S03).** Connors & Raschke (1995) on daily bars; the M5 adaptation is a hypothesis.
- **RSI(2) with a 200-day filter (S07).** Connors & Alvarez (2008) on US equities, mostly long-side. Using it on metals, on M15 bars with an H1 regime filter, and on the short side are all adaptations.
- **Squeeze (S06).** Bollinger (2001); Carter (2005). Compression predicts expansion better than it predicts direction.
- **Gold/silver ratio (S10).** Gatev, Goetzmann & Rouwenhorst (2006) for the pairs methodology. Cointegration studies of gold and silver (TCD IIIS DP 55; MPRA 109830) give mixed findings. The ratio trended for years at a time (e.g. 2011-2020), so it can stop mean-reverting for long stretches.
- **Multiple testing.** Bailey & López de Prado (2014, *JPM* 40(5)): Deflated Sharpe Ratio, implemented in `research/validation.py`.

Sources I could not verify against primary full text (I relied on abstracts and well-known summaries): Crabel (out of print), Connors & Raschke, Carter. Treat their descriptions as practitioner rules, not peer-reviewed evidence.

## Explicit design decisions

- Every exposure change is labelled `OPEN_LONG`, `CLOSE_LONG`, `OPEN_SHORT` or `COVER_SHORT`. A buy is never ambiguous.
- Decisions use completed bars only. Orders fill at the next bar's open in backtests, and on the next live tick after the bar closes in live trading.
- Each opening carries a protective stop that is sent to the server with the order.
- Sizing comes from the bot's own equity: risk budget ÷ (loss at stop per lot + estimated round-trip cost per lot), rounded down to the lot step. Operator policy is a **0.1 lot minimum** (`risk.min_lot`): when the budget allows more, the bot trades more; when it allows less, it still trades 0.1. With `min_lot: 0`, a trade whose broker minimum exceeds the budget is skipped instead.
- S10 opens two legs as one trade group. Both legs must pass the auditor and risk checks; if one leg fails after the other has filled, the filled leg is closed. The money popup fires only when the whole group has closed.
- Strategy rules are never rewritten automatically. Parameter changes are an operator action, allowed only while the bot is paused and flat, and each change is versioned and logged.

## A consequence you should know before arming

The operator runs a £3,000 demo so each bot has £300, the amount the best bot would get live. Operator choices:

- **Minimum lot per trade: gold 0.04, silver 0.02** (`min_lot: {GOLD: 0.04, SILVER: 0.02}`), even when that risks more than 3% of the bot. A bot trades more than the minimum only when 3% of its equity allows it, which at £300 is rare. There is **no per-trade loss limit** (`per_trade_limit_enabled: false`).
- What that means (from the terminal's own figures, GBP account): gold 0.04 lot is about **£3 per $1 move**; silver 0.02 lot is about **£75 per $1 move**. Examples: a $5 gold stop loses about £15 (5% of £300); a $15 gold stop about £45 (15%). A 20-cent silver stop loses about £15 (5%); a 50-cent silver stop about £37 (12%).
- Leverage (1:500) sets the margin, not the loss. Margin for 0.04 lot gold is lots × 100 oz × price ÷ 500, a few tens of pounds, so margin rarely limits these trades. The loss at the stop is the same at any leverage.
- **Daily stops**: no new entries for the rest of the UTC day after losing 15% (£45) or gaining 30% (£90). Trades already open keep running, so a day can end worse than -15%.
- **Drawdown pause at 50%** (`max_drawdown_pause: 0.50`): a bot that falls 50% (£150) from its best balance stops opening trades until the operator restarts it. Open trades keep being managed.
- **No pre-trade risk caps** (`pre_trade_limits: false`): per-trade risk, margin-share and exposure caps are off. Still enforced before each entry: the bot's own free capital covers the margin, the account margin level stays above 300%, at most 2 open trades and 5 new trades a day per bot, and the daily stops.
- **News checks are off** (`news.enabled: false`).
- **Flat for the weekend** (`weekend_flat_from: "20:00"`): from Friday 20:00 UTC every bot closes its positions and opens nothing until Sunday 22:00 UTC. Overnight holds on weekdays are allowed.
- Exposure caps are per bot (`exposure_scope: per_bot`) at 200× equity; they only stop extreme stacking.

**Choosing a live candidate** (Ranking page): a bot is eligible after at least 50 closed demo trades and a profit after costs; eligible bots are ranked by smallest maximum drawdown, then net profit. Picking the best of ten still favours luck, so treat the top rank as a shortlist and check it with the research tools (walk-forward, deflated Sharpe, cost stress) before risking money.
