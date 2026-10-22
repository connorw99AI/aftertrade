from .base import Param, Strategy, StrategyContext  # noqa: F401
from .daytrading import BreakoutRetest, LondonOpeningRangeBreakout, VolatilityCompressionBreakout
from .scalping import AsianRangeReversion, FailedBreakoutReversal, TrendPullbackScalper
from .swing import DonchianChannelBreakout, GoldSilverRatioReversion, RegimeRSI2Reversion, TimeSeriesMomentum

STRATEGY_CLASSES = {
    c.strategy_id: c
    for c in (
        TrendPullbackScalper, AsianRangeReversion, FailedBreakoutReversal,
        LondonOpeningRangeBreakout, BreakoutRetest, VolatilityCompressionBreakout,
        RegimeRSI2Reversion, DonchianChannelBreakout, TimeSeriesMomentum, GoldSilverRatioReversion,
    )
}

MAGIC_BASE = 7_710_000


def magic_for(strategy_id: str) -> int:
    """Unique, stable magic number per strategy."""
    return MAGIC_BASE + STRATEGY_CLASSES[strategy_id].magic_offset


def strategy_for_magic(magic: int):
    for sid, cls in STRATEGY_CLASSES.items():
        if MAGIC_BASE + cls.magic_offset == magic:
            return sid
    return None


def build(strategy_id: str, params: dict | None = None) -> Strategy:
    return STRATEGY_CLASSES[strategy_id](params)
