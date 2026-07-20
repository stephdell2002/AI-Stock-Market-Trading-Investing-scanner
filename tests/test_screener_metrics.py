"""Known-answer tests for metric extraction. Every expected value here is
hand-computable from the fixture inputs."""

from __future__ import annotations

import pytest
from tests.screener_fixtures import make_fundamentals, make_price_history, make_statements

from watchman.screener.metrics import (
    cagr_pct,
    collect_raw_metrics,
    growth_metrics,
    line,
    momentum_metrics,
    quality_metrics,
    valuation_inputs,
)


class TestLine:
    def test_finds_by_alias_case_insensitive(self):
        stmts = make_statements()
        s = line(stmts.income, "total revenue")
        assert s is not None
        assert s.iloc[-1] == pytest.approx(172.8)
        assert s.index.is_monotonic_increasing  # oldest first after normalization

    def test_missing_line_is_none(self):
        stmts = make_statements()
        assert line(stmts.income, "Nonexistent Item") is None

    def test_empty_frame_is_none(self):
        import pandas as pd

        assert line(pd.DataFrame(), "Total Revenue") is None


class TestGrowth:
    def test_revenue_cagr_known_answer(self):
        # 100 -> 172.8 over exactly 3 years = 20% CAGR.
        m = growth_metrics(make_statements())
        assert m["revenue_cagr"] == pytest.approx(20.0, abs=0.1)

    def test_eps_cagr_known_answer(self):
        m = growth_metrics(make_statements())  # 1.0 -> 1.728 over 3y
        assert m["eps_cagr"] == pytest.approx(20.0, abs=0.1)

    def test_negative_start_eps_is_unknowable(self):
        stmts = make_statements(diluted_eps=[-0.5, 0.2, 0.8, 1.5])
        assert growth_metrics(stmts)["eps_cagr"] is None

    def test_cagr_needs_three_points(self):
        import pandas as pd

        s = pd.Series([100.0, 120.0], index=pd.to_datetime(["2024-12-31", "2025-12-31"]))
        assert cagr_pct(s) is None


class TestQuality:
    def test_roic_known_answer(self):
        # EBIT 200, tax rate 47.5/190 = 25% -> NOPAT 150; IC 1000 -> 15%.
        m = quality_metrics(make_statements(), make_fundamentals())
        assert m["roic"] == pytest.approx(15.0, abs=0.01)

    def test_debt_to_ebitda_known_answer(self):
        m = quality_metrics(make_statements(), make_fundamentals())  # 300/150
        assert m["debt_to_ebitda"] == pytest.approx(2.0)

    def test_fcf_conversion_known_answer(self):
        m = quality_metrics(make_statements(), make_fundamentals())  # 90/100
        assert m["fcf_conversion"] == pytest.approx(90.0)

    def test_fcf_conversion_unknowable_when_unprofitable(self):
        stmts = make_statements(net_income=-50.0)
        assert quality_metrics(stmts, None)["fcf_conversion"] is None

    def test_gross_margin_trend_known_answer(self):
        # Margins 40, 42, 44, 46% across four year-ends -> ~ +2pp/yr.
        stmts = make_statements(
            revenue=[100.0] * 4, gross_profit=[40.0, 42.0, 44.0, 46.0]
        )
        m = quality_metrics(stmts, None)
        assert m["gross_margin_trend"] == pytest.approx(2.0, abs=0.05)

    def test_no_statements_all_none_except_info_fallbacks(self):
        fund = make_fundamentals(total_debt=300.0, ebitda=150.0)
        m = quality_metrics(None, fund)
        assert m["roic"] is None
        assert m["gross_margin_trend"] is None
        assert m["debt_to_ebitda"] == pytest.approx(2.0)  # info fallback


class TestValuation:
    def test_fcf_yield_prefers_statement_fcf(self):
        # Statement FCF 90 / market cap 3000 = 3%.
        m = valuation_inputs(make_fundamentals(), make_statements())
        assert m["fcf_yield"] == pytest.approx(3.0)

    def test_fcf_yield_falls_back_to_info(self):
        m = valuation_inputs(make_fundamentals(free_cash_flow=150.0), None)
        assert m["fcf_yield"] == pytest.approx(5.0)

    def test_negative_pe_is_unknowable(self):
        m = valuation_inputs(make_fundamentals(trailing_pe=-8.0), None)
        assert m["trailing_pe"] is None

    def test_ev_ebitda_computed_when_ratio_missing(self):
        fund = make_fundamentals(ev_to_ebitda=None, enterprise_value=1500.0, ebitda=100.0)
        assert valuation_inputs(fund, None)["ev_to_ebitda"] == pytest.approx(15.0)


class TestMomentum:
    def test_relative_strength_known_answer(self):
        # Stock +0.1%/day vs SPY flat: 126-day rel = (1.001^126 - 1)*100.
        stock = make_price_history(0.001, days=300)
        spy = make_price_history(0.0, days=300)
        m = momentum_metrics(stock, spy)
        assert m["rel_strength_6m"] == pytest.approx((1.001**126 - 1) * 100, rel=1e-6)
        assert m["rel_strength_12m"] == pytest.approx((1.001**252 - 1) * 100, rel=1e-6)

    def test_short_history_is_unknowable_not_zero(self):
        stock = make_price_history(0.001, days=140)  # enough for 6m, not 12m
        spy = make_price_history(0.0, days=300)
        m = momentum_metrics(stock, spy)
        assert m["rel_strength_6m"] is not None
        assert m["rel_strength_12m"] is None


def test_collect_raw_metrics_merges_all_pillars():
    got = collect_raw_metrics(
        make_fundamentals(),
        make_statements(),
        make_price_history(0.001, days=300),
        make_price_history(0.0004, days=300),
    )
    assert got["roic"] == pytest.approx(15.0, abs=0.01)
    assert got["revenue_cagr"] == pytest.approx(20.0, abs=0.1)
    assert got["fcf_yield"] == pytest.approx(3.0)
    assert got["rel_strength_6m"] is not None
