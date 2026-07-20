"""YFinanceProvider unit tests against mocked yfinance responses.

The sandbox that builds Watchman has no market-data network access, so these
tests pin the *normalization contract*: whatever shape yfinance returns, the
provider must emit canonical bars and honestly-labeled fundamentals.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pandas as pd
import pytest
from tests.conftest import make_bars

from watchman.data.provider import BAR_COLUMNS, Freshness
from watchman.data.yfinance_provider import YFinanceProvider


class FakeTicker:
    """Mimics yfinance.Ticker closely enough for the provider's usage."""

    raw_history: ClassVar[pd.DataFrame] = pd.DataFrame()
    raw_info: ClassVar[dict] = {}
    last_history_kwargs: ClassVar[dict] = {}

    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, **kwargs):
        FakeTicker.last_history_kwargs = kwargs
        return FakeTicker.raw_history

    @property
    def info(self):
        return FakeTicker.raw_info


class FakeYF:
    Ticker = FakeTicker


@pytest.fixture
def provider(monkeypatch):
    p = YFinanceProvider.__new__(YFinanceProvider)
    p._yf = FakeYF
    return p


def yf_style_history(start: str, end: str) -> pd.DataFrame:
    """Bars shaped like real yfinance output: capitalized columns,
    tz-aware New York index, Dividends/Stock Splits columns."""
    bars = make_bars(start, end)
    out = pd.DataFrame(
        {
            "Open": bars["open"].values,
            "High": bars["high"].values,
            "Low": bars["low"].values,
            "Close": bars["close"].values,
            "Adj Close": bars["adj_close"].values,
            "Volume": bars["volume"].values,
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=bars.index.tz_localize("America/New_York"),
    )
    return out


def test_daily_bars_normalized_to_canonical_shape(provider):
    FakeTicker.raw_history = yf_style_history("2024-01-02", "2024-01-31")
    got = provider.daily_bars("AAPL", datetime(2024, 1, 2), datetime(2024, 1, 31))
    assert list(got.columns) == BAR_COLUMNS
    assert got.index.tz is None
    assert got.index.is_monotonic_increasing
    assert "dividends" not in got.columns


def test_yfinance_exclusive_end_is_compensated(provider):
    """Our contract is [start, end] inclusive; yfinance treats end as
    exclusive, so the provider must ask for end + 1 day."""
    FakeTicker.raw_history = yf_style_history("2024-01-02", "2024-01-31")
    provider.daily_bars("AAPL", datetime(2024, 1, 2), datetime(2024, 1, 31))
    assert FakeTicker.last_history_kwargs["end"] == "2024-02-01"
    assert FakeTicker.last_history_kwargs["auto_adjust"] is False


def test_missing_adj_close_falls_back_to_close(provider):
    raw = yf_style_history("2024-01-02", "2024-01-10").drop(columns=["Adj Close"])
    FakeTicker.raw_history = raw
    got = provider.daily_bars("AAPL", datetime(2024, 1, 2), datetime(2024, 1, 10))
    assert got["adj_close"].tolist() == got["close"].tolist()


def test_empty_history_raises_instead_of_returning_garbage(provider):
    FakeTicker.raw_history = pd.DataFrame()
    with pytest.raises(ValueError, match="no daily bars"):
        provider.daily_bars("NOPE", datetime(2024, 1, 2), datetime(2024, 1, 31))


def test_fundamentals_labeled_not_point_in_time(provider):
    FakeTicker.raw_info = {
        "sector": "Technology",
        "marketCap": 3_000_000_000_000,
        "trailingPE": 30.5,
        "grossMargins": 0.44,
        "totalDebt": 100_000_000_000,
    }
    f = provider.fundamentals("aapl")
    assert f.symbol == "AAPL"
    assert f.point_in_time is False  # yfinance = latest snapshot only, never PIT
    assert f.market_cap == 3_000_000_000_000
    assert f.gross_margin == 0.44
    assert f.raw["sector"] == "Technology"


def test_fundamentals_tolerates_junk_values(provider):
    FakeTicker.raw_info = {"marketCap": "Infinity", "trailingPE": None, "ebitda": True}
    f = provider.fundamentals("AAPL")
    assert f.market_cap is None
    assert f.trailing_pe is None
    assert f.ebitda is None  # bool is not a number


def test_freshness_is_eod(provider):
    assert provider.quote_freshness() == Freshness.EOD


class TestFinancialStatements:
    def test_statements_labeled_not_point_in_time(self, provider):
        stmt_df = pd.DataFrame(
            {pd.Timestamp("2025-12-31"): [100.0], pd.Timestamp("2024-12-31"): [90.0]},
            index=["Total Revenue"],
        )
        FakeTicker.income_stmt = stmt_df
        FakeTicker.balance_sheet = stmt_df
        FakeTicker.cashflow = stmt_df
        got = provider.financial_statements("aapl")
        assert got.symbol == "AAPL"
        assert got.point_in_time is False
        assert "Total Revenue" in got.income.index

    def test_broken_statement_comes_back_empty_not_crashing(self, provider):
        class Exploding:
            def __get__(self, obj, objtype=None):
                raise RuntimeError("yfinance parse error")

        FakeTicker.income_stmt = pd.DataFrame({"a": [1.0]})
        FakeTicker.balance_sheet = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        FakeTicker.cashflow = pd.DataFrame()
        got = provider.financial_statements("AAPL")
        assert not got.income.empty
        assert got.balance.empty  # failed statement -> empty, metrics say None
        assert got.cashflow.empty
