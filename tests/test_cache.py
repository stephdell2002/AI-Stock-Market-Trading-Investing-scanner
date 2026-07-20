"""SQLite bar cache: correctness of storage, serving, and refetch decisions."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from tests.conftest import SyntheticProvider, make_bars

from watchman.data.cache import CachedBars
from watchman.db import connect


def _cache(tmp_path, bars=None):
    if bars is None:
        bars = make_bars("2024-01-02", "2024-06-28")
    provider = SyntheticProvider(bars)
    conn = connect(tmp_path / "test.db")
    return provider, CachedBars(provider, conn)


def test_roundtrip_preserves_values(tmp_path):
    provider, cache = _cache(tmp_path)
    got = cache.daily_bars("TEST", datetime(2024, 2, 1), datetime(2024, 2, 29))
    expected = provider._bars.loc["2024-02-01":"2024-02-29"]
    assert len(got) == len(expected)
    pd.testing.assert_index_equal(got.index, expected.index, check_names=False)
    assert got["close"].tolist() == expected["close"].tolist()
    assert got["adj_close"].tolist() == expected["adj_close"].tolist()


def test_second_request_within_coverage_hits_cache_not_provider(tmp_path):
    provider, cache = _cache(tmp_path)
    cache.daily_bars("TEST", datetime(2024, 1, 2), datetime(2024, 3, 28))
    assert len(provider.calls) == 1
    cache.daily_bars("TEST", datetime(2024, 2, 1), datetime(2024, 2, 15))
    assert len(provider.calls) == 1  # served locally


def test_request_beyond_coverage_triggers_union_refetch(tmp_path):
    provider, cache = _cache(tmp_path)
    cache.daily_bars("TEST", datetime(2024, 2, 1), datetime(2024, 2, 29))
    got = cache.daily_bars("TEST", datetime(2024, 1, 2), datetime(2024, 4, 30))
    assert len(provider.calls) == 2
    # The refetch covered the union, so both old and new dates are present.
    assert got.index.min() == pd.Timestamp("2024-01-02")
    assert got.index.max() == pd.Timestamp("2024-04-30")
    # And coverage now absorbs the widest range ever requested.
    cache.daily_bars("TEST", datetime(2024, 1, 15), datetime(2024, 3, 15))
    assert len(provider.calls) == 2


def test_symbols_are_cached_independently(tmp_path):
    provider, cache = _cache(tmp_path)
    cache.daily_bars("AAA", datetime(2024, 2, 1), datetime(2024, 2, 29))
    cache.daily_bars("BBB", datetime(2024, 2, 1), datetime(2024, 2, 29))
    assert [c[0] for c in provider.calls] == ["AAA", "BBB"]
    assert cache.coverage("AAA") is not None
    assert cache.coverage("BBB") is not None
    assert cache.coverage("CCC") is None


def test_persistence_across_connections(tmp_path):
    _provider, cache = _cache(tmp_path)
    cache.daily_bars("TEST", datetime(2024, 2, 1), datetime(2024, 2, 29))

    fresh_provider = SyntheticProvider(make_bars("2024-01-02", "2024-06-28"))
    cache2 = CachedBars(fresh_provider, connect(tmp_path / "test.db"))
    got = cache2.daily_bars("TEST", datetime(2024, 2, 1), datetime(2024, 2, 29))
    assert not got.empty
    assert fresh_provider.calls == []  # nothing refetched


class TestFundamentalsAndStatementsCaches:
    def _counting_provider(self):
        from tests.screener_fixtures import CompanyProvider, make_fundamentals, make_statements

        provider = CompanyProvider(
            {"TEST": {"fundamentals": make_fundamentals(), "statements": make_statements()}}
        )
        counts = {"fundamentals": 0, "statements": 0}
        orig_f, orig_s = provider.fundamentals, provider.financial_statements

        def count_f(symbol):
            counts["fundamentals"] += 1
            return orig_f(symbol)

        def count_s(symbol):
            counts["statements"] += 1
            return orig_s(symbol)

        provider.fundamentals = count_f  # type: ignore[method-assign]
        provider.financial_statements = count_s  # type: ignore[method-assign]
        return provider, counts

    def test_fundamentals_cached_within_max_age(self, tmp_path):
        from watchman.data.cache import FundamentalsCache

        provider, counts = self._counting_provider()
        cache = FundamentalsCache(provider, connect(tmp_path / "f.db"), timedelta(days=3))
        first = cache.get_or_fetch("TEST")
        second = cache.get_or_fetch("TEST")
        assert counts["fundamentals"] == 1
        assert second.market_cap == first.market_cap
        assert second.fetched_at == first.fetched_at  # true fetch time preserved

    def test_fundamentals_refetched_after_max_age(self, tmp_path):
        from watchman.data.cache import FundamentalsCache

        provider, counts = self._counting_provider()
        cache = FundamentalsCache(provider, connect(tmp_path / "f.db"), timedelta(seconds=0))
        cache.get_or_fetch("TEST")
        cache.get_or_fetch("TEST")
        assert counts["fundamentals"] == 2

    def test_statements_roundtrip_preserves_frames(self, tmp_path):
        from watchman.data.cache import StatementsCache
        from watchman.screener.metrics import line

        provider, counts = self._counting_provider()
        cache = StatementsCache(provider, connect(tmp_path / "s.db"), timedelta(days=3))
        cache.get_or_fetch("TEST")
        got = cache.get_or_fetch("TEST")  # this one comes from SQLite
        assert counts["statements"] == 1
        revenue = line(got.income, "Total Revenue")
        assert revenue is not None
        assert float(revenue.iloc[-1]) == 172.8
        assert got.point_in_time is False

    def test_cached_provider_composes_everything(self, tmp_path):
        from tests.screener_fixtures import make_price_history

        from watchman.data.cache import CachedProvider
        from watchman.data.provider import Freshness

        provider, counts = self._counting_provider()
        provider.companies["TEST"]["bars"] = make_price_history(0.001, days=30)
        cached = CachedProvider(provider, connect(tmp_path / "c.db"))
        assert cached.name == "company-synthetic+cache"
        assert cached.quote_freshness() == Freshness.EOD
        cached.fundamentals("TEST")
        cached.fundamentals("TEST")
        cached.financial_statements("TEST")
        cached.financial_statements("TEST")
        assert counts == {"fundamentals": 1, "statements": 1}
