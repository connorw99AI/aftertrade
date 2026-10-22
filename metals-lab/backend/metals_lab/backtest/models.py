"""Explicit, recorded backtest assumptions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class CostModel:
    commission_per_lot_side: float = 3.5      # account currency per 1.0 lot per side (assumption unless broker-confirmed)
    slippage_points: dict = field(default_factory=lambda: {"GOLD": 5, "SILVER": 8})
    spread_source: str = "bar"                # "bar" = per-bar terminal spread column; "fixed" = fixed_spread_points
    fixed_spread_points: dict = field(default_factory=lambda: {"GOLD": 25, "SILVER": 30})
    min_spread_points: dict = field(default_factory=lambda: {"GOLD": 10, "SILVER": 15})
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_multiplier: float = 1.0
    swap_multiplier: float = 1.0
    use_swaps: bool = True
    rollover_utc_hour: int = 21               # approx. server midnight for a UTC+3 summer server; configure per broker
    fx_to_account: float = 1.0                # profit currency (USD) -> account currency; 1.0 assumes a USD account

    def describe(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionModel:
    reject_prob: float = 0.0
    partial_prob: float = 0.0
    partial_fraction: float = 0.5
    seed: int = 1
    leverage: float = 100.0                   # margin = notional / leverage (simplified; live uses order_calc_margin)
    max_entry_gap_bars: float = 3.0           # if the next bar opens later than this many bars, fill at reopen and flag


@dataclass
class RiskParams:
    risk_per_trade: float = 0.005
    daily_loss_pause: float = 0.02
    max_drawdown_pause: float = 0.10
    max_open_positions: int = 2
    max_margin_fraction: float = 0.5          # of the bot's own equity
    drawdown_resume: str = "never"            # "never" mirrors live (operator must resume)


@dataclass
class BacktestConfig:
    initial_capital: float = 1000.0
    costs: CostModel = field(default_factory=CostModel)
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    risk: RiskParams = field(default_factory=RiskParams)
    start: Optional[str] = None
    end: Optional[str] = None
    label: str = ""

    def describe(self) -> dict:
        return asdict(self)
