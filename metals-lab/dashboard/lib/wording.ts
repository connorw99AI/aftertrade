/* Plain-English wording for the codes the auditor, risk engine and alerts use. One place, so every page says the same thing. */

const REASON: Record<string, string> = {
  // risk engine
  MAX_ENTRIES_PER_DAY: "daily trade limit reached",
  DAILY_LOSS_LIMIT: "daily loss stop reached",
  DAILY_PROFIT_TARGET_REACHED: "daily profit stop reached",
  MAX_DRAWDOWN_LIMIT: "drawdown pause reached",
  MAX_POSITIONS_PER_BOT: "too many trades already open",
  MIN_LOT_EXCEEDS_RISK_BUDGET: "minimum lot too big for the budget",
  PLANNED_RISK_EXCEEDS_LIMIT: "trade risks more than allowed",
  INSUFFICIENT_BOT_FREE_CAPITAL: "not enough free money",
  BOT_EQUITY_NON_POSITIVE: "bot has no money left",
  BOT_MARGIN_FRACTION_LIMIT: "bot would use too much margin",
  ACCOUNT_MARGIN_LIMIT: "account would use too much margin",
  ACCOUNT_MARGIN_LEVEL_LOW: "account margin level too low",
  MARGIN_CALC_UNAVAILABLE: "MT5 could not work out the margin",
  GROSS_METALS_EXPOSURE: "too much gold and silver open in total",
  CORRELATED_METALS_EXPOSURE: "too much open in the same direction on gold and silver",
  ORDER_IN_FLIGHT_FOR_SYMBOL: "another order on this metal is still being sent",
  BLOCKING_ALERTS: "an open alert is blocking new trades",
  RECONCILIATION_UNCERTAIN: "the lab's records don't match MT5 yet",
  GLOBAL_PAUSE: "all bots paused",
  KILL_SWITCH_ACTIVE: "kill switch on",
  ACCOUNT_NOT_HEDGING: "account is not a hedging account",
  ACCOUNT_CHANGED_SINCE_ACTIVATION: "a different MT5 account is connected",
  NO_PROTECTIVE_STOP: "trade has no stop-loss",
  STOP_ON_WRONG_SIDE: "stop-loss on the wrong side of the price",
  STOP_INSIDE_BROKER_STOPS_LEVEL: "stop-loss too close to the price for the broker",
  TARGET_INSIDE_BROKER_STOPS_LEVEL: "take-profit too close to the price for the broker",
  SYMBOL_TRADING_DISABLED: "broker has trading switched off for this metal",
  SYMBOL_LONG_NOT_ALLOWED: "broker allows no longs on this metal",
  SYMBOL_SHORT_NOT_ALLOWED: "broker allows no shorts on this metal",
  UNKNOWN_INSTRUMENT: "metal not found in MT5",
  CLOSE_TARGET_NOT_OWNED_BY_BOT: "trade to close belongs to another bot",
  CLOSE_VOLUME_EXCEEDS_POSITION: "close is bigger than the open trade",
  SPREAD_TOO_WIDE: "spread too wide",
  NO_QUOTE: "no price from MT5",
  // market auditor
  R01_SESSION_CLOSED: "market closed",
  R02_NO_QUOTE: "no price from MT5",
  R03_STALE_QUOTE: "price has not updated",
  R04_SPREAD_EXPANSION: "spread suddenly widened",
  R05_INSUFFICIENT_DATA: "not enough price history yet",
  R06_ATR_MOVE: "unusually large price move",
  R07_GAP: "price gapped",
  R08_RV_SPIKE: "volatility spike",
  R09_TICK_ACTIVITY: "unusual tick activity",
  R10_CORRELATED_SHOCK: "gold and silver both jumped",
  R11_REFERENCE_SHOCK: "shock in a related market",
  R12_NEWS_UNKNOWN: "news calendar unavailable",
  R13_NEWS_BLACKOUT: "major news due",
  R14_OPERATOR_ALERT: "your market alert is active",
  R15_TRADING_RESTRICTED: "broker restricts trading on this metal",
  R16_RAPID_REVERSAL: "price reversing sharply",
  R17_VOLUME_INVALID: "lot size not accepted by the broker",
  R18_BELOW_MIN_LOT: "lot size below the broker minimum",
  R19_NO_STOP: "trade has no stop-loss",
  R20_STOP_WRONG_SIDE: "stop-loss on the wrong side of the price",
  R21_STOP_INSIDE_BROKER_LEVEL: "stop-loss too close to the price for the broker",
  R22_STOP_ABNORMAL: "stop-loss unusually far away",
  R23_TARGET_WRONG_SIDE: "take-profit on the wrong side of the price",
  R24_TARGET_INSIDE_BROKER_LEVEL: "take-profit too close to the price for the broker",
};

