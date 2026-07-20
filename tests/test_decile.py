"""Momentum-decile backtest: constructed winners must land in the top decile,
costs must bite, and the disclaimers must always be present."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from tests.screener_fixtures import make_price_history

from watchman.backtest.decile import momentum_decile_backtest
from watchman.config import CostsConfig
from watchman.data.provider import DataProvider, Freshness, Fundamentals

NO_COSTS = CostsConfig(slippage_bps=0.0, spread_bps=0.0, commission_per_trade=0.0)
COSTS = CostsConfig(slippage_bps=5.0, spread_bps=2.0, commission_per_trade=0.0)

START = datetime(2024, 1, 2)
END = datetime(2026, 6, 30)
DAYS = 900  # bars start well before START so the momentum lookback is warm


class BarsOnlyProvider(DataProvider):
    name = "bars-only"

    def __init__(self, bars: dict[str, pd.DataFrame]):
        self.bars = bars

    def daily_bars(self, symbol, start, end):
        df = self.bars[symbol]
        mask = (df.index >= pd.Timestamp(start.date())) & (df.index <= pd.Timestamp(end.date()))
        return df.loc[mask]

    def fundamentals(self, symbol) -> Fundamentals:
        raise ValueError("no fundamentals in this test")

    def quote_freshness(self) -> Freshness:
        return Freshness.EOD


@pytest.fixture
def trending_provider() -> BarsOnlyProvider:
    """6 persistent winners (+0.15%/day), 6 persistent losers (-0.05%/day),
    SPY in between: momentum ranking is stable, so the top tercile must win."""
    end = "2026-06-30"
    bars = {"SPY": make_price_history(0.0004, days=DAYS, end=end)}
    for i in range(6):
        bars[f"WIN{i}"] = make_price_history(0.0015, days=DAYS, base=50 + i, end=end)
        bars[f"LOSE{i}"] = make_price_history(-0.0005, days=DAYS, base=50 + i, end=end)
    return BarsOnlyProvider(bars)


def symbols(provider: BarsOnlyProvider) -> list[str]:
    return [s for s in provider.bars if s != "SPY"]


class TestDecileBacktest:
    def test_top_decile_holds_the_constructed_winners(self, trending_provider):
        result = momentum_decile_backtest(
            trending_provider, symbols(trending_provider), START, END,
            NO_COSTS, n_deciles=3,
        )
        top = result.metrics_by_decile[1]["cagr_pct"]
        bottom = result.metrics_by_decile[3]["cagr_pct"]
        assert top > bottom
        assert result.top_bottom_spread_pct > 0
        assert result.monotonic_fraction == 1.0  # perfectly ordered by construction

    def test_costs_reduce_returns(self, trending_provider):
        free = momentum_decile_backtest(
            trending_provider, symbols(trending_provider), START, END,
            NO_COSTS, n_deciles=3,
        )
        # Force turnover-sensitive comparison with real costs.
        costed = momentum_decile_backtest(
            trending_provider, symbols(trending_provider), START, END,
            COSTS, n_deciles=3,
        )
        assert (
            costed.metrics_by_decile[1]["cagr_pct"]
            <= free.metrics_by_decile[1]["cagr_pct"]
        )
        assert costed.costs_bps_per_side == 7.0

    def test_disclaimers_always_present(self, trending_provider):
        result = momentum_decile_backtest(
            trending_provider, symbols(trending_provider), START, END,
            NO_COSTS, n_deciles=3,
        )
        text = " ".join(result.warnings)
        assert "SURVIVORSHIP" in text
        assert "MOMENTUM ONLY" in text
        assert "not point-in-time" in text

    def test_missing_symbols_reported_not_silently_dropped(self, trending_provider):
        result = momentum_decile_backtest(
            trending_provider, [*symbols(trending_provider), "GHOST1", "GHOST2"],
            START, END, NO_COSTS, n_deciles=3,
        )
        assert any("DATA GAPS" in w and "GHOST1" in w for w in result.warnings)

    def test_too_short_period_refuses_to_pretend(self, trending_provider):
        with pytest.raises(ValueError, match="month-ends"):
            momentum_decile_backtest(
                trending_provider, symbols(trending_provider),
                datetime(2026, 1, 2), END, NO_COSTS, n_deciles=3,
            )
