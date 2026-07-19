"""SQLite bar cache: correctness of storage, serving, and refetch decisions."""

from __future__ import annotations

from datetime import datetime

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
