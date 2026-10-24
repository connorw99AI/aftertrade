# Auditor, risk engine and execution safety

```
strategy signal ─► Proposal ─► Market Auditor ─► Central Risk Engine ─► Executor ─► MT5 (demo only)
                      (sized from     (read-only;     (verifies AUDIT      (verifies both approvals,
                       own equity)     issues AUDIT    approval, issues     re-checks DEMO at the
                                       approval)       RISK approval)       order boundary)
```

## Why the gates cannot be skipped

- The auditor and the strategies only ever receive a `ReadOnlyGateway`, which has no `order_send` or `order_check`. Only the executor holds a `TradingGateway`.
- Approvals are HMAC-signed with a per-process key that belongs to each authority. Each approval is bound to the proposal hash, the account fingerprint and an expiry (default 20 s), and it is stored in SQLite.
  - The risk engine rejects any proposal without a valid AUDIT approval.
  - The executor needs both approvals. It consumes them in the same database transaction that writes the durable intent, so an approval cannot be reused.
  - Disarming, or switching accounts, revokes every outstanding approval.
- Tests in `test_pipeline.py` attempt to bypass each gate: no auditor, no risk check, a forged signature, an approval for a different proposal, an expired approval, a replayed approval, and a switched account.
- Limit of this design: it makes a bypass a structural error. It does not protect against hostile code running inside the same Python process.

## Demo-only enforcement

`account_info().trade_mode` must equal `ACCOUNT_TRADE_MODE_DEMO`. Live, contest, unknown and disconnected states are all rejected. The check runs:

- at startup (`connect`);
- before activation (`preflight`/`arm`);
- every cycle, including after a reconnect;
- inside `TradingGateway.order_send`, immediately before the order is sent.

A live account found at the order boundary disarms the system and raises a critical alert. Activation is bound to a salted SHA-256 fingerprint of (login, server, company); the login itself is never stored. Under pytest, `order_send` refuses any backend that is not the simulator.

## Market auditor (ruleset `metals-audit-1.0`)

Decisions: `APPROVED`, `DELAYED` (with a reassessment time), `REJECTED`, `MANUAL_REVIEW_REQUIRED`. Each decision records the triggered rules, the reasons, an evidence dict, the data-quality status, the news status and the ruleset version.

| Rule | Applies to | Effect |
|---|---|---|
| R01 session closed (MQL5 export or config) | all | DELAYED |
| R02 no quote | openings: REJECTED; closes: DELAYED | |
| R03 stale quote (> 30 s) | openings | DELAYED (closes are flagged only) |
| R04 spread > 3× this instrument's hourly baseline | openings | DELAYED |
| R05 too few bars to assess | openings | DELAYED |
| R06 last bar moved > 3 ATR | openings | DELAYED |
| R07 gap > 2 ATR | openings | DELAYED |
| R08 realized volatility (12 bars vs 288) > 3× | openings | DELAYED |
| R09 tick activity z > 4 together with a large move (tick activity is quote-update count, not traded volume) | openings | DELAYED |
| R10 gold and silver both move > 2.5 ATR the same way (macro/dollar shock) | openings | DELAYED |
| R11 optional reference symbols (e.g. EURUSD), read-only | openings | DELAYED |
| R12 news status UNKNOWN | openings | policy: MANUAL_REVIEW (default) / DELAYED / allow with flag |
| R13 inside a high-impact release window (−30/+30 min) | openings | DELAYED until the window ends |
| R14 operator market alert (e.g. geopolitics) | openings | MANUAL_REVIEW |
| R15 symbol trade mode forbids new positions | openings | REJECTED |
| R16 rapid spike and reversal | openings | DELAYED |

- **Closes and covers** are blocked only when the market is closed; every other rule is recorded as evidence. This keeps risk-reducing exits possible.
- **DELAYED proposals are never queued.** At the reassessment time the strategy reruns on fresh bars, with fresh sizing and a fresh audit.
- **News.** Providers are replaceable (`auditor/news.py`). The bundled provider reads an operator-maintained CSV, which must carry an `# as_of:` verification header.
  - A missing, stale or failed calendar returns UNKNOWN, never "no news".
  - `known_since_utc` makes historical use point-in-time.
  - No AI classifier is used. If one is added, it may only add evidence.
- **Filter on/off comparison.** `Research.event_filter_comparison` runs the same data with and without the non-safety filter. It reports blocked trades, blocked profitable trades, opportunity cost and avoided losses. Filtering is not assumed to help.

## Trade-setup checks (auditor)

Before any opening order is sent, the auditor checks the order itself and rejects it if:

| Rule | Check |
|---|---|
| R17 | lot size is not a valid lot for the symbol (minimum, step, maximum) |
| R18 | lot size is below the configured `min_lot` for that metal |
| R19 | there is no stop-loss |
| R20 | the stop is on the wrong side of the entry price |
| R21 | the stop is inside the broker's minimum stop distance |
| R22 | the stop is more than `auditor.max_stop_atr` (15) ATRs away, which points to a bug |
| R23 | the take-profit is on the wrong side of the entry price |
| R24 | the take-profit is inside the broker's minimum stop distance |

