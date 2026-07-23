"""The debut verdict — a deterministic, data-derived scorecard.

This is the module that must be "based on data, not on the model." The verdict
is a PURE FUNCTION of numbers: the comparable-cohort base rate, the listing's
price vs its offer, and whatever fundamentals exist. The constants below encode
DOCUMENTED empirical regularities about IPOs (not hunches), each with its
rationale in a comment, exactly like the screener's deteriorator thresholds.
Change them in the open; never hand-wave a verdict.

Key documented facts baked in as a skeptical prior:
- IPOs underperform the broad market on average in the years after listing
  (the long-run IPO underperformance literature).
- Buying AFTER a large first-day pop tends to underperform buying near offer;
  first-day retail buyers historically fare worst.
So the score starts negative and it is hard, by design, to reach the top label.
"""

from __future__ import annotations

from watchman.data.provider import Freshness, Fundamentals, IpoEvent
from watchman.debuts.comparables import MIN_COHORT
from watchman.debuts.model import ComparableStats, DebutAnalysis, DebutVerdict

# --- scorecard constants (explicit, arguable, documented) -------------------
IPO_SKEPTICAL_PRIOR = -10.0     # start below neutral: IPOs underperform on avg
MIN_CONFIDENT_COHORT = 8        # below this the base rate is down-weighted

BASE_RATE_RETURN_CAP = 30.0     # clamp cohort median 90d return contribution
PCT_POSITIVE_WEIGHT = 40.0      # (pct_positive - 0.5) * this

POP_HOT_THRESHOLD = 30.0        # % above offer that counts as "already popped"
POP_HOT_MAX_PENALTY = 25.0
NEAR_OFFER_BONUS = 5.0          # within +/-10% of offer: less pop risk
BROKEN_BELOW_OFFER = -20.0      # far below offer = weak demand
BROKEN_PENALTY = -5.0

LOW_CONFIDENCE_WEIGHT = 0.4     # base-rate multiplier when cohort is thin

# label thresholds on the net score
AVOID_AT = -20.0
CAUTION_AT = -5.0
FAVORABLE_AT = 15.0

STANDING_CAUTIONS = (
    "IPOs underperform the market on average in the years after listing; "
    "first-day retail buyers historically do worst.",
    "This is base-rate evidence from a small, survivorship-affected sample — "
    "weak evidence, NOT a recommendation.",
    "A newly public company has roughly one filing of history; the point-in-time "
    "record the rest of Watchman relies on does not exist yet for this ticker.",
)


def _label_for(score: float) -> str:
    if score <= AVOID_AT:
        return "AVOID"
    if score <= CAUTION_AT:
        return "CAUTION"
    if score < FAVORABLE_AT:
        return "NEUTRAL"
    return "LEAN_FAVORABLE"


def _base_rate_points(cohort: ComparableStats | None) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    cautions: list[str] = []
    if cohort is None or cohort.median_90d is None:
        cautions.append("No comparable cohort could be built — base rate unknown.")
        return 0.0, reasons, cautions

    median = max(-BASE_RATE_RETURN_CAP, min(BASE_RATE_RETURN_CAP, cohort.median_90d))
    points = median
    if cohort.pct_positive_90d is not None:
        points += (cohort.pct_positive_90d - 0.5) * PCT_POSITIVE_WEIGHT

    weight = 1.0 if cohort.sample >= MIN_CONFIDENT_COHORT else LOW_CONFIDENCE_WEIGHT
    points *= weight

    pct = "n/a" if cohort.pct_positive_90d is None else f"{cohort.pct_positive_90d:.0%}"
    reasons.append(
        f"Comparable cohort ({cohort.basis}, n={cohort.sample}): "
        f"median 90d return {cohort.median_90d:+.1f}%, {pct} positive at 90d"
        + (f", median max drawdown {cohort.median_max_drawdown:.0f}%"
           if cohort.median_max_drawdown is not None else "")
        + "."
    )
    if cohort.sample < MIN_COHORT:
        cautions.append(
            f"Cohort is tiny (n={cohort.sample}); its base rate is barely evidence."
        )
    elif cohort.sample < MIN_CONFIDENT_COHORT:
        cautions.append(
            f"Cohort is small (n={cohort.sample}); base rate is down-weighted."
        )
    if cohort.basis != "sector+size":
        cautions.append(
            f"Cohort matched on '{cohort.basis}', not sector+size — a looser comparison."
        )
    return points, reasons, cautions