/** Short plain-English label for an auditor rule or risk reason code. */
export function reasonText(code: string): string {
  return REASON[code] ?? code.replace(/^R\d+_/, "").replace(/_/g, " ").toLowerCase();
}

/** Plain-English name for one alert code, e.g. MISSING_CLOSE_1070. */
export function alertCodeText(code: string): string {
  const m = code.match(/^([A-Z_]+?)_(\d+)$/);
  const [kind, n] = m ? [m[1], m[2]] : [code, ""];
  const t: Record<string, string> = {
    MISSING_CLOSE: `trade #${n} disappeared from MT5 without a closing record`,
    UNKNOWN_POSITION: `MT5 has open trade #${n} that no bot owns`,
    UNKNOWN_DEAL: `MT5 recorded deal #${n} that no bot made`,
    UNTRACKED_POSITION: `trade #${n} carries a bot's number but the lab never sent it`,
    BALANCE_OP: `deposit or withdrawal #${n} needs acknowledging`,
    CLOSE_DELAYED: `closing trade #${n} was held back`,
    UNPROTECTED: `trade #${n} was closed because its stop-loss could not be set`,
    PROTECTION: `trade #${n} is missing its stop-loss or take-profit`,
  };
  return t[kind] ?? reasonText(code);
}

/** The detail behind a blocked trade, in words. Empty when the code already says it all. */
export function blockDetail(code: string, example: unknown): string {
  const ex = String(example ?? "");
  if (!ex || ex === code) return "";
  if (ex.startsWith("BLOCKING_ALERTS:")) {
    const codes = ex.slice("BLOCKING_ALERTS:".length).split(",").filter(Boolean);
    const shown = codes.slice(0, 2).map(alertCodeText).join("; ");
    return codes.length > 2 ? `${shown}; and ${codes.length - 2} more (see Activity)` : shown;
  }
  return ex.startsWith(code) ? ex.slice(code.length).replace(/^[:\s]+/, "") : ex;
}

/** Kind of alert, without its trade/deal number: MISSING_CLOSE_1070 -> MISSING_CLOSE. */
export const alertKind = (code: string) => code.replace(/_\d+$/, "");

/** Title for several alerts of the same kind. */
export function alertGroupTitle(kind: string, n: number): string {
  const t: Record<string, string> = {
    MISSING_CLOSE: `${n} trades disappeared from MT5 without a closing record`,
    UNKNOWN_POSITION: `MT5 has ${n} open trades that no bot owns`,
    UNKNOWN_DEAL: `MT5 recorded ${n} deals that no bot made`,
    UNTRACKED_POSITION: `${n} trades carry a bot's number but the lab never sent them`,
    BALANCE_OP: `${n} deposits or withdrawals need acknowledging`,
    CLOSE_DELAYED: `${n} closes were held back`,
    UNPROTECTED: `${n} trades were closed because their stop-loss could not be set`,
    PROTECTION: `${n} trades are missing their stop-loss or take-profit`,
  };
  return t[kind] ?? `${n} alerts: ${reasonText(kind)}`;
}

export const capitalise = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
