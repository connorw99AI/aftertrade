# Wiring the risk guard into a bot

Contents: install, API, inputs, switches, resetting a halt, demo test checklist, what the guard cannot do.

## Install

1. Copy `MetalLabRiskGuard.mqh` to `<MT5 data folder>/MQL5/Include/` (File > Open Data Folder).
2. Copy `ExampleEA.mq5` to `MQL5/Experts/` and compile in MetaEditor (F7). Fix any compile errors before anything else; the file was written without a compiler available.
3. Copy `DumpSymbolSpecs.mq5` and `ExportDeals.mq5` to `MQL5/Scripts/` and compile them too.
4. Attach to a **demo** account first.

## API

| call | use |
| --- | --- |
| `MLG_Defaults(cfg)` | Fill a `MLGuardConfig` with conservative placeholders, then override from inputs. |
| `MLG_Init(cfg, logFile, resetPeak)` | Once, in `OnInit`. Returns false if equity is unreadable. |
| `MLG_Check()` | Every tick and from `OnTimer` (5 s). Rolls the day/week, tracks the equity peak, trips halts, flattens. Returns true if trading is allowed. |
| `MLG_LotsForRisk(sym, type, entry, sl, riskPct, extraPoints)` | Lots so a stop-out costs `riskPct` of equity. Uses the terminal's own `OrderCalcProfit`, so tick value and currency conversion are the broker's. Rounds down, returns 0 if the minimum lot is too big. `extraPoints` budgets spread and slippage. |
| `MLG_Open(trade, sym, type, lots, sl, tp, comment)` | The only way a bot should open a trade. Runs every pre-trade rule, sends, logs. |
| `MLG_AllowEntry(...)` | The rules alone, if the EA must send orders itself. |
| `MLG_CloseAll(why)` | Close everything on the account and delete pending orders. |
| `MLG_Log(event, sym, detail)` | Append to the guard log. |

## Rules enforced on entry

Needs a stop loss; lot cap per trade and in total; open-position cap; entries per day; consecutive-loss cap and cooldown after a loss; spread limit; server-time session window and Friday cutoff; risk at the stop within the per-trade limit; today's loss plus this trade's risk within the daily limit; margin level after the trade above the floor.

## Rules enforced continuously (`MLG_Check`)

Daily loss from the day's starting equity (floating P/L counts), weekly loss, drawdown from peak equity. Daily and weekly halts end at the next server day or week. A drawdown halt lasts until a human resets it. When a limit trips and `close_on_breach` is true, everything on the account is flattened, including positions from bots that do not use the guard, and anything opened while halted is flattened too. Decide deliberately whether you want that; it also closes manual trades.

## Switches (Tools > Global Variables, F3)

Names are `MLG_<account login>_<NAME>`.

- `KILL` = 1: block new entries. Nothing is closed. Set to 0 to resume.
- `FLATTEN` = 1: close everything now. The guard resets it to 0.
- `HALT_UNTIL`: deleting it clears a halt.

## Resuming after a drawdown halt

This is meant to be a pause for a human decision, not an inconvenience to route around.

1. Work out why it happened (run `review.py`; read the guard log).
2. Delete `MLG_<login>_HALT_UNTIL` in the Global Variables window.
3. Restart the EA once with `InpResetPeak = true` so the peak resets to current equity, then set it back to false. Without this the guard re-trips immediately.

Deposits and withdrawals change the baselines the same way: restart with `InpResetPeak = true` after moving money.

## Server time

`session_*` and `friday_cutoff_hour` use broker **server** time, not GMT and not your local time, and some servers shift with daylight saving. Find your broker's offset (compare `TimeCurrent()` with `TimeGMT()` in a script) before setting hours.

## Demo test checklist (do all of it before live)

1. Compile cleanly; attach to a demo chart; check the Experts tab for `INIT` in the log.
2. Set `InpMaxDailyLossPct` tiny (0.05) with a position open: confirm HALT, flatten, and that new entries are rejected with a reason.
3. Set `InpMaxOpenPositions` to 1: confirm the second entry is rejected.
4. Set `InpMaxSpreadPoints` to 1: confirm entries are rejected for spread.
5. Set the Friday cutoff or session window to exclude now: confirm rejection.
6. Set `KILL`=1, confirm blocked entries; `FLATTEN`=1, confirm positions close.
7. Restart the terminal mid-day: confirm the day's starting equity and peak were remembered (log `INIT` line).
8. Open a trade and check the stop is on the **broker's server** (visible in the Trade tab) from the first moment. If your broker rejects stops on market orders, change the EA to open then `PositionModify`, and flatten if the stop cannot be set.
9. Run for a week; run `ExportDeals.mq5` and `review.py`; check the numbers match the terminal's own history.

## What the guard cannot do

- It runs inside the terminal. If the terminal or VPS is off, it does nothing; only stops held at the broker protect you.
- It cannot stop a gap or a bad fill. Loss at the stop is a plan, not a guarantee.
- It sees only what the terminal sees. Trades on other accounts or platforms are invisible.
- Prop firm accounts may measure daily loss differently (often from the day's higher of balance or equity, in a different time zone). Match their rule exactly if you trade one.
