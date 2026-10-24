from .base import Param, Strategy, StrategyContext  # noqa: F401
from .daytrading import BreakoutRetest, NewYorkOpeningRangeBreakout, VolatilityCompressionBreakout
from .scalping import AsianRangeReversion, FailedBreakoutReversal, TrendPullbackScalper
from .swing import DonchianChannelBreakout, GoldSilverRatioReversion, RegimeRSI2Reversion, TimeSeriesMomentum

STRATEGY_CLASSES = {
    c.strategy_id: c
    for c in (
        TrendPullbackScalper, AsianRangeReversion, FailedBreakoutReversal,
        NewYorkOpeningRangeBreakout, BreakoutRetest, VolatilityCompressionBreakout,
        RegimeRSI2Reversion, DonchianChannelBreakout, TimeSeriesMomentum, GoldSilverRatioReversion,
    )
}

# Every strategy runs as two bots: a long half (L) and a short half (S), each with its own money,
# magic number and record, so one half can be retired without touching the other.
# For the gold/silver pair (S10) the halves are its two directions: L = long silver / short gold, S = the reverse.
SIDES = {"L": "long", "S": "short"}
BOT_IDS = [f"{sid}{h}" for sid in STRATEGY_CLASSES for h in SIDES]

MAGIC_BASE = 7_710_000


def is_bot(bot_id: str) -> bool:
    return len(bot_id) == 4 and bot_id[:3] in STRATEGY_CLASSES and bot_id[3] in SIDES


def strategy_of(bot_id: str) -> str:
    return bot_id[:3] if is_bot(bot_id) else bot_id


def side_of(bot_id: str) -> str | None:
    return SIDES[bot_id[3]] if is_bot(bot_id) else None


def bot_class(bot_id: str):
    return STRATEGY_CLASSES[strategy_of(bot_id)]


def bot_name(bot_id: str) -> str:
    cls = bot_class(bot_id)
    side = side_of(bot_id)
    if not side:
        return cls.name
    if cls.pair_trades:
        return f"{cls.name} ({'silver cheap' if side == 'long' else 'gold cheap'})"
    return f"{cls.name} ({side})"


def magic_for(bot_id: str) -> int:
    """Unique, stable magic number per bot: base + 10 x strategy offset + 1 (long) / 2 (short)."""
    off = bot_class(bot_id).magic_offset
    side = side_of(bot_id)
    return MAGIC_BASE + off * 10 + (1 if side == "long" else 2) if side else MAGIC_BASE + off


def strategy_for_magic(magic: int):
    """The bot that owns a magic number. Magic numbers from before the long/short split are not claimed."""
    for b in BOT_IDS:
        if magic_for(b) == magic:
            return b
    return None


def build(bot_or_strategy_id: str, params: dict | None = None) -> Strategy:
    """A bot id ('S03L') builds its strategy with the side fixed; a strategy id ('S03') builds it with both sides."""
    cls = bot_class(bot_or_strategy_id)
    side = side_of(bot_or_strategy_id)
    p = dict(params or {})
    if side:
        p["sides"] = side
    return cls(p)


# Halves the assessor recommended retiring after the one-year MT5 backtest (2025-10-23..2026-10-23, in-sample).
# They start RETIRED on a fresh lab; the operator can un-retire any of them.
ASSESSOR_RETIRED = {
    "S01L": "no edge: lost 48% with a 15% win rate", "S01S": "no edge: lost 48% with a 15% win rate",
    "S02L": "no edge: expectancy -0.06R over 202 trades", "S02S": "no edge: expectancy -0.06R over 202 trades",
    "S03S": "shorts lost -76 while longs made +96", "S04L": "London-open version: longs lost -247 while shorts made +220 (S04 now trades the New York open; re-test before retiring)",
    "S05L": "longs lost -81 while shorts made +190", "S06S": "shorts lost -269 while longs made +276",
    "S07S": "shorts lost -135 while longs made +31",
}
