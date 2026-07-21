"""Real-time REST providers (Finnhub / Polygon / FMP) against MOCKED HTTP.

The sandbox has no market-data network access, so — exactly like the yfinance
tests — these pin the normalization contract: whatever each API returns, the
provider must emit canonical bars, honestly-labeled fundamentals/statements,
and timestamp-clippable news. No test here touches the network.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import watchman.data.finnhub_provider as fh
import watchman.data.fmp_provider as fmp
import watchman.data.polygon_provider as pg
from watchman.data.provider import BAR_COLUMNS, ET, INTRADAY_COLUMNS, Freshness


class FakeHttp:
    """Routes get_json(url, params) to canned responses keyed by URL substring."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params=None, **kwargs):
        self.calls.append((url, params or {}))
        for needle, payload in self.routes.items():
            if needle in url:
                return payload() if callable(payload) else payload
        raise AssertionError(f"unexpected URL in test: {url}")


# ---------------------------------------------------------------- Finnhub ----
class TestFinnhub:
    def _provider(self, monkeypatch, routes):
        monkeypatch.setattr(fh, "get_json", FakeHttp(routes))
        return fh.FinnhubProvider("test_key")

    def test_daily_bars_normalized(self, monkeypatch):
        candles = {
            "s": "ok",
            "t": [1704200400, 1704286800],   # 2024-01-02, 2024-01-03 (UTC)
            "o": [100.0, 101.0], "h": [102.0, 103.0],
            "l": [99.0, 100.5], "c": [101.0, 102.5], "v": [1e6, 1.1e6],
        }
        p = self._provider(monkeypatch, {"/stock/candle": candles})
        bars = p.daily_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 5))
        assert list(bars.columns) == BAR_COLUMNS
        assert bars.index.tz is None
        assert bars["close"].tolist() == [101.0, 102.5]
        assert bars["adj_close"].tolist() == [101.0, 102.5]  # fallback to close

    def test_intraday_filters_premarket_by_default(self, monkeypatch):
        # 09:25 ET (pre) and 09:35 ET (regular), as UTC unix seconds.
        pre = int(datetime(2024, 1, 2, 9, 25, tzinfo=ET).timestamp())
        reg = int(datetime(2024, 1, 2, 9, 35, tzinfo=ET).timestamp())
        candles = {"s": "ok", "t": [pre, reg], "o": [10, 11], "h": [10, 11],
                   "l": [10, 11], "c": [10, 11], "v": [500, 600]}
        p = self._provider(monkeypatch, {"/stock/candle": candles})
        regular = p.intraday_bars("AAPL", "5m")
        assert list(regular.columns) == INTRADAY_COLUMNS
        assert len(regular) == 1                      # pre-market dropped
        assert regular.index[0].time().strftime("%H:%M") == "09:35"
        with_pre = p.intraday_bars("AAPL", "5m", include_premarket=True)
        assert len(with_pre) == 2

    def test_no_data_raises(self, monkeypatch):
        p = self._provider(monkeypatch, {"/stock/candle": {"s": "no_data"}})
        with pytest.raises(ValueError, match="no candles"):
            p.daily_bars("NOPE", datetime(2024, 1, 1), datetime(2024, 1, 5))

    def test_fundamentals_labeled_not_point_in_time(self, monkeypatch):
        routes = {
            "/stock/metric": {"metric": {"peTTM": 28.5, "grossMarginTTM": 44.0,
                                         "netProfitMarginTTM": 25.0}},
            "/stock/profile2": {"marketCapitalization": 3_000_000,  # in millions
                                "finnhubIndustry": "Technology",
                                "shareOutstanding": 15_000},
        }
        p = self._provider(monkeypatch, routes)
        f = p.fundamentals("aapl")
        assert f.symbol == "AAPL"
        assert f.point_in_time is False
        assert f.trailing_pe == 28.5
        assert f.gross_margin == pytest.approx(0.44)   # percent -> fraction
        assert f.market_cap == pytest.approx(3_000_000 * 1e6)
        assert f.sector == "Technology"

    def test_news_parsed_and_sorted(self, monkeypatch):
        routes = {"/company-news": [
            {"datetime": 1704200400, "headline": "older", "source": "Reuters"},
            {"datetime": 1704300400, "headline": "newer", "source": "Bloomberg"},
        ]}
        p = self._provider(monkeypatch, routes)
        items = p.news("AAPL")
        assert [n.title for n in items] == ["newer", "older"]
        assert all(n.published_at.tzinfo is not None for n in items)

    def test_freshness_defaults_delayed_and_is_configurable(self, monkeypatch):
        monkeypatch.setattr(fh, "get_json", FakeHttp({}))
        assert fh.FinnhubProvider("k").quote_freshness() == Freshness.DELAYED
        assert fh.FinnhubProvider("k", "REALTIME").quote_freshness() == Freshness.REALTIME

    def test_missing_key_rejected(self):
        with pytest.raises(ValueError, match="requires an API key"):
            fh.FinnhubProvider("")


