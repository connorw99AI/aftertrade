"""Thin, normalising wrapper around the official MetaTrader5 Python API.

Only documented package functions are used:
initialize, shutdown, last_error, version, terminal_info, account_info,
symbols_get, symbol_info, symbol_info_tick, symbol_select,
copy_rates_range, copy_rates_from_pos, copy_ticks_range,
order_calc_margin, order_calc_profit, order_check, order_send,
orders_get, positions_get, history_orders_get, history_deals_get.

The gateway is split in two:
* ``ReadOnlyGateway`` - market/account reads. Given to strategies' data service,
  the auditor and the risk engine. It has no order methods at all.
* ``TradingGateway`` - adds ``order_check``/``order_send``. Only the executor
  holds one, and it re-verifies demo mode immediately before every send.
"""
from __future__ import annotations

from ..timeutil import utc_now as _tnow

import os
import sys
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from . import constants as C


class MT5Error(RuntimeError):
    pass


class LiveAccountBlocked(MT5Error):
    """Raised whenever the connected account is not a verified demo account."""


def _as_dict(obj: Any) -> Optional[dict]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    return dict(vars(obj))


def _under_test() -> bool:
    return "pytest" in sys.modules or os.environ.get("METALS_LAB_TEST_MODE") == "1"


class ReadOnlyGateway:
    def __init__(self, backend: Any, *, is_simulated: bool = False):
        self._b = backend
        self.is_simulated = is_simulated

    # -- constants ---------------------------------------------------------
    def const(self, name: str) -> Any:
        return C.resolve(self._b, name)

    # -- lifecycle ---------------------------------------------------------
    def initialize(self, path: Optional[str] = None, timeout_ms: int = 60000) -> bool:
        # Attach to the already running, already logged-in terminal. We never
        # pass login/password: credentials stay inside the terminal.
        if path:
            ok = self._b.initialize(path, timeout=timeout_ms)
        else:
            ok = self._b.initialize(timeout=timeout_ms)
        return bool(ok)

    def shutdown(self) -> None:
        try:
            self._b.shutdown()
        except Exception:  # pragma: no cover - best effort
            pass

    def last_error(self) -> Any:
        try:
            return self._b.last_error()
        except Exception:  # pragma: no cover
            return None

    def version(self) -> Any:
        return self._b.version()

    def terminal_info(self) -> Optional[dict]:
        return _as_dict(self._b.terminal_info())

    def account_info(self) -> Optional[dict]:
        return _as_dict(self._b.account_info())

    # -- symbols -----------------------------------------------------------
    def symbols(self) -> list[dict]:
        res = self._b.symbols_get()
        return [_as_dict(s) for s in (res or [])]

    def symbol_info(self, symbol: str) -> Optional[dict]:
        return _as_dict(self._b.symbol_info(symbol))

    def symbol_info_tick(self, symbol: str) -> Optional[dict]:
        return _as_dict(self._b.symbol_info_tick(symbol))

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return bool(self._b.symbol_select(symbol, enable))

    # -- history -----------------------------------------------------------
    @staticmethod
    def _rates_to_frame(rates: Any) -> pd.DataFrame:
        cols = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
        if rates is None or len(rates) == 0:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(np.asarray(rates))
        df = df[[c for c in cols if c in df.columns]]
        # MT5 returns epoch seconds expressed in *trade-server* time. The
        # conversion to true UTC is done by sessions.ServerClock, not here.
        df["server_time"] = pd.to_datetime(df["time"], unit="s")
        return df.drop(columns=["time"])

    def copy_rates_range(self, symbol: str, timeframe: int, start: datetime, end: datetime) -> pd.DataFrame:
        return self._rates_to_frame(self._b.copy_rates_range(symbol, timeframe, start, end))

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> pd.DataFrame:
        return self._rates_to_frame(self._b.copy_rates_from_pos(symbol, timeframe, start_pos, count))

    def copy_ticks_range(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        ticks = self._b.copy_ticks_range(symbol, start, end, self.const("COPY_TICKS_ALL"))
        if ticks is None or len(ticks) == 0:
            return pd.DataFrame(columns=["server_time", "bid", "ask", "last", "volume", "flags"])
        df = pd.DataFrame(np.asarray(ticks))
        if "time_msc" in df.columns:
            df["server_time"] = pd.to_datetime(df["time_msc"], unit="ms")
        else:
            df["server_time"] = pd.to_datetime(df["time"], unit="s")
        keep = [c for c in ["server_time", "bid", "ask", "last", "volume", "flags"] if c in df.columns]
        return df[keep]

    # -- calculators -------------------------------------------------------
    def order_calc_margin(self, order_type: int, symbol: str, volume: float, price: float) -> Optional[float]:
        v = self._b.order_calc_margin(order_type, symbol, volume, price)
        return None if v is None else float(v)

    def order_calc_profit(self, order_type: int, symbol: str, volume: float, price_open: float, price_close: float) -> Optional[float]:
        v = self._b.order_calc_profit(order_type, symbol, volume, price_open, price_close)
        return None if v is None else float(v)

    # -- trading state -----------------------------------------------------
    def positions(self, symbol: Optional[str] = None) -> list[dict]:
        res = self._b.positions_get(symbol=symbol) if symbol else self._b.positions_get()
        return [_as_dict(p) for p in (res or [])]

    def history_deals(self, start: datetime, end: datetime) -> list[dict]:
        return [_as_dict(d) for d in (self._b.history_deals_get(start, end) or [])]


class TradingGateway(ReadOnlyGateway):
    """Adds order methods. Every send re-checks demo mode at the last moment."""

    def read_only(self) -> ReadOnlyGateway:
        return ReadOnlyGateway(self._b, is_simulated=self.is_simulated)

    def assert_demo_now(self) -> dict:
        info = self.account_info()
        if info is None:
            raise LiveAccountBlocked("account_info() unavailable - terminal disconnected")
        if info.get("trade_mode") != self.const("ACCOUNT_TRADE_MODE_DEMO"):
            raise LiveAccountBlocked(f"account trade_mode={info.get('trade_mode')} is not DEMO")
        return info

    def order_check(self, request: dict) -> Optional[dict]:
        self.assert_demo_now()
        return _as_dict(self._b.order_check(request))

    def order_send(self, request: dict) -> Optional[dict]:
        if _under_test() and not self.is_simulated:
            raise MT5Error("Refusing order_send to a real terminal while running tests")
        self.assert_demo_now()  # final boundary, after all approvals
        return _as_dict(self._b.order_send(request))


def load_real_backend() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as e:  # pragma: no cover - depends on platform
        raise MT5Error(
            "The official MetaTrader5 package is not installed. It is Windows-only: "
            "pip install MetaTrader5 (inside the project venv on the Windows machine)."
        ) from e
    return mt5


def utc_now() -> datetime:
    return _tnow()
