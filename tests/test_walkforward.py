"""Walk-forward validation: window construction, per-window optimization,
and strict in-sample / out-of-sample separation."""

from __future__ import annotations

from itertools import pairwise

import pandas as pd
import pytest

from watchman.backtest.engine import BacktestResult
from watchman.backtest.metrics import performance_metrics
from watchman.backtest.walkforward import day_windows, walk_forward

CALENDAR = pd.bdate_range("2022-01-03", periods=300)


class TestWindows:
    def test_windows_tile_without_overlap(self):
        windows = day_windows(CALENDAR, opt_days=100, test_days=50)
        assert len(windows) == 4
        for w in windows:
            assert w.opt_end < w.test_start  # optimization never sees its test data
        for a, b in pairwise(windows):
            assert b.test_start > a.test_start
            # Consecutive OOS chunks are contiguous, so the stitched curve is real.
            assert b.test_start == CALENDAR[CALENDAR.get_loc(a.test_end) + 1]

    def test_too_short_calendar_yields_no_windows(self):
        assert day_windows(CALENDAR[:120], opt_days=100, test_days=50) == []


def fake_result(daily_return: float, n: int, start: pd.Timestamp) -> BacktestResult:
    idx = pd.bdate_range(start, periods=n)
    equity = pd.Series([10_000 * (1 + daily_return) ** i for i in range(n)], index=idx)
    result = BacktestResult(
        strategy_name="fake", start=idx[0], end=idx[-1], initial_cash=10_000,
        equity_curve=equity, fills=[], trades=[],
    )
    result.metrics = performance_metrics(equity)
    return result


class TestWalkForward:
    def test_picks_in_sample_winner_and_reports_oos_separately(self):
        """Param x=1 wins in window 1's opt period, x=2 wins in window 2's.
        The optimizer must pick each accordingly, and OOS metrics must come
        from the test windows only."""
        windows = day_windows(CALENDAR, opt_days=100, test_days=50)[:2]
        w1_opt_end, w2_opt_end = windows[0].opt_end, windows[1].opt_end

        def run_fn(params, start, end):
            n = len(pd.bdate_range(start, end))
            if end == w1_opt_end:  # window 1 in-sample: x=1 shines
                r = 0.002 if params["x"] == 1 else 0.0005
            elif end == w2_opt_end:  # window 2 in-sample: x=2 shines
                r = 0.002 if params["x"] == 2 else 0.0005
            else:  # OOS periods: everything mediocre and identical
                r = 0.0003
            return fake_result(r, n, start)

        wf = walk_forward(run_fn, [{"x": 1}, {"x": 2}], windows, objective="sharpe")
        assert wf.windows[0].best_params == {"x": 1}
        assert wf.windows[1].best_params == {"x": 2}
        # OOS Sharpe reflects the mediocre 3bp/day, not the shiny in-sample 20bp.
        assert wf.oos_objective < wf.in_sample_objective_avg
        assert any("OVERFITTING SIGNAL" in w for w in wf.warnings)

    def test_oos_curve_stitches_all_test_windows(self):
        windows = day_windows(CALENDAR, opt_days=100, test_days=50)[:2]

        def run_fn(params, start, end):
            return fake_result(0.001, len(pd.bdate_range(start, end)), start)

        wf = walk_forward(run_fn, [{"x": 1}], windows)
        # Two test windows of 50 sessions -> 49 returns each stitched together.
        assert len(wf.oos_equity) == 98

    def test_empty_grid_or_windows_raise(self):
        with pytest.raises(ValueError, match="param_grid"):
            walk_forward(lambda *_: None, [], [1])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="windows"):
            walk_forward(lambda *_: None, [{"x": 1}], [])  # type: ignore[arg-type]
