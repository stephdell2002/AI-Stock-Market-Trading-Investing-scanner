"""Cross-sectional scoring: raw metrics -> percentile ranks -> pillar scores
-> 0-100 composite.

Method, stated plainly so it can be challenged:
- Valuation raw inputs become sector-relative first: pe_vs_sector =
  sector_median_pe / pe (>1 = cheaper than sector). Sectors with fewer than
  MIN_SECTOR_PEERS names fall back to the universe median.
- Every metric is percentile-ranked across the universe (0-100, higher =
  better), with lower-is-better metrics negated before ranking. Percentile
  ranks are robust to outliers, so no winsorizing.
- Pillar score = mean of that pillar's available metric ranks.
- Composite = weighted mean of available pillar scores with weights
  renormalized, so a missing pillar doesn't silently drag a name to zero.
- coverage = fraction of the 12 metrics present. Low coverage is displayed,
  not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from watchman.screener.metrics import ALL_METRICS, LOWER_IS_BETTER, PILLAR_METRICS

MIN_SECTOR_PEERS = 5

PILLARS = list(PILLAR_METRICS)


@dataclass
class ScoreOutput:
    #: index=symbol; columns: name, sector, composite, quality, growth,
    #: valuation, momentum, coverage, rank (1 = best; NaN if unscoreable)
    scores: pd.DataFrame
    #: index=symbol; per-metric percentile ranks (0-100), NaN where missing
    metric_ranks: pd.DataFrame
    #: index=sector; columns trailing_pe, ev_to_ebitda (the medians used)
    sector_medians: pd.DataFrame
    #: index=symbol; final metric values incl. derived *_vs_sector columns
    metrics: pd.DataFrame


def _sector_relative(raw: pd.DataFrame, column: str) -> tuple[pd.Series, pd.Series]:
    """value -> sector_median/value (>1 = cheaper). Returns (metric, medians_by_symbol)."""
    values = raw[column]
    universe_median = values.median()
    sector_counts = raw.groupby("sector")[column].transform("count")
    sector_median = raw.groupby("sector")[column].transform("median")
    medians = sector_median.where(sector_counts >= MIN_SECTOR_PEERS, universe_median)
    metric = medians / values
    return metric.where(values > 0), medians


def score_universe(raw: pd.DataFrame, weights: dict[str, float]) -> ScoreOutput:
    """raw: index=symbol; columns include name, sector, the quality/growth/
    momentum metrics, and valuation raw inputs (fcf_yield, trailing_pe,
    ev_to_ebitda)."""
    metrics = raw.copy()
    metrics["pe_vs_sector"], pe_medians = _sector_relative(metrics, "trailing_pe")
    metrics["ev_ebitda_vs_sector"], ev_medians = _sector_relative(metrics, "ev_to_ebitda")

    sector_medians = pd.DataFrame(
        {
            "trailing_pe": pe_medians,
            "ev_to_ebitda": ev_medians,
            "sector": metrics["sector"],
        }
    ).groupby("sector").first()

    ranks = pd.DataFrame(index=metrics.index)
    for metric in ALL_METRICS:
        values = pd.to_numeric(metrics[metric], errors="coerce")
        if metric in LOWER_IS_BETTER:
            values = -values
        ranks[metric] = values.rank(pct=True) * 100

    scores = pd.DataFrame(index=metrics.index)
    scores["name"] = metrics.get("name", "")
    scores["sector"] = metrics["sector"]
    for pillar, pillar_metrics in PILLAR_METRICS.items():
        scores[pillar] = ranks[pillar_metrics].mean(axis=1)

    weight_vec = pd.Series(weights, dtype=float).reindex(PILLARS)
    pillar_scores = scores[PILLARS]
    available = pillar_scores.notna()
    weight_matrix = available.mul(weight_vec, axis=1)
    weight_sums = weight_matrix.sum(axis=1)
    weighted = pillar_scores.fillna(0.0).mul(weight_vec, axis=1).sum(axis=1)
    scores["composite"] = np.where(weight_sums > 0, weighted / weight_sums, np.nan)

    scores["coverage"] = ranks[ALL_METRICS].notna().sum(axis=1) / len(ALL_METRICS)
    scores["rank"] = scores["composite"].rank(ascending=False, method="first")
    scores = scores.sort_values("rank")

    return ScoreOutput(
        scores=scores,
        metric_ranks=ranks.loc[scores.index],
        sector_medians=sector_medians,
        metrics=metrics.loc[scores.index],
    )
