"""Demonstration strategies for the Module C engine.

These exist to exercise the engine, walk-forward validation, and the test
suite. The real intraday setup classes (Module B) arrive in Phase 4.
"""

from __future__ import annotations

import math

import pandas as pd

from watchman.backtest.engine import Order, Portfolio, Strategy, pad_start
from watchman.data.provider import AsOfView

#: Fraction of cash a full-position entry commits, leaving room for
#: slippage/spread and overnight gaps between decision and fill.
CASH_BUFFER = 0.98


class BuyAndHold(Strategy):
    """Buy a fixed quantity on the first session, hold to the end.
    The known-answer tests hand-compute this strategy's exact final equity."""

    name = "buy-and-hold"

    def __init__(self, symbol: str, qty: int):
        self.symbol = symbol
        self.qty = qty
        self._bought = False

    def on_session(self, session, view, portfolio) -> list[Order]:
        if self._bought:
            return []
        self._bought = True
        return [Order(self.symbol, "buy", self.qty)]


class MovingAverageCross(Strategy):
    """Long when the fast SMA is above the slow SMA, flat otherwise.

    A deliberately plain strategy: its job is to demonstrate honest
    engine mechanics and walk-forward optimization of (fast, slow).
    """

    name = "ma-cross"

    def __init__(self, symbol: str, fast: int, slow: int):
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be < slow ({slow})")
        self.symbol = symbol
        self.fast = fast
        self.slow = slow
        self.name = f"ma-cross({fast},{slow})"

    def on_session(
        self, session: pd.Timestamp, view: AsOfView, portfolio: Portfolio
    ) -> list[Order]:
        start = pad_start(view.as_of, int(self.slow * 2.2) + 10)
        bars = view.daily_bars(self.symbol, start=start)
        if len(bars) < self.slow:
            return []
        closes = bars["close"]
        fast_ma = float(closes.tail(self.fast).mean())
        slow_ma = float(closes.tail(self.slow).mean())
        held = portfolio.qty(self.symbol)
        if fast_ma > slow_ma and held == 0:
            last_close = float(closes.iloc[-1])
            qty = math.floor(portfolio.cash * CASH_BUFFER / last_close)
            if qty > 0:
                return [Order(self.symbol, "buy", qty)]
        elif fast_ma <= slow_ma and held > 0:
            return [Order(self.symbol, "sell", held)]
        return []
