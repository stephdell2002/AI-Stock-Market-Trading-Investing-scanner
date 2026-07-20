"""SignalEngine gates: every spec field present, R:R >= 2 enforced, sizing per
risk config, circuit breaker, max positions, freshness labeling, confidence."""

from __future__ import annotations

from datetime import datetime

import pytest

from watchman.config import RiskConfig
from watchman.data.provider import ET, Freshness
from watchman.signals.engine import SignalEngine
from watchman.signals.model import GateState, RejectedSignal, Signal, SignalDraft

RISK = RiskConfig()  # 1% risk, 3 positions, -2% breaker, 2:1 minimum
NOW = datetime(2026, 7, 20, 10, 35, tzinfo=ET)


def draft(**overrides) -> SignalDraft:
    base = {
        "symbol": "TEST", "setup": "orb-15m", "direction": "long",
        "entry": 100.0, "stop": 98.0, "rationale": "test rationale citing 100.00",
    }
    base.update(overrides)
    return SignalDraft(**base)


def engine(freshness=Freshness.DELAYED, equity=10_000.0, confidence=None) -> SignalEngine:
    kwargs = {"risk": RISK, "day_equity": equity, "freshness": freshness}
    if confidence is not None:
        kwargs["confidence"] = confidence
    return SignalEngine(**kwargs)


def state(**kw) -> GateState:
    kw.setdefault("now", NOW)
    return GateState(**kw)


class TestSpecFields:
    def test_signal_carries_every_required_field(self):
        s = engine().finalize(draft(), state())
        assert isinstance(s, Signal)
        assert s.symbol == "TEST"
        assert s.setup == "orb-15m"
        assert s.entry == 100.0
        assert s.stop == 98.0
        assert s.targets == (104.0, 106.0)      # 2R and 3R
        assert s.risk_reward == pytest.approx(2.0)
        # 1% of $10k = $100 risk / $2 per share = 50 shares.
        assert s.shares == 50
        assert s.risk_dollars == pytest.approx(100.0)
        assert s.confidence_win_rate is None    # no ledger yet
        assert "no live signals" in s.confidence_label
        assert s.rationale
        assert s.created_at == NOW

    def test_short_targets_point_down(self):
        s = engine().finalize(draft(direction="short", entry=100.0, stop=102.0), state())
        assert isinstance(s, Signal)
        assert s.targets == (96.0, 94.0)

    def test_delayed_data_labeled_not_actionable(self):
        s = engine(freshness=Freshness.DELAYED).finalize(draft(), state())
        assert s.actionable is False
        assert "NOT ACTIONABLE" in s.freshness_label

    def test_realtime_data_is_actionable(self):
        s = engine(freshness=Freshness.REALTIME).finalize(draft(), state())
        assert s.actionable is True

    def test_confidence_comes_from_ledger_when_available(self):
        class FakeLedger:
            def rolling_win_rate(self, setup, days=30):
                return 0.62, 21

        s = engine(confidence=FakeLedger()).finalize(draft(), state())
        assert s.confidence_win_rate == pytest.approx(0.62)
        assert "62% live win rate (21 signals" in s.confidence_label


class TestGates:
    def test_invalid_stop_rejected(self):
        r = engine().finalize(draft(stop=101.0), state())  # stop above long entry
        assert isinstance(r, RejectedSignal)
        assert "invalid stop" in r.reason

    def test_unsizeable_trade_rejected(self):
        # Stop $50 away on a $10k account: 1% risk = $100 -> 2 shares works;
        # make it truly unsizeable with a tiny account.
        r = engine(equity=40.0).finalize(draft(), state())
        assert isinstance(r, RejectedSignal)
        assert "unsizeable" in r.reason

    def test_circuit_breaker_stops_signals(self):
        r = engine().finalize(draft(), state(day_pnl_pct=-2.5))
        assert isinstance(r, RejectedSignal)
        assert "CIRCUIT BREAKER" in r.reason

    def test_circuit_breaker_not_tripped_above_threshold(self):
        s = engine().finalize(draft(), state(day_pnl_pct=-1.5))
        assert isinstance(s, Signal)

    def test_max_concurrent_positions_enforced(self):
        r = engine().finalize(draft(), state(open_day_positions=3))
        assert isinstance(r, RejectedSignal)
        assert "max concurrent" in r.reason

    def test_unknown_book_reported_as_unenforced_not_assumed(self):
        e = engine()
        s = e.finalize(draft(), state())  # day_pnl_pct=None, positions=None
        assert isinstance(s, Signal)
        assert any("circuit breaker" in g for g in e.unenforced_gates)
        assert any("concurrent" in g for g in e.unenforced_gates)

    def test_duplicate_suppressed(self):
        e = engine()
        st = state()
        first = e.finalize(draft(), st)
        second = e.finalize(draft(), st)
        assert isinstance(first, Signal)
        assert isinstance(second, RejectedSignal)
        assert "duplicate" in second.reason

    def test_rr_gate_uses_config_minimum(self):
        strict = RiskConfig(min_risk_reward=2.5)
        e = SignalEngine(risk=strict, day_equity=10_000.0, freshness=Freshness.DELAYED)
        r = e.finalize(draft(), state())
        # Targets are 2R/3R; first target R:R = 2.0 < required 2.5.
        assert isinstance(r, RejectedSignal)
        assert "R:R 2.0 < required 2.5" in r.reason
