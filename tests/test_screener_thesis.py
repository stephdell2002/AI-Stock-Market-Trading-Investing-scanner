"""Theses must cite real numbers, name the weak spot, and flag thin data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from watchman.screener.thesis import build_thesis


def score_row(**overrides) -> pd.Series:
    row = {
        "name": "Acme Corp", "sector": "Tech", "rank": 2.0, "composite": 78.0,
        "quality": 90.0, "growth": 70.0, "valuation": 60.0, "momentum": 40.0,
        "coverage": 1.0,
    }
    row.update(overrides)
    return pd.Series(row)


def metric_row(**overrides) -> pd.Series:
    row = {
        "roic": 24.5, "debt_to_ebitda": 1.2, "fcf_conversion": 105.0,
        "gross_margin_trend": 0.8, "operating_margin_trend": 0.4,
        "revenue_cagr": 14.2, "eps_cagr": 18.9,
        "fcf_yield": 4.1, "trailing_pe": 21.0, "ev_to_ebitda": 13.0,
        "rel_strength_6m": -3.2, "rel_strength_12m": 5.6,
    }
    row.update(overrides)
    return pd.Series(row)


def test_thesis_cites_numbers_from_strongest_pillar():
    t = build_thesis("ACME", score_row(), metric_row(), 500, sector_pe=18.0)
    assert "Acme Corp scores 78/100 (#2 of 500)" in t
    assert "led by quality" in t
    assert "ROIC 24.5%" in t
    assert "debt/EBITDA 1.2x" in t


def test_thesis_names_weakest_pillar():
    t = build_thesis("ACME", score_row(), metric_row(), 500, sector_pe=18.0)
    assert "Weak spot: momentum" in t
    assert "-3.2% vs SPY over 6m" in t


def test_thesis_shows_sector_median_pe_when_valuation_mentioned():
    row = score_row(valuation=95.0, quality=50.0)  # valuation now leads
    t = build_thesis("ACME", row, metric_row(), 500, sector_pe=18.0)
    assert "P/E 21.0 vs sector median 18.0" in t


def test_low_coverage_warns():
    t = build_thesis("ACME", score_row(coverage=0.4), metric_row(), 500, sector_pe=None)
    assert "treat this rank skeptically" in t


def test_missing_metrics_are_skipped_not_faked():
    metrics = metric_row(roic=np.nan, debt_to_ebitda=np.nan, fcf_conversion=np.nan,
                         gross_margin_trend=np.nan, operating_margin_trend=np.nan)
    t = build_thesis("ACME", score_row(), metrics, 500, sector_pe=None)
    assert "ROIC" not in t
    assert "nan" not in t.lower()


def test_two_to_three_sentence_range_holds_roughly():
    t = build_thesis("ACME", score_row(), metric_row(), 500, sector_pe=18.0)
    n_sentences = t.count(". ") + 1
    assert 2 <= n_sentences <= 4
