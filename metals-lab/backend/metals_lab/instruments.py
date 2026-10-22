"""Gold/silver instrument discovery and contract specifications.

Nothing is assumed about symbol names. Every symbol the terminal exposes is
classified from its own properties (calculation mode, base currency, path,
description). Equities/ETFs that merely have "gold" in their name (e.g. a
miner trading as ``GOLD``) are rejected. If more than one acceptable contract
exists for a metal and the operator has not chosen one explicitly, the metal is
left unresolved - we never silently substitute one contract for another.
"""
from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from typing import Optional

from .mt5 import constants as C
from .mt5.gateway import ReadOnlyGateway

ROLES = ("GOLD", "SILVER")

_EQUITY_HINTS = re.compile(r"\b(corp|inc|plc|ltd|mining|miners|etf|trust|shares|resources|royalt)", re.I)
_EQUITY_PATH = re.compile(r"(stock|share|equit|etf|fund)", re.I)

CALC_MODE_KIND = {
    C.SYMBOL_CALC_MODE_FOREX: "SPOT_OTC_FOREX_STYLE",
    C.SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE: "SPOT_OTC_FOREX_STYLE",
    C.SYMBOL_CALC_MODE_CFD: "CFD",
    C.SYMBOL_CALC_MODE_CFDLEVERAGE: "CFD",
    C.SYMBOL_CALC_MODE_CFDINDEX: "CFD_INDEX",
    C.SYMBOL_CALC_MODE_FUTURES: "FUTURES",
    C.SYMBOL_CALC_MODE_EXCH_FUTURES: "EXCHANGE_FUTURES",
    C.SYMBOL_CALC_MODE_EXCH_FUTURES_FORTS: "EXCHANGE_FUTURES",
    C.SYMBOL_CALC_MODE_EXCH_STOCKS: "EXCHANGE_STOCK",
    C.SYMBOL_CALC_MODE_EXCH_STOCKS_MOEX: "EXCHANGE_STOCK",
    C.SYMBOL_CALC_MODE_EXCH_BONDS: "EXCHANGE_BOND",
    C.SYMBOL_CALC_MODE_EXCH_BONDS_MOEX: "EXCHANGE_BOND",
    C.SYMBOL_CALC_MODE_SERV_COLLATERAL: "COLLATERAL",
}
ACCEPTED_KINDS = {"SPOT_OTC_FOREX_STYLE", "CFD", "FUTURES", "EXCHANGE_FUTURES"}

TRADE_MODE_NAME = {0: "DISABLED", 1: "LONG_ONLY", 2: "SHORT_ONLY", 3: "CLOSE_ONLY", 4: "FULL"}


