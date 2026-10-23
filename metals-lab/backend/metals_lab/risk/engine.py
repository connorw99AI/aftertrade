"""Central risk engine.

Limits are expressed against each bot's OWN virtual equity, plus aggregate
account-level limits. Planned risk is the loss at the protective stop computed
with the terminal's documented ``order_calc_profit`` (account currency), plus
estimated spread/commission. These are planning numbers, not guarantees:
gaps and slippage can make realised losses larger.

Opening proposals must carry a valid AUDIT approval; the risk engine verifies it
before evaluating and issues its own RISK approval. Risk-reducing proposals
are approved unless ownership/existence checks fail, even when the bot or the
whole system is paused, so positions can always be managed down.
"""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..domain import Decision, Intent
from ..execution.approvals import ApprovalAuthority, ApprovalError, Proposal
from ..instruments import InstrumentSpec
from ..ledger import BotBook
from ..mt5.guard import AccountCheck
from ..store import Store


def risk_pct_for(cfg: dict, bot_id: str) -> float:
    """Planned risk per trade for one bot: its own override if set, else the default, never above the hard cap."""
    default = float(cfg["risk"]["risk_per_trade"])
    override = (cfg.get("strategies", {}).get(bot_id) or {}).get("risk_per_trade")
    pct = float(override) if override is not None else default
    return max(0.0, min(pct, float(cfg["risk"].get("max_risk_per_trade_cap", 0.03))))


@dataclass
class RiskContext:
    account: AccountCheck
    activation_fp: Optional[str]
    armed: bool
    killed: bool
    global_paused: bool
    bot_status: str
    book: BotBook
    day_start_equity: float
    peak_equity: float
    bot_open_positions: int
    account_equity: float
    account_margin: float
    account_margin_level: float
    gross_exposure_notional: float           # all bot-owned positions, account currency
    same_direction_notional: dict = field(default_factory=dict)   # {"LONG": x, "SHORT": y}
    reconciliation_clean: bool = True
    blocking_alerts: list = field(default_factory=list)
    in_flight_symbols: set = field(default_factory=set)
    tick: Optional[dict] = None
    spec: Optional[InstrumentSpec] = None
    owned_position: Optional[dict] = None
    entries_today: int = 0                   # trades this bot opened (or shadow-approved) this UTC day


@dataclass
class RiskDecision:
    decision: Decision
    reasons: list[str]
    metrics: dict
    approval: object = None

    def public(self) -> dict:
        return {"decision": self.decision.value, "reasons": self.reasons, "metrics": self.metrics}