def _pop_points(vs_offer_pct: float | None) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    cautions: list[str] = []
    if vs_offer_pct is None:
        return 0.0, reasons, cautions
    if vs_offer_pct >= POP_HOT_THRESHOLD:
        penalty = -min((vs_offer_pct - POP_HOT_THRESHOLD) / 2.0, POP_HOT_MAX_PENALTY)
        reasons.append(
            f"Trading {vs_offer_pct:+.1f}% vs offer — the first-day pop has largely "
            "happened; buying after a big pop historically underperforms buying near offer."
        )
        return penalty, reasons, cautions
    if -10.0 <= vs_offer_pct <= 10.0:
        reasons.append(
            f"Trading {vs_offer_pct:+.1f}% vs offer — near the offer price, so less "
            "post-pop downside than a hot open."
        )
        return NEAR_OFFER_BONUS, reasons, cautions
    if vs_offer_pct <= BROKEN_BELOW_OFFER:
        reasons.append(
            f"Trading {vs_offer_pct:+.1f}% vs offer — broken well below offer, a sign "
            "of weak demand (could be value or a dud; the data can't tell you which)."
        )
        return BROKEN_PENALTY, reasons, cautions
    return 0.0, reasons, cautions


def analyze_debut(
    event: IpoEvent,
    *,
    freshness: Freshness,
    is_trading: bool,
    days_since_ipo: int | None,
    current_price: float | None,
    vs_offer_pct: float | None,
    first_day_pop_pct: float | None,
    cohort: ComparableStats | None,
    fundamentals: Fundamentals | None,
    coverage_note: str = "",
) -> DebutAnalysis:
    """Compute the deterministic verdict for one debut. Pure given its inputs."""
    score = IPO_SKEPTICAL_PRIOR
    reasons: list[str] = []
    cautions: list[str] = list(STANDING_CAUTIONS)

    br_points, br_reasons, br_cautions = _base_rate_points(cohort)
    score += br_points
    reasons += br_reasons
    cautions += br_cautions

    pop_points, pop_reasons, pop_cautions = _pop_points(vs_offer_pct)
    score += pop_points
    reasons += pop_reasons
    cautions += pop_cautions

    # Profitability at IPO (documented risk: unprofitable IPOs fare worse).
    if fundamentals is not None:
        pe = fundamentals.trailing_pe
        margin = fundamentals.profit_margin
        if (pe is None or pe <= 0) and (margin is not None and margin < 0):
            score -= 5.0
            reasons.append(
                f"Unprofitable at listing (net margin {margin:.0%}); unprofitable IPOs "
                "have historically underperformed profitable ones."
            )

    # Lockup dilution risk for very recent debuts (standard 90-180d lockups).
    if is_trading and days_since_ipo is not None and days_since_ipo < 180:
        cautions.append(
            "Insider lockups typically expire ~90-180 days post-IPO; the added supply "
            "is a known headwind you are still inside the window for."
        )

    # Hard data-coverage gate: no base rate AND no fundamentals -> force CAUTION.
    if (cohort is None or cohort.median_90d is None) and fundamentals is None:
        score = min(score, CAUTION_AT)
        cautions.append(
            "Insufficient data (no cohort, no fundamentals) — verdict capped at CAUTION."
        )

    verdict = DebutVerdict(
        label=_label_for(score),
        score=round(score, 1),
        reasons=reasons,
        cautions=cautions,
    )
    return DebutAnalysis(
        event=event,
        freshness=freshness.value,
        is_trading=is_trading,
        days_since_ipo=days_since_ipo,
        current_price=current_price,
        vs_offer_pct=vs_offer_pct,
        first_day_pop_pct=first_day_pop_pct,
        cohort=cohort,
        verdict=verdict,
        coverage_note=coverage_note,
    )
