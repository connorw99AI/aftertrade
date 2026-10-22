# Test results

Environment: Linux cloud container, Python 3.13.16, pandas 3.1, numpy 2.5, FastAPI 0.161, Node 22, Next.js 15.5.27.
All tests run against the built-in `SimTerminal`. No real MT5 terminal was used. The gateway refuses `order_send` to a non-simulated backend while tests run.

```
cd metals-lab/backend && python -m pytest tests
91 passed (about 5 minutes; the deterministic end-to-end simulation dominates)
cd metals-lab/dashboard && npx tsc --noEmit && npx next build
typecheck clean; static export built (9 routes)
```

| File | Covers |
|---|---|
| `test_strategies.py` (26) | Ten strategies with full metadata and citations. Every strategy emits both OPEN_LONG and OPEN_SHORT with correct stop placement. CLOSE_LONG and COVER_SHORT are distinguished. No lookahead: indicators computed on truncated data equal those on full data, for every column, at random cut points. Sizing uses the bot's own equity and skips trades whose minimum lot exceeds the budget. Pair strategy produces two-leg groups. Unknown parameters are rejected. |
| `test_instruments_sessions.py` (10) | Discovery rejects a stock CFD named GOLD and requires an explicit choice when several contracts qualify. Contract kinds are classified. Lot and price rounding, filling-mode selection, DST-aware server clock, measured offsets, session windows and closures, MQL5 export parsing, London-open DST shift, gap inference. |
| `test_backtest.py` (10) | Next-bar-open fills at ask/bid. Commissions. Stop gaps fill at the worse open. Ambiguous bars assume the stop first, and ticks resolve them. Short stops use the ask. Lot rounding and minimum-lot skips, margin rejection, swap at rollover, seeded rejections and partial fills, determinism, reliability labels, daily-loss pause. |
| `test_ledger_guard.py` (12) | 10% snapshot taken once. Capital isolation between bots. Idempotent deal booking. Short proceeds are never spendable. Allocation changes need funding, a reason and an operator. Live, contest, unknown and disconnected accounts are rejected. Live account blocked at the order boundary. Non-sim backend refused under test. Fingerprints hide the login. Netting accounts are blocked. |
| `test_pipeline.py` (20) | Auditor has no order capability. Executor refuses: no auditor, no risk, forged signature, approval for another proposal, expired approval, replayed approval. Risk engine rejects proposals without an audit approval. Not armed means nothing is sent. Account switch disarms and revokes approvals. A live account found mid-run disarms. Duplicate prevention. Server-side stop attached; an unprotectable position is closed. Partial fill and partial close produce no popup until the full close, with net including costs. Unrealized changes produce no popup. Manual trade and deposit raise blocking alerts and the deposit is not distributed. Closes still work while paused or killed. Timeout followed by restart recovers ownership from terminal records. Disconnect and reconnect. News outage requires manual review while exits still pass. News blackout delays with a reassessment time; a stale calendar is UNKNOWN. Stale-quote and spread-blowout delays. Risk limits and stop validation. Shadow mode sends nothing. |
| `test_api_research.py` (12) | Dashboard API contracts. No arm endpoint exists. Bot controls and parameter-change rules. API token enforcement. Backtest endpoint labels output as historical replay. Dataset versioning, integrity and quality report. Bars built from ticks. Chronological splits and walk-forward windows. Deflated Sharpe penalises more trials. Bootstrap CI. Holdout usable only once. Event-filter opportunity-cost report. |
| `test_e2e.py` (1) | Full simulated run (arm, 10 bots, 350 bars) is deterministic across runs. The account balance change equals the sum of all bots' booked P&L. One TRADE_CLOSED event per closed trade. |

Manual checks done here: the dashboard was served by the API in simulated mode and screenshotted at desktop (1280 px) and phone (390 px) widths. The trade-close popup, bot detail, risk centre and settings pages rendered correctly. The research CLI ran end to end on synthetic data.
