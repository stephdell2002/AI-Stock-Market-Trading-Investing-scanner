"""Position sizing: risk dollars / (entry - stop), never over 1% risk,
never over account equity."""

from __future__ import annotations

import pytest

from watchman.risk import risk_reward, shares_for_risk


class TestSharesForRisk:
    def test_textbook_long(self):
        # $10k account, 1% risk = $100. Entry 50, stop 49 -> $1/share -> 100 shares.
        assert shares_for_risk(10_000, 1.0, entry=50.0, stop=49.0) == 100

    def test_risk_never_exceeded(self):
        shares = shares_for_risk(10_000, 1.0, entry=50.0, stop=49.34)
        assert shares * (50.0 - 49.34) <= 100.0 + 1e-9
        # And one more share would break the budget.
        assert (shares + 1) * (50.0 - 49.34) > 100.0

    def test_capped_by_equity_no_leverage(self):
        # Tight stop wants 1000 shares, but $10k only affords 200 at $50.
        assert shares_for_risk(10_000, 1.0, entry=50.0, stop=49.90) == 200

    def test_short_side_uses_absolute_stop_distance(self):
        assert shares_for_risk(10_000, 1.0, entry=49.0, stop=50.0) == 100

    def test_zero_stop_distance_is_unsizeable(self):
        assert shares_for_risk(10_000, 1.0, entry=50.0, stop=50.0) == 0

    @pytest.mark.parametrize(
        ("equity", "risk_pct", "entry"),
        [(0, 1.0, 50.0), (-5, 1.0, 50.0), (10_000, 0, 50.0), (10_000, 1.0, 0)],
    )
    def test_degenerate_inputs_size_zero(self, equity, risk_pct, entry):
        assert shares_for_risk(equity, risk_pct, entry=entry, stop=entry - 1) == 0


class TestRiskReward:
    def test_long_setup(self):
        assert risk_reward(entry=100.0, stop=98.0, target=106.0) == pytest.approx(3.0)

    def test_short_setup(self):
        assert risk_reward(entry=100.0, stop=102.0, target=94.0) == pytest.approx(3.0)

    def test_incoherent_direction_scores_zero(self):
        # Target below entry while stop is also below entry: nonsense trade.
        assert risk_reward(entry=100.0, stop=98.0, target=97.0) == 0.0

    def test_zero_risk_scores_zero(self):
        assert risk_reward(entry=100.0, stop=100.0, target=110.0) == 0.0
