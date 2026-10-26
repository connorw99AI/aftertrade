# Strategy research notes

Eleven explicit, rule-based hypotheses, each run as two bots (a long half and a short half, 22 bots in all), for spot/CFD gold and silver. None of them is a proven edge. The defaults are starting hypotheses taken from the cited sources or from common practice. Their job is to be tested on your broker's real data, not trusted.

Full rules, parameters and per-strategy citations: [STRATEGY_REFERENCE.md](STRATEGY_REFERENCE.md) (generated from code).

## Long and short halves

Every strategy runs as two independent bots: `S04L` only takes long entries and `S04S` only takes short ones. Each half has its own magic number (`7710000 + strategy*10 + 1` long, `+ 2` short), its own 1/22 allocation (£227.27 on the £5,000 demo), its own ledger and its own record, so a side that loses can be retired without retiring the strategy. S10 is a two-leg pair, so its halves are split by direction of the trade rather than by a single side: `S10L` buys silver and sells gold when silver is cheap on the ratio, `S10S` does the opposite.

**Retiring a half** (bot page or Assessor page, operator name required): the half must have no open trades. It stops opening trades and keeps its own balance; the two halves never share money, and each starts with 1/22 of the starting balance. Un-retiring brings it back stopped with that balance. Labs from before this change moved a retired half's balance to the other half; un-retiring moves it back (an audited ledger entry). The Lab page has **Un-retire all**, and the CLI has `metals-lab unretire-all --operator NAME`. A retired half collapses to one line at the bottom of the Lab page.

## Assessor changes applied from the backtest review

The Assessor's ranked suggestions from the October 2025 → October 2026 backtest were applied as defaults. They are **in-sample**: they were chosen on the same year they are judged on, so they will look better on that year than they really are. Validate them on an earlier year (download e.g. 2024-10 → 2025-10 on the Research page and run all the bots on that range) before trusting them.

| Strategy | Hours the backtest preferred, UTC (not applied: every bot starts all day) | Why (backtest evidence) |
|---|---|---|
| S03 | 12-21 UTC | silver and Asian/London hours lost |
| S04 | none (now trades the New York open) | the London-open version lost in London hours |
| S05 | (short half only trades) | longs lost |
| S06 | 0-7 and 16-24 UTC | silver and London hours lost |
| S07 | 12-24 UTC | early hours lost |
| S08 | 7-12 and 16-24 UTC | the 12-16 window lost |
| S09 | 0-7 and 21-24 UTC | best hours; others near zero |
| S10 | 21-24 UTC | only late hours were positive |

**Metals and trading windows are set per bot** (bot page > Rules & settings > Metals and trading windows). Each bot has a
gold switch and a silver switch (`trade_gold`, `trade_silver`: on/off) and its own permitted trading windows per metal
(`gold_windows`, `silver_windows`), in **UK time** (Europe/London, so they follow BST/GMT), e.g. `06:00-11:00, 13:00-17:00`
or `00:00-06:00, 09:20-13:00, 16:00-18:00, 20:00-22:00`. `00:00-24:00` is all day; a window can cross midnight
(`22:00-02:00`). Hours the market is shut are blocked anyway. A new entry on a metal needs that metal switched on and the
UK time inside one of its windows; S10 (a gold/silver pair) needs both metals' windows open and ignores the switches.
Backtests use the same windows. "Copy to all bots" on the bot page copies one bot's metals and windows to every paused,
flat bot.

**Every bot starts with both metals on and 00:00-24:00**, so for now they trade every hour the market is open. The
built-in session windows are off as well: S01 (07-20 UTC), S02 (Asian hours, flat before the London open) and S03 (08:00-21:00
London) trade at any hour. S04 and S05 still build their range in session (08:30-09:00 New York; 08:00-13:00 London) and
then may enter at any hour until the next day's range starts, with no forced afternoon close: they exit by stop, target or
maximum holding time. Each strategy keeps its session logic behind `use_session: on`. Still in force: the Friday close
from 20:00 UTC (S06 also stops entering at 18:00 on Fridays) and the broker's own closed hours, checked by the market
auditor. Expect more losing trades in the hours the backtest flagged, and wider spreads in quiet hours (the spread
guards stay).

## How the Assessor works

