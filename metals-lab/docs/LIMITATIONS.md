# Known limitations, unverified items and next priorities

## Not verified, because this was built in a Linux cloud container, not on your PC

- **No real MT5 connection was made.** The `MetaTrader5` package is Windows-only and could not be installed here. The real gateway passes through to documented package functions and uses the package's own constants at runtime. Its behaviour against a real terminal is **unverified**, including:
  - the exact field names returned by `symbol_info`, `positions_get` and `history_deals_get`;
  - `order_check` return codes on your broker;
  - the filling modes your broker accepts.
- **No demo orders were submitted, and none were attempted.**
- **The Windows launcher (`setup.ps1`, `start.bat`, `check.bat`) has not been run on Windows.**
- **Server-time handling** follows how MT5 behaves in practice (epoch values in server wall time). It still needs checking against your broker with `metals-lab check`.

## Data and research

- **No real-market backtest results exist yet.** Every backtest and research run so far used SYNTHETIC data and is labelled as such. Such results say nothing about profitability.
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
- **Performance:** a live cycle with all 20 bots takes about 150-200 ms on the simulator. Real terminal I/O will add to that. Long M5 backtests take tens of seconds per year of data.

## Next priorities

1. Run `setup.ps1` and `check.bat` on your PC and fix whatever the real terminal returns differently.
2. Fetch 1-2 years of real broker history and run `research` for each bot without touching holdouts. Only then decide which bots to start.
3. Maintain the news calendar, or add an authoritative calendar provider.
4. Run the MQL5 session export and configure `server_timezone`.
5. Run a week of shadow mode before arming, and compare shadow decisions with what you would expect.
