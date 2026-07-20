"""Module C — event-driven backtesting engine.

Walk-forward validation, out-of-sample reporting, costs on every fill,
lookahead-hostile by construction (strategies see data only through AsOfView).
"""

from watchman.backtest.decile import DecileResult, momentum_decile_backtest
from watchman.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    Fill,
    Order,
    Portfolio,
    Strategy,
    Trade,
)
from watchman.backtest.metrics import (
    format_metrics,
    honesty_flags,
    performance_metrics,
    trade_metrics,
)
from watchman.backtest.strategies import BuyAndHold, MovingAverageCross
from watchman.backtest.walkforward import (
    WalkForwardResult,
    Window,
    day_windows,
    walk_forward,
)

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BuyAndHold",
    "DecileResult",
    "Fill",
    "MovingAverageCross",
    "Order",
    "Portfolio",
    "Strategy",
    "Trade",
    "WalkForwardResult",
    "Window",
    "day_windows",
    "format_metrics",
    "honesty_flags",
    "momentum_decile_backtest",
    "performance_metrics",
    "trade_metrics",
    "walk_forward",
]
