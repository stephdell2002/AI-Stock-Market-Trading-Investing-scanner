"""Lookahead-bias canary.

This file proves the point-in-time discipline of the data layer: an AsOfView
pinned to a moment can never serve data from after that moment. Every future
strategy, screener, and backtest goes through AsOfView, so this canary guards
all of them. DO NOT delete, skip, or weaken these tests to make something
else pass — a red canary means the change being tested is dishonest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from tests.conftest import SyntheticProvider, make_bars

from watchman.data.provider import ET, AsOfView, Freshness, Fundamentals, LookaheadError


class TestDailyBarClipping:
    def test_historical_view_hides_future_bars(self, synthetic_provider):
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 18, 0))
        bars = view.daily_bars("TEST")
        assert bars.index.max() == pd.Timestamp("2024-02-15")
        assert pd.Timestamp("2024-02-16") not in bars.index

    def test_intraday_view_excludes_todays_incomplete_bar(self, synthetic_provider):
        # 2024-02-15 10:30 ET: that session has not closed; its daily bar
        # does not exist yet and must be invisible.
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 10, 30))
        bars = view.daily_bars("TEST")
        assert bars.index.max() == pd.Timestamp("2024-02-14")

    def test_at_close_view_includes_todays_bar(self, synthetic_provider):
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 16, 0))
        assert view.daily_bars("TEST").index.max() == pd.Timestamp("2024-02-15")

    def test_requesting_past_the_pin_is_clipped_not_honored(self, synthetic_provider):
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 18, 0))
        bars = view.daily_bars("TEST", end=datetime(2024, 3, 28))
        assert bars.index.max() == pd.Timestamp("2024-02-15")

    def test_view_never_trusts_the_provider(self, q1_bars):
        """Even a buggy/malicious provider that ignores the requested range
        cannot leak future bars through the view."""

        class LeakyProvider(SyntheticProvider):
            def daily_bars(self, symbol, start, end):
                return self._bars  # ignores the range entirely

        view = AsOfView(LeakyProvider(q1_bars), datetime(2024, 2, 15, 18, 0))
        assert view.daily_bars("TEST").index.max() == pd.Timestamp("2024-02-15")

    def test_utc_timestamps_are_normalized_to_eastern(self, synthetic_provider):
        # 2024-02-15 20:00 UTC == 15:00 ET, before the close: same-day bar hidden.
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 20, 0, tzinfo=UTC))
        assert view.daily_bars("TEST").index.max() == pd.Timestamp("2024-02-14")

    def test_empty_when_pin_predates_history(self, synthetic_provider):
        view = AsOfView(synthetic_provider, datetime(2023, 6, 1, 12, 0))
        assert view.daily_bars("TEST").empty


class TestFundamentalsDiscipline:
    def _fundamentals(self, **kwargs) -> Fundamentals:
        defaults = {
            "symbol": "TEST",
            "fetched_at": datetime.now(tz=ET),
            "point_in_time": False,
        }
        defaults.update(kwargs)
        return Fundamentals(**defaults)

    def test_latest_snapshot_fundamentals_refused_for_historical_views(self, q1_bars):
        """yfinance-style fundamentals (latest snapshot only) must NOT be
        served as if they were known at a past date — that is lookahead."""
        provider = SyntheticProvider(q1_bars, fundamentals=self._fundamentals())
        view = AsOfView(provider, datetime(2024, 2, 15, 12, 0))
        with pytest.raises(LookaheadError):
            view.fundamentals("TEST")

    def test_latest_snapshot_fundamentals_allowed_live(self, q1_bars):
        provider = SyntheticProvider(q1_bars, fundamentals=self._fundamentals())
        view = AsOfView(provider, datetime.now(tz=ET) - timedelta(hours=1))
        assert view.fundamentals("TEST").symbol == "TEST"

    def test_point_in_time_fundamentals_served_only_after_available_at(self, q1_bars):
        available = datetime(2024, 2, 10, 8, 0, tzinfo=ET)
        f = self._fundamentals(point_in_time=True, available_at=available)
        provider = SyntheticProvider(q1_bars, fundamentals=f)

        before = AsOfView(provider, datetime(2024, 2, 9, 12, 0))
        with pytest.raises(LookaheadError):
            before.fundamentals("TEST")

        after = AsOfView(provider, datetime(2024, 2, 12, 12, 0))
        assert after.fundamentals("TEST").available_at == available


class TestStatementsDiscipline:
    """Free-source statements are latest-restatement only — same honesty rule
    as fundamentals: refused for historical views, allowed live."""

    def _provider(self, q1_bars):
        from tests.screener_fixtures import make_statements

        provider = SyntheticProvider(q1_bars)
        stmts = make_statements("TEST")
        provider.financial_statements = lambda symbol: stmts  # type: ignore[method-assign]
        return provider

    def test_refused_for_historical_views(self, q1_bars):
        view = AsOfView(self._provider(q1_bars), datetime(2024, 2, 15, 12, 0))
        with pytest.raises(LookaheadError):
            view.financial_statements("TEST")

    def test_allowed_live(self, q1_bars):
        view = AsOfView(self._provider(q1_bars), datetime.now(tz=ET))
        assert view.financial_statements("TEST").symbol == "TEST"


class TestIntradayClipping:
    """Intraday canary: an in-progress bar does not exist yet, and future
    bars never leak — same discipline as daily bars."""

    def _view_at(self, hh: int, mm: int, q1_bars):
        from datetime import date, time

        from tests.intraday_fixtures import bars_from_closes

        provider = SyntheticProvider(q1_bars)
        day = date(2024, 2, 15)
        intraday = bars_from_closes(
            [100 + i * 0.1 for i in range(78)], day, time(9, 30), interval_minutes=5
        )
        provider.intraday_bars = (  # type: ignore[method-assign]
            lambda symbol, interval="5m", days=1, include_premarket=False: intraday
        )
        return AsOfView(provider, datetime(2024, 2, 15, hh, mm)), day

    def test_in_progress_bar_is_invisible(self, q1_bars):
        # At 10:03 the 10:00 bar (5m) is still forming: last visible = 9:55.
        view, _day = self._view_at(10, 3, q1_bars)
        bars = view.intraday_bars("TEST", "5m")
        assert bars.index.max().time().strftime("%H:%M") == "09:55"

    def test_bar_appears_exactly_at_completion(self, q1_bars):
        view, _day = self._view_at(10, 5, q1_bars)
        bars = view.intraday_bars("TEST", "5m")
        assert bars.index.max().time().strftime("%H:%M") == "10:00"

    def test_future_bars_never_leak(self, q1_bars):
        # At 10:03, exactly 6 five-minute bars are complete: 9:30 through
        # 9:55 (the 9:55 bar completed at 10:00; the 10:00 bar is in progress).
        view, _ = self._view_at(10, 3, q1_bars)
        bars = view.intraday_bars("TEST", "5m")
        assert len(bars) == 6
        assert bars.index.min().time().strftime("%H:%M") == "09:30"

    def test_unsupported_interval_refused(self, q1_bars):
        view, _ = self._view_at(10, 3, q1_bars)
        with pytest.raises(ValueError, match="unsupported interval"):
            view.intraday_bars("TEST", "42m")


class TestNewsClipping:
    def test_future_and_undated_news_hidden(self, q1_bars):
        from watchman.data.provider import NewsItem

        provider = SyntheticProvider(q1_bars)
        items = [
            NewsItem("TEST", "old news", datetime(2024, 2, 14, 9, 0, tzinfo=ET)),
            NewsItem("TEST", "future news", datetime(2024, 2, 16, 9, 0, tzinfo=ET)),
        ]
        provider.news = lambda symbol: items  # type: ignore[method-assign]
        view = AsOfView(provider, datetime(2024, 2, 15, 12, 0))
        visible = view.news("TEST")
        assert [n.title for n in visible] == ["old news"]


class TestViewMetadata:
    def test_freshness_passes_through(self, synthetic_provider):
        view = AsOfView(synthetic_provider, datetime(2024, 2, 15, 18, 0))
        assert view.quote_freshness() == Freshness.EOD

    def test_last_complete_session_rolls_back_before_close(self):
        provider = SyntheticProvider(make_bars("2024-01-02", "2024-03-28"))
        assert AsOfView(provider, datetime(2024, 2, 15, 9, 30)).last_complete_session() == (
            pd.Timestamp("2024-02-14")
        )
        assert AsOfView(provider, datetime(2024, 2, 15, 16, 0)).last_complete_session() == (
            pd.Timestamp("2024-02-15")
        )
