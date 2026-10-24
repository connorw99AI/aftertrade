---
name: metal-lab-mt5
description: Risk controls, lot sizing and performance review for unattended MetaTrader 5 trading bots on metals (XAUUSD gold, XAGUSD silver and similar). Use whenever the user works on Metal Lab, MT5 expert advisors or EAs, MQL5 code, bot lot sizing, daily-loss or drawdown limits, kill switches, the assessor bot, reviewing bot or deal history, strategy-tester validation, or putting bots live or on a VPS, even if they don't say "risk". Provides an MQL5 risk guard that enforces limits inside the EA, a lot sizer, a config preflight and an assessor review of exported deals. Claude never places or modifies live orders.
---

# Metal Lab MT5

Tooling for running leveraged metals bots without anyone watching. The central idea: **limits that protect the account must run inside MT5, not in a chat.** A bot trading at 3am cannot ask Claude for permission, and a rule that lives in a prompt is a suggestion. Claude's job here is to help the user design, size, review and validate. It does not trade.

## What Claude must not do

- Place, modify or close live orders, or change a live bot's settings. There is no connector for this and none should be built into this workflow.
- Promise or imply profitability. Most retail leveraged traders lose money; a clean backtest is not evidence of an edge.
- Help loosen limits right after a drawdown or a halt. If the user asks, say plainly that this is the moment limits matter most, and ask what changed in the bot rather than in the market.
- Help add martingale, grid or averaging-down without a hard cap enforced by the guard. These turn many small wins into one account-ending loss.
- Present untested MQL5 as working. The MQL5 here was written without MetaEditor. Tell the user to compile it and test on a demo account first, every time.

## The pieces

| piece | what it does |
| --- | --- |
| `assets/mql5/MetalLabRiskGuard.mqh` | Include file. Account-wide daily/weekly loss, peak drawdown, position and lot caps, spread, session, Friday cutoff, margin floor, loss-streak cooldown, kill and flatten switches. Funnels every entry through `MLG_Open()`. Persists state across restarts. Logs to CSV. |
| `assets/mql5/ExampleEA.mq5` | Skeleton showing the wiring. No strategy. |
| `assets/mql5/DumpSymbolSpecs.mq5` | Script: exports the broker's real contract specs to JSON. |
| `assets/mql5/ExportDeals.mq5` | Script: exports deal history to CSV for review. |
| `scripts/size.py` | Lot size so a stop-out costs a set % of equity. Rounds down, refuses if the minimum lot is too big. |
| `scripts/riskcheck.py` | Preflight on the limits config: contradictions, worst cases, warnings. Can emit an MT5 `.set` preset. |
| `scripts/review.py` | Assessor: per-bot stats, confidence interval on expectancy, KEEP / WATCH / PAUSE flags, guard-log summary. |
| `assets/metal_lab.example.json`, `assets/specs.example.json` | Illustrative config and specs. The numbers are placeholders, not advice. |

## Workflow

Pick the stage the user is at; they rarely need all of it at once.

1. **Set limits.** Ask what account size and risk tolerance they actually have, then fill `metal_lab.json` and run `python3 scripts/riskcheck.py --config metal_lab.json`. Read the warnings back to the user in plain words. Challenge limits that look high, and explain the arithmetic (for example, N positions of X% risk is N×X% if they stop out together, and gold and silver move together).
2. **Wire the guard into a bot.** Read `references/guard-integration.md`. Every entry goes through `MLG_Open()`; `MLG_Check()` runs on every tick and on a timer; sizing uses `MLG_LotsForRisk()`. If the user pastes an existing EA, find every place it calls `trade.Buy`, `trade.Sell`, `OrderSend` or `PositionOpen` and route them all through the guard. Missing one defeats the point.
3. **Size trades offline** when the user wants to sanity-check the EA's lots: dump specs with `DumpSymbolSpecs.mq5`, then `scripts/size.py`.
4. **Demo test the guard** before live (checklist in `references/guard-integration.md`): force each limit to trip with tiny values and confirm the halt, the flatten, the restart persistence and the log.
5. **Validate the strategy** before it earns real money. Read `references/validation.md`. Be blunt about overfitting, costs and sample size.
6. **Review running bots** (weekly is a sensible habit). User runs `ExportDeals.mq5`, then `scripts/review.py --deals ... --config ... --balance ... --guard-log ...`. Report each bot's verdict and the reasons, lead with PAUSE, and treat KEEP as "no flags", never as an endorsement. Read `references/architecture.md` for what to check around the bots (VPS, server time, spreads, weekend gaps).

## How to talk to the user about this

The user has asked for direct, unflattering assessments. If a design choice is dangerous (no stop on an order, no Friday cutoff, correlated positions stacked, risk per trade above what the daily limit can absorb), say so first, then help them do it as safely as possible. Raise a serious concern once; if they decide to proceed, help them execute it well. Separate facts (what the code does), informed opinion (what is prudent) and speculation (what the market might do).

## Honest limits of this skill

The guard can only stop what goes through it and what the terminal can see. It cannot protect against a terminal that is switched off or disconnected (only a server-side stop on every order does that), a broker that fills stops badly in a gap, or a bot whose strategy simply has no edge. Say so when relevant.