# ---------------------------------------------------------------- Polygon ----
class TestPolygon:
    def _provider(self, monkeypatch, routes):
        monkeypatch.setattr(pg, "get_json", FakeHttp(routes))
        return pg.PolygonProvider("test_key")

    def test_daily_aggregates_normalized(self, monkeypatch):
        aggs = {"results": [
            {"t": 1704200400000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1e6},
            {"t": 1704286800000, "o": 101, "h": 103, "l": 100, "c": 102, "v": 1.1e6},
        ]}
        p = self._provider(monkeypatch, {"/range/1/day/": aggs})
        bars = p.daily_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 5))
        assert list(bars.columns) == BAR_COLUMNS
        assert bars["close"].tolist() == [101.0, 102.0]

    def test_intraday_premarket_filter(self, monkeypatch):
        pre = int(datetime(2024, 1, 2, 8, 0, tzinfo=ET).timestamp() * 1000)
        reg = int(datetime(2024, 1, 2, 10, 0, tzinfo=ET).timestamp() * 1000)
        aggs = {"results": [
            {"t": pre, "o": 10, "h": 10, "l": 10, "c": 10, "v": 100},
            {"t": reg, "o": 11, "h": 11, "l": 11, "c": 11, "v": 200},
        ]}
        p = self._provider(monkeypatch, {"/range/5/minute/": aggs})
        assert len(p.intraday_bars("AAPL", "5m")) == 1
        assert len(p.intraday_bars("AAPL", "5m", include_premarket=True)) == 2

    def test_empty_results_raises(self, monkeypatch):
        p = self._provider(monkeypatch, {"/range/1/day/": {"results": []}})
        with pytest.raises(ValueError, match="no 1day bars"):
            p.daily_bars("NOPE", datetime(2024, 1, 1), datetime(2024, 1, 5))

    def test_fundamentals_not_implemented_triggers_fallback(self, monkeypatch):
        p = self._provider(monkeypatch, {})
        with pytest.raises(NotImplementedError):
            p.fundamentals("AAPL")

    def test_news_parsed(self, monkeypatch):
        routes = {"/reference/news": {"results": [
            {"published_utc": "2024-01-02T14:00:00Z", "title": "headline",
             "publisher": {"name": "Polygon"}},
        ]}}
        p = self._provider(monkeypatch, routes)
        items = p.news("AAPL")
        assert items[0].title == "headline"
        assert items[0].source == "Polygon"


