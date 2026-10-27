# Metal Lab

A demo-only MetaTrader 5 laboratory for gold and silver. 24 independently configurable bots (12 strategies, each run as a long half and a short half, with separate gold and silver settings), each a pixel creature with its own virtual portfolio, share one MT5 **demo** account. Every trade goes strategy → independent market auditor → central risk engine → demo executor. Live accounts are technically blocked.

This makes no claim of profitability. The first backtests on real MT5 history (October 2026, with the old, larger trade sizes) lost money on every bot; they need re-running with the current small-trade sizing before any bot is judged.

```
metals-lab/
  backend/            Python package `metals_lab` (authoritative logic) + pytest suite
  dashboard/          Next.js/TypeScript creature dashboard (prebuilt static export in dashboard/out)
  config/             example config (no credentials) and news-calendar template
  mql5/               ExportSessions.mq5: read-only script that exports trading sessions
  scripts/windows/    setup.ps1 (guided, read-only), start.bat, check.bat
  docs/               operations, strategies, risk/auditor, limitations, test results
```

The existing `index.html` (the Aftertrade journal) at the repository root is untouched. It contained no Alpaca code and no creature dashboard, and neither did any other repository on the account, so the creatures were drawn new. The visual language takes Aftertrade's mobile-first single column and adds its own palette.

## Quick start (Windows)

1. Open an MT5 **hedging demo** account in the terminal, stay logged in, and enable Algo Trading.
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1` installs dependencies and runs read-only checks.
3. `scripts\windows\start.bat` starts the API and dashboard on http://127.0.0.1:8765 in shadow mode.
4. When you are ready, arm manually: **Arm demo trading** on the Lab or System page (on the PC running Metal Lab), or `.venv\Scripts\metals-lab arm --operator "Your Name"`. Both re-check the demo account and need a typed phrase.

Details: [docs/OPERATIONS.md](docs/OPERATIONS.md).

Without MT5, on any OS, you can try the UI with simulated terminal and synthetic data. It uses a separate database and is labelled SIMULATED throughout:

```
cd backend && pip install -e . && cd .. && metals-lab --sim serve
```

## Commands

| Command | What it does |
|---|---|
| `metals-lab check` | Read-only: demo verification, instruments and contract specs, clock, sessions, blockers |
| `metals-lab serve` | API + dashboard + live loop (shadow until armed) |
| `metals-lab arm --operator NAME` | Enable demo execution from a terminal (typed confirmation); the dashboard's Arm button does the same from the PC running Metal Lab |
| `metals-lab disarm` / `kill --reason` / `clear-kill` | Stop trading; kill also closes all bot positions |
| `metals-lab bot S04 start\|pause\|stop` | Bot control |
| `metals-lab allocate --bot --delta --reason --operator` | Audited allocation change |
| `metals-lab fetch-history [--ticks]` | Cache terminal history to Parquet with provenance |
| `metals-lab backtest --strategy S08` / `research --strategy S08 [--holdout]` | Research workflow |

## Documentation

- [Strategy research notes](docs/STRATEGIES.md) and the [generated strategy reference](docs/STRATEGY_REFERENCE.md)
- [Auditor, risk engine, execution safety](docs/RISK_AND_AUDITOR.md)
- [Operations: setup, activation, data, server move, database](docs/OPERATIONS.md)
- [Limitations and next priorities](docs/LIMITATIONS.md)
- [Test results](docs/TEST_RESULTS.md)

## Honest status

| Item | Status |
|---|---|
| Backend, twelve strategies (24 long/short bots), Assessor and Engineer, backtester, research, auditor, risk, ledgers, reconciliation, API, dashboard | Implemented; automated tests pass against a simulated terminal |
| Real MT5 demo connection | In use on the operator's Windows PC (reported by the operator; not checked from here). The code is still built and tested in a Linux container where the Windows-only MetaTrader5 package cannot run |
| Demo execution against a real terminal | Demo trades taken on the operator's PC (reported by the operator). Live accounts stay blocked in code |
| Windows launcher | Run on the operator's PC (reported by the operator) |
| Real-data backtests | Run on 1M+ M5 bars of MT5 history: every bot lost money under the old sizing. Not yet re-run with the small-trade sizing |
