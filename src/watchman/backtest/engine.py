"""Event-driven backtest engine (Module C).

Honesty by construction:
- Strategies see market data ONLY through an AsOfView pinned to the session
  close they are reacting to. The engine-level canary test proves a strategy
  cannot see past its pin.
- Orders decided after session D's close fill at session D+1's OPEN, with
  slippage + spread + commission applied to every fill (costs from
  settings.yaml). No same-bar fills, no fill-at-decision-price.
- Rejected orders (insufficient cash, missing bar) are recorded in
  result.warnings, never silently dropped.

Known v1 limitations (stated, not hidden):
- Long-only; no shorting, no leverage.
- Fills use raw open prices; dividends are not credited to cash, so
  engine results understate total return for dividend payers. (The decile
  backtest uses adjusted closes and does include dividends.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal

import pandas as pd

from watchman.config import CostsConfig
from watchman.data.provider import ET, AsOfView, DataProvider, normalize_bars


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"order qty must be positive, got {self.qty}")


@dataclass(frozen=True)
class Fill:
    date: pd.Timestamp
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    reference_price: float  # the raw open the fill was based on
    price: float            # all-in per-share price after slippage + spread
    commission: float

    @property
    def cost_drag(self) -> float:
        """Dollars lost to costs on this fill vs. filling at the raw open."""
        return abs(self.price - self.reference_price) * self.qty + self.commission


@dataclass(frozen=True)
class Trade:
    """A closed round trip (FIFO-paired)."""

    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    qty: int
    entry_price: float
    exit_price: float

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def ret_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100


@dataclass
class _Lot:
    qty: int
    price: float
    date: pd.Timestamp


class Portfolio:
    """Cash + long positions. Strategies receive this read-only (by
    convention): inspect cash/qty/equity, never mutate."""

    def __init__(self, cash: float):
        self.cash = cash
        self._lots: dict[str, deque[_Lot]] = {}

    def qty(self, symbol: str) -> int:
        return sum(lot.qty for lot in self._lots.get(symbol, ()))

    @property
    def held_symbols(self) -> list[str]:
        return [s for s, lots in self._lots.items() if lots]

    def equity(self, prices: dict[str, float]) -> float:
        value = self.cash
        for symbol in self.held_symbols:
            value += self.qty(symbol) * prices[symbol]
        return value

    # -- engine-internal mutation ------------------------------------------
    def _apply_buy(self, fill: Fill) -> None:
        self.cash -= fill.qty * fill.price + fill.commission
        self._lots.setdefault(fill.symbol, deque()).append(
            _Lot(fill.qty, fill.price, fill.date)
        )

    def _apply_sell(self, fill: Fill) -> list[Trade]:
        self.cash += fill.qty * fill.price - fill.commission
        lots = self._lots[fill.symbol]
        remaining = fill.qty
        trades: list[Trade] = []
        while remaining > 0:
            lot = lots[0]
            take = min(lot.qty, remaining)
            trades.append(
                Trade(
                    symbol=fill.symbol,
                    entry_date=lot.date,
                    exit_date=fill.date,
                    qty=take,
                    entry_price=lot.price,
                    exit_price=fill.price,
                )
            )
            lot.qty -= take
            remaining -= take
            if lot.qty == 0:
                lots.popleft()
        return trades


class Strategy(ABC):
    """A trading strategy. Receives an AsOfView pinned at the session close it
    is reacting to — the ONLY market data it may use."""

    name: str = "strategy"

    @abstractmethod
    def on_session(
        self, session: pd.Timestamp, view: AsOfView, portfolio: Portfolio
    ) -> list[Order]:
        """Called after `session`'s close. Returned orders fill at the NEXT
        session's open."""


class CostModel:
    def __init__(self, costs: CostsConfig):
        self.costs = costs

    def buy_price(self, reference: float) -> float:
        return reference * (1 + self.costs.total_bps_per_side / 1e4)

    def sell_price(self, reference: float) -> float:
        return reference * (1 - self.costs.total_bps_per_side / 1e4)

    @property
    def commission(self) -> float:
        return self.costs.commission_per_trade


@dataclass
class BacktestResult:
    strategy_name: str
    start: pd.Timestamp
    end: pd.Timestamp
    initial_cash: float
    equity_curve: pd.Series  # daily close marks, index = session dates
    fills: list[Fill]
    trades: list[Trade]
    metrics: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1])


