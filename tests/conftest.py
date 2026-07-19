"""Shared fixtures. All tests run offline: no fixture may touch the network."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from watchman.data.provider import DataProvider, Freshness, Fundamentals


def make_bars(start: str, end: str, base_price: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic daily bars on business days.

    close(day i) = base_price + i, so any test can compute the expected value
    for a given date by counting business days. Volume = 1e6 + 1000*i.
    """
    dates = pd.bdate_range(start, end)
    i = np.arange(len(dates), dtype=float)
    close = base_price + i
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "adj_close": close * 0.99,
            "volume": 1_000_000 + 1000 * i,
        },
        index=dates,
    )


class SyntheticProvider(DataProvider):
    """In-memory provider with a fixed history; records every call it serves."""

    name = "synthetic"

    def __init__(
        self,
        bars: pd.DataFrame,
        fundamentals: Fundamentals | None = None,
        freshness: Freshness = Freshness.EOD,
    ):
        self._bars = bars
        self._fundamentals = fundamentals
        self._freshness = freshness
        self.calls: list[tuple[str, datetime, datetime]] = []

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.calls.append((symbol, start, end))
        mask = (self._bars.index >= pd.Timestamp(start.date())) & (
            self._bars.index <= pd.Timestamp(end.date())
        )
        return self._bars.loc[mask]

    def fundamentals(self, symbol: str) -> Fundamentals:
        if self._fundamentals is None:
            raise ValueError("no fundamentals configured")
        return self._fundamentals

    def quote_freshness(self) -> Freshness:
        return self._freshness


@pytest.fixture
def q1_bars() -> pd.DataFrame:
    return make_bars("2024-01-02", "2024-03-28")


@pytest.fixture
def synthetic_provider(q1_bars: pd.DataFrame) -> SyntheticProvider:
    return SyntheticProvider(q1_bars)
