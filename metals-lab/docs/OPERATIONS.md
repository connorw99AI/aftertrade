# Operations: Windows setup, data, research, manual demo activation

> **Status: not yet run on Windows.** Everything below was written for Windows and tested only on Linux against a simulated terminal. Expect to fix small issues on the first run. See [LIMITATIONS.md](LIMITATIONS.md).

## 1. Prerequisites (Windows 10/11, 64-bit)

- **MetaTrader 5** from your broker or metaquotes.net.
  - Open a **demo** account with File → Open an Account, choosing a demo server (for example MetaQuotes-Demo), and prefer a **hedging** account.
  - Stay logged in.
  - Turn on *Algo Trading* in the toolbar, and under Tools → Options → Expert Advisors allow algorithmic trading.
- **Python 3.10-3.13, 64-bit** (3.12 recommended) from python.org, with "Add to PATH" ticked. The official `MetaTrader5` package ships Windows wheels only. Check on PyPI that a wheel exists for your Python version.
- **Node.js 20+** (optional). A prebuilt dashboard ships in `dashboard/out`; you only need Node to rebuild it.

## 2. Guided setup (read-only)

```powershell
cd path\to\aftertrade\metals-lab
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
```

The script:

1. Finds Python.
2. Creates `.venv` and installs `backend/requirements-windows.txt`.
3. Creates `config/config.yaml` and `config/news_calendar.csv` from the examples.
4. Checks the dashboard build.
5. Checks that `terminal64.exe` is running.
6. Runs `metals-lab check`.

It never arms trading and never sends orders. Rerun the read-only check at any time with `scripts\windows\check.bat`.

The check prints:

- account type (it must say `DEMO`), server, hedging or netting, and a short account fingerprint (never the login);
- every gold and silver candidate, with the reason each was accepted or rejected. Equities and ETFs named "GOLD" are rejected, and CFDs, spot-style and futures contracts are told apart;
- full contract specifications for the chosen symbols;
- how server time is converted to UTC;
- session source;
- anything that would block execution.

If the broker exposes several acceptable gold or silver contracts, set them explicitly in `config.yaml` (`instruments.gold: "XAUUSD"`). The system will not pick one for you.

### Server clock

MT5 bar times use the trade server's wall clock.

- Best: set `mt5.server_timezone` to the IANA zone your broker uses (many use `Europe/Athens`-style EET/EEST). The system then verifies it against live ticks and blocks entries on a mismatch.
- `auto` measures the offset from a fresh tick and follows DST shifts live. Historical bars from before a DST change are then converted with the current offset; this is noted in the dataset provenance.

### Sessions

The Python API cannot read trading sessions. To get them:

1. Copy `mql5/ExportSessions.mq5` to `<MT5 data folder>\MQL5\Scripts`. Find the folder with File → Open Data Folder.
2. Compile it in MetaEditor.
3. Drag it onto any chart. It writes `metals_lab_sessions.json` to `%APPDATA%\MetaQuotes\Terminal\Common\Files`.
4. Point `mt5.sessions_export` at that file.

Rerun the export after broker schedule changes and holidays. You can also configure sessions and holiday closures explicitly in `config.yaml`.

### News calendar

Edit `config/news_calendar.csv` with verified FOMC, CPI, Employment Situation, GDP and PCE dates. Sources are listed in the file header. Then set `# as_of:` to the time you verified it.

Until then, news status is UNKNOWN, and with the default policy every *opening* needs manual review. You can choose `unknown_policy: allow_with_flag` instead; that choice is written into every audit record.

## 3. Start the lab

```
scripts\windows\start.bat
```

This starts the API and live loop on `http://127.0.0.1:8765` and opens the dashboard.

- Until you arm, the loop runs in **shadow mode**: signals, auditor and risk decisions are recorded, and no orders are sent.
- Start, pause and stop bots from the dashboard or with `metals-lab bot S04 start`. Only RUNNING bots trade once armed.

## 4. Manual demo activation (the only way to enable trading)

```
.venv\Scripts\metals-lab arm --operator "Your Name"
```

1. The command re-verifies DEMO, hedging, Algo Trading, instruments and clock.
2. It shows the starting balance snapshot and the per-bot allocation (10% each).
3. It asks you to type `ARM DEMO <6-char fingerprint>`.

On the first activation for an account, the balance is snapshotted once and the allocations are created. Re-arming later never resets them.

- Switching the terminal to another account disarms the system and revokes approvals.
- Disarm: `metals-lab disarm`, or the Risk centre.
- Kill everything: the Risk centre kill switch, or `metals-lab kill --reason "..."`.
- Allocation changes (audited, funded only from unallocated money): `metals-lab allocate --bot S03 --delta -200 --reason "..." --operator "..."`.

## 5. Historical data and research

```
metals-lab fetch-history --days 730 --tf M5 M15 H1 H4 D1        # Parquet cache + manifest + quality report
metals-lab fetch-history --days 30 --tf M1 --ticks              # bars built from ticks (real spreads); history depth varies by broker
metals-lab backtest --strategy S08 --capital 1000
metals-lab research --strategy S08 --trials 20                  # train/validation/test, walk-forward, sensitivity, cost stress, DSR
metals-lab research --strategy S08 --holdout                    # touches the final holdout ONCE per strategy+dataset
```

- `--synthetic` runs the same commands on clearly labelled synthetic data. It only checks the machinery.
- Reports are written to `reports/`. Every trial, including failures, is stored in the `experiments` table and shown on the Research page.
- How much history you get is limited by the terminal's "Max bars in chart" setting and by the broker.

## 6. Moving to a server later

The MetaTrader5 package needs a Windows host with a logged-in terminal, so use a Windows VPS or server.

- Copy `metals-lab`, run the setup, and keep `data/state.sqlite`. It holds activation, ledgers and history.
- A new account fingerprint means new allocations.
- If you must expose the API beyond localhost, set `METALS_LAB_API_TOKEN`, put HTTPS in front with a reverse proxy, and set `api.host`. The server refuses non-local binding without a token.

## 7. Database

SQLite schema: `backend/metals_lab/store/schema.sql`.

| Area | Tables |
|---|---|
| Activation and capital | activation, capital_snapshot, unallocated_events |
| Per-bot accounting | ledger (allocations, P&L, commission, swap, fees, unique per MT5 deal), bots, config_audit |
| Execution | intents (durable, before send), approvals (single use), positions, deals (MT5 mirror), trades (closed groups, provisional or final) |
| Decisions | audit_decisions, risk_decisions, signals_log |
| Research | experiments, holdout_usage, backtests |
| UI and safety | events (popups/alerts feed), alerts, control |

Historical datasets live in `data/cache/<source>/<symbol>/<tf>/<version>.parquet`, each with a JSON manifest holding provenance, a SHA-256 hash and a quality report.
