"""SignalLedger: recording, outcomes, rolling windows, confidence feedback,
and the live-vs-backtest divergence warning."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from watchman.data.provider import ET, Freshness
from watchman.db import connect
from watchman.paper import SignalLedger
from watchman.signals.model import Signal

NOW = datetime(2026, 7, 20, 10, 0, tzinfo=ET)


def make_signal(symbol="TEST", setup="orb-15m", entry=100.0, stop=98.0) -> Signal:
    return Signal(
        symbol=symbol, setup=setup, direction="long", entry=entry, stop=stop,
        targets=(entry + 2 * (entry - stop), entry + 3 * (entry - stop)),
        risk_reward=2.0, shares=50, risk_dollars=100.0,
        confidence_win_rate=None, confidence_sample=0,
        rationale="test", freshness=Freshness.DELAYED, created_at=NOW,
    )


@pytest.fixture
def ledger(tmp_path):
    return SignalLedger(connect(tmp_path / "l.db"))


def seed(ledger, outcomes: list[tuple[str, float]], setup="orb-15m",
         session=None) -> None:
    """Record + resolve a batch: outcomes = [(status, pnl), ...]."""
    session = session or NOW.date()
    for i, (status, pnl) in enumerate(outcomes):
        sid = ledger.record(make_signal(symbol=f"S{i}", setup=setup), session)
        ledger.resolve(sid, status, NOW, 100.0, pnl)


class TestRecordResolve:
    def test_r_multiple_computed_from_initial_risk(self, ledger):
        sid = ledger.record(make_signal(), NOW.date())  # risk = $2 x 50 = $100
        ledger.resolve(sid, "target", NOW, 104.0, 200.0)
        row = ledger.session_signals(NOW.date())[0]
        assert row.status == "target"
        assert row.r_multiple == pytest.approx(2.0)

    def test_bad_status_refused(self, ledger):
        sid = ledger.record(make_signal(), NOW.date())
        with pytest.raises(ValueError, match="status must be one of"):
            ledger.resolve(sid, "moon", NOW, 104.0, 200.0)

    def test_open_signals_listed_until_resolved(self, ledger):
        sid = ledger.record(make_signal(), NOW.date())
        assert len(ledger.open_signals()) == 1
        ledger.resolve(sid, "stopped", NOW, 98.0, -100.0)
        assert ledger.open_signals() == []

    def test_already_emitted_covers_the_session(self, ledger):
        ledger.record(make_signal(symbol="AAA"), NOW.date())
        keys = ledger.already_emitted(NOW.date())
        assert ("AAA", "orb-15m", "long") in keys


class TestRollingStats:
    def test_win_rate_and_avg_r(self, ledger):
        seed(ledger, [("target", 200.0), ("target", 200.0), ("stopped", -100.0)])
        rate, sample = ledger.rolling_win_rate("orb-15m", days=30)
        assert sample == 3
        assert rate == pytest.approx(2 / 3)
        stats = ledger.setup_stats("orb-15m", 30, NOW)
        assert stats.avg_r == pytest.approx((2 + 2 - 1) / 3)
        assert stats.total_pnl == pytest.approx(300.0)

    def test_old_signals_fall_out_of_the_window(self, ledger):
        old_session = (NOW - timedelta(days=45)).date()
        seed(ledger, [("stopped", -100.0)] * 5, session=old_session)
        seed(ledger, [("target", 200.0)] * 2)
        rate30, n30 = ledger.rolling_win_rate("orb-15m", days=30)
        assert (rate30, n30) == (1.0, 2)          # only the fresh wins
        stats90 = ledger.setup_stats("orb-15m", 90, NOW)
        assert stats90.sample == 7                 # 90d window sees both

    def test_expired_at_loss_is_not_a_win(self, ledger):
        seed(ledger, [("expired", -20.0), ("expired", 15.0)])
        rate, sample = ledger.rolling_win_rate("orb-15m", days=30)
        assert sample == 2
        assert rate == pytest.approx(0.5)  # positive-pnl expiry counts, loss doesn't

    def test_no_history_is_none_not_zero(self, ledger):
        rate, sample = ledger.rolling_win_rate("vwap-reclaim", days=30)
        assert rate is None
        assert sample == 0


class TestConfidenceFeedback:
    def test_ledger_plugs_into_signal_engine(self, ledger):
        from watchman.config import RiskConfig
        from watchman.signals.engine import SignalEngine
        from watchman.signals.model import GateState, SignalDraft

        seed(ledger, [("target", 200.0)] * 3 + [("stopped", -100.0)])
        engine = SignalEngine(risk=RiskConfig(), day_equity=10_000.0,
                              freshness=Freshness.DELAYED, confidence=ledger)
        signal = engine.finalize(
            SignalDraft("NEW", "orb-15m", "long", 100.0, 98.0, "why"),
            GateState(day_pnl_pct=0.0, open_day_positions=0, now=NOW),
        )
        assert signal.confidence_win_rate == pytest.approx(0.75)
        assert "75% live win rate (4 signals" in signal.confidence_label


class TestDivergence:
    def test_no_baseline_no_warning(self, ledger):
        seed(ledger, [("stopped", -100.0)] * 25)
        assert ledger.divergence_warning("orb-15m") is None

    def test_thin_sample_no_warning(self, ledger):
        ledger.set_baseline("orb-15m", 0.60, 500, "hypothetical backtest")
        seed(ledger, [("stopped", -100.0)] * 5)
        assert ledger.divergence_warning("orb-15m") is None

    def test_bad_divergence_warns_loudly(self, ledger):
        ledger.set_baseline("orb-15m", 0.60, 500, "hypothetical backtest")
        seed(ledger, [("stopped", -100.0)] * 18 + [("target", 200.0)] * 2)  # 10% live
        warning = ledger.divergence_warning("orb-15m")
        assert warning is not None
        assert "DIVERGENCE" in warning
        assert "BELOW" in warning
        assert "backtest was too optimistic" in warning

    def test_matching_performance_no_warning(self, ledger):
        ledger.set_baseline("orb-15m", 0.60, 500, "hypothetical backtest")
        seed(ledger, [("target", 200.0)] * 12 + [("stopped", -100.0)] * 8)  # 60%
        assert ledger.divergence_warning("orb-15m") is None
