"""Module F — Debuts: detect new/upcoming stock listings and analyze them
against the empirical performance of comparable past IPOs.

The verdict is a deterministic, rules-based scorecard computed ENTIRELY from
data (a comparable-cohort base rate + the listing's own numbers), never from an
opinion. It is deliberately hedged (AVOID/CAUTION/NEUTRAL/LEAN_FAVORABLE) and
carries standing caveats, because IPOs underperform the market on average and a
brand-new ticker has almost no history to reason from.
"""

from watchman.debuts.analysis import analyze_debut
from watchman.debuts.comparables import (
    build_cohort,
    post_ipo_performance,
    size_bucket,
)
from watchman.debuts.model import ComparableStats, DebutAnalysis, DebutVerdict
from watchman.debuts.runner import DebutsResult, run_debuts

__all__ = [
    "ComparableStats",
    "DebutAnalysis",
    "DebutVerdict",
    "DebutsResult",
    "analyze_debut",
    "build_cohort",
    "post_ipo_performance",
    "run_debuts",
    "size_bucket",
]
