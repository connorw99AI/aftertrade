# Operations: Windows setup, data, research, manual demo activation

> **Status:** in use on your Windows PC with an MT5 demo account. Automated tests run only on Linux against a simulated terminal, so check anything new on your PC first. See [LIMITATIONS.md](LIMITATIONS.md).

## 1. Prerequisites (Windows 10/11, 64-bit)

- **MetaTrader 5** from your broker or metaquotes.net.
  - Open a **demo** account with File → Open an Account, choosing a demo server (for example MetaQuotes-Demo), and prefer a **hedging** account.
  - Stay logged in.
  - Turn on *Algo Trading* in the toolbar, and under Tools → Options → Expert Advisors allow algorithmic trading.
- **Python 3.10-3.14, 64-bit** (3.12 recommended) from python.org, with "Add to PATH" ticked. The official `MetaTrader5` package ships Windows wheels only. Check on PyPI that a wheel exists for your Python version.
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

Double-click the **Metal Lab** icon on your desktop (setup creates it; to create it again run
`powershell -ExecutionPolicy Bypass -File scripts\windows\install-shortcut.ps1`). The icon runs `scripts\windows\launch.ps1`, which:

1. fetches the latest version with `git pull` (if that fails, for example offline or with local edits to tracked files, it says so and starts the version you have);
2. reinstalls the Python package only when its dependency files changed;
3. starts the lab and opens `http://127.0.0.1:8765` once it answers. If the lab is already running it only opens the dashboard.

Close the Metal Lab window to stop the lab. The icon never arms trading: the lab comes back in the state you left it (shadow mode unless you armed it). `scripts\windows\start.bat` still works if you prefer it (no update step).

- Until you arm, the loop runs in **shadow mode**: signals, auditor and risk decisions are recorded, and no orders are sent.
- Start, pause and stop bots from the dashboard or with `metals-lab bot S04L start`. Only RUNNING bots trade once armed.
- The Lab page has **Start all**, **Pause all** and **Kill all**. Each card shows ▶ running, ⏸ paused, ■ stopped, or a skull while the kill switch is on. Every action that changes trading opens a dialog that says what will happen (for example "Closes 3 open trades on 2 bots at market now"), asks for your name for the audit log (remembered in this browser), and for Kill all asks you to type KILL. Kill all closes every bot trade, disarms and blocks new trades until `metals-lab clear-kill`.
- Keys: `/` jumps to any bot by name; on a focused bot card or a bot page, `S` starts and `P` pauses (both through the same dialog).
- Every amount is shown in the account currency (£ on a GBP account) and dates are day first.

### Dashboard pages

Six pages, each named for a job. Old addresses (/positions, /history, /compare, /activity, /backtest, /risk, /auditor, /settings) redirect to their new place, so bookmarks keep working.

| Page | What it is for |
|---|---|
| Lab | Every bot as a creature card: state symbol, bull or bear plaque, today's P&L on the art (open P&L instead while it is in a trade, with the side, lot and entry price underneath), a one-line thought written from its live checklist ("1 of 3 conditions for a gold long; missing: …", "Off duty on gold until 13:00 UK time."), a readiness meter per metal, equity, today and trades, one Start/Pause button and a ⋯ menu (open, its trades, stop, retire). Zoom changes cards per row; phones get a compact two-column roster. Bots in a trade sort to the top automatically, biggest open P&L first. **Action now** at the top lists every bot about to enter (inside its window, all filters passing, at most one entry condition missing, or an entry signal on the latest bar). "On duty now" opens a 24-hour UK-time strip per bot showing when it can trade today. Retired bots collapse to one line. |
| Bot page | Who it is, its trading window, rules version (v1, v2… each parameter save is a new version) and Start/Pause/Stop/Retire. Live tab: equity with a sparkline, today, return, trades today; a decision pipeline per metal (Session → Filters → Setup → Sizing → Auditor → Risk → Order) showing where today's entry stopped, with the full checklist one click away; the last 7 days of signals; the last decision as a sentence; open trades with a stop-to-target bar; the equity curve with rule changes marked. Tabs for Trades, Review and Rules & settings. |
| Live | Gold and silver charts from the live price stream, each open trade's lines ending in its creature; the on-duty strip; every bot's verdict and readiness. Bots decide in the backend on completed candles; the page only displays. |
| Trades | Open: every trade with live P&L, a bar from stop to target with entry and the current price marked, and whether MT5 still holds the stop and target; close one, one bot's, or all. Closed: a daily P&L calendar for the last eight weeks (click a day to filter), filters, every closed trade with its rules version and reasons, CSV download. |
| Assessor | Grades (a progress bar until there are enough trades to judge), Leaderboard (one small equity chart per bot on the same scale with the live-candidate checks, and the ranking table), Suggestions to test and apply, and Blocked trades (why trades did or did not happen, what the auditor and risk engine blocked, stops and targets put back). |
| Research | Download MT5 history (with a coverage timeline of what you have, and a list of every download with Delete and Delete all; live trading never reads these files), run one bot or all 22 over a date range with your live risk settings, results by bot name, the newest run opened automatically, and backtest results next to demo results. |
| System | Risk controls and every limit written as a sentence, account and capital, the audit log grouped ("Initial allocation, all 22 bots, £227.27 each" on a £5,000 demo), the market auditor, the MT5 connection, the full decision Log filterable by bot, and research Experiments. |