**Backtests: one overall assessment per bot** (`metals_lab/optimise.py`). It runs when you press **Assess** (bot page,
Review tab) or **Test all bots** (Assessor page), and once for every bot when **Run all** on MT5 history finishes. A
single backtest run is no longer reviewed on its own. Each assessment:

1. Backtests the bot as it stands over all the MT5 history downloaded for its charts, reviews those trades and names its
   **key failures** in plain words: no edge (loses, and no setting fixes it), costs eating the moves, winners turning
   into losers, profit from a handful of trades, most losses from one side or metal, only working in one year, too few
   trades to judge.
2. **Tunes** it in up to three rounds. History is split by time: search (first 60%), choose (next 20%), final (last 20%).
   Each round tries up to 14 changes on search (stops wider/tighter, targets nearer/further, holds shorter/longer,
   breakeven and trailing exits on/off, adding back a cut metal or hours, plus the review's own ideas). Changes that make
   more net profit with no deeper worst drawdown are checked on choose; the best is kept, and the next round searches on
   top of it.
3. Checks the tuned bot **once** on the final 20%, which tuning never saw. Only if it beats the original there (more net
   profit, worst drawdown no deeper, 10+ trades) is the tuned change offered; otherwise it is reported as probably
   overfit. Re-using the choose period across rounds is exactly why this last check exists.

Apply takes the whole tuned change in one step and only works while the assessment is current (the bot's settings have
not changed since). Applied changes are recorded and fold to one line. Two backtests run at a time
(`assessor.parallel_backtests`); a full assessment is roughly 30-60 backtests, so assessing all 22 bots on a year or
more of M5 history takes hours.

**Demo trading: every trade and the whole bot.** When a trade closes, the Assessor writes a note on it (shown on the bot's
Trades tab and in History): why it entered and exited, the result in R, how far it went for and against (in R), slippage
against the planned price, rule issues (no stop, closed by hand), and a verdict: good trade, unlucky (went +1R then
reversed), poor setup (never went its way), execution problem (bad fill or no stop) or scratch. Then the whole bot is
reviewed again.

**Clearing.** *Clear suggestions* removes open suggestions and finished assessments so new tests start from a clean list
(applied records stay). *Clear Assessor history* removes every review, assessment, applied record, trade note and
Workshop request. Neither touches backtests, trades, settings or the settings audit log; both need your name and are
logged.

**The Engineer (Workshop tab).** Rule ideas and key failures that no setting can fix are written up as change requests:
the problem, the evidence, the rule to build (as a new setting that starts off), and how to test it. **Copy for Claude
Code** puts the request on the clipboard to hand to a developer session; once it is built, press Assess and the Assessor
decides whether the new setting earns its place. Nothing is coded or changed automatically.

## Exit options: breakeven and trailing stop

Every bot except the S10 pair has two exit settings, both off by default:

- `breakeven_r`: once a trade is this many R in profit (R = distance from entry to the original stop), the stop moves to
  the entry price.
- `trail_atr`: the stop follows this many ATR(14) behind the best price since entry. When it is on, the fixed target is
  dropped so winners can run; the strategy's own exit signals and maximum hold still apply.

A stop only ever moves towards profit and stays on the right side of price (respecting the broker's minimum stop
distance). Backtests move it at each bar close; live, the lab stores the new stop and the per-cycle protection check
sends it to MT5 (if the broker refuses, entries are blocked and an alert is raised, as for any missing stop). The
Assessor tests both options per bot; turn them on only when a test passes.

## S11 Session Momentum (added October 2026)

Based on Gao, Han, Li and Zhou (2018), "Market intraday momentum": the return from the previous session's close to the
end of the first half hour predicts the last half hour's return (shown for US stock ETFs and index futures; a
commodity-futures study reports the same, but gold and silver are not confirmed). S11 uses the COMEX metals session
(08:20-13:30 New York): at 13:00 New York it goes long (S11L) if the first half hour was up, short (S11S) if it was
down, and closes at 13:30. Stop 2 ATR(14) on M5; one trade per metal per day. Unproven for spot metals: backtest it on
your own MT5 history before trusting it.