class RiskEngine:
    def __init__(self, cfg: dict, store: Store, audit_authority: ApprovalAuthority, authority: ApprovalAuthority, gw_ro):
        self.cfg = cfg["risk"]
        self.root_cfg = cfg
        self.db = store
        self.audit_auth = audit_authority
        self.auth = authority
        self.gw = gw_ro

    # -------------------------------------------------------------- sizing help
    def loss_at_stop(self, spec: InstrumentSpec, side: str, entry: float, stop: float, volume: float) -> float:
        otype = self.gw.const("ORDER_TYPE_BUY") if side == "LONG" else self.gw.const("ORDER_TYPE_SELL")
        v = self.gw.order_calc_profit(otype, spec.symbol, volume, entry, stop)
        if v is None:
            raise RuntimeError(f"order_calc_profit unavailable for {spec.symbol}")
        return abs(v)

    def est_cost_per_lot(self, spec: InstrumentSpec, tick: Optional[dict], commission_per_lot_side: float = 0.0) -> float:
        if not tick:
            return 0.0
        spread = tick["ask"] - tick["bid"]
        otype = self.gw.const("ORDER_TYPE_BUY")
        v = self.gw.order_calc_profit(otype, spec.symbol, 1.0, tick["ask"], tick["ask"] - spread) or 0.0
        return abs(v) + 2 * commission_per_lot_side

    # ---------------------------------------------------------------- evaluate
    def evaluate(self, p: Proposal, audit_approval, ctx: RiskContext, now: Optional[datetime] = None) -> RiskDecision:
        now = now or _tnow()
        reasons: list[str] = []
        m: dict = {}
        fp = ctx.account.fingerprint or ""

        def reject(why):
            reasons.append(why)

        # The auditor gate is mandatory for every proposal, opening or closing.
        try:
            self.audit_auth.verify(audit_approval, p.proposal_hash, fp, now)
        except ApprovalError as e:
            return self._done(p, Decision.REJECTED, [f"AUDIT_GATE: {e}"], m, fp)

        if not ctx.account.is_demo:
            reject(f"ACCOUNT_NOT_DEMO:{ctx.account.state.value}")
        if ctx.activation_fp and ctx.account.fingerprint != ctx.activation_fp:
            reject("ACCOUNT_CHANGED_SINCE_ACTIVATION")

        if p.intent.is_risk_reducing:
            if ctx.owned_position is None:
                reject("CLOSE_TARGET_NOT_OWNED_BY_BOT")
            elif p.volume > float(ctx.owned_position.get("volume", 0)) + 1e-9:
                reject("CLOSE_VOLUME_EXCEEDS_POSITION")
            if ctx.spec is not None and not ctx.spec.can_close:
                reject("SYMBOL_TRADING_DISABLED")
            return self._done(p, Decision.REJECTED if reasons else Decision.APPROVED, reasons, m, fp)

        # ---- opening checks --------------------------------------------------
        if ctx.killed:
            reject("KILL_SWITCH_ACTIVE")
        if ctx.global_paused:
            reject("GLOBAL_PAUSE")
        if ctx.bot_status != "RUNNING":
            reject(f"BOT_STATUS_{ctx.bot_status}")
        if not ctx.account.hedging:
            reject("ACCOUNT_NOT_HEDGING")
        if not ctx.reconciliation_clean:
            reject("RECONCILIATION_UNCERTAIN")
        if ctx.blocking_alerts:
            reject("BLOCKING_ALERTS:" + ",".join(a["code"] for a in ctx.blocking_alerts))
        if p.symbol in ctx.in_flight_symbols:
            reject("ORDER_IN_FLIGHT_FOR_SYMBOL")
        spec = ctx.spec
        if spec is None:
            return self._done(p, Decision.REJECTED, reasons + ["UNKNOWN_INSTRUMENT"], m, fp)
        if p.intent == Intent.OPEN_LONG and not spec.can_open_long:
            reject("SYMBOL_LONG_NOT_ALLOWED")
        if p.intent == Intent.OPEN_SHORT and not spec.can_open_short:
            reject("SYMBOL_SHORT_NOT_ALLOWED")

        eq = ctx.book.equity
        m["bot_equity"] = round(eq, 2)
        m["bot_free_capital"] = round(ctx.book.free_capital, 2)
        if eq <= 0:
            reject("BOT_EQUITY_NON_POSITIVE")
        daily = (eq - ctx.day_start_equity) / ctx.day_start_equity if ctx.day_start_equity else 0.0
        dd = (eq - ctx.peak_equity) / ctx.peak_equity if ctx.peak_equity else 0.0
        m["daily_pnl_pct"], m["drawdown_pct"] = round(daily, 5), round(dd, 5)
        if daily <= -self.cfg["daily_loss_pause"]:
            reject("DAILY_LOSS_LIMIT")
        if dd <= -self.cfg["max_drawdown_pause"]:
            reject("MAX_DRAWDOWN_LIMIT")
        if ctx.bot_open_positions >= self.cfg["max_positions_per_bot"]:
            reject("MAX_POSITIONS_PER_BOT")
        cap = int(self.cfg.get("max_entries_per_bot_per_day", 0) or 0)
        m["entries_today"] = ctx.entries_today
        if cap and ctx.entries_today >= cap:
            reject("MAX_ENTRIES_PER_DAY")

        # volume / lot step
        vol = spec.round_volume_down(p.volume)
        if vol != p.volume or vol <= 0:
            reject(f"VOLUME_NOT_ON_STEP_OR_BELOW_MIN ({p.volume} -> {vol})")

        tick = ctx.tick
        if not tick:
            reject("NO_QUOTE")
        else:
            spr_pts = (tick["ask"] - tick["bid"]) / spec.point
            m["spread_points"] = round(spr_pts, 1)
            if spr_pts > self.cfg["max_spread_points"].get(p.role, 1e9):
                reject("SPREAD_TOO_WIDE")
            entry = tick["ask"] if p.intent == Intent.OPEN_LONG else tick["bid"]
            m["entry_ref"] = entry
            # protective stop validation
            if p.sl is None:
                if self.cfg["require_stop"]:
                    reject("NO_PROTECTIVE_STOP")
            else:
                min_d = (spec.stops_level_points + self.cfg["stop_buffer_points"]) * spec.point
                freeze = spec.freeze_level_points * spec.point
                dist = (entry - p.sl) if p.intent == Intent.OPEN_LONG else (p.sl - entry)
                m["stop_distance"] = round(dist, 6)
                if dist <= 0:
                    reject("STOP_ON_WRONG_SIDE")
                elif dist < max(min_d, freeze):
                    reject("STOP_INSIDE_BROKER_STOPS_LEVEL")
                if p.tp is not None:
                    tdist = (p.tp - entry) if p.intent == Intent.OPEN_LONG else (entry - p.tp)
                    if tdist < max(min_d, freeze):
                        reject("TARGET_INSIDE_BROKER_STOPS_LEVEL")
                if dist > 0 and vol > 0:
                    loss = self.loss_at_stop(spec, p.intent.position_side, entry, p.sl, vol)
                    cost = self.est_cost_per_lot(spec, tick) * vol
                    planned = loss + cost
                    m["planned_risk"] = round(planned, 2)
                    limit = eq * risk_pct_for(self.root_cfg, p.bot_id)
                    m["risk_limit"] = round(limit, 2)
                    m["planned_risk_pct_of_bot"] = round(planned / eq, 5) if eq > 0 else None
                    if self.cfg.get("per_trade_limit_enabled", True) and planned > limit * 1.0001:
                        reject("PLANNED_RISK_EXCEEDS_LIMIT")
            # margin reservation against the bot's own capital
            if vol > 0:
                otype = self.gw.const("ORDER_TYPE_BUY") if p.intent == Intent.OPEN_LONG else self.gw.const("ORDER_TYPE_SELL")
                margin = self.gw.order_calc_margin(otype, spec.symbol, vol, entry)
                if margin is None:
                    reject("MARGIN_CALC_UNAVAILABLE")
                else:
                    m["margin_required"] = round(margin, 2)
                    p.est_margin = margin
                    if margin > ctx.book.free_capital:
                        reject("INSUFFICIENT_BOT_FREE_CAPITAL")
                    if ctx.book.reserved_margin + ctx.book.pending_reserved + margin > eq * self.cfg["max_bot_margin_fraction"]:
                        reject("BOT_MARGIN_FRACTION_LIMIT")
                    if ctx.account_equity > 0:
                        if (ctx.account_margin + margin) / ctx.account_equity > self.cfg["max_account_margin_used"]:
                            reject("ACCOUNT_MARGIN_LIMIT")
                        if ctx.account_margin > 0 and ctx.account_margin_level < self.cfg["min_account_margin_level"]:
                            reject("ACCOUNT_MARGIN_LEVEL_LOW")
                        notional = vol * spec.contract_size * entry  # profit currency; USD account assumed for the ratio
                        gross = (ctx.gross_exposure_notional + notional) / ctx.account_equity
                        m["gross_exposure_after"] = round(gross, 3)
                        if gross > self.cfg["max_metals_gross_exposure"]:
                            reject("ACCOUNT_GROSS_METALS_EXPOSURE")
                        side = p.intent.position_side
                        same = (ctx.same_direction_notional.get(side, 0.0) + notional) / ctx.account_equity
                        m["same_direction_exposure_after"] = round(same, 3)
                        if same > self.cfg["max_same_direction_exposure"]:
                            reject("CORRELATED_METALS_EXPOSURE")
        return self._done(p, Decision.REJECTED if reasons else Decision.APPROVED, reasons, m, fp)

    def _done(self, p: Proposal, d: Decision, reasons: list[str], m: dict, fp: str) -> RiskDecision:
        rd = RiskDecision(d, reasons, m)
        if d == Decision.APPROVED:
            rd.approval = self.auth.issue(p.proposal_hash, "APPROVED", fp)
        return rd