class BacktestEngine:
    def __init__(self, provider: DataProvider, costs: CostsConfig, initial_cash: float):
        self.provider = provider
        self.cost_model = CostModel(costs)
        self.initial_cash = initial_cash

    def run(
        self,
        strategy: Strategy,
        symbols: list[str],
        start: datetime,
        end: datetime,
        calendar_symbol: str | None = None,
    ) -> BacktestResult:
        calendar_symbol = calendar_symbol or symbols[0]
        bars: dict[str, pd.DataFrame] = {}
        for symbol in dict.fromkeys([calendar_symbol, *symbols]):
            bars[symbol] = normalize_bars(self.provider.daily_bars(symbol, start, end))
        calendar = bars[calendar_symbol].index
        if len(calendar) < 2:
            raise ValueError(
                f"calendar symbol {calendar_symbol} has {len(calendar)} sessions "
                f"in [{start}..{end}]; need at least 2"
            )

        portfolio = Portfolio(self.initial_cash)
        equity: dict[pd.Timestamp, float] = {}
        fills: list[Fill] = []
        trades: list[Trade] = []
        warnings: list[str] = []
        pending: list[Order] = []

        for session in calendar:
            # 1) Fill orders decided after the PREVIOUS close, at today's open.
            for order in pending:
                fill_or_warning = self._execute(order, session, bars, portfolio)
                if isinstance(fill_or_warning, str):
                    warnings.append(fill_or_warning)
                    continue
                fills.append(fill_or_warning)
                if fill_or_warning.side == "buy":
                    portfolio._apply_buy(fill_or_warning)
                else:
                    trades.extend(portfolio._apply_sell(fill_or_warning))
            pending = []

            # 2) Mark the book at today's close.
            marks: dict[str, float] = {}
            for symbol in portfolio.held_symbols:
                sym_bars = bars[symbol]
                marked = sym_bars.loc[sym_bars.index <= session]
                marks[symbol] = float(marked["close"].iloc[-1])
            equity[session] = portfolio.equity(marks)

            # 3) Let the strategy react to today's close — through a pinned
            #    view only. Orders fill tomorrow.
            pin = datetime.combine(session.date(), time(16, 0), tzinfo=ET)
            view = AsOfView(self.provider, pin)
            pending = list(strategy.on_session(session, view, portfolio))

        if pending:
            warnings.append(
                f"{len(pending)} order(s) from the final session never filled "
                "(no next session) and were discarded"
            )

        curve = pd.Series(equity).sort_index()
        from watchman.backtest.metrics import performance_metrics, trade_metrics

        result = BacktestResult(
            strategy_name=strategy.name,
            start=calendar[0],
            end=calendar[-1],
            initial_cash=self.initial_cash,
            equity_curve=curve,
            fills=fills,
            trades=trades,
            warnings=warnings,
        )
        result.metrics = {
            **performance_metrics(curve),
            **trade_metrics(trades),
        }
        from watchman.backtest.metrics import honesty_flags

        result.warnings.extend(honesty_flags(result.metrics))
        return result

    def _execute(
        self,
        order: Order,
        session: pd.Timestamp,
        bars: dict[str, pd.DataFrame],
        portfolio: Portfolio,
    ) -> Fill | str:
        sym_bars = bars.get(order.symbol)
        if sym_bars is None or session not in sym_bars.index:
            return f"{session.date()}: no bar for {order.symbol}, {order.side} rejected"
        reference = float(sym_bars.loc[session, "open"])
        if order.side == "buy":
            price = self.cost_model.buy_price(reference)
            cost = order.qty * price + self.cost_model.commission
            if cost > portfolio.cash + 1e-9:
                return (
                    f"{session.date()}: buy {order.qty} {order.symbol} needs "
                    f"${cost:,.2f}, cash ${portfolio.cash:,.2f} — rejected (no leverage)"
                )
        else:
            held = portfolio.qty(order.symbol)
            if order.qty > held:
                return (
                    f"{session.date()}: sell {order.qty} {order.symbol} exceeds "
                    f"held {held} — rejected (no shorting in engine v1)"
                )
            price = self.cost_model.sell_price(reference)
        return Fill(
            date=session,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            reference_price=reference,
            price=price,
            commission=self.cost_model.commission,
        )


def session_close(session: pd.Timestamp) -> datetime:
    """The pinned moment a strategy reacting to `session` may know about."""
    return datetime.combine(session.date(), time(16, 0), tzinfo=ET)


def pad_start(start: datetime, lookback_days: int) -> datetime:
    """Warm-up padding so indicators have history before the first session."""
    return start - timedelta(days=lookback_days)