@dataclass
class InstrumentSpec:
    role: str
    symbol: str
    description: str = ""
    path: str = ""
    contract_kind: str = "CFD"
    calc_mode: int = C.SYMBOL_CALC_MODE_CFD
    contract_size: float = 100.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    tick_size: float = 0.01
    tick_value: float = 1.0
    point: float = 0.01
    digits: int = 2
    currency_base: str = ""
    currency_profit: str = "USD"
    currency_margin: str = "USD"
    trade_mode: int = C.SYMBOL_TRADE_MODE_FULL
    filling_mode: int = C.SYMBOL_FILLING_FOK | C.SYMBOL_FILLING_IOC
    stops_level_points: int = 0
    freeze_level_points: int = 0
    swap_mode: int = C.SYMBOL_SWAP_MODE_POINTS
    swap_long: float = 0.0
    swap_short: float = 0.0
    swap_rollover3days: int = 3
    margin_initial: float = 0.0
    expiration_time: int = 0
    bid: Optional[float] = None
    ask: Optional[float] = None
    quote_server_time: Optional[int] = None
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------- helpers
    @property
    def can_open_long(self) -> bool:
        return self.trade_mode in (C.SYMBOL_TRADE_MODE_FULL, C.SYMBOL_TRADE_MODE_LONGONLY)

    @property
    def can_open_short(self) -> bool:
        return self.trade_mode in (C.SYMBOL_TRADE_MODE_FULL, C.SYMBOL_TRADE_MODE_SHORTONLY)

    @property
    def can_close(self) -> bool:
        return self.trade_mode != C.SYMBOL_TRADE_MODE_DISABLED

    def round_volume_down(self, volume: float) -> float:
        """Floor to the volume step; returns 0 if below the minimum."""
        if volume <= 0 or not math.isfinite(volume):
            return 0.0
        step = Decimal(str(self.volume_step))
        v = (Decimal(str(volume)) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        v = min(v, Decimal(str(self.volume_max)))
        if v < Decimal(str(self.volume_min)):
            return 0.0
        return float(v)

    def round_price(self, price: float) -> float:
        ts = Decimal(str(self.tick_size))
        return float((Decimal(str(price)) / ts).to_integral_value() * ts)

    def min_stop_distance(self) -> float:
        return self.stops_level_points * self.point

    def preferred_filling(self) -> int:
        if self.filling_mode & C.SYMBOL_FILLING_FOK:
            return C.ORDER_FILLING_FOK
        if self.filling_mode & C.SYMBOL_FILLING_IOC:
            return C.ORDER_FILLING_IOC
        return C.ORDER_FILLING_RETURN

    def value_per_price_unit(self, volume: float) -> float:
        """Profit-currency P&L of a 1.0 price move (used by the backtester)."""
        return self.contract_size * volume

    def public(self) -> dict:
        d = asdict(self)
        d["trade_mode_name"] = TRADE_MODE_NAME.get(self.trade_mode, str(self.trade_mode))
        d["filling_modes"] = [n for bit, n in ((C.SYMBOL_FILLING_FOK, "FOK"), (C.SYMBOL_FILLING_IOC, "IOC")) if self.filling_mode & bit] or ["RETURN"]
        return d


@dataclass
class Candidate:
    symbol: str
    role: Optional[str]
    kind: str
    accepted: bool
    reasons: list[str]
    info: dict


def _metal_role(info: dict) -> Optional[str]:
    name = (info.get("name") or "").upper()
    desc = (info.get("description") or "").lower()
    base = (info.get("currency_base") or "").upper()
    if base == "XAU" or name.startswith("XAU") or name.startswith("GOLD") or "gold" in desc:
        return "GOLD"
    if base == "XAG" or name.startswith("XAG") or name.startswith("SILVER") or "silver" in desc:
        return "SILVER"
    return None


def classify(info: dict) -> Candidate:
    role = _metal_role(info)
    kind = CALC_MODE_KIND.get(info.get("trade_calc_mode"), "UNKNOWN")
    reasons: list[str] = []
    accepted = role is not None
    if role is None:
        reasons.append("not a gold/silver symbol")
    if kind not in ACCEPTED_KINDS:
        accepted = False
        reasons.append(f"contract kind {kind} not accepted (stocks/ETFs/bonds/other excluded)")
    if _EQUITY_HINTS.search(info.get("description") or "") or _EQUITY_PATH.search(info.get("path") or ""):
        accepted = False
        reasons.append("description/path indicates an equity, ETF or fund")
    if (info.get("currency_profit") or "").upper() != "USD":
        reasons.append(f"profit currency {info.get('currency_profit')} (non-USD quote; allowed only if selected explicitly)")
        accepted = False
    if info.get("trade_mode") == C.SYMBOL_TRADE_MODE_DISABLED:
        reasons.append("trading disabled for this symbol")
    if accepted:
        reasons.append("accepted")
    return Candidate(info.get("name"), role, kind, accepted, reasons, info)


def spec_from_info(role: str, info: dict, tick: Optional[dict] = None) -> InstrumentSpec:
    return InstrumentSpec(
        role=role,
        symbol=info["name"],
        description=info.get("description", ""),
        path=info.get("path", ""),
        contract_kind=CALC_MODE_KIND.get(info.get("trade_calc_mode"), "UNKNOWN"),
        calc_mode=info.get("trade_calc_mode", -1),
        contract_size=float(info.get("trade_contract_size", 0)),
        volume_min=float(info.get("volume_min", 0)),
        volume_max=float(info.get("volume_max", 0)),
        volume_step=float(info.get("volume_step", 0)),
        tick_size=float(info.get("trade_tick_size", 0)),
        tick_value=float(info.get("trade_tick_value", 0)),
        point=float(info.get("point", 0)),
        digits=int(info.get("digits", 0)),
        currency_base=info.get("currency_base", ""),
        currency_profit=info.get("currency_profit", ""),
        currency_margin=info.get("currency_margin", ""),
        trade_mode=int(info.get("trade_mode", 0)),
        filling_mode=int(info.get("filling_mode", 0)),
        stops_level_points=int(info.get("trade_stops_level", 0)),
        freeze_level_points=int(info.get("trade_freeze_level", 0)),
        swap_mode=int(info.get("swap_mode", 0)),
        swap_long=float(info.get("swap_long", 0)),
        swap_short=float(info.get("swap_short", 0)),
        swap_rollover3days=int(info.get("swap_rollover3days", 3)),
        margin_initial=float(info.get("margin_initial", 0)),
        expiration_time=int(info.get("expiration_time", 0)),
        bid=(tick or {}).get("bid", info.get("bid")),
        ask=(tick or {}).get("ask", info.get("ask")),
        quote_server_time=(tick or {}).get("time", info.get("time")),
    )


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    resolved: dict[str, InstrumentSpec]
    unresolved: dict[str, str]

    def public(self) -> dict:
        return {
            "resolved": {r: s.public() for r, s in self.resolved.items()},
            "unresolved": self.unresolved,
            "candidates": [
                {"symbol": c.symbol, "role": c.role, "kind": c.kind, "accepted": c.accepted, "reasons": c.reasons}
                for c in self.candidates if c.role is not None
            ],
        }


def discover(gw: ReadOnlyGateway, selected: Optional[dict[str, Optional[str]]] = None) -> DiscoveryResult:
    """Classify every terminal symbol and resolve exactly one contract per metal.

    ``selected`` maps role -> explicit symbol name or glob pattern from config.
    """
    selected = selected or {}
    infos = gw.symbols()
    cands = [classify(i) for i in infos]
    resolved: dict[str, InstrumentSpec] = {}
    unresolved: dict[str, str] = {}
    for role in ROLES:
        want = selected.get(role)
        if want:
            matches = [c for c in cands if fnmatch.fnmatchcase(c.symbol, want)]
            if len(matches) != 1:
                unresolved[role] = f"configured symbol '{want}' matched {len(matches)} terminal symbols"
                continue
            c = matches[0]
            # An explicit choice may override the USD-quote preference, but never
            # the metal/contract-kind/equity checks.
            equity_like = _EQUITY_HINTS.search(c.info.get("description") or "") or \
                _EQUITY_PATH.search(c.info.get("path") or "")
            if c.role != role or c.kind not in ACCEPTED_KINDS or equity_like:
                unresolved[role] = f"configured symbol '{c.symbol}' rejected: {'; '.join(c.reasons)}"
                continue
        else:
            ok = [c for c in cands if c.role == role and c.accepted]
            if len(ok) == 0:
                unresolved[role] = "no acceptable contract found in terminal"
                continue
            if len(ok) > 1:
                unresolved[role] = ("multiple acceptable contracts (" + ", ".join(c.symbol for c in ok) +
                                    ") - set instruments.%s in config explicitly" % role.lower())
                continue
            c = ok[0]
        gw.symbol_select(c.symbol, True)
        info = gw.symbol_info(c.symbol) or c.info
        resolved[role] = spec_from_info(role, info, gw.symbol_info_tick(c.symbol))
    return DiscoveryResult(cands, resolved, unresolved)
