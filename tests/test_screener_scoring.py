"""Cross-sectional scoring: ranking direction, sector-relative valuation,
missing-pillar renormalization, coverage."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from watchman.screener.metrics import ALL_METRICS
from watchman.screener.scoring import score_universe

WEIGHTS = {"quality": 0.30, "growth": 0.25, "valuation": 0.25, "momentum": 0.20}


def raw_frame(rows: dict[str, dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(rows, orient="index")
    df["name"] = df.index + " Corp"
    if "sector" not in df.columns:
        df["sector"] = "Tech"
    return df


def base_row(**overrides) -> dict:
    row = {
        "roic": 15.0, "gross_margin_trend": 0.5, "operating_margin_trend": 0.5,
        "debt_to_ebitda": 2.0, "fcf_conversion": 90.0,
        "revenue_cagr": 10.0, "eps_cagr": 10.0,
        "fcf_yield": 4.0, "trailing_pe": 20.0, "ev_to_ebitda": 12.0,
        "rel_strength_6m": 0.0, "rel_strength_12m": 0.0,
    }
    row.update(overrides)
    return row


def test_better_on_everything_ranks_first():
    out = score_universe(
        raw_frame(
            {
                "STRONG": base_row(roic=30.0, revenue_cagr=25.0, trailing_pe=10.0,
                                   rel_strength_12m=20.0, debt_to_ebitda=0.5),
                "MID": base_row(),
                "WEAK": base_row(roic=3.0, revenue_cagr=-5.0, trailing_pe=50.0,
                                 rel_strength_12m=-15.0, debt_to_ebitda=6.0),
            }
        ),
        WEIGHTS,
    )
    assert list(out.scores.index) == ["STRONG", "MID", "WEAK"]
    assert out.scores.loc["STRONG", "rank"] == 1


def test_lower_pe_scores_higher_valuation_all_else_equal():
    out = score_universe(
        raw_frame({"CHEAP": base_row(trailing_pe=10.0), "DEAR": base_row(trailing_pe=40.0)}),
        WEIGHTS,
    )
    assert out.scores.loc["CHEAP", "valuation"] > out.scores.loc["DEAR", "valuation"]


def test_lower_debt_scores_higher_quality_all_else_equal():
    out = score_universe(
        raw_frame({"LIGHT": base_row(debt_to_ebitda=0.5), "HEAVY": base_row(debt_to_ebitda=6.0)}),
        WEIGHTS,
    )
    assert out.scores.loc["LIGHT", "quality"] > out.scores.loc["HEAVY", "quality"]


def test_sector_relative_uses_sector_median_with_enough_peers():
    # 5 tech names at P/E 20 except one at 10; 5 utilities at P/E 10 except one at 20.
    rows = {}
    for i in range(4):
        rows[f"T{i}"] = base_row(sector="Tech", trailing_pe=20.0)
        rows[f"U{i}"] = base_row(sector="Utilities", trailing_pe=10.0)
    rows["TCHEAP"] = base_row(sector="Tech", trailing_pe=10.0)
    rows["UDEAR"] = base_row(sector="Utilities", trailing_pe=20.0)
    df = raw_frame(rows)
    df.loc[df.index.str.startswith("U"), "sector"] = "Utilities"
    out = score_universe(df, WEIGHTS)
    # Same absolute P/E (10 vs 20 medians differ): cheap-vs-sector beats dear-vs-sector.
    assert (
        out.metrics.loc["TCHEAP", "pe_vs_sector"]
        > out.metrics.loc["UDEAR", "pe_vs_sector"]
    )
    assert out.sector_medians.loc["Tech", "trailing_pe"] == pytest.approx(20.0)
    assert out.sector_medians.loc["Utilities", "trailing_pe"] == pytest.approx(10.0)


def test_small_sector_falls_back_to_universe_median():
    rows = {f"T{i}": base_row(sector="Tech", trailing_pe=20.0) for i in range(6)}
    rows["LONER"] = base_row(sector="Energy", trailing_pe=20.0)  # 1 peer only
    df = raw_frame(rows)
    df.loc["LONER", "sector"] = "Energy"
    out = score_universe(df, WEIGHTS)
    # Universe median is 20 -> pe_vs_sector = 1.0, not 20/20 within a 1-name "sector".
    assert out.metrics.loc["LONER", "pe_vs_sector"] == pytest.approx(1.0)


def test_missing_pillar_renormalizes_instead_of_zeroing():
    no_momentum = base_row()
    no_momentum["rel_strength_6m"] = None
    no_momentum["rel_strength_12m"] = None
    out = score_universe(
        raw_frame({"NOMO": no_momentum, "FULL": base_row(), "OTHER": base_row()}),
        WEIGHTS,
    )
    row = out.scores.loc["NOMO"]
    assert np.isnan(row["momentum"])
    # Composite is the weighted mean of the three present pillars, renormalized —
    # not dragged down by a phantom zero momentum score.
    present = ["quality", "growth", "valuation"]
    expected = sum(row[p] * WEIGHTS[p] for p in present) / sum(WEIGHTS[p] for p in present)
    assert row["composite"] == pytest.approx(expected)


def test_all_missing_symbol_is_unranked_not_ranked_last():
    empty = dict.fromkeys(base_row())
    out = score_universe(raw_frame({"GHOST": empty, "A": base_row(), "B": base_row()}), WEIGHTS)
    assert np.isnan(out.scores.loc["GHOST", "composite"])
    assert np.isnan(out.scores.loc["GHOST", "rank"])
    assert out.scores.loc["GHOST", "coverage"] == 0.0


def test_coverage_counts_metrics_present():
    partial = dict.fromkeys(base_row())
    partial.update({"roic": 10.0, "revenue_cagr": 5.0, "rel_strength_6m": 1.0})
    out = score_universe(raw_frame({"PART": partial, "A": base_row(), "B": base_row()}), WEIGHTS)
    assert out.scores.loc["PART", "coverage"] == pytest.approx(3 / len(ALL_METRICS))


def test_weights_are_respected():
    """A momentum-only winner beats a quality-only winner under momentum-heavy
    weights and loses under quality-heavy weights."""
    rows = raw_frame(
        {
            "QUAL": base_row(roic=40.0, debt_to_ebitda=0.2, fcf_conversion=150.0),
            "MOMO": base_row(rel_strength_6m=30.0, rel_strength_12m=50.0),
            "BLAH": base_row(),
        }
    )
    quality_heavy = score_universe(
        rows, {"quality": 0.7, "growth": 0.1, "valuation": 0.1, "momentum": 0.1}
    )
    momentum_heavy = score_universe(
        rows, {"quality": 0.1, "growth": 0.1, "valuation": 0.1, "momentum": 0.7}
    )
    assert (
        quality_heavy.scores.loc["QUAL", "composite"]
        > quality_heavy.scores.loc["MOMO", "composite"]
    )
    assert (
        momentum_heavy.scores.loc["MOMO", "composite"]
        > momentum_heavy.scores.loc["QUAL", "composite"]
    )
