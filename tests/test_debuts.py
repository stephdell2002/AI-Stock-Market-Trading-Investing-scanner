"""Module F (Debuts): IPO-calendar parsing, cohort math, and the deterministic
verdict scorecard. The verdict tests are known-answer — proving the conclusion
is a pure function of the numbers, not an opinion."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from watchman.data.provider import Freshness, Fundamentals, IpoEvent
from watchman.debuts.analysis import (
    IPO_SKEPTICAL_PRIOR,
    STANDING_CAUTIONS,
    analyze_debut,
)
from watchman.debuts.comparables import (
    CandidateData,
    build_cohort,
    post_ipo_performance,
    size_bucket,
)
from watchman.debuts.model import ComparableStats


def bars_from(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    return pd.DataFrame({"close": closes, "open": closes, "high": closes,
                         "low": closes, "adj_close": closes, "volume": 1e6}, index=idx)


def event(symbol="NEW", sector_hint="Tech", offer=20.0, ipo=date(2025, 2, 21)) -> IpoEvent:
    return IpoEvent(symbol=symbol, name=f"{symbol} Inc", ipo_date=ipo,
                    offer_price=offer, status="priced")


# ------------------------------------------------------------ size buckets ---
class TestSizeBucket:
    @pytest.mark.parametrize(
        ("cap", "bucket"),
        [(100e6, "micro"), (1e9, "small"), (5e9, "mid"), (50e9, "large"),
         (None, "unknown"), (0, "unknown")],
    )
    def test_buckets(self, cap, bucket):
        assert size_bucket(cap) == bucket


# --------------------------------------------------------- performance math --
class TestPostIpoPerformance:
    def test_known_returns_and_pop(self):
        # first close 22 (offer 20 -> +10% pop); +21 sessions = 26.4 -> +20%.
        closes = [22.0] + [22.0] * 20 + [26.4] + [22.0] * 200
        perf = post_ipo_performance(bars_from(closes), offer_price=20.0)
        assert perf["first_day_pop_pct"] == pytest.approx(10.0)
        assert perf["returns"][21] == pytest.approx(20.0)

    def test_max_drawdown(self):
        closes = [100.0, 120.0, 60.0, 90.0]  # peak 120 -> trough 60 = -50%
        perf = post_ipo_performance(bars_from(closes), offer_price=100.0)
        assert perf["max_drawdown_pct"] == pytest.approx(-50.0)

    def test_missing_horizon_is_none_not_guessed(self):
        perf = post_ipo_performance(bars_from([20.0, 21.0, 22.0]), offer_price=20.0)
        assert perf["returns"][63] is None      # not enough history
        assert perf["returns"][21] is None

    def test_no_offer_means_no_pop(self):
        perf = post_ipo_performance(bars_from([20.0] * 30), offer_price=None)
        assert perf["first_day_pop_pct"] is None

    def test_empty_bars(self):
        perf = post_ipo_performance(pd.DataFrame(), offer_price=20.0)
        assert perf["returns"][21] is None
        assert perf["max_drawdown_pct"] is None


# -------------------------------------------------------------- cohort build --
class TestBuildCohort:
    def _candidates(self):
        return [event(symbol=f"C{i}") for i in range(10)]

    def test_matches_sector_and_size_first(self):
        cands = self._candidates()

        def fetch(e):
            # All Tech, mid-cap, +10% at 90d.
            closes = [20.0] + [20.0] * 62 + [22.0] + [20.0] * 100
            return CandidateData("Tech", 5e9, bars_from(closes), offer_price=20.0)

        cohort = build_cohort("Tech", "mid", cands, fetch, min_cohort=5)
        assert cohort is not None
        assert cohort.basis == "sector+size"
        assert cohort.sample == 10
        assert cohort.median_90d == pytest.approx(10.0)
        assert cohort.pct_positive_90d == 1.0

    def test_widens_to_sector_when_size_too_thin(self):
        cands = self._candidates()

        def fetch(e):
            # Tech but LARGE-cap (target wants mid) -> sector+size won't hit 5.
            closes = [20.0] + [20.0] * 62 + [18.0] + [20.0] * 100  # -10% at 90d
            return CandidateData("Tech", 50e9, bars_from(closes), offer_price=20.0)

        cohort = build_cohort("Tech", "mid", cands, fetch, min_cohort=5)
        assert cohort.basis == "sector"          # widened
        assert cohort.median_90d == pytest.approx(-10.0)

    def test_widens_to_all_recent_when_sector_unknown(self):
        cands = self._candidates()

        def fetch(e):
            closes = [20.0] + [20.0] * 62 + [24.0] + [20.0] * 100
            return CandidateData("Energy", 5e9, bars_from(closes), offer_price=20.0)

        cohort = build_cohort("Tech", "mid", cands, fetch, min_cohort=5)
        assert cohort.basis == "all-recent"

    def test_skips_symbols_without_bars(self):
        def fetch(e):
            return None  # nothing fetchable

        assert build_cohort("Tech", "mid", self._candidates(), fetch) is None


# ----------------------------------------------------- the verdict scorecard --
class TestVerdictKnownAnswer:
    """The verdict must be a deterministic function of the numbers. Every case
    here fixes the inputs and checks the resulting label/score."""

    def _analyze(self, cohort=None, vs_offer=None, fundamentals=None,
                 is_trading=True, days_since=10):
        return analyze_debut(
            event(), freshness=Freshness.REALTIME, is_trading=is_trading,
            days_since_ipo=days_since, current_price=22.0, vs_offer_pct=vs_offer,
            first_day_pop_pct=None, cohort=cohort, fundamentals=fundamentals,
        )

    def _cohort(self, median_90d, pct_pos, sample=10, basis="sector+size"):
        return ComparableStats(
            basis=basis, sample=sample, members=[f"C{i}" for i in range(sample)],
            median_return={21: median_90d / 2, 63: median_90d, 126: median_90d},
            pct_positive_90d=pct_pos, median_first_day_pop=8.0,
            median_max_drawdown=-25.0,
        )

    def test_starts_from_skeptical_prior(self):
        # No cohort, no fundamentals -> capped at CAUTION, score near prior.
        a = self._analyze()
        assert a.verdict.label == "CAUTION"
        assert "Insufficient data" in " ".join(a.verdict.cautions)

    def test_strong_positive_base_rate_can_lean_favorable(self):
        # +25% median, 80% positive, near offer -> should clear FAVORABLE_AT.
        a = self._analyze(cohort=self._cohort(25.0, 0.80), vs_offer=5.0)
        # prior -10 + base(25 + (0.8-0.5)*40=12 -> 37) + near-offer 5 = +32.
        assert a.verdict.score == pytest.approx(32.0)
        assert a.verdict.label == "LEAN_FAVORABLE"

    def test_negative_base_rate_is_avoid(self):
        a = self._analyze(cohort=self._cohort(-20.0, 0.30), vs_offer=5.0)
        # prior -10 + (-20 + (0.3-0.5)*40=-8 = -28) + 5 = -33.
        assert a.verdict.score == pytest.approx(-33.0)
        assert a.verdict.label == "AVOID"

    def test_hot_pop_penalizes_even_a_good_cohort(self):
        near = self._analyze(cohort=self._cohort(20.0, 0.70), vs_offer=5.0)
        hot = self._analyze(cohort=self._cohort(20.0, 0.70), vs_offer=90.0)
        assert hot.verdict.score < near.verdict.score
        assert any("pop has largely happened" in r for r in hot.verdict.reasons)

    def test_thin_cohort_is_downweighted(self):
        big = self._analyze(cohort=self._cohort(20.0, 0.70, sample=10), vs_offer=5.0)
        thin = self._analyze(cohort=self._cohort(20.0, 0.70, sample=6), vs_offer=5.0)
        assert thin.verdict.score < big.verdict.score   # 0.4x weight
        assert any("down-weighted" in c for c in thin.verdict.cautions)

    def test_unprofitable_fundamentals_subtract(self):
        fund = Fundamentals(symbol="NEW", fetched_at=datetime.now(),
                            trailing_pe=None, profit_margin=-0.20)
        with_loss = self._analyze(cohort=self._cohort(20.0, 0.70), vs_offer=5.0,
                                  fundamentals=fund)
        assert any("Unprofitable at listing" in r for r in with_loss.verdict.reasons)

    def test_standing_cautions_always_present(self):
        a = self._analyze(cohort=self._cohort(25.0, 0.80), vs_offer=5.0)
        for caution in STANDING_CAUTIONS:
            assert caution in a.verdict.cautions

    def test_lockup_caution_for_recent_debut(self):
        a = self._analyze(cohort=self._cohort(20.0, 0.70), vs_offer=5.0, days_since=30)
        assert any("lockup" in c.lower() for c in a.verdict.cautions)

    def test_verdict_never_says_buy(self):
        for pct in (0.9, 0.95):
            a = self._analyze(cohort=self._cohort(30.0, pct), vs_offer=0.0)
            assert a.verdict.label in ("NEUTRAL", "LEAN_FAVORABLE")  # never "BUY"

    def test_prior_is_negative(self):
        assert IPO_SKEPTICAL_PRIOR < 0  # the whole model starts skeptical