# -------------------------------------------------------------------- FMP ----
class TestFmp:
    def _provider(self, monkeypatch, routes):
        monkeypatch.setattr(fmp, "get_json", FakeHttp(routes))
        return fmp.FmpProvider("test_key")

    def test_daily_bars_uses_adjclose(self, monkeypatch):
        routes = {"/historical-price-full/": {"historical": [
            {"date": "2024-01-03", "open": 101, "high": 103, "low": 100,
             "close": 102, "adjClose": 101.5, "volume": 1.1e6},
            {"date": "2024-01-02", "open": 100, "high": 102, "low": 99,
             "close": 101, "adjClose": 100.5, "volume": 1e6},
        ]}}
        p = self._provider(monkeypatch, routes)
        bars = p.daily_bars("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 5))
        assert list(bars.columns) == BAR_COLUMNS
        assert bars.index.is_monotonic_increasing        # sorted ascending
        assert bars["adj_close"].tolist() == [100.5, 101.5]

    def test_intraday_eastern_timestamps(self, monkeypatch):
        routes = {"/historical-chart/5min/": [
            {"date": "2024-01-02 09:35:00", "open": 11, "high": 11, "low": 11,
             "close": 11, "volume": 600},
            {"date": "2024-01-02 08:00:00", "open": 10, "high": 10, "low": 10,
             "close": 10, "volume": 100},
        ]}
        p = self._provider(monkeypatch, routes)
        regular = p.intraday_bars("AAPL", "5m")
        assert len(regular) == 1                          # 08:00 pre-market dropped
        assert str(regular.index.tz) == "America/New_York"

    def test_fundamentals_from_profile_and_ratios(self, monkeypatch):
        routes = {
            "/profile/": [{"mktCap": 3e12, "sector": "Technology",
                           "sharesOutstanding": 15e9}],
            "/ratios-ttm/": [{"peRatioTTM": 28.5, "grossProfitMarginTTM": 0.44,
                              "netProfitMarginTTM": 0.25}],
            "/key-metrics-ttm/": [{"enterpriseValueOverEBITDATTM": 20.0,
                                   "enterpriseValueTTM": 3.1e12}],
        }
        p = self._provider(monkeypatch, routes)
        f = p.fundamentals("AAPL")
        assert f.point_in_time is False
        assert f.market_cap == 3e12
        assert f.trailing_pe == 28.5
        assert f.gross_margin == 0.44                     # already a fraction
        assert f.ev_to_ebitda == 20.0

    def test_statements_reshaped_to_watchman_layout(self, monkeypatch):
        income = [
            {"date": "2025-12-31", "revenue": 400, "grossProfit": 180,
             "operatingIncome": 120, "netIncome": 100, "epsdiluted": 6.0,
             "ebitda": 150, "incomeBeforeTax": 110, "incomeTaxExpense": 22},
            {"date": "2024-12-31", "revenue": 350, "grossProfit": 150,
             "operatingIncome": 100, "netIncome": 80, "epsdiluted": 5.0,
             "ebitda": 130, "incomeBeforeTax": 95, "incomeTaxExpense": 19},
        ]
        balance = [
            {"date": "2025-12-31", "totalDebt": 100,
             "totalStockholdersEquity": 500, "cashAndCashEquivalents": 60},
            {"date": "2024-12-31", "totalDebt": 110,
             "totalStockholdersEquity": 450, "cashAndCashEquivalents": 50},
        ]
        cashflow = [
            {"date": "2025-12-31", "freeCashFlow": 90, "operatingCashFlow": 120,
             "capitalExpenditure": -30},
            {"date": "2024-12-31", "freeCashFlow": 70, "operatingCashFlow": 100,
             "capitalExpenditure": -30},
        ]
        routes = {
            "/income-statement/": income,
            "/balance-sheet-statement/": balance,
            "/cash-flow-statement/": cashflow,
        }
        p = self._provider(monkeypatch, routes)
        stmts = p.financial_statements("AAPL")
        assert stmts.point_in_time is False

        # The screener's own `line()` lookup must find the reshaped rows.
        from watchman.screener.metrics import line

        revenue = line(stmts.income, "Total Revenue")
        assert revenue is not None
        assert revenue.iloc[-1] == 400            # most recent last after sort
        assert line(stmts.balance, "Total Debt").iloc[-1] == 100
        assert line(stmts.cashflow, "Free Cash Flow").iloc[-1] == 90

    def test_statements_end_to_end_metrics(self, monkeypatch):
        """The reshaped statements must feed the real metric extractors."""
        income = [{"date": f"{y}-12-31", "revenue": rev, "grossProfit": rev * 0.4,
                   "operatingIncome": rev * 0.2, "netIncome": rev * 0.15,
                   "epsdiluted": eps, "ebitda": rev * 0.25,
                   "incomeBeforeTax": rev * 0.18, "incomeTaxExpense": rev * 0.04}
                  for y, rev, eps in [(2022, 100, 1.0), (2023, 120, 1.2),
                                      (2024, 144, 1.44), (2025, 172.8, 1.728)]]
        p = self._provider(monkeypatch, {"/income-statement/": income,
                                         "/balance-sheet-statement/": [],
                                         "/cash-flow-statement/": []})
        stmts = p.financial_statements("AAPL")
        from watchman.screener.metrics import growth_metrics

        g = growth_metrics(stmts)
        assert g["revenue_cagr"] == pytest.approx(20.0, abs=0.1)  # 100->172.8 /3y

    def test_news_eastern_and_sorted(self, monkeypatch):
        routes = {"/stock_news": [
            {"publishedDate": "2024-01-02 08:00:00", "title": "older", "site": "x"},
            {"publishedDate": "2024-01-02 14:00:00", "title": "newer", "site": "y"},
        ]}
        p = self._provider(monkeypatch, routes)
        items = p.news("AAPL")
        assert [n.title for n in items] == ["newer", "older"]
        assert all(str(n.published_at.tzinfo) == "America/New_York" for n in items)
