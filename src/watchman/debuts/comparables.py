"""Comparable-cohort construction and post-IPO performance math.

Pure functions: given already-fetched data, compute the empirical performance
distribution of past IPOs similar to a target. This is the base-rate evidence
the verdict rests on — no opinion, just what comparable new listings actually
did.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from watchman.data.provider import IpoEvent
from watchman.debuts.model import HORIZONS, ComparableStats

#: Market-cap buckets ($), explicit so a "comparable" is defined, not vibed.
SIZE_BUCKETS = [
    ("micro", 0.0, 300e6),
    ("small", 300e6, 2e9),
    ("mid", 2e9, 10e9),
    ("large", 10e9, float("inf")),
]

#: A cohort needs at least this many members before its base rate is treated as
#: anything more than a curiosity (the verdict weights it down below this).
MIN_COHORT = 5


def size_bucket(market_cap: float | None) -> str:
    if market_cap is None or market_cap <= 0:
        return "unknown"
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= market_cap < hi:
            return name
    return "large"


@dataclass(frozen=True)
class CandidateData:
    """What build_cohort needs about one past IPO (supplied by the caller's
    fetch, so this module stays pure and offline-testable)."""

    sector: str | None
    market_cap: float | None
    bars: pd.DataFrame            # daily bars from the IPO onward
    offer_price: float | None


def post_ipo_performance(bars: pd.DataFrame, offer_price: float | None) -> dict:
    """Performance of a single listing from its FIRST close.

    Returns a dict with per-horizon returns (%), first-day pop (% vs offer),
    and max drawdown (%). Missing values are None — never guessed."""
    out: dict = {
        "first_day_pop_pct": None,
        "max_drawdown_pct": None,
        "returns": {h: None for h in HORIZONS},
    }
    if bars is None or bars.empty:
        return out
    closes = bars["close"].to_numpy(dtype=float)
    first_close = closes[0]
    if first_close <= 0:
        return out
    if offer_price and offer_price > 0:
        out["first_day_pop_pct"] = (first_close / offer_price - 1) * 100
    for h in HORIZONS:
        if len(closes) > h:
            out["returns"][h] = (closes[h] / first_close - 1) * 100
    running_max = np.maximum.accumulate(closes)
    out["max_drawdown_pct"] = float((closes / running_max - 1).min()) * 100
    return out


def build_cohort(
    target_sector: str | None,
    target_bucket: str,
    candidates: list[IpoEvent],
    fetch: Callable[[IpoEvent], CandidateData | None],
    min_cohort: int = MIN_COHORT,
    max_fetch: int = 60,
) -> ComparableStats | None:
    """Build the comparable cohort, matching from most specific to least.

    Tries sector+size, then sector, then all-recent, stopping at the first tier
    that reaches `min_cohort`. `fetch` returns per-candidate data (or None to
    skip). `max_fetch` caps API work. Returns None only when nothing is usable.
    """
    fetched: list[tuple[IpoEvent, CandidateData, dict]] = []
    for event in candidates[:max_fetch]:
        data = fetch(event)
        if data is None or data.bars is None or data.bars.empty:
            continue
        perf = post_ipo_performance(data.bars, data.offer_price or event.expected_price)
        fetched.append((event, data, perf))

    if not fetched:
        return None

    def matches(data: CandidateData, tier: str) -> bool:
        if tier == "sector+size":
            return (
                target_sector is not None
                and data.sector == target_sector
                and size_bucket(data.market_cap) == target_bucket
            )
        if tier == "sector":
            return target_sector is not None and data.sector == target_sector
        return True  # all-recent

    for tier in ("sector+size", "sector", "all-recent"):
        group = [(e, d, p) for (e, d, p) in fetched if matches(d, tier)]
        if len(group) >= min_cohort or (tier == "all-recent" and group):
            return _aggregate(tier, group)
    # Nothing reached min_cohort; fall back to whatever the widest tier had.
    return _aggregate("all-recent", fetched)


def _aggregate(basis: str, group: list[tuple[IpoEvent, CandidateData, dict]]) -> ComparableStats:
    median_return: dict[int, float] = {}
    for h in HORIZONS:
        vals = [p["returns"][h] for (_e, _d, p) in group if p["returns"][h] is not None]
        if vals:
            median_return[h] = float(np.median(vals))

    r90 = [p["returns"][63] for (_e, _d, p) in group if p["returns"][63] is not None]
    pct_pos = (sum(1 for v in r90 if v > 0) / len(r90)) if r90 else None

    pops = [p["first_day_pop_pct"] for (_e, _d, p) in group if p["first_day_pop_pct"] is not None]
    dds = [p["max_drawdown_pct"] for (_e, _d, p) in group if p["max_drawdown_pct"] is not None]

    return ComparableStats(
        basis=basis,
        sample=len(group),
        members=[e.symbol for (e, _d, _p) in group],
        median_return=median_return,
        pct_positive_90d=pct_pos,
        median_first_day_pop=float(np.median(pops)) if pops else None,
        median_max_drawdown=float(np.median(dds)) if dds else None,
    )
