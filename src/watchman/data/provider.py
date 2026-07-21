"""DataProvider interface and the point-in-time access discipline.

The honesty cornerstone of Watchman's data layer is AsOfView: strategy,
screener, and backtest code never touches a DataProvider directly. It receives
an AsOfView pinned to a moment in time, which physically cannot serve data from
after that moment. The lookahead canary test in tests/test_lookahead_canary.py
proves this behavior and must never be deleted or skipped.

Conventions
-----------
- Symbols are yfinance-style uppercase (BRK-B, not BRK.B).
- Daily bars are a pandas DataFrame with columns
  [open, high, low, close, adj_close, volume] indexed by tz-naive normalized
  Timestamps representing the US trading date.
- A daily bar for date D does not exist until that session closes: an AsOfView
  pinned intraday on D excludes D's bar.
- Timestamps passed as naive datetimes are interpreted as US/Eastern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
PREMARKET_OPEN = time(4, 0)

BAR_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
INTRADAY_COLUMNS = ["open", "high", "low", "close", "volume"]

#: Supported intraday intervals -> bar length. A bar is complete (and thus
#: visible through AsOfView) only once its start + length has passed.
INTRADAY_INTERVALS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "60m": timedelta(minutes=60),
}


class LookaheadError(RuntimeError):
    """Raised when code asks for data that did not exist at the pinned time."""


class Freshness(StrEnum):
    """How current a provider's 'now' data is. Signals inherit this label:
    anything not REALTIME is stamped DELAYED — NOT ACTIONABLE by Module B."""

    REALTIME = "REALTIME"
    DELAYED = "DELAYED"
    EOD = "EOD"


class Fundamentals(BaseModel):
    """Fundamental snapshot for one symbol.

    point_in_time=False means the provider only serves the LATEST snapshot
    (yfinance works this way). Such data is only honest for decisions made
    near fetch time — AsOfView refuses to serve it for historical timestamps.
    When a provider gives true point-in-time data, available_at holds the
    moment the figures became publicly known.
    """

    symbol: str
    fetched_at: datetime
    point_in_time: bool = False
    available_at: datetime | None = None

    sector: str | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    revenue_growth: float | None = None
    total_debt: float | None = None
    ebitda: float | None = None
    free_cash_flow: float | None = None
    shares_outstanding: float | None = None
    float_shares: float | None = None
    enterprise_value: float | None = None
    ev_to_ebitda: float | None = None
    operating_cashflow: float | None = None
    trailing_eps: float | None = None
    total_revenue: float | None = None
    net_income: float | None = None

    raw: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class NewsItem:
    """A headline with the moment it became public knowledge."""

    symbol: str
    title: str
    published_at: datetime  # tz-aware
    source: str = ""


@dataclass
class StatementSet:
    """Annual financial statements for one symbol.

    DataFrames follow the yfinance convention: rows = line items
    ("Total Revenue", "Stockholders Equity", ...), columns = fiscal period end
    dates. Free sources serve statements *as currently restated*, not as
    originally reported, and only ~4 fiscal years — point_in_time stays False
    and AsOfView refuses to serve these for historical timestamps.
    """

    symbol: str
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    fetched_at: datetime
    point_in_time: bool = False


class DataProvider(ABC):
    """Abstract market-data source. Implementations: yfinance (EOD) now;
    Polygon/Finnhub/FMP slots later via env keys."""

    name: str = "abstract"

    @abstractmethod
    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Daily OHLCV for [start, end] inclusive, in the canonical bar format."""

    @abstractmethod
    def fundamentals(self, symbol: str) -> Fundamentals:
        """Latest known fundamentals for the symbol."""

    def financial_statements(self, symbol: str) -> StatementSet:
        """Annual income/balance/cashflow statements (latest restatement)."""
        raise NotImplementedError(f"{self.name} does not serve financial statements")

    def intraday_bars(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        """Intraday OHLCV in canonical form (see normalize_intraday_bars)."""
        raise NotImplementedError(f"{self.name} does not serve intraday bars")

    def news(self, symbol: str) -> list[NewsItem]:
        """Recent headlines with publish timestamps, newest first."""
        raise NotImplementedError(f"{self.name} does not serve news")

    @abstractmethod
    def quote_freshness(self) -> Freshness:
        """Best freshness this provider can promise for 'current' data."""


def as_eastern(ts: datetime) -> datetime:
    """Normalize a datetime to US/Eastern; naive input is assumed Eastern."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def normalize_intraday_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical intraday shape: [open, high, low, close, volume], tz-aware
    US/Eastern DatetimeIndex named `timestamp` (bar START times), ascending.
    A naive index is assumed to already be Eastern."""
    out = df.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    out = out[INTRADAY_COLUMNS]
    idx = pd.to_datetime(out.index)
    idx = idx.tz_localize(ET) if idx.tz is None else idx.tz_convert(ET)
    out.index = idx
    out.index.name = "timestamp"
    return out.sort_index()


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a bar frame to the canonical shape: lowercase columns
    [open, high, low, close, adj_close, volume], tz-naive normalized index,
    sorted ascending. Missing adj_close falls back to close."""
    out = df.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    if "adj_close" not in out.columns:
        out["adj_close"] = out["close"]
    out = out[BAR_COLUMNS]
    idx = pd.to_datetime(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert(ET).tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    return out.sort_index()


class AsOfView:
    """A window onto a DataProvider pinned to a single moment (`as_of`).

    Every read is clipped to what was knowable at that moment. This is the ONLY
    interface strategy/screener/backtest code may use to reach market data.
    """

    #: Fundamentals lacking availability info are served only if the view is
    #: pinned within this distance of fetch time ("live" use).
    LIVE_TOLERANCE = timedelta(days=1)

    def __init__(self, provider: DataProvider, as_of: datetime):
        self._provider = provider
        self.as_of = as_eastern(as_of)

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def quote_freshness(self) -> Freshness:
        return self._provider.quote_freshness()

    def last_complete_session(self) -> pd.Timestamp:
        """Latest calendar date whose daily bar may be visible from this view.

        The bar for date D exists only once D's session has closed, so an
        intraday view on D sees at most D-1. (Weekends/holidays simply have no
        bar; this is an upper bound, not a trading-day calendar.)
        """
        d = self.as_of.date()
        if self.as_of.timetz() < MARKET_CLOSE.replace(tzinfo=ET):
            d = d - timedelta(days=1)
        return pd.Timestamp(d)

    def daily_bars(
        self,
        symbol: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Daily bars clipped so nothing after `as_of` is visible.

        Requesting an end beyond the pinned moment is not an error — the
        request is clipped, mirroring what a live query would have returned.
        """
        cutoff = self.last_complete_session()
        start_ts = (
            pd.Timestamp(2000, 1, 1)
            if start is None
            else pd.Timestamp(as_eastern(start).date())
        )
        end_ts = cutoff if end is None else min(pd.Timestamp(as_eastern(end).date()), cutoff)
        if end_ts < start_ts:
            return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], name="date"))
        bars = normalize_bars(
            self._provider.daily_bars(
                symbol,
                datetime.combine(start_ts.date(), time(0, 0)),
                datetime.combine(end_ts.date(), time(0, 0)),
            )
        )
        # Belt and suspenders: clip again regardless of what the provider returned.
        return bars.loc[bars.index <= cutoff]

    def fundamentals(self, symbol: str) -> Fundamentals:
        """Fundamentals as knowable at `as_of`, or LookaheadError if the
        provider cannot honestly answer for this moment."""
        f = self._provider.fundamentals(symbol)
        if f.available_at is not None:
            if as_eastern(f.available_at) > self.as_of:
                raise LookaheadError(
                    f"{symbol}: fundamentals became available {f.available_at}, "
                    f"after the pinned time {self.as_of}"
                )
            return f
        if not f.point_in_time:
            fetched = as_eastern(f.fetched_at)
            if fetched - self.as_of > self.LIVE_TOLERANCE:
                raise LookaheadError(
                    f"{symbol}: provider '{self._provider.name}' only serves latest-snapshot "
                    f"fundamentals (point_in_time=False); serving them for the historical "
                    f"time {self.as_of} would be lookahead bias. Use a point-in-time "
                    f"fundamentals source for backtests."
                )
            return f
        return f

    def financial_statements(self, symbol: str) -> StatementSet:
        """Statements as knowable at `as_of`. Free sources are not
        point-in-time (latest restatement only), so historical views refuse
        them the same way fundamentals are refused."""
        s = self._provider.financial_statements(symbol)
        if not s.point_in_time:
            fetched = as_eastern(s.fetched_at)
            if fetched - self.as_of > self.LIVE_TOLERANCE:
                raise LookaheadError(
                    f"{symbol}: provider '{self._provider.name}' serves latest-restatement "
                    f"financial statements (point_in_time=False); using them at the "
                    f"historical time {self.as_of} would be lookahead bias."
                )
        return s

    def intraday_bars(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        """Intraday bars COMPLETE as of the pinned moment.

        A bar starting at T with interval L exists only once T+L has passed;
        the in-progress bar is invisible, exactly like today's daily bar
        before the close. Clipped regardless of what the provider returns.
        """
        if interval not in INTRADAY_INTERVALS:
            raise ValueError(
                f"unsupported interval {interval!r}; use one of {sorted(INTRADAY_INTERVALS)}"
            )
        bar_len = INTRADAY_INTERVALS[interval]
        bars = normalize_intraday_bars(
            self._provider.intraday_bars(
                symbol, interval=interval, days=days, include_premarket=include_premarket
            )
        )
        return bars.loc[bars.index + bar_len <= self.as_of]

    def news(self, symbol: str) -> list[NewsItem]:
        """Headlines published at or before the pinned moment; items without
        a usable timestamp are dropped rather than trusted."""
        items = self._provider.news(symbol)
        visible: list[NewsItem] = []
        for item in items:
            if item.published_at is None or item.published_at.tzinfo is None:
                continue
            if item.published_at.astimezone(ET) <= self.as_of:
                visible.append(item)
        return visible
