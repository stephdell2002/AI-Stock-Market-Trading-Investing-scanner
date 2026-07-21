"""Provider factory: turn config + env keys into a DataProvider.

Selection is `settings.data.provider` (yfinance | finnhub | polygon | fmp).
Real-time providers pull their key from env vars only (never YAML). A provider
that can't serve fundamentals/statements on its own (Polygon has none; Finnhub
has no statements) is transparently composed with yfinance for those methods,
so the Module A screener works regardless of the market-data source.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from watchman.config import ProviderKeys, WatchmanConfig
from watchman.data.provider import (
    DataProvider,
    Freshness,
    Fundamentals,
    NewsItem,
    StatementSet,
)

REALTIME_PROVIDERS = ("finnhub", "polygon", "fmp")
KNOWN_PROVIDERS = ("yfinance", *REALTIME_PROVIDERS)

_KEY_ENV = {
    "finnhub": ("finnhub", "WATCHMAN_FINNHUB_KEY"),
    "polygon": ("polygon", "WATCHMAN_POLYGON_KEY"),
    "fmp": ("fmp", "WATCHMAN_FMP_KEY"),
}


class ProviderConfigError(RuntimeError):
    """The configured provider can't be constructed (unknown, or missing key)."""


class FundamentalsFallbackProvider(DataProvider):
    """Serve bars/news/freshness from a primary market-data provider, but route
    fundamentals/statements the primary can't answer (NotImplementedError) to a
    fallback (yfinance). Freshness is the primary's — that's what labels signals.
    """

    def __init__(self, primary: DataProvider, fallback_factory=None):
        self.primary = primary
        self.name = f"{primary.name}+yfinance-fundamentals"
        self._fallback: DataProvider | None = None
        if fallback_factory is None:
            from watchman.data.yfinance_provider import YFinanceProvider

            fallback_factory = YFinanceProvider
        self._fallback_factory = fallback_factory

    def _fb(self) -> DataProvider:
        if self._fallback is None:
            self._fallback = self._fallback_factory()
        return self._fallback

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        return self.primary.daily_bars(symbol, start, end)

    def intraday_bars(self, symbol, interval="5m", days=1, include_premarket=False):
        return self.primary.intraday_bars(
            symbol, interval=interval, days=days, include_premarket=include_premarket
        )

    def news(self, symbol: str) -> list[NewsItem]:
        try:
            return self.primary.news(symbol)
        except NotImplementedError:
            return self._fb().news(symbol)

    def fundamentals(self, symbol: str) -> Fundamentals:
        try:
            return self.primary.fundamentals(symbol)
        except NotImplementedError:
            return self._fb().fundamentals(symbol)

    def financial_statements(self, symbol: str) -> StatementSet:
        try:
            return self.primary.financial_statements(symbol)
        except NotImplementedError:
            return self._fb().financial_statements(symbol)

    def quote_freshness(self) -> Freshness:
        return self.primary.quote_freshness()


def _require_key(keys: ProviderKeys, name: str) -> str:
    attr, env_var = _KEY_ENV[name]
    key = getattr(keys, attr)
    if not key:
        raise ProviderConfigError(
            f"provider '{name}' needs an API key — set {env_var} in your environment "
            f"(or .env). See docs/realtime-data.md and .env.example."
        )
    return key


def make_provider(cfg: WatchmanConfig) -> DataProvider:
    """Construct the configured provider. Raises ProviderConfigError on an
    unknown provider or a missing key."""
    name = cfg.settings.data.provider.strip().lower()
    freshness = cfg.settings.data.freshness

    if name == "yfinance":
        from watchman.data.yfinance_provider import YFinanceProvider

        return YFinanceProvider()

    if name == "finnhub":
        from watchman.data.finnhub_provider import FinnhubProvider

        primary: DataProvider = FinnhubProvider(_require_key(cfg.keys, "finnhub"), freshness)
    elif name == "polygon":
        from watchman.data.polygon_provider import PolygonProvider

        primary = PolygonProvider(_require_key(cfg.keys, "polygon"), freshness)
    elif name == "fmp":
        from watchman.data.fmp_provider import FmpProvider

        primary = FmpProvider(_require_key(cfg.keys, "fmp"), freshness)
    else:
        raise ProviderConfigError(
            f"unknown data provider {name!r}; choose one of {', '.join(KNOWN_PROVIDERS)}"
        )

    # Compose with yfinance for any fundamentals/statements the primary lacks.
    return FundamentalsFallbackProvider(primary)