With S11 there are 22 bots, each with 1/22 (about 4.5%) of the starting balance (`capital.bot_fraction: 0.045454`; the
desktop launcher updates your config once). A lab that was already running with 20 bots at 5% is re-split once on the
next launch, keeping every trade: each bot's starting share becomes 1/22 of the recorded starting balance (the 20 bots give
up £22.73 each on a £5,000 demo, S11L and S11S get £227.27 each), and each balance is that share plus its own results so
far. Every adjustment is in the audit log ("Starting share re-split for 22 bots").

No half is retired by default. The Assessor flagged S01L, S01S, S02L, S02S (no edge on either side), S03S, S06S, S07S (shorts lost), S04L and S05L (longs lost); set `capital.apply_assessor_retirements: true` to start a fresh lab with those retired. Retire them yourself once demo results agree. Each filter is a normal setting (`sides`, the metal switches and windows) shown on the bot page, so you can change or remove it.

No bot showed a statistically real edge (best t-stat about 1.1). Treat everything above as hypotheses to test, not improvements you have.

## Watch chart and entry chart

Trend bots read direction from a slower chart and enter on a faster one (the common rule of thumb is a slower chart about 4-6 times slower): S01 trades M5 pullbacks only in the M15 trend; S07, S08 and S09 only trade with the hourly (H1) trend. Session bots (S02-S05) and the squeeze and ratio bots (S06, S10) are built around one chart. Session windows are set in London or New York time, so they follow clock changes; each bot's page shows the hours (UK time) it can actually open trades today on each metal (its own session combined with that metal's windows).

## Distribution and why

Timeframes were set by the operator: nothing slower than M15 for entries and no M1. Six strategies enter on M5 (S01-S05 and S11; S01 also reads the M15 trend) and five on M15 (S06-S10; S07-S09 also read the H1 trend). The former swing strategies (S07, S08, S09) now run intraday on M15, so there is no swing bot any more.

| Bot | Timeframe | Style | Mechanism |
|---|---|---|---|
| S01 Trend Pullback Scalper | M5 entries, M15 trend | scalp | trend continuation |
| S02 Asian Range Reversion | M5 (00:00-07:00 London) | scalp | range mean reversion |
| S03 Failed Breakout Reversal | M5 | scalp | failed-break reversal |
| S04 New York Opening Range Breakout | M5 (range 08:30-09:00 New York) | day | first break of the opening range |
| S05 London-Range Breakout & Retest | M5 | day | break plus retest |
| S06 Volatility Compression Breakout | M15 | day | compression release |
| S10 Gold/Silver Ratio Reversion | M15 | day | intraday relative value (two-leg group) |
| S07 Regime-Filtered RSI(2) Reversion | M15 (H1 regime filter) | day | pullback reversion with trend filter |
| S08 Donchian Channel Breakout | M15 entries, H1 trend | day | 6-hour channel breakout with the hourly trend |
| S09 Time-Series Momentum | M15 entries, H1 trend | day | one-day momentum with the hourly trend, evaluated hourly |

**No one-minute entries.** Every bot enters on M5 or M15 charts (S01 moved from M1 to M5) because MT5 keeps years of M5/M15 history but often only months of M1, so every bot can be backtested over the same long periods. H1 is used only as a trend filter by S07-S09 and has the deepest history of all. S01 keeps a spread guard (spread ≤ 20% of ATR) and a 2:1 target; on gold, the roughly $0.43 spread is a much smaller share of a five-minute range than of a one-minute one.

S07, S08, S09 and S10 on M15 are adaptations. The cited evidence for all four is at daily or monthly horizons.

**Trade frequency.** The operator target is about 5 trades per bot per day, and the risk engine caps each bot at 5 new trades per UTC day (`risk.max_entries_per_bot_per_day`; a two-leg S10 group counts once). Filters were loosened to trade more often. On synthetic data, S03 and S07 reach roughly 3-5 a day, S01 fewer since it moved to M5 entries, and S08, S09 and S10 roughly 2-3. The session strategies (S02 Asian range, S04 New York opening range, S05 London range, S06 squeeze) are built around one setup per session, so expect about 1 a day from each. Real-market frequency will differ from the synthetic figures. Trading more often is not evidence of edge: each extra trade pays the spread again.

