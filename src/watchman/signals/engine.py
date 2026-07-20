"""SignalEngine: every draft passes through every gate, in one place.

Gates, in order (each rejection is returned with its reason, never dropped):
1. duplicate  — one signal per (symbol, setup, direction) per run/session
2. circuit breaker — day P&L <= risk.daily_circuit_breaker_pct stops all
   day-trade signals (skipped-but-reported until Module D supplies live P&L)
3. max concurrent positions (same: skipped-but-reported until Module D)
4. valid stop — risk per share must be positive
5. R:R >= risk.min_risk_reward (targets are 2R and 3R by default)
6. position size — shares_for_risk() > 0 under the 1%-risk / no-leverage caps

What comes out is a fully-populated Signal: entry/stop/targets, R:R, size,
confidence (rolling live win rate from the ledger — 'n/a' until Module D),
rationale, and the freshness label (DELAYED — NOT ACTIONABLE on free data).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from watchman.config import RiskConfig
from watchman.data.provider import ET, Freshness
from watchman.risk import shares_for_risk
from watchman.signals.model import (
    ConfidenceSource,
    GateState,
    NoLiveHistory,
    RejectedSignal,
    Signal,
    SignalDraft,
)

TARGET_R_MULTIPLES = (2.0, 3.0)


@dataclass
class SignalEngine:
    risk: RiskConfig
    day_equity: float
    freshness: Freshness
    confidence: ConfidenceSource = field(default_factory=NoLiveHistory)
    unenforced_gates: set[str] = field(default_factory=set)

    def finalize(self, draft: SignalDraft, state: GateState) -> Signal | RejectedSignal:
        key = (draft.symbol, draft.setup, draft.direction)

        def reject(reason: str) -> RejectedSignal:
            return RejectedSignal(draft.symbol, draft.setup, draft.direction, reason)

        if key in state.already_emitted:
            return reject("duplicate: already emitted this run")

        if state.day_pnl_pct is None:
            self.unenforced_gates.add(
                "circuit breaker (no live day P&L until Module D paper book)"
            )
        elif state.day_pnl_pct <= self.risk.daily_circuit_breaker_pct:
            return reject(
                f"CIRCUIT BREAKER: day P&L {state.day_pnl_pct:+.1f}% <= "
                f"{self.risk.daily_circuit_breaker_pct:+.1f}% — no more day-trade "
                "signals today"
            )

        if state.open_day_positions is None:
            self.unenforced_gates.add(
                "max concurrent positions (no live book until Module D)"
            )
        elif state.open_day_positions >= self.risk.max_concurrent_day_positions:
            return reject(
                f"max concurrent day positions reached "
                f"({state.open_day_positions}/{self.risk.max_concurrent_day_positions})"
            )

        risk_per_share = (
            draft.entry - draft.stop if draft.direction == "long" else draft.stop - draft.entry
        )
        if risk_per_share <= 0:
            return reject(
                f"invalid stop: {draft.stop:.2f} not protective of "
                f"{draft.direction} entry {draft.entry:.2f}"
            )

        sign = 1 if draft.direction == "long" else -1
        targets = tuple(
            round(draft.entry + sign * m * risk_per_share, 2) for m in TARGET_R_MULTIPLES
        )
        risk_reward = abs(targets[0] - draft.entry) / risk_per_share
        if risk_reward < self.risk.min_risk_reward - 1e-9:
            return reject(
                f"R:R {risk_reward:.1f} < required {self.risk.min_risk_reward:.1f}"
            )

        shares = shares_for_risk(
            self.day_equity, self.risk.max_risk_per_trade_pct, draft.entry, draft.stop
        )
        if shares == 0:
            return reject(
                f"unsizeable within {self.risk.max_risk_per_trade_pct}% risk of "
                f"${self.day_equity:,.0f} (stop {risk_per_share:.2f} away at "
                f"entry {draft.entry:.2f})"
            )

        win_rate, sample = self.confidence.rolling_win_rate(draft.setup)
        state.already_emitted.add(key)
        return Signal(
            symbol=draft.symbol,
            setup=draft.setup,
            direction=draft.direction,
            entry=round(draft.entry, 2),
            stop=round(draft.stop, 2),
            targets=targets,
            risk_reward=round(risk_reward, 2),
            shares=shares,
            risk_dollars=round(shares * risk_per_share, 2),
            confidence_win_rate=win_rate,
            confidence_sample=sample,
            rationale=draft.rationale,
            freshness=self.freshness,
            created_at=state.now or datetime.now(tz=ET),
        )
