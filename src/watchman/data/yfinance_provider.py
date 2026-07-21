"""yfinance-backed DataProvider: free EOD prices + latest-snapshot fundamentals.

Honesty notes baked into this implementation:
- quote_freshness() is EOD. Module B will label every signal built on this
  provider DELAYED — NOT ACTIONABLE.
- fundamentals() returns point_in_time=False because yfinance only exposes the
  latest snapshot. AsOfView therefore refuses to serve these fundamentals for
  historical timestamps (see provider.AsOfView.fundamentals).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from watchman.data.provider import (
    DataProvider,
    Freshness,
    Fundamentals,
    NewsItem,
    StatementSet,
    normalize_bars,
    normalize_intraday_bars,
)


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def __init__(self) -> None:
        # Imported lazily so the rest of the package (and the test suite)
        # never depends on network-touching module import side effects.
        import yfinance

        self._yf = yfinance

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        ticker = self._yf.Ticker(symbol)
        # yfinance treats `end` as exclusive; extend by one day to make our
        # contract ([start, end] inclusive) hold.
        raw = ticker.history(
            start=start.date().isoformat(),
            end=(end.date() + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
        )
        if raw is None or raw.empty:
            raise ValueError(
                f"yfinance returned no daily bars for {symbol!r} "
                f"({start.date()}..{end.date()}) — bad symbol, delisted, or network issue"
            )
        keep = [c for c in raw.columns if c not in ("Dividends", "Stock Splits")]
        return normalize_bars(raw[keep])

    def fundamentals(self, symbol: str) -> Fundamentals:
        ticker = self._yf.Ticker(symbol)
        info: dict = ticker.info or {}

        def num(key: str) -> float | None:
            v = info.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
            return None

        return Fundamentals(
            symbol=symbol.strip().upper(),
            fetched_at=datetime.now(),
            point_in_time=False,
            sector=info.get("sector"),
            market_cap=num("marketCap"),
            trailing_pe=num("trailingPE"),
            forward_pe=num("forwardPE"),
            gross_margin=num("grossMargins"),
            operating_margin=num("operatingMargins"),
            profit_margin=num("profitMargins"),
            revenue_growth=num("revenueGrowth"),
            total_debt=num("totalDebt"),
            ebitda=num("ebitda"),
            free_cash_flow=num("freeCashflow"),
            shares_outstanding=num("sharesOutstanding"),
            float_shares=num("floatShares"),
            enterprise_value=num("enterpriseValue"),
            ev_to_ebitda=num("enterpriseToEbitda"),
            operating_cashflow=num("operatingCashflow"),
            trailing_eps=num("trailingEps"),
            total_revenue=num("totalRevenue"),
            net_income=num("netIncomeToCommon"),
            raw=info,
        )

    def financial_statements(self, symbol: str) -> StatementSet:
        """Annual statements (~4 fiscal years, latest restatement — NOT
        point-in-time). Individual statements that fail to load come back
        empty; metric extraction treats missing lines as honestly unknown."""
        ticker = self._yf.Ticker(symbol)

        def grab(attr: str) -> pd.DataFrame:
            try:
                df = getattr(ticker, attr)
            except Exception:
                return pd.DataFrame()
            return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

        return StatementSet(
            symbol=symbol.strip().upper(),
            income=grab("income_stmt"),
            balance=grab("balance_sheet"),
            cashflow=grab("cashflow"),
            fetched_at=datetime.now(),
            point_in_time=False,
        )

    def intraday_bars(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        """Intraday bars. yfinance serves these DELAYED (and 1m only for
        recent days) — Module B labels anything built on them accordingly."""
        ticker = self._yf.Ticker(symbol)
        raw = ticker.history(
            period=f"{days}d",
            interval=interval,
            prepost=include_premarket,
            auto_adjust=False,
        )
        if raw is None or raw.empty:
            raise ValueError(
                f"yfinance returned no {interval} bars for {symbol!r} "
                f"(last {days}d, prepost={include_premarket})"
            )
        keep = [c for c in raw.columns if c in ("Open", "High", "Low", "Close", "Volume")]
        return normalize_intraday_bars(raw[keep])

    def news(self, symbol: str) -> list[NewsItem]:
        """Recent headlines. Items without a parseable publish time are
        dropped — an undated headline cannot be honestly time-filtered."""
        ticker = self._yf.Ticker(symbol)
        try:
            raw_items = ticker.news or []
        except Exception:
            return []
        items: list[NewsItem] = []
        for raw in raw_items:
            content = raw.get("content", raw) if isinstance(raw, dict) else {}
            title = content.get("title") or ""
            published = None
            pub_date = content.get("pubDate") or content.get("displayTime")
            epoch = raw.get("providerPublishTime") if isinstance(raw, dict) else None
            if isinstance(pub_date, str):
                try:
                    published = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                except ValueError:
                    published = None
            elif isinstance(epoch, (int, float)):
                published = datetime.fromtimestamp(float(epoch), tz=UTC)
            if title and published is not None and published.tzinfo is not None:
                source = ""
                provider_field = content.get("provider")
                if isinstance(provider_field, dict):
                    source = provider_field.get("displayName", "")
                items.append(
                    NewsItem(
                        symbol=symbol.strip().upper(),
                        title=title,
                        published_at=published,
                        source=source,
                    )
                )
        items.sort(key=lambda n: n.published_at, reverse=True)
        return items

    def quote_freshness(self) -> Freshness:
        return Freshness.EOD
