"""Pre-market scanner: gates, ranking, the <=10 cap, and honest unknowns."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from tests.intraday_fixtures import IntradayProvider, bars_from_closes, daily_history

from watchman.config import SignalsConfig
from watchman.data.provider import ET, AsOfView, Fundamentals, NewsItem
from watchman.signals.scanner import (
    TYPICAL_PREMARKET_FRACTION,
    ScannerInput,
    build_scanner_input,
    scan_premarket,
)

CFG = SignalsConfig()  # defaults: min_price 5, min $vol 20M, gap 2%, relvol 1.5


def make_input(**overrides) -> ScannerInput:
    base = {
        "symbol": "TEST",
        "prev_close": 100.0,
        "last_price": 104.0,          # +4% gap
        "premarket_volume": 500_000.0,
        "avg_daily_volume": 5_000_000.0,  # relvol = 0.5M / (5M*0.05) = 2.0x
        "atr": 3.0,
        "float_shares": 50_000_000.0,
        "catalyst": True,
    }
    base.update(overrides)
    return ScannerInput(**base)


class TestGates:
    def test_passing_candidate_has_expected_numbers(self):
        outcome = scan_premarket([make_input()], CFG)
        assert len(outcome.candidates) == 1
        c = outcome.candidates[0]
        assert c.gap_pct == pytest.approx(4.0)
        assert c.rel_vol == pytest.approx(2.0)
        assert c.atr_pct == pytest.approx(3.0)
        assert c.score == pytest.approx(8.0)  # |gap| x relvol

    @pytest.mark.parametrize(
        ("override", "reason_part"),
        [
            ({"last_price": 4.0, "prev_close": 3.9}, "price"),
            ({"avg_daily_volume": 100_000.0}, "illiquid"),
            ({"last_price": 101.0}, "gap"),
            ({"premarket_volume": 100_000.0}, "rel vol"),
        ],
    )
    def test_each_gate_rejects_with_reason(self, override, reason_part):
        outcome = scan_premarket([make_input(**override)], CFG)
        assert outcome.candidates == []
        assert any(reason_part in reason for reason in outcome.rejected)

    def test_gap_down_counts_too(self):
        outcome = scan_premarket([make_input(last_price=95.0)], CFG)
        assert len(outcome.candidates) == 1
        assert outcome.candidates[0].gap_pct == pytest.approx(-5.0)


class TestRankingAndCap:
    def test_focus_list_capped_at_ten_ranked_by_score(self):
        inputs = [
            make_input(symbol=f"S{i:02d}", last_price=100.0 + 2 + i * 0.5)
            for i in range(15)  # gaps 2%..9%, all passing
        ]
        outcome = scan_premarket(inputs, CFG)
        assert len(outcome.candidates) == 10  # spec cap
        scores = [c.score for c in outcome.candidates]
        assert scores == sorted(scores, reverse=True)
        assert outcome.candidates[0].symbol == "S14"  # biggest gap first

    def test_scanned_count_reported(self):
        outcome = scan_premarket([make_input(), make_input(last_price=100.5)], CFG)
        assert outcome.scanned == 2


class TestBuildScannerInput:
    def _provider(self, news: list | None = None) -> IntradayProvider:
        day = date.today()
        premarket = bars_from_closes(
            [102.0, 103.0, 104.0], day, start=time(8, 0), interval_minutes=5,
            volume=200_000.0,
        )
        return IntradayProvider(
            {
                "TEST": {
                    "daily": daily_history(prev_close=100.0, avg_volume=5_000_000.0),
                    "premarket": premarket,
                    "fundamentals": Fundamentals(
                        symbol="TEST", fetched_at=datetime.now(),
                        float_shares=42_000_000.0,
                    ),
                    **({"news": news} if news is not None else {}),
                }
            }
        )

    def _view(self, provider) -> AsOfView:
        return AsOfView(provider, datetime.combine(date.today(), time(9, 0), tzinfo=ET))

    def test_assembles_gap_and_premarket_volume(self):
        inp = build_scanner_input(self._view(self._provider()), "TEST")
        assert inp.prev_close == pytest.approx(100.0)
        assert inp.last_price == pytest.approx(104.0)
        assert inp.premarket_volume == pytest.approx(600_000.0)
        assert inp.float_shares == pytest.approx(42_000_000.0)

    def test_news_catalyst_true_for_fresh_headline(self):
        # Published 6:00 ET today: before the 9:00 pin AND within 18h of it.
        published = datetime.combine(date.today(), time(6, 0), tzinfo=ET)
        fresh = NewsItem("TEST", "earnings beat", published)
        inp = build_scanner_input(self._view(self._provider(news=[fresh])), "TEST")
        assert inp.catalyst is True

    def test_news_catalyst_false_for_stale_headlines_only(self):
        published = datetime.combine(date.today(), time(6, 0), tzinfo=ET) - timedelta(days=5)
        stale = NewsItem("TEST", "old story", published)
        inp = build_scanner_input(self._view(self._provider(news=[stale])), "TEST")
        assert inp.catalyst is False

    def test_no_news_support_is_unknown_not_false(self):
        inp = build_scanner_input(self._view(self._provider()), "TEST")
        assert inp.catalyst is None

    def test_premarket_fraction_is_documented_approximation(self):
        # The constant the rel-vol approximation depends on is explicit.
        assert TYPICAL_PREMARKET_FRACTION == 0.05
