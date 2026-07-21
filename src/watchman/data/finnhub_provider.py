"""Finnhub-backed DataProvider (https://finnhub.io).

Strengths: real-time US quotes and company news on the free tier. Intraday
stock candles moved to Finnhub's paid plans in 2024 — on a free key the candle
calls return an access error, which surfaces honestly as a per-symbol failure
rather than silently faking bars.

Freshness is configured, not assumed: it defaults to DELAYED and the user sets
REALTIME only if their subscription genuinely delivers it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from watchman.data.http import HttpError, get_json
from watchman.data.provider import (
    DataProvider,
    Freshness,
    Fundamentals,
    NewsItem,
    normalize_bars,
    normalize_intraday_bars,
)
from watchman.data.rest_common import (
    check_interval,
    regular_session_only,
    resolve_freshness,
)

BASE = "https://finnhub.io/api/v1"

# Watchman interval -> Finnhub candle resolution.
_RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60"}


class FinnhubProvider(DataProvider):
    name = "finnhub"

    def __init__(self, api_key: str, freshness: str | None = None):
        if not api_key:
            raise ValueError("FinnhubProvider requires an API key (WATCHMAN_FINNHUB_KEY)")
        self._key = api_key
        self._freshness = resolve_freshness(freshness)

    def _candles(self, symbol: str, resolution: str, start: datetime, end: datetime):
        data = get_json(
            f"{BASE}/stock/candle",
            {
                "symbol": symbol.strip().upper(),
                "resolution": resolution,
                "from": int(start.replace(tzinfo=UTC).timestamp()),
                "to": int(end.replace(tzinfo=UTC).timestamp()),
                "token": self._key,
            },
        )
        if not data or data.get("s") == "no_data":
            raise ValueError(f"finnhub returned no candles for {symbol!r}")
        if data.get("s") != "ok":
            raise ValueError(f"finnhub candle error for {symbol!r}: status={data.get('s')}")
        return data

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        data = self._candles(symbol, "D", start, end + timedelta(days=1))
        frame = pd.DataFrame(
            {
                "open": data["o"], "high": data["h"], "low": data["l"],
                "close": data["c"], "volume": data["v"],
            },
            index=pd.to_datetime(data["t"], unit="s", utc=True),
        )
        return normalize_bars(frame)

    def intraday_bars(
        self, symbol: str, interval: str = "5m", days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        check_interval(interval)
        end = datetime.now(tz=UTC)
        start = end - timedelta(days=max(days, 1) + 1)
        data = self._candles(symbol, _RESOLUTION[interval], start, end)
        frame = pd.DataFrame(
            {
                "open": data["o"], "high": data["h"], "low": data["l"],
                "close": data["c"], "volume": data["v"],
            },
            index=pd.to_datetime(data["t"], unit="s", utc=True),
        )
        bars = normalize_intraday_bars(frame)
        return bars if include_premarket else regular_session_only(bars)

    def fundamentals(self, symbol: str) -> Fundamentals:
        symbol = symbol.strip().upper()
        metric = get_json(
            f"{BASE}/stock/metric",
            {"symbol": symbol, "metric": "all", "token": self._key},
        ) or {}
        m = metric.get("metric", {}) or {}
        profile = get_json(
            f"{BASE}/stock/profile2", {"symbol": symbol, "token": self._key}
        ) or {}

        def num(*keys: str) -> float | None:
            for key in keys:
                v = m.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
            return None

        market_cap = profile.get("marketCapitalization")
        market_cap = float(market_cap) * 1e6 if isinstance(market_cap, (int, float)) else None
        return Fundamentals(
            symbol=symbol,
            fetched_at=datetime.now(),
            point_in_time=False,
            sector=profile.get("finnhubIndustry"),
            market_cap=market_cap,
            trailing_pe=num("peBasicExclExtraTTM", "peTTM", "peInclExtraTTM"),
            gross_margin=_pct(num("grossMarginTTM", "grossMarginAnnual")),
            operating_margin=_pct(num("operatingMarginTTM", "operatingMarginAnnual")),
            profit_margin=_pct(num("netProfitMarginTTM", "netProfitMarginAnnual")),
            revenue_growth=_pct(num("revenueGrowthTTMYoy")),
            shares_outstanding=(
                float(profile["shareOutstanding"]) * 1e6
                if isinstance(profile.get("shareOutstanding"), (int, float)) else None
            ),
            raw={"metric": m, "profile": profile},
        )

    def news(self, symbol: str) -> list[NewsItem]:
        today = datetime.now(tz=UTC).date()
        try:
            items = get_json(
                f"{BASE}/company-news",
                {
                    "symbol": symbol.strip().upper(),
                    "from": (today - timedelta(days=7)).isoformat(),
                    "to": today.isoformat(),
                    "token": self._key,
                },
            ) or []
        except HttpError:
            return []
        out: list[NewsItem] = []
        for item in items:
            ts = item.get("datetime")
            if not isinstance(ts, (int, float)):
                continue
            out.append(
                NewsItem(
                    symbol=symbol.strip().upper(),
                    title=str(item.get("headline", "")),
                    published_at=datetime.fromtimestamp(float(ts), tz=UTC),
                    source=str(item.get("source", "")),
                )
            )
        out.sort(key=lambda n: n.published_at, reverse=True)
        return out

    def quote_freshness(self) -> Freshness:
        return self._freshness


def _pct(v: float | None) -> float | None:
    """Finnhub margins are percentages (e.g. 44.1); Watchman uses fractions."""
    return None if v is None else v / 100.0