After a trade is open, every cycle (about 10 seconds) the executor compares each open position in MT5 with the stop and target it was opened with. A missing or changed level is put back (event `PROTECTION_RESTORED`); if that fails, an alert blocks new entries until it is fixed (`PROTECTION_FAILED`).

News checks are off by operator choice (`news.enabled: false`).

## Central risk engine

With `pre_trade_limits: false` (operator choice) the per-trade risk, bot margin share, account margin share and exposure caps in this table are not applied. The rest still are.

All limits are configurable (`config.yaml → risk`) and measured against each bot's own equity unless marked otherwise.

| Check | Default |
|---|---|
| Planned loss at stop (`order_calc_profit`) + estimated spread/commission ≤ risk per trade | **off** (`per_trade_limit_enabled: false`); planned risk is logged. 3% when on |
| Minimum trade size (sizing floor) | gold 0.04 lot, silver 0.02 lot (`min_lot`) |
| Trade skipped if even the minimum size would lose more than this share of the bot at its stop | 30% (`max_trade_risk`) |
| New trades per bot per UTC day (a two-leg group counts once; shadow approvals count too) | 5 |
| Daily bot loss → entries blocked for the rest of the UTC day | 30% (`daily_loss_pause`) |
| Profit lock: once up 5%+ on the day, giving back half the day's best gain → entries blocked for the rest of the day | 5% / half (`daily_lock_from`, `daily_giveback`) |
| Daily bot gain → entries blocked for the rest of the UTC day | off (`daily_profit_stop: 0`) |
| A daily stop, once hit, holds until the UTC day ends (open trades still managed) | |
| Bot drawdown from peak → bot set to PAUSED_RISK; operator must resume (open trades still managed) | 50% |
| Friday from 20:00 UTC → close all positions, no entries until Sunday 22:00 UTC | on (`weekend_flat_from`) |
| Open positions per bot | 2 |
| Bot margin (`order_calc_margin`) ≤ free capital, and total bot margin ≤ 50% of bot equity | |
| Account margin used ≤ 50% of account equity; margin level ≥ 300% | account-wide |
| Gross metals value ≤ 200× equity; same-direction gold+silver value ≤ 200× (account currency) | per bot by default (`exposure_scope: per_bot`), or account-wide |
| Spread cap per instrument; stop and target outside the broker's stops/freeze level + buffer; stop on the correct side | |
| Lot on the volume step and ≥ minimum | |
| Kill switch, global pause, bot status, blocking alerts, unclean reconciliation, an order already in flight for this bot and symbol | |

These are planning limits, not guarantees: gaps, slippage and weekend opens can produce larger realized losses. Risk-reducing proposals are approved whenever ownership and volume check out, even while paused or killed.

## Executor

1. Verify both approvals.
2. Run a fresh account check: DEMO, same fingerprint as at activation, hedging, algo trading enabled.
3. Prevent duplicates: one intent per signal, and none while another order for the same bot and symbol is in flight.
4. Write a durable `SUBMITTING` intent to the database.
5. Call `order_check`.
6. Call `order_send`, which carries SL/TP for openings and `position=<ticket>` for closes, with the filling mode taken from the symbol's flags.
7. Handle the result:
   - `DONE` or `DONE_PARTIAL`: record the fill and the position.
   - Any other retcode: `REJECTED`.
   - No reply or an exception: `UNKNOWN`, resolved later by reconciliation (comment tag, then order ticket).
8. After a fill, confirm the server-side stop exists. If it is missing, try `TRADE_ACTION_SLTP`. If that fails, close the position immediately and raise an alert.

## Pause and kill

- **Pause** (per bot or global): no new entries. Strategy exits and server-side stops keep working.
- **Kill** (dashboard button with confirmation, or `metals-lab kill`):
  1. Set the kill flag, which blocks all entries.
  2. Close every bot-owned position through the normal auditor → risk → executor path.
  3. Disarm.
  - Positions that cannot be closed (market closed, rejection) keep their server-side stops and raise blocking alerts.
  - `metals-lab clear-kill` clears the flag, but the system stays disarmed until you arm again.

## Reconciliation and ownership

- Hedging accounts are required. On netting or exchange accounts, automatic execution is blocked, because separate magic numbers cannot create independent positions when the server nets them.
- Every cycle, MT5 deals are mirrored idempotently (unique deal ticket) and attributed by position ticket first, then by magic number.
- Each deal's profit, commission, swap and fee are booked to the owning bot (unique `(kind, ref)` ledger rows). Partial closes reduce open volume.
- Popups: a `TRADE_CLOSED` event fires only when a position or trade group is fully closed. It stays provisional until a settle window (default 5 min) passes with no further deals, then becomes `TRADE_FINALIZED`.
- The following raise **blocking** alerts and stop new entries until an operator reviews them: manual or unknown trades, unknown positions, positions that vanished without a closing deal, and balance operations (deposits, withdrawals, credits).
- Balance operations land in an unallocated pool and are never distributed automatically.
- Every half has its own allocation (5% of the starting balance) and its own ledger; retiring a half only stops new entries, its balance stays on its own book. Un-retiring returns any balance an older lab moved to the other half, as an audited `ALLOCATION_MOVE`.
