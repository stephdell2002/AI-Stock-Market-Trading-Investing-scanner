"""Data model for Module F (Debuts). All fields are data-derived; nothing here
is an opinion."""

from __future__ import annotations

from dataclasses import dataclass, field

from watchman.data.provider import IpoEvent

#: Verdict labels, deliberately hedged — there is no "BUY". The evidence for a
#: brand-new listing is weak by nature, and these words say so.
VERDICT_LABELS = ("AVOID", "CAUTION", "NEUTRAL", "LEAN_FAVORABLE")

#: Post-IPO horizons in trading sessions (~1mo / ~3mo / ~6mo).
HORIZONS = (21, 63, 126)


@dataclass(frozen=True)
class ComparableStats:
    """Empirical performance of past IPOs comparable to the target.

    `basis` records how the cohort was matched, so a widened (weaker) match is
    never hidden: 'sector+size' | 'sector' | 'all-recent'.
    """

    basis: str
    sample: int
    members: list[str]
    #: horizon sessions -> median % return from the first close
    median_return: dict[int, float]
    pct_positive_90d: float | None
    median_first_day_pop: float | None
    median_max_drawdown: float | None

    @property
    def median_90d(self) -> float | None:
        return self.median_return.get(63)


@dataclass(frozen=True)
class DebutVerdict:
    """The rules-based conclusion. `score` is a net evidence score (roughly
    -100..+100); `label` is derived from it by fixed thresholds. Every reason
    and caution is a string built from actual numbers or documented facts."""

    label: str
    score: float
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DebutAnalysis:
    event: IpoEvent
    freshness: str
    is_trading: bool
    days_since_ipo: int | None
    current_price: float | None
    vs_offer_pct: float | None       # current price vs offer/expected price
    first_day_pop_pct: float | None  # first close vs offer, if both known
    cohort: ComparableStats | None
    verdict: DebutVerdict
    coverage_note: str = ""
