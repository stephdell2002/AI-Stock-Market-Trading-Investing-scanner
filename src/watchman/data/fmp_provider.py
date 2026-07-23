"""Financial Modeling Prep-backed DataProvider (https://financialmodelingprep.com).

The most complete of the three REST providers: daily + intraday bars, news,
deep fundamentals, AND full annual statements — so FMP can drive both the
Module A screener and the Module B signal engine on its own.

Freshness is configured (default DELAYED), never assumed. Statements are still
latest-restatement (point_in_time=False), so AsOfView refuses to serve them for
historical timestamps, exactly as with yfinance.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from watchman.data.http import HttpError, get_json
from watchman.data.provider import (
    DataProvider,
    Freshness,
    Fundamentals,
    IpoEvent,
    NewsItem,
    StatementSet,
    normalize_bars,
    normalize_intraday_bars,
)
from watchman.data.rest_common import (
    check_interval,
    parse_price_range,
    regular_session_only,
    resolve_freshness,
)

BASE = "https://financialmodelingprep.com/api/v3"

# Watchman interval -> FMP historical-chart interval.
_INTERVAL = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "1hour"}


class FmpProvider(DataProvider):
    name = "fmp"

    def __init__(self, api_key: str, freshness: str | None = None):
        if not api_key:
            raise ValueError("FmpProvider requires an API key (WATCHMAN_FMP_KEY)")
        self._key = api_key
        self._freshness = resolve_freshness(freshness)

    def daily_bars(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        data = get_json(
            f"{BASE}/historical-price-full/{symbol.strip().upper()}",
            {"from": start.date().isoformat(), "to": end.date().isoformat(),
             "apikey": self._key},
        ) or {}
        history = data.get("historical")
        if not history:
            raise ValueError(f"fmp returned no daily bars for {symbol!r}")
        frame = pd.DataFrame(history)
        frame.index = pd.to_datetime(frame["date"])
        out = pd.DataFrame(
            {
                "open": frame["open"], "high": frame["high"], "low": frame["low"],
                "close": frame["close"],
                "adj_close": frame.get("adjClose", frame["close"]),
                "volume": frame["volume"],
            },
            index=frame.index,
        )
        return normalize_bars(out)

    def intraday_bars(
        self, symbol: str, interval: str = "5m", days: int = 1,
        include_premarket: bool = False,
    ) -> pd.DataFrame:
        check_interval(interval)
        end = datetime.now().date()
        start = end - timedelta(days=max(days, 1) + 3)
        data = get_json(
            f"{BASE}/historical-chart/{_INTERVAL[interval]}/{symbol.strip().upper()}",
            {"from": start.isoformat(), "to": end.isoformat(), "apikey": self._key},
        )
        if not data:
            raise ValueError(f"fmp returned no {interval} bars for {symbol!r}")
        frame = pd.DataFrame(data)
        # FMP intraday timestamps are US/Eastern wall-clock strings.
        frame.index = pd.to_datetime(frame["date"])
        out = pd.DataFrame(
            {"open": frame["open"], "high": frame["high"], "low": frame["low"],
             "close": frame["close"], "volume": frame["volume"]},
            index=frame.index,
        )
        bars = normalize_intraday_bars(out)
        return bars if include_premarket else regular_session_only(bars)

    def fundamentals(self, symbol: str) -> Fundamentals:
        symbol = symbol.strip().upper()
        profile = _first(get_json(f"{BASE}/profile/{symbol}", {"apikey": self._key}))
        ratios = _first(get_json(f"{BASE}/ratios-ttm/{symbol}", {"apikey": self._key}))
        metrics = _first(get_json(f"{BASE}/key-metrics-ttm/{symbol}", {"apikey": self._key}))

        return Fundamentals(
            symbol=symbol,
            fetched_at=datetime.now(),
            point_in_time=False,
            sector=profile.get("sector"),
            market_cap=_num(profile.get("mktCap")),
            trailing_pe=_num(ratios.get("peRatioTTM")),
            gross_margin=_num(ratios.get("grossProfitMarginTTM")),
            operating_margin=_num(ratios.get("operatingProfitMarginTTM")),
            profit_margin=_num(ratios.get("netProfitMarginTTM")),
            # key-metrics gives FCF *per share*, not total FCF — leave it None
            # and let financial_statements() supply real Free Cash Flow.
            free_cash_flow=None,
            enterprise_value=_num(metrics.get("enterpriseValueTTM")),
            ev_to_ebitda=_num(metrics.get("enterpriseValueOverEBITDATTM")),
            shares_outstanding=_num(profile.get("sharesOutstanding")),
            raw={"profile": profile, "ratios": ratios, "metrics": metrics},
        )

    def financial_statements(self, symbol: str) -> StatementSet:
        symbol = symbol.strip().upper()
        income = _statement_frame(
            get_json(f"{BASE}/income-statement/{symbol}",
                     {"period": "annual", "limit": 5, "apikey": self._key})
        )
        balance = _statement_frame(
            get_json(f"{BASE}/balance-sheet-statement/{symbol}",
                     {"period": "annual", "limit": 5, "apikey": self._key})
        )
        cashflow = _statement_frame(
            get_json(f"{BASE}/cash-flow-statement/{symbol}",
                     {"period": "annual", "limit": 5, "apikey": self._key})
        )
        return StatementSet(
            symbol=symbol, income=income, balance=balance, cashflow=cashflow,
            fetched_at=datetime.now(), point_in_time=False,
        )

    def news(self, symbol: str) -> list[NewsItem]:
        try:
            data = get_json(
                f"{BASE}/stock_news",
                {"tickers": symbol.strip().upper(), "limit": 20, "apikey": self._key},
            ) or []
        except HttpError:
            return []
        out: list[NewsItem] = []
        for item in data:
            parsed = _parse_fmp_date(item.get("publishedDate"))
            if parsed is None:
                continue
            out.append(
                NewsItem(
                    symbol=symbol.strip().upper(),
                    title=str(item.get("title", "")),
                    published_at=parsed,
                    source=str(item.get("site", "")),
                )
            )
        out.sort(key=lambda n: n.published_at, reverse=True)
        return out

    def ipo_calendar(self, start, end) -> list[IpoEvent]:
        data = get_json(
            f"{BASE}/ipo_calendar",
            {"from": start.isoformat(), "to": end.isoformat(), "apikey": self._key},
        ) or []
        out: list[IpoEvent] = []
        for row in data:
            symbol = str(row.get("symbol", "")).strip().upper()
            date_str = row.get("date")
            if not symbol or not date_str:
                continue
            try:
                ipo_date = datetime.fromisoformat(str(date_str)).date()
            except ValueError:
                continue
            low, high = parse_price_range(row.get("priceRange"))
            action = str(row.get("actions", "")).lower()
            out.append(
                IpoEvent(
                    symbol=symbol,
                    name=str(row.get("company", "")),
                    ipo_date=ipo_date,
                    exchange=str(row.get("exchange", "")),
                    price_low=low,
                    price_high=high,
                    offer_price=(low if action == "priced" and low == high else None),
                    shares=_num(row.get("shares")),
                    status=action or "expected",
                    source="fmp",
                )
            )
        out.sort(key=lambda e: e.ipo_date)
        return out

    def quote_freshness(self) -> Freshness:
        return self._freshness


# FMP statement line-item names -> Watchman's canonical row labels (which the
# screener's `line()` lookup already knows).
_INCOME_MAP = {
    "revenue": "Total Revenue",
    "grossProfit": "Gross Profit",
    "operatingIncome": "Operating Income",
    "ebitda": "EBITDA",
    "incomeBeforeTax": "Pretax Income",
    "incomeTaxExpense": "Tax Provision",
    "netIncome": "Net Income",
    "epsdiluted": "Diluted EPS",
    "eps": "Basic EPS",
    "operatingIncome_ebit": "EBIT",
}
_BALANCE_MAP = {
    "totalDebt": "Total Debt",
    "totalStockholdersEquity": "Stockholders Equity",
    "cashAndCashEquivalents": "Cash And Cash Equivalents",
}
_CASHFLOW_MAP = {
    "freeCashFlow": "Free Cash Flow",
    "operatingCashFlow": "Operating Cash Flow",
    "capitalExpenditure": "Capital Expenditure",
}
_ALL_MAPS = {**_INCOME_MAP, **_BALANCE_MAP, **_CASHFLOW_MAP}


def _statement_frame(records) -> pd.DataFrame:
    """FMP returns newest-first records with a `date` (fiscal period end).
    Reshape to Watchman's statement layout: rows = line items, columns =
    fiscal-period-end dates."""
    if not records:
        return pd.DataFrame()
    by_date: dict[pd.Timestamp, dict[str, float]] = {}
    for rec in records:
        try:
            period = pd.Timestamp(rec["date"])
        except (KeyError, ValueError):
            continue
        row: dict[str, float] = {}
        for fmp_key, label in _ALL_MAPS.items():
            source_key = "operatingIncome" if fmp_key == "operatingIncome_ebit" else fmp_key
            value = rec.get(source_key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[label] = float(value)
        by_date[period] = row
    if not by_date:
        return pd.DataFrame()
    frame = pd.DataFrame(by_date)  # columns = dates, index = labels
    return frame.reindex(sorted(frame.columns), axis=1)


def _first(payload) -> dict:
    if isinstance(payload, list) and payload:
        return payload[0] if isinstance(payload[0], dict) else {}
    return payload if isinstance(payload, dict) else {}


def _num(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _parse_fmp_date(value) -> datetime | None:
    """FMP news timestamps look like '2024-01-02 09:30:00' (US/Eastern)."""
    if not isinstance(value, str):
        return None
    from watchman.data.provider import ET

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=ET) if parsed.tzinfo is None else parsed
        except ValueError:
            continue
    return None
