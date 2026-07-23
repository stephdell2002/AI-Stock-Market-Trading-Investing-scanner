"""SQLite-backed caches for bars, fundamentals, and financial statements,
plus CachedProvider — a DataProvider that composes them.

Bar strategy: track the requested date range we have already fetched per symbol
(bar_coverage). A request fully inside covered range is served locally; anything
else triggers one provider fetch for the union range, which replaces the cached
rows. Fundamentals/statements are cached whole with a max-age. Simple, correct,
and avoids hammering the free API on every run.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, datetime, time, timedelta
from io import StringIO

import pandas as pd

from watchman.data.provider import (
    BAR_COLUMNS,
    DataProvider,
    Freshness,
    Fundamentals,
    StatementSet,
    normalize_bars,
)


def _iso(d: date) -> str:
    return d.isoformat()


class CachedBars:
    """Wraps a DataProvider's daily_bars with a persistent SQLite cache."""

    def __init__(self, provider: DataProvider, conn: sqlite3.Connection):
        self.provider = provider
        self.conn = conn

    def coverage(self, symbol: str) -> tuple[date, date] | None:
        row = self.conn.execute(
            "SELECT start_date, end_date FROM bar_coverage WHERE symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row[0]), date.fromisoformat(row[1])

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        symbol = symbol.strip().upper()
        req_start, req_end = start.date(), end.date()
        cov = self.coverage(symbol)
        if cov is None or req_start < cov[0] or req_end > cov[1]:
            self._refetch(symbol, req_start, req_end, cov)
        return self._read(symbol, req_start, req_end)

    def _refetch(
        self, symbol: str, req_start: date, req_end: date, cov: tuple[date, date] | None
    ) -> None:
        fetch_start = req_start if cov is None else min(req_start, cov[0])
        fetch_end = req_end if cov is None else max(req_end, cov[1])
        bars = normalize_bars(
            self.provider.daily_bars(
                symbol,
                datetime.combine(fetch_start, time(0, 0)),
                datetime.combine(fetch_end, time(0, 0)),
            )
        )
        with self.conn:
            self.conn.execute("DELETE FROM daily_bars WHERE symbol = ?", (symbol,))
            self.conn.executemany(
                "INSERT OR REPLACE INTO daily_bars "
                "(symbol, date, open, high, low, close, adj_close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        symbol,
                        _iso(idx.date()),
                        float(row.open),
                        float(row.high),
                        float(row.low),
                        float(row.close),
                        float(row.adj_close),
                        float(row.volume),
                    )
                    for idx, row in bars.iterrows()
                ],
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO bar_coverage (symbol, start_date, end_date, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (symbol, _iso(fetch_start), _iso(fetch_end), datetime.now().isoformat()),
            )

    def _read(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, adj_close, volume FROM daily_bars "
            "WHERE symbol = ? AND date >= ? AND date <= ? ORDER BY date",
            (symbol, _iso(start), _iso(end)),
        ).fetchall()
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], name="date"))
        df = pd.DataFrame(rows, columns=["date", *BAR_COLUMNS])
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")

    def stale_days(self, symbol: str) -> int | None:
        """How many days behind today the cached coverage ends, or None if uncached."""
        cov = self.coverage(symbol)
        if cov is None:
            return None
        return max(0, (date.today() - cov[1]) // timedelta(days=1))


def _df_to_json(df: pd.DataFrame) -> str:
    return df.to_json(orient="split", date_format="iso")


def _df_from_json(text: str) -> pd.DataFrame:
    df = pd.read_json(StringIO(text), orient="split")
    if len(df.columns):
        # Statement columns are fiscal-period dates; non-date columns stay as-is.
        with contextlib.suppress(ValueError, TypeError):
            df.columns = pd.to_datetime(df.columns)
    return df


class FundamentalsCache:
    """Whole-object cache for Fundamentals with a max-age policy. The cached
    fetched_at is preserved, so AsOfView's honesty checks still see the true
    fetch time, not the cache-read time."""

    def __init__(self, provider: DataProvider, conn: sqlite3.Connection, max_age: timedelta):
        self.provider = provider
        self.conn = conn
        self.max_age = max_age

    def get_cached(self, symbol: str) -> Fundamentals | None:
        row = self.conn.execute(
            "SELECT fetched_at, payload FROM fundamentals_cache WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        if datetime.now() - datetime.fromisoformat(row[0]) > self.max_age:
            return None
        return Fundamentals.model_validate_json(row[1])

    def get_or_fetch(self, symbol: str) -> Fundamentals:
        symbol = symbol.strip().upper()
        cached = self.get_cached(symbol)
        if cached is not None:
            return cached
        fresh = self.provider.fundamentals(symbol)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO fundamentals_cache (symbol, fetched_at, payload) "
                "VALUES (?, ?, ?)",
                (symbol, fresh.fetched_at.isoformat(), fresh.model_dump_json()),
            )
        return fresh


class StatementsCache:
    """Whole-object cache for StatementSet with a max-age policy."""

    def __init__(self, provider: DataProvider, conn: sqlite3.Connection, max_age: timedelta):
        self.provider = provider
        self.conn = conn
        self.max_age = max_age

    def get_cached(self, symbol: str) -> StatementSet | None:
        row = self.conn.execute(
            "SELECT fetched_at, income, balance, cashflow FROM statements_cache "
            "WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row[0])
        if datetime.now() - fetched_at > self.max_age:
            return None
        return StatementSet(
            symbol=symbol.strip().upper(),
            income=_df_from_json(row[1]),
            balance=_df_from_json(row[2]),
            cashflow=_df_from_json(row[3]),
            fetched_at=fetched_at,
            point_in_time=False,
        )

    def get_or_fetch(self, symbol: str) -> StatementSet:
        symbol = symbol.strip().upper()
        cached = self.get_cached(symbol)
        if cached is not None:
            return cached
        fresh = self.provider.financial_statements(symbol)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO statements_cache "
                "(symbol, fetched_at, income, balance, cashflow) VALUES (?, ?, ?, ?, ?)",
                (
                    symbol,
                    fresh.fetched_at.isoformat(),
                    _df_to_json(fresh.income),
                    _df_to_json(fresh.balance),
                    _df_to_json(fresh.cashflow),
                ),
            )
        return fresh


class CachedProvider(DataProvider):
    """A DataProvider that serves everything through the SQLite caches.

    This is what live code hands to AsOfView: same interface, same honesty
    labels (freshness and point_in_time pass through from the inner provider),
    but repeated runs don't re-hit the free API.
    """

    def __init__(
        self,
        inner: DataProvider,
        conn: sqlite3.Connection,
        fundamentals_max_age: timedelta = timedelta(days=3),
    ):
        self.inner = inner
        self.name = f"{inner.name}+cache"
        self._bars = CachedBars(inner, conn)
        self._fundamentals = FundamentalsCache(inner, conn, fundamentals_max_age)
        self._statements = StatementsCache(inner, conn, fundamentals_max_age)

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return self._bars.daily_bars(symbol, start, end)

    def fundamentals(self, symbol: str) -> Fundamentals:
        return self._fundamentals.get_or_fetch(symbol)

    def financial_statements(self, symbol: str) -> StatementSet:
        return self._statements.get_or_fetch(symbol)

    def intraday_bars(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        # Intraday data is too perishable to cache usefully: pass through.
        return self.inner.intraday_bars(
            symbol, interval=interval, days=days, include_premarket=include_premarket
        )

    def news(self, symbol: str):
        return self.inner.news(symbol)

    def ipo_calendar(self, start, end):
        # Calendar is small and time-sensitive: pass through, don't cache.
        return self.inner.ipo_calendar(start, end)

    def quote_freshness(self) -> Freshness:
        return self.inner.quote_freshness()
