# Known limitations, unverified items and next priorities

## Checked only on your PC, not here

The code is built and tested in a Linux cloud container. The `MetaTrader5` package is Windows-only, so every automated test uses the simulated terminal. You have run the launcher, downloaded MT5 history and traded on the demo account on your PC; that is your report, not something checked from here. Still worth watching against your broker:
  - the exact field names returned by `symbol_info`, `positions_get` and `history_deals_get`;
  - `order_check` return codes on your broker;
  - the filling modes your broker accepts.
- **Server-time handling** follows how MT5 behaves in practice (epoch values in server wall time). It still needs checking against your broker with `metals-lab check`.

## Data and research

- **Real-market backtests so far lost money.** The first runs on MT5 history lost about 98% on every bot (your report). Most of that came from trade sizes that risked 14-40% of a £227 bot per trade, plus restarts after each 50% drawdown; costs alone are about −0.08R per trade on M5. Sizing is now small (2% per trade, 0.01 lot floor); re-run before judging any bot. Synthetic results are labelled and say nothing about profitability.
- **Scalper results on OHLC bars are unreliable.** Intrabar ordering is unknown. The engine assumes the stop is hit before the target, counts every ambiguous bar, and uses ticks to resolve them when tick data is supplied. Collecting deep tick history from MT5 is broker-limited.
- **Historical swaps, commissions and conversion rates are assumptions**, recorded in each result: the current symbol swap in points, a configurable commission and a USD account. Only swap mode POINTS is modelled; other modes are reported as unsupported.
- **Backtest margin** is approximated as notional ÷ leverage. Live trading uses `order_calc_margin`.
- **H4/D1 bars resampled in UTC** differ from broker bars anchored to server midnight. For research, fetch terminal H4/D1 bars instead of resampling.
- **Synthetic data in tests** exercises the machinery only. Some strategy filters were loosened in the long/short coverage test because synthetic spreads block them, and this is noted in the test.

## Product gaps

- **News:** only an operator-maintained CSV provider ships. Automated, authoritative calendar ingestion (Fed, BLS, BEA) is the top next priority. Until a verified calendar exists, the default policy sends every new entry to manual review.
- **Manual review** is recorded as an event and alert. There is no approve-this-trade button; the bot re-proposes on its next decision. This matches the requirement not to approve trades one by one.
- **Per-bot P&L includes only that bot's MT5 deals.** Account-level charges without a position (some fees) land in the unallocated pool.
- **The approval mechanism** stops accidental bypass. It is not a defence against malicious code inside the process.
- **Mobile access** is local-network only, with no auth beyond an optional token. Secure remote access is deferred.
- **Performance:** a live cycle with all 22 bots takes about 150-200 ms on the simulator. Real terminal I/O will add to that. Long M5 backtests take tens of seconds per year of data, and an Assessor assessment is 30-60 backtests over all the downloaded history, so assessing all 22 bots on many years of M5 data can take a day or more.

## Next priorities

1. Clear Assessor history, re-run the backtests on MT5 history with the small-trade sizing, then Test all bots. Only then decide which bots to keep running.
2. Run `check.bat` after any broker or terminal change and fix whatever the real terminal returns differently.
3. Maintain the news calendar, or add an authoritative calendar provider.
4. Run the MQL5 session export and configure `server_timezone`.
5. Run a week of shadow mode before arming, and compare shadow decisions with what you would expect.
