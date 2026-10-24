# Running metals bots unattended

Contents: layers of protection, failure modes specific to metals, deployment ladder, weekly routine, things to avoid.

## Layers, from most to least reliable

1. **Broker-side stop on every order.** Survives a dead terminal, VPS or internet connection. Non-negotiable.
2. **Account-level guard in the EA** (`MetalLabRiskGuard.mqh`). Daily, weekly and drawdown halts, caps, spread and session filters. Needs the terminal alive.
3. **Per-bot logic.** The strategy's own rules. The least trustworthy layer, because it fails in exactly the situations the strategy did not anticipate.
4. **The assessor review.** A weekly human-in-the-loop check with `review.py`. Catches slow decay and drift that no per-trade rule will.
5. **Alerts.** The guard sends a push notification on halts if MetaQuotes ID is configured in the terminal (Tools > Options > Notifications). Test it.

## Metals-specific failure modes

- **Gaps and weekends.** Gold and silver can open Monday well away from Friday's close. A stop becomes a market order at the new price. Use the Friday cutoff and consider flattening before the close for bots that are not built to hold over weekends.
- **Rollover and news spreads.** Spreads widen sharply around the daily rollover and at data releases (US CPI, NFP, FOMC). The spread filter and a news blackout matter more than an extra indicator.
- **Correlation.** Gold and silver tend to move together, so two "separate" positions are close to one bet. Size the open-position cap as if they will all lose at once.
- **Server time and DST.** Session hours and "daily" boundaries follow broker server time. A bot tuned for a London-open pattern can silently shift an hour twice a year.
- **Leverage and margin.** A tight margin level plus a gap is how accounts get stopped out before the bot's own stop. Keep a margin-level floor.
- **Swaps.** Holding overnight costs (or occasionally pays) swap; it can turn a marginal bot negative. `review.py` reports costs separately.
- **Execution.** Slippage and requotes are worse in fast markets, which is when stops matter. Budget for it when sizing (`extraPoints`).
- **Demo flatters.** Demo fills are cleaner than live ones. Treat demo results as an upper bound.

## Deployment ladder

1. Strategy tester validation (see `validation.md`).
2. Demo, with the guard, for at least a few weeks and a meaningful number of trades; run the guard checklist.
3. Live at a fraction of intended risk (for example a quarter), same limits.
4. Raise size only after a review shows live behaviour matches the tests: costs, win rate, drawdown. Raise in small steps, one at a time.
5. Never raise risk to recover a loss.

## Weekly routine (15 minutes)

1. Run `ExportDeals.mq5` and `review.py` with the guard log.
2. Read PAUSE and WATCH reasons. Ask what changed: market regime, spreads, broker, code, settings.
3. Look at the guard's rejections: a bot rejected constantly for spread or session has a mismatch with its environment.
4. Any HALT in the log is a mini post-mortem: what did the bot do before it, and was the limit working as designed?
5. Check the VPS is up and the terminal is connected.

## Things to avoid

- Martingale, grids and averaging down without a hard cap. They look great until the one loss that ends the account.
- Moving or removing stops on a losing trade.
- Changing the parameters of several bots at once; you will not know what worked.
- Optimising parameters until the backtest looks smooth (see `validation.md`).
- Letting the bots' combined risk exceed what the daily limit can absorb. `riskcheck.py` shows the numbers.
- Running bots you cannot explain in a sentence. The assessor can flag trouble, but it cannot supply understanding.
