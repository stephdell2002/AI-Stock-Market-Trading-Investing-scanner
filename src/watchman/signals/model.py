"""Signal data model — the spec's non-negotiable fields, enforced in one place.

Every emitted Signal carries: ticker, setup name, direction, entry, stop,
targets, risk:reward, position size per risk config, confidence (that setup's
rolling LIVE win rate + sample size), a one-line rationale, and a freshness
label. Signals built on non-realtime data are actionable=False and display
DELAYED — NOT ACTIONABLE. Anything that fails a gate becomes a
RejectedSignal with the reason shown, not a silently dropped signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from watchman.data.provider import Freshness

Direction = Literal["long", "short"]


class ConfidenceSource(Protocol):
    """Where confidence numbers come from: the signal ledger (Module D).
    Until it exists, NoLiveHistory below is the honest placeholder."""

    def rolling_win_rate(self, setup: str, days: int = 30) -> tuple[float | None, int]:
        """Return (win_rate 0..1 or None, sample_size) for the setup's live
        paper record over the trailing window."""
        ...


class NoLiveHistory:
    """No signal ledger yet (arrives with Module D): confidence is honestly
    'no live sample', never a made-up number."""

    def rolling_win_rate(self, setup: str, days: int = 30) -> tuple[float | None, int]:
        return None, 0


@dataclass(frozen=True)
class SignalDraft:
    """What a setup class proposes; the SignalEngine applies every gate."""

    symbol: str
    setup: str
    direction: Direction
    entry: float
    stop: float
    rationale: str


@dataclass(frozen=True)
class Signal:
    symbol: str
    setup: str
    direction: Direction
    entry: float
    stop: float
    targets: tuple[float, ...]
    risk_reward: float          # to the first target
    shares: int
    risk_dollars: float         # shares x |entry - stop|
    confidence_win_rate: float | None
    confidence_sample: int
    rationale: str
    freshness: Freshness
    created_at: datetime

    @property
    def actionable(self) -> bool:
        return self.freshness == Freshness.REALTIME

    @property
    def freshness_label(self) -> str:
        if self.actionable:
            return "REALTIME"
        return f"{self.freshness.value} — NOT ACTIONABLE"

    @property
    def confidence_label(self) -> str:
        if self.confidence_win_rate is None or self.confidence_sample == 0:
            return "n/a (no live signals logged yet)"
        return (
            f"{self.confidence_win_rate:.0%} live win rate "
            f"({self.confidence_sample} signals, 30d)"
        )


@dataclass(frozen=True)
class RejectedSignal:
    symbol: str
    setup: str
    direction: Direction
    reason: str


@dataclass
class GateState:
    """Portfolio-level context the engine needs to honor the risk limits.
    Values are None until Module D's paper book exists to supply them; a None
    check is skipped and reported as unenforced, never assumed safe."""

    day_pnl_pct: float | None = None
    open_day_positions: int | None = None
    already_emitted: set[tuple[str, str, str]] = field(default_factory=set)
    now: datetime | None = None  # signal timestamp; defaults to wall clock
