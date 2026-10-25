# Metal Lab

A demo-only MetaTrader 5 laboratory for gold and silver. 22 independently configurable bots (11 strategies, each run as a long half and a short half), each a pixel creature with its own virtual portfolio, share one MT5 **demo** account. Every trade goes strategy → independent market auditor → central risk engine → demo executor. Live accounts are technically blocked.

This makes no claim of profitability. No real-market backtests have been run yet, so there is no evidence either way.

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
4. When you are ready, arm manually: `.venv\Scripts\metals-lab arm --operator "Your Name"`.

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
| `metals-lab arm --operator NAME` | The only way to enable demo execution (typed confirmation) |
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
| Backend, eleven strategies (22 long/short bots), Assessor and Engineer, backtester, research, auditor, risk, ledgers, reconciliation, API, dashboard | Implemented; automated tests pass against a simulated terminal |
| Real MT5 demo connection | **Not verified.** Built in a Linux container where the Windows-only MetaTrader5 package cannot run |
| Demo execution against a real terminal | **Not operational until verified on your PC** |
| Demo orders submitted | **None** |
| Windows launcher | Written, **not run on Windows** |
| Real-data backtests | None yet (synthetic only, labelled) |
