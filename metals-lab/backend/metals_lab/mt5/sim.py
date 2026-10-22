"""Deterministic simulated MT5 terminal.

Implements the subset of the official MetaTrader5 module interface the project
uses, so the whole pipeline (guard -> auditor -> risk -> executor ->
reconciliation -> ledger) can be exercised in tests without touching any real
terminal. It is a test double: it has no network access and cannot reach a
broker. Its behaviour (fills, partial fills, rejections, netting) is explicit
and controllable from tests.
"""
from __future__ import annotations

import itertools
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from . import constants as C

AccountInfo = namedtuple(
    "AccountInfo",
    "login trade_mode leverage limit_orders margin_so_mode trade_allowed trade_expert margin_mode "
    "currency_digits fifo_close balance credit profit equity margin margin_free margin_level "
    "margin_so_call margin_so_so name server currency company",
)
TerminalInfo = namedtuple("TerminalInfo", "connected trade_allowed tradeapi_disabled build name company path")
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags volume_real")
Position = namedtuple(
    "Position",
    "ticket time time_msc type magic identifier reason volume price_open sl tp price_current swap profit symbol comment",
)
Deal = namedtuple(
    "Deal",
    "ticket order time time_msc type entry magic position_id reason volume price commission swap profit fee symbol comment",
)
OrderResult = namedtuple("OrderResult", "retcode deal order volume price bid ask comment request_id")
CheckResult = namedtuple("CheckResult", "retcode balance equity profit margin margin_free margin_level comment")

SYMBOL_FIELDS = (
    "name description path visible select trade_calc_mode trade_mode trade_exemode filling_mode order_mode "
    "digits point trade_tick_size trade_tick_value trade_tick_value_profit trade_tick_value_loss "
    "trade_contract_size volume_min volume_max volume_step trade_stops_level trade_freeze_level "
    "currency_base currency_profit currency_margin margin_initial margin_maintenance swap_mode swap_long "
    "swap_short swap_rollover3days spread spread_float bid ask time expiration_time"
)
SymbolInfo = namedtuple("SymbolInfo", SYMBOL_FIELDS)


def default_symbols() -> dict[str, dict]:
    gold = dict(
        name="XAUUSD", description="Gold vs US Dollar", path="Metals\\XAUUSD", visible=True, select=True,
        trade_calc_mode=C.SYMBOL_CALC_MODE_CFD, trade_mode=C.SYMBOL_TRADE_MODE_FULL, trade_exemode=2,
        filling_mode=C.SYMBOL_FILLING_FOK | C.SYMBOL_FILLING_IOC, order_mode=127, digits=2, point=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0, trade_tick_value_profit=1.0, trade_tick_value_loss=1.0,
        trade_contract_size=100.0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        trade_stops_level=20, trade_freeze_level=0, currency_base="XAU", currency_profit="USD",
        currency_margin="USD", margin_initial=0.0, margin_maintenance=0.0, swap_mode=C.SYMBOL_SWAP_MODE_POINTS,
        swap_long=-30.0, swap_short=10.0, swap_rollover3days=3, spread=25, spread_float=True,
        bid=2000.0, ask=2000.25, time=0, expiration_time=0,
    )
    silver = dict(gold)
    silver.update(
        name="XAGUSD", description="Silver vs US Dollar", path="Metals\\XAGUSD", digits=3, point=0.001,
        trade_tick_size=0.001, trade_tick_value=5.0, trade_tick_value_profit=5.0, trade_tick_value_loss=5.0,
        trade_contract_size=5000.0, currency_base="XAG", swap_long=-5.0, swap_short=1.0, spread=30,
        bid=25.0, ask=25.03, trade_stops_level=30,
    )
    miner = dict(gold)  # deliberately confusing name: a stock CFD called GOLD (e.g. a miner)
    miner.update(name="GOLD.US", description="Barrick Gold Corp", path="Stocks\\US\\GOLD",
                 trade_calc_mode=C.SYMBOL_CALC_MODE_EXCH_STOCKS, currency_base="USD", trade_contract_size=1.0,
                 bid=18.0, ask=18.02, digits=2)
    eurusd = dict(gold)
    eurusd.update(name="EURUSD", description="Euro vs US Dollar", path="Forex\\EURUSD", digits=5, point=0.00001,
                  trade_calc_mode=C.SYMBOL_CALC_MODE_FOREX, trade_contract_size=100000.0, currency_base="EUR",
                  bid=1.08, ask=1.08008, trade_tick_size=0.00001, trade_tick_value=1.0)
    return {s["name"]: s for s in (gold, silver, miner, eurusd)}