They are deliberately not eleven renamings of one idea. They differ in mechanism (trend, mean reversion, failed break, volatility regime, cross-sectional relative value), horizon and entry trigger.

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
- Sizing comes from the bot's own equity: risk budget ÷ (loss at stop per lot + estimated round-trip cost per lot), rounded down to the lot step. Operator policy is a minimum size per metal: tighter stops trade the larger size (`risk.big_lot`: gold 0.1, silver 0.06) when its loss at the stop is at most 30% of the bot (`big_lot_max_risk`), wider stops the smaller one (`risk.min_lot`: gold 0.05, silver 0.03). When 3% of the bot allows more, it trades more. With `min_lot: 0`, a trade whose broker minimum exceeds the budget is skipped instead.
- S10 opens two legs as one trade group. Both legs must pass the auditor and risk checks; if one leg fails after the other has filled, the filled leg is closed. The money popup fires only when the whole group has closed.
- Strategy rules are never rewritten automatically. Parameter changes are an operator action, allowed only while the bot is paused and flat, and each change is versioned and logged.

## A consequence you should know before arming

The operator runs a £5,000 demo. With 22 independent bots at 1/22 each, every bot starts with £227.27. Operator choices:

- **Two trade sizes, chosen by the strategy's stop** (the stop is always the strategy's own; risk limits never move it):
  - Tighter stops: **gold 0.1, silver 0.06** (`big_lot`), used when that size loses at most 30% of the bot at the stop (`big_lot_max_risk: 0.30`).
  - Wider stops: **gold 0.05, silver 0.03** (`min_lot`).
  - No per-trade cap (`max_trade_risk: 0`): no trade is skipped for its stop being wide, so one wide-stop loss can exceed 30% of a bot.
  - A bot trades more than these only when 3% of its balance allows it, which at £227 is rare.

- What that means (from the terminal's own figures, GBP account): gold 0.1 lot is about **£7.50 per $1 move** and 0.05 about £3.75; silver 0.06 lot is about **£225 per $1 move** and 0.03 about £115 (scaled from the terminal's 0.04/0.02 figures). On a £227 bot the larger size is used for gold stops up to about $9 and silver stops up to about 30 cents. Examples at the smaller size: a $15 gold stop loses about £56 (25%); a 40-cent silver stop about £46 (20%).
- Leverage (1:500) sets the margin, not the loss. Margin for 0.1 lot gold is lots × 100 oz × price ÷ 500, roughly £50-80, and each bot must cover it from its own free balance, so two open gold trades can use half or more of a £227 bot. The loss at the stop is the same at any leverage.
- **Daily stops** (new entries only; open trades keep running, so a day can end worse):
  - **Loss stop 30%** (`daily_loss_pause: 0.30`): down 30% on the day.
  - **Profit lock** (`daily_lock_from: 0.05`, `daily_giveback: 0.5`): once a bot is up 5% or more on the day, it stops for the day if it gives back half of its best gain that day. Three wins then losses: it stops with about half the day's gain kept.
  - **No daily profit stop** (`daily_profit_stop: 0`).
  - A daily stop, once hit, holds until the UTC day ends even if equity recovers.
- **Drawdown pause at 50%** (`max_drawdown_pause: 0.50`): a bot that falls 50% (£114 from £227) from its best balance stops opening trades until the operator restarts it. Open trades keep being managed.
- **No pre-trade risk caps** (`pre_trade_limits: false`): per-trade risk, margin-share and exposure caps are off. Still enforced before each entry: the bot's own free capital covers the margin, the account margin level stays above 300%, at most 2 open trades and 5 new trades a day per bot, and the daily stops.
- **News checks are off** (`news.enabled: false`).
- **Flat for the weekend** (`weekend_flat_from: "20:00"`): from Friday 20:00 UTC every bot closes its positions and opens nothing until Sunday 22:00 UTC. Overnight holds on weekdays are allowed.
- Exposure caps are per bot (`exposure_scope: per_bot`) at 200× equity; they only stop extreme stacking.

**Choosing a live candidate** (Ranking page): a bot is eligible after at least 50 closed demo trades and a profit after costs; eligible bots are ranked by smallest maximum drawdown, then net profit. Picking the best of 22 bots still favours luck, so treat the top rank as a shortlist and check it with the research tools (walk-forward, deflated Sharpe, cost stress) before risking money.