The bell at the top right holds open alerts on every page; resolve them from its drawer.

## 4. Manual demo activation (the only way to enable trading)

Press **Arm demo trading** on the Lab page (shown while not armed) or on the System page, in the browser on the PC
running Metal Lab. Requests from any other device are refused. Or, in a terminal:

```
.venv\Scripts\metals-lab arm --operator "Your Name"
```

Both do the same, and the dashboard refuses while the kill switch is on (clear it with `metals-lab clear-kill`).
**Disarm** on the System page stops all new automatic trading at any time.

1. Arming re-verifies DEMO, hedging, Algo Trading, instruments and clock.
2. It shows the starting balance snapshot and the per-bot allocation (1/22 each, £227.27 on a £5,000 demo; a lab set up with 20 bots is re-split to 1/22 once by the launcher, keeping its trades).
3. It asks you to type `ARM DEMO <6-char fingerprint>` (shown in the dialog), and logs your name.

On the first activation for an account, the balance is snapshotted once and the allocations are created. Re-arming later never resets them.

- Switching the terminal to another account disarms the system and revokes approvals.
- Disarm: `metals-lab disarm`, or System → Risk & safety.
- Kill everything: Kill all on the Lab page, System → Risk & safety, or `metals-lab kill --reason "..."`.
- Allocation changes (audited, funded only from unallocated money): `metals-lab allocate --bot S03L --delta -200 --reason "..." --operator "..."`.

## 5. Historical data and research

The Research page does the common steps from the dashboard: **Download history from MT5** for a From/To date range (M5, M15, H1 for both metals; set MT5's Tools → Options → Charts → Max bars in chart to Unlimited and restart MT5 first, or downloads stop at about 100,000 bars), **Run all 22 bots** over a date range, **Clear old results**. Backtests run in separate processes (two at a time), so the dashboard and the trading loop stay responsive while they run. The command line does the same and more:

```
metals-lab fetch-history --days 730 --tf M5 M15 H1        # Parquet cache + manifest + quality report
metals-lab fetch-history --days 30 --tf M5 --ticks              # bars built from ticks (real spreads); history depth varies by broker
metals-lab backtest --strategy S08 --capital 1000
metals-lab research --strategy S08 --trials 20                  # train/validation/test, walk-forward, sensitivity, cost stress, DSR
metals-lab research --strategy S08 --holdout                    # touches the final holdout ONCE per strategy+dataset
```

- Downloads for different ranges are kept and merged per timeframe, so you can add an earlier year without losing the current one. Use this for out-of-sample checks: the Assessor's filters were chosen on Oct 2025 → Oct 2026, so test them on Oct 2024 → Oct 2025.
- `metals-lab export-backtests --out reports/backtests.md` writes the latest result per bot.
- When a backtest hits the 50% drawdown pause, it restarts the bot the next day with a fresh peak, as if you pressed Start, and lists each restart above the run's trades. Live, pressing Start on a drawdown-paused bot also measures the drawdown from that moment.
- Every run's trades (entry, exit, stop, target, profit, R, and why it entered and exited) are on the Research page and on each bot's Trades tab under Backtest trades.
- `--synthetic` runs the same commands on clearly labelled synthetic data. It only checks the machinery.
- Reports are written to `reports/`. Every trial, including failures, is stored in the `experiments` table and shown on the Research page.
- How much history you get is limited by the terminal's "Max bars in chart" setting and by the broker.

## 6. Upgrading to the long/short split (one-off)

Old databases have ten bots with the old magic numbers. The new loop does not recognise those, so:

1. Close every open trade first (the command refuses while any are open).
2. Stop the lab (Ctrl+C), then `git pull`.
3. In `config\config.yaml` set `capital: bot_fraction: 0.05`.
4. `.venv\Scripts\metals-lab new-lab --confirm "NEW LAB"`. This moves the old database to `data\archive\` (nothing is deleted) and starts a fresh one with 22 bots.
5. Start Metal Lab, then **Arm demo trading** on the Lab page (or `metals-lab arm --operator "Your Name"`). The balance is snapshotted again and allocations are created.
6. Start the halves you want from the Lab page.

## 7. Moving to a server later

The MetaTrader5 package needs a Windows host with a logged-in terminal, so use a Windows VPS or server.

- Copy `metals-lab`, run the setup, and keep `data/state.sqlite`. It holds activation, ledgers and history.
- A new account fingerprint means new allocations.
- If you must expose the API beyond localhost, set `METALS_LAB_API_TOKEN`, put HTTPS in front with a reverse proxy, and set `api.host`. The server refuses non-local binding without a token.

## 8. Database

SQLite schema: `backend/metals_lab/store/schema.sql`.

| Area | Tables |
|---|---|
| Activation and capital | activation, capital_snapshot, unallocated_events |
| Per-bot accounting | ledger (allocations, P&L, commission, swap, fees, unique per MT5 deal), bots, config_audit |
| Execution | intents (durable, before send), approvals (single use), positions, deals (MT5 mirror), trades (closed groups, provisional or final) |
| Decisions | audit_decisions, risk_decisions, signals_log |
| Research | experiments, holdout_usage, backtests, reviews (Assessor) |
| UI and safety | events (popups/alerts feed), alerts, control |

Historical datasets live in `data/cache/<source>/<symbol>/<tf>/<version>.parquet`, each with a JSON manifest holding provenance, a SHA-256 hash and a quality report.