@dataclass
class SimAccount:
    login: int
    server: str
    trade_mode: int = C.ACCOUNT_TRADE_MODE_DEMO
    margin_mode: int = C.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    balance: float = 10000.0
    leverage: int = 100
    currency: str = "USD"
    company: str = "Simulated Broker Ltd"
    name: str = "Simulated"


@dataclass
class _Pos:
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    magic: int
    comment: str
    time: int
    swap: float = 0.0


@dataclass
class SimTerminal:
    """Controllable fake of the MetaTrader5 module."""

    accounts: dict[int, SimAccount] = field(default_factory=dict)
    current_login: Optional[int] = None
    symbols: dict[str, dict] = field(default_factory=default_symbols)
    server_utc_offset_hours: int = 2
    connected: bool = True
    commission_per_lot: float = 3.5  # per side, account currency
    # behaviour switches for tests
    reject_next: list[int] = field(default_factory=list)
    partial_next: list[float] = field(default_factory=list)
    timeout_next: int = 0
    fail_sltp: bool = False
    drop_sl_on_fill: bool = False
    rates: dict[tuple[str, int], np.ndarray] = field(default_factory=dict)
    sent_requests: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self._ticket = itertools.count(1000)
        self._positions: dict[int, _Pos] = {}
        self._deals: list[Any] = []
        self._orders_hist: list[dict] = []
        self._now: datetime = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        self._last_error = (1, "Success")
        if not self.accounts:
            self.add_account(SimAccount(login=51234567, server="Sim-Demo"))
        if self.current_login is None:
            self.current_login = next(iter(self.accounts))
        for s in self.symbols.values():
            s["time"] = self._server_epoch()

    # ------------------------------------------------------------------ control
    def add_account(self, acc: SimAccount) -> None:
        self.accounts[acc.login] = acc

    def switch_account(self, login: int) -> None:
        self.current_login = login

    def set_time(self, now_utc: datetime) -> None:
        self._now = now_utc
        for s in self.symbols.values():
            s["time"] = self._server_epoch()

    def advance(self, seconds: float) -> None:
        self.set_time(self._now + timedelta(seconds=seconds))

    def set_quote(self, symbol: str, bid: float, ask: Optional[float] = None, *, stale_seconds: float = 0) -> None:
        s = self.symbols[symbol]
        s["bid"] = bid
        s["ask"] = ask if ask is not None else bid + s["spread"] * s["point"]
        s["time"] = self._server_epoch() - int(stale_seconds)
        self._check_stops(symbol)

    def deposit(self, amount: float) -> None:
        acc = self._acc()
        acc.balance += amount
        self._add_deal(type_=C.DEAL_TYPE_BALANCE, entry=C.DEAL_ENTRY_IN, symbol="", volume=0, price=0,
                       profit=amount, magic=0, position_id=0, comment="deposit")

    def open_manual_position(self, symbol: str, side: int, volume: float) -> int:
        """Simulates a trade placed by a human in the terminal (magic 0)."""
        res = self.order_send({"action": C.TRADE_ACTION_DEAL, "symbol": symbol, "volume": volume, "type": side,
                               "price": 0, "magic": 0, "comment": "manual"})
        return res.order

    def _server_epoch(self) -> int:
        return int((self._now + timedelta(hours=self.server_utc_offset_hours)).replace(tzinfo=None)
                   .replace(tzinfo=timezone.utc).timestamp())

    def _acc(self) -> SimAccount:
        return self.accounts[self.current_login]

    # ------------------------------------------------------------------ API
    def initialize(self, *args, **kwargs) -> bool:
        if not self.connected:
            self._last_error = (-10003, "IPC initialize failed")
            return False
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self):
        return self._last_error

    def version(self):
        return (500, 9999, "sim")

    def terminal_info(self):
        if not self.connected:
            return None
        return TerminalInfo(True, True, False, 9999, "SimTerminal", "Sim", "C:/sim")

    def account_info(self):
        if not self.connected or self.current_login is None:
            return None
        a = self._acc()
        profit = sum(self._pos_profit(p) for p in self._positions.values())
        margin = sum(self._margin(p.symbol, p.volume, p.price_open) for p in self._positions.values())
        equity = a.balance + profit
        return AccountInfo(
            a.login, a.trade_mode, a.leverage, 200, 0, True, True, a.margin_mode, 2, False, a.balance, 0.0,
            profit, equity, margin, equity - margin, (equity / margin * 100) if margin else 0.0, 50.0, 30.0,
            a.name, a.server, a.currency, a.company,
        )

    def symbols_get(self, group: Optional[str] = None):
        return tuple(SymbolInfo(**s) for s in self.symbols.values())

    def symbol_info(self, symbol: str):
        s = self.symbols.get(symbol)
        return SymbolInfo(**s) if s else None

    def symbol_info_tick(self, symbol: str):
        s = self.symbols.get(symbol)
        if not s or not self.connected:
            return None
        return Tick(s["time"], s["bid"], s["ask"], 0.0, 0, s["time"] * 1000, 6, 0.0)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return symbol in self.symbols

    def copy_rates_range(self, symbol, timeframe, start, end):
        arr = self.rates.get((symbol, timeframe))
        if arr is None:
            return None
        t0, t1 = int(start.timestamp()), min(int(end.timestamp()), self._server_epoch())
        return arr[(arr["time"] >= t0) & (arr["time"] <= t1)]

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        arr = self.rates.get((symbol, timeframe))
        if arr is None:
            return None
        arr = arr[arr["time"] <= self._server_epoch()]  # like MT5: up to and including the forming bar
        n = len(arr)
        end = n - start_pos
        return arr[max(0, end - count):end]

    def copy_ticks_range(self, symbol, start, end, flags):
        return None

    def order_calc_margin(self, order_type, symbol, volume, price):
        if symbol not in self.symbols:
            return None
        return self._margin(symbol, volume, price)

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        s = self.symbols.get(symbol)
        if not s:
            return None
        sign = 1 if order_type == C.ORDER_TYPE_BUY else -1
        return round(sign * (price_close - price_open) * s["trade_contract_size"] * volume, 2)

    def positions_get(self, symbol=None, ticket=None):
        if not self.connected:
            return None
        out = []
        for p in self._positions.values():
            if symbol and p.symbol != symbol:
                continue
            if ticket and p.ticket != ticket:
                continue
            s = self.symbols[p.symbol]
            cur = s["bid"] if p.type == C.POSITION_TYPE_BUY else s["ask"]
            out.append(Position(p.ticket, p.time, p.time * 1000, p.type, p.magic, p.ticket, 3, p.volume,
                                p.price_open, p.sl, p.tp, cur, p.swap, self._pos_profit(p), p.symbol, p.comment))
        return tuple(out)

    def orders_get(self, *a, **k):
        return tuple()

    def history_deals_get(self, date_from, date_to, **kwargs):
        if not self.connected:
            return None
        t0, t1 = int(date_from.timestamp()), int(date_to.timestamp())
        return tuple(d for d in self._deals if t0 <= d.time <= t1)

    def history_orders_get(self, date_from, date_to, **kwargs):
        return tuple()

    def order_check(self, request: dict):
        a = self.account_info()
        return CheckResult(0, a.balance, a.equity, a.profit, a.margin, a.margin_free, a.margin_level, "Done")

    def order_send(self, request: dict):
        self.sent_requests.append(dict(request))
        if not self.connected:
            return None
        if self.timeout_next:
            self.timeout_next -= 1
            # The request *was* executed server side but the reply was lost.
            self._execute(request)
            return None
        if self.reject_next:
            code = self.reject_next.pop(0)
            return OrderResult(code, 0, 0, 0.0, 0.0, 0.0, 0.0, "rejected", 1)
        if request.get("action") == C.TRADE_ACTION_SLTP:
            if self.fail_sltp:
                return OrderResult(C.TRADE_RETCODE_INVALID_STOPS, 0, 0, 0, 0, 0, 0, "invalid stops", 1)
            p = self._positions.get(request["position"])
            if not p:
                return OrderResult(C.TRADE_RETCODE_INVALID, 0, 0, 0, 0, 0, 0, "no position", 1)
            p.sl, p.tp = request.get("sl", 0.0), request.get("tp", 0.0)
            return OrderResult(C.TRADE_RETCODE_DONE, 0, 0, 0, 0, 0, 0, "done", 1)
        return self._execute(request)

    # ------------------------------------------------------------------ internals
    def _margin(self, symbol: str, volume: float, price: float) -> float:
        s = self.symbols[symbol]
        return round(volume * s["trade_contract_size"] * price / self._acc().leverage, 2)

    def _pos_profit(self, p: _Pos) -> float:
        s = self.symbols[p.symbol]
        cur = s["bid"] if p.type == C.POSITION_TYPE_BUY else s["ask"]
        sign = 1 if p.type == C.POSITION_TYPE_BUY else -1
        return round(sign * (cur - p.price_open) * s["trade_contract_size"] * p.volume, 2)

    def _add_deal(self, *, type_, entry, symbol, volume, price, profit, magic, position_id, comment,
                  order=0, commission=0.0, swap=0.0):
        t = next(self._ticket)
        d = Deal(t, order or t, self._server_epoch(), self._server_epoch() * 1000, type_, entry, magic, position_id,
                 0, volume, price, commission, swap, profit, 0.0, symbol, comment)
        self._deals.append(d)
        return d

    def _execute(self, request: dict):
        sym = request["symbol"]
        s = self.symbols[sym]
        side = request["type"]
        volume = float(request["volume"])
        if self.partial_next:
            frac = self.partial_next.pop(0)
            step = s["volume_step"]
            volume = max(step, round(np.floor(volume * frac / step) * step, 8))
            retcode = C.TRADE_RETCODE_DONE_PARTIAL
        else:
            retcode = C.TRADE_RETCODE_DONE
        price = s["ask"] if side == C.ORDER_TYPE_BUY else s["bid"]
        order_ticket = next(self._ticket)
        acc = self._acc()
        commission = -round(self.commission_per_lot * volume, 2)
        target = request.get("position")
        netting = acc.margin_mode != C.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
        if target is None and netting:
            same = [p for p in self._positions.values() if p.symbol == sym]
            if same:
                target = same[0].ticket
        if target is not None and target in self._positions:
            p = self._positions[target]
            if p.type != side:  # closing / reducing
                close_vol = min(volume, p.volume)
                sign = 1 if p.type == C.POSITION_TYPE_BUY else -1
                profit = round(sign * (price - p.price_open) * s["trade_contract_size"] * close_vol, 2)
                acc.balance += profit + commission
                d = self._add_deal(type_=side, entry=C.DEAL_ENTRY_OUT, symbol=sym, volume=close_vol, price=price,
                                   profit=profit, magic=request.get("magic", 0), position_id=p.ticket,
                                   comment=request.get("comment", ""), order=order_ticket, commission=commission)
                p.volume = round(p.volume - close_vol, 8)
                if p.volume <= 1e-9:
                    del self._positions[p.ticket]
                return OrderResult(retcode, d.ticket, order_ticket, close_vol, price, s["bid"], s["ask"], "done", 1)
            # netting add to same direction
            new_vol = p.volume + volume
            p.price_open = (p.price_open * p.volume + price * volume) / new_vol
            p.volume = new_vol
            acc.balance += commission
            d = self._add_deal(type_=side, entry=C.DEAL_ENTRY_IN, symbol=sym, volume=volume, price=price, profit=0.0,
                               magic=request.get("magic", 0), position_id=p.ticket, comment=request.get("comment", ""),
                               order=order_ticket, commission=commission)
            return OrderResult(retcode, d.ticket, order_ticket, volume, price, s["bid"], s["ask"], "done", 1)
        pos_ticket = order_ticket  # MT5: position ticket == opening order ticket
        sl = 0.0 if self.drop_sl_on_fill else float(request.get("sl", 0.0) or 0.0)
        self._positions[pos_ticket] = _Pos(pos_ticket, sym, side, volume, price, sl, float(request.get("tp", 0.0) or 0.0),
                                           request.get("magic", 0), request.get("comment", ""), self._server_epoch())
        acc.balance += commission
        d = self._add_deal(type_=side, entry=C.DEAL_ENTRY_IN, symbol=sym, volume=volume, price=price, profit=0.0,
                           magic=request.get("magic", 0), position_id=pos_ticket, comment=request.get("comment", ""),
                           order=order_ticket, commission=commission)
        return OrderResult(retcode, d.ticket, order_ticket, volume, price, s["bid"], s["ask"], "done", 1)

    def _check_stops(self, symbol: str) -> None:
        """Server-side SL/TP execution."""
        s = self.symbols[symbol]
        for p in list(self._positions.values()):
            if p.symbol != symbol:
                continue
            if p.type == C.POSITION_TYPE_BUY:
                hit = (p.sl and s["bid"] <= p.sl) or (p.tp and s["bid"] >= p.tp)
                close_side = C.ORDER_TYPE_SELL
            else:
                hit = (p.sl and s["ask"] >= p.sl) or (p.tp and s["ask"] <= p.tp)
                close_side = C.ORDER_TYPE_BUY
            if hit:
                self._execute({"symbol": symbol, "type": close_side, "volume": p.volume, "position": p.ticket,
                               "magic": p.magic, "comment": "[sl/tp]"})
