"""Intraday synthetic data builders for Module B tests. Offline always."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from watchman.data.provider import ET, DataProvider, Freshness, Fundamentals, NewsItem


def intraday_index(
    day: date_type, start: time, bars: int, interval_minutes: int
) -> pd.DatetimeIndex:
    first = datetime.combine(day, start, tzinfo=ET)
    return pd.DatetimeIndex(
        [first + timedelta(minutes=interval_minutes * i) for i in range(bars)],
        name="timestamp",
    )


def bars_from_closes(
    closes: list[float],
    day: date_type,
    start: time = time(9, 30),
    interval_minutes: int = 5,
    volume: float | list[float] = 50_000.0,
    spread: float = 0.2,
) -> pd.DataFrame:
    """Bars where open=previous close (first open = first close), high/low
    wrap the body by `spread`. Deterministic and hand-checkable."""
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    highs = np.maximum(opens, closes_arr) + spread
    lows = np.minimum(opens, closes_arr) - spread
    vols = (
        np.full(len(closes_arr), float(volume))
        if np.isscalar(volume)
        else np.asarray(volume, dtype=float)
    )
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes_arr, "volume": vols},
        index=intraday_index(day, start, len(closes_arr), interval_minutes),
    )


def daily_history(
    prev_close: float, days: int = 60, avg_volume: float = 5_000_000.0
) -> pd.DataFrame:
    """Flat daily history ending yesterday with a known close and volume."""
    end = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
    dates = pd.bdate_range(end=end, periods=days)
    return pd.DataFrame(
        {
            "open": prev_close, "high": prev_close + 1.0, "low": prev_close - 1.0,
            "close": prev_close, "adj_close": prev_close, "volume": avg_volume,
        },
        index=dates,
    )


class IntradayProvider(DataProvider):
    """Serves configured daily + intraday bars, fundamentals, and news."""

    name = "intraday-synthetic"

    def __init__(self, companies: dict[str, dict]):
        """companies: symbol -> dict with optional keys
        daily, intraday (regular), premarket (concat'd), fundamentals, news."""
        self.companies = companies

    def daily_bars(self, symbol, start, end):
        df = self.companies[symbol]["daily"]
        mask = (df.index >= pd.Timestamp(start.date())) & (df.index <= pd.Timestamp(end.date()))
        return df.loc[mask]

    def intraday_bars(self, symbol, interval="5m", days=1, include_premarket=False):
        entry = self.companies[symbol]
        frames = []
        if include_premarket and "premarket" in entry:
            frames.append(entry["premarket"])
        if "intraday" in entry:
            frames.append(entry["intraday"])
        if not frames:
            raise ValueError(f"no intraday bars for {symbol}")
        return pd.concat(frames).sort_index()

    def fundamentals(self, symbol) -> Fundamentals:
        f = self.companies[symbol].get("fundamentals")
        if f is None:
            raise ValueError(f"no fundamentals for {symbol}")
        return f

    def news(self, symbol) -> list[NewsItem]:
        items = self.companies[symbol].get("news")
        if items is None:
            raise NotImplementedError("no news configured")
        return items

    def quote_freshness(self) -> Freshness:
        return Freshness.DELAYED
