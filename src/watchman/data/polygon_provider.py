"""Polygon-backed DataProvider (https://polygon.io).

Strengths: a clean aggregate-bars API covering daily and intraday, including
extended hours. The free tier is end-of-day / delayed; paid tiers add 15-minute
or real-time data. Freshness is configured (default DELAYED), never assumed.

Polygon's fundamentals coverage is thin, so fundamentals() and
financial_statements() are intentionally not implemented here — the provider
factory composes Polygon bars with yfinance fundamentals for the screener.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from watchman.data.http import HttpError, get_json
from watchman.data.provider import (
    DataProvider,
    Freshness,
    NewsItem,
    normalize_bars,
    normalize_intraday_bars,
)
from watchman.data.rest_common import (
    check_interval,
    regular_session_only,
    resolve_freshness,
)

BASE = "https://api.polygon.io"

# Watchman interval -> Polygon (multiplier, timespan).
_TIMESPAN = {
    "1m": (1, "minute"), "5m": (5, "minute"), "15m": (15, "minute"),
    "30m": (30, "minute"), "60m": (1, "hour"),
}


class PolygonProvider(DataProvider):
    name = "polygon"

    def __init__(self, api_key: str, freshness: str | None = None):
        if not api_key:
            raise ValueError("PolygonProvider requires an API key (WATCHMAN_POLYGON_KEY)")
        self._key = api_key
        self._freshness = resolve_freshness(freshness)

    def _aggs(self, symbol: str, mult: int, span: str, start: str, end: str) -> pd.DataFrame:
        url = (
            f"{BASE}/v2/aggs/ticker/{symbol.strip().upper()}/range/"
            f"{mult}/{span}/{start}/{end}"
        )
        data = get_json(
            url, {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": self._key}
        )
        results = (data or {}).get("results")
        if not results:
            raise ValueError(
                f"polygon returned no {mult}{span} bars for {symbol!r} ({start}..{end})"
            )
        frame = pd.DataFrame(results)
        # Set the index on `frame` first so the column Series share it — building
        # a new DataFrame from Series that carry a different index realigns to NaN.
        frame.index = pd.to_datetime(frame["t"], unit="ms", utc=True)
        return pd.DataFrame(
            {"open": frame["o"], "high": frame["h"], "low": frame["l"],
             "close": frame["c"], "volume": frame["v"]},
        )

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        frame = self._aggs(
            symbol, 1, "day", start.date().isoformat(), end.date().isoformat()
        )
        return normalize_bars(frame)

    def intraday_bars(
        self, symbol: str, interval: str = "5m", days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        check_interval(interval)
        mult, span = _TIMESPAN[interval]
        end = datetime.now(tz=timezone.utc).date()
        start = end - timedelta(days=max(days, 1) + 3)
        frame = self._aggs(symbol, mult, span, start.isoformat(), end.isoformat())
        bars = normalize_intraday_bars(frame)
        return bars if include_premarket else regular_session_only(bars)

    def fundamentals(self, symbol: str):
        # Polygon's fundamentals coverage is thin and awkward; the provider
        # factory composes yfinance fundamentals instead. Declaring this
        # NotImplementedError is what triggers that fallback.
        raise NotImplementedError(
            "PolygonProvider does not serve fundamentals; use the factory, which "
            "composes yfinance fundamentals with Polygon bars"
        )

    def news(self, symbol: str) -> list[NewsItem]:
        try:
            data = get_json(
                f"{BASE}/v2/reference/news",
                {"ticker": symbol.strip().upper(), "limit": 20, "apiKey": self._key},
            ) or {}
        except HttpError:
            return []
        out: list[NewsItem] = []
        for item in data.get("results", []) or []:
            published = item.get("published_utc")
            parsed = _parse_utc(published)
            if parsed is None:
                continue
            publisher = item.get("publisher") or {}
            out.append(
                NewsItem(
                    symbol=symbol.strip().upper(),
                    title=str(item.get("title", "")),
                    published_at=parsed,
                    source=str(publisher.get("name", "")),
                )
            )
        out.sort(key=lambda n: n.published_at, reverse=True)
        return out

    def quote_freshness(self) -> Freshness:
        return self._freshness


def _parse_utc(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
