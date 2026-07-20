"""Known-answer tests for performance/trade metrics and the honesty flags."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from watchman.backtest.engine import Trade
from watchman.backtest.metrics import (
    honesty_flags,
    performance_metrics,
    trade_metrics,
)


def curve(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2024-01-02", periods=len(values)))


def trade(entry: float, exit_: float, qty: int = 10) -> Trade:
    return Trade(
        symbol="T", entry_date=pd.Timestamp("2024-01-02"),
        exit_date=pd.Timestamp("2024-02-02"), qty=qty,
        entry_price=entry, exit_price=exit_,
    )


class TestPerformance:
    def test_total_return_and_drawdown_known_answer(self):
        m = performance_metrics(curve([100, 110, 99, 120]))
        assert m["total_return_pct"] == pytest.approx(20.0)
        assert m["max_drawdown_pct"] == pytest.approx(-10.0)  # 110 -> 99

    def test_cagr_one_year_doubles(self):
        values = [100.0] + [100.0 * (2 ** (i / 252)) for i in range(1, 253)]
        m = performance_metrics(curve(values))
        assert m["cagr_pct"] == pytest.approx(100.0, rel=1e-3)

    def test_sharpe_formula(self):
        c = curve([100, 101, 100.5, 101.5, 102])
        returns = c.pct_change().dropna()
        expected = returns.mean() / returns.std() * math.sqrt(252)
        assert performance_metrics(c)["sharpe"] == pytest.approx(float(expected))

    def test_short_curve_returns_nan_not_zero(self):
        m = performance_metrics(curve([100.0]))
        assert math.isnan(m["cagr_pct"])
        assert math.isnan(m["sharpe"])


class TestTrades:
    def test_known_trade_stats(self):
        trades = [trade(100, 110), trade(100, 105), trade(100, 90)]
        m = trade_metrics(trades)
        assert m["n_trades"] == 3
        assert m["win_rate"] == pytest.approx(2 / 3)
        # PnLs: +100, +50, -100 -> profit factor 1.5, expectancy $16.67.
        assert m["profit_factor"] == pytest.approx(1.5)
        assert m["expectancy_usd"] == pytest.approx(50 / 3)
        assert m["avg_win_usd"] == pytest.approx(75.0)
        assert m["avg_loss_usd"] == pytest.approx(-100.0)

    def test_no_trades_is_nan_not_perfect(self):
        m = trade_metrics([])
        assert m["n_trades"] == 0
        assert math.isnan(m["win_rate"])


class TestHonestyFlags:
    def test_high_sharpe_flagged_as_probable_bug(self):
        flags = honesty_flags({"sharpe": 4.2, "n_trades": 100, "win_rate": 0.5})
        assert any("probable bug" in f and "Sharpe" in f for f in flags)

    def test_high_win_rate_flagged(self):
        flags = honesty_flags({"sharpe": 1.0, "n_trades": 50, "win_rate": 0.85})
        assert any("win rate 85%" in f for f in flags)

    def test_small_sample_flagged(self):
        flags = honesty_flags({"sharpe": 1.0, "n_trades": 12, "win_rate": 0.6})
        assert any("SMALL SAMPLE" in f for f in flags)

    def test_reasonable_results_not_flagged(self):
        flags = honesty_flags({"sharpe": 1.4, "n_trades": 120, "win_rate": 0.55})
        assert flags == []
