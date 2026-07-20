"""Module C engine tests: the engine-level lookahead canary, the known-answer
test on synthetic data, and the costs-applied test — the three tests the
project principles require. Plus rejection honesty."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from tests.conftest import SyntheticProvider, make_bars

from watchman.backtest.engine import BacktestEngine, Order, Strategy
from watchman.backtest.strategies import BuyAndHold, MovingAverageCross
from watchman.config import CostsConfig

START = datetime(2024, 1, 2)
END = datetime(2024, 6, 28)

NO_COSTS = CostsConfig(slippage_bps=0.0, spread_bps=0.0, commission_per_trade=0.0)
REAL_COSTS = CostsConfig(slippage_bps=5.0, spread_bps=2.0, commission_per_trade=1.0)


def flat_bars(open_px: float = 100.0, close_px: float = 101.0) -> pd.DataFrame:
    bars = make_bars("2024-01-02", "2024-06-28")
    out = bars.copy()
    out["open"] = open_px
    out["close"] = close_px
    out["high"] = max(open_px, close_px) + 1
    out["low"] = min(open_px, close_px) - 1
    out["adj_close"] = close_px
    return out


class TestEngineLookaheadCanary:
    """Engine-level canary: DO NOT delete or weaken. A strategy inside the
    engine loop must never see past the session it is reacting to, and its
    orders must never fill at prices it could see when deciding."""

    def test_strategy_view_is_pinned_to_each_session(self):
        seen: dict[pd.Timestamp, pd.Timestamp] = {}

        class Recorder(Strategy):
            name = "recorder"

            def on_session(self, session, view, portfolio):
                bars = view.daily_bars("TEST", end=datetime(2030, 1, 1))
                seen[session] = bars.index.max()  # greedy request, still clipped
                return []

        provider = SyntheticProvider(make_bars("2024-01-02", "2024-06-28"))
        engine = BacktestEngine(provider, NO_COSTS, 10_000)
        engine.run(Recorder(), ["TEST"], START, END)
        assert seen, "canary strategy never ran"
        for session, max_visible in seen.items():
            assert max_visible == session, (
                f"LOOKAHEAD: on {session.date()} the strategy saw {max_visible.date()}"
            )

    def test_orders_fill_at_next_open_not_decision_price(self):
        # Close on decision day 101, next open jumps to 150: an engine that
        # filled at decision-day prices would be lying about entry cost.
        bars = flat_bars()
        jump_day = bars.index[10]
        bars.loc[jump_day:, "open"] = 150.0
        bars.loc[jump_day:, "close"] = 151.0

        class BuyOnce(Strategy):
            name = "buy-once"

            def __init__(self):
                self.done = False

            def on_session(self, session, view, portfolio):
                # Decide on the session BEFORE the jump.
                if not self.done and session == bars.index[9]:
                    self.done = True
                    return [Order("TEST", "buy", 10)]
                return []

        engine = BacktestEngine(SyntheticProvider(bars), NO_COSTS, 10_000)
        result = engine.run(BuyOnce(), ["TEST"], START, END)
        assert len(result.fills) == 1
        fill = result.fills[0]
        assert fill.date == jump_day          # next session,
        assert fill.price == pytest.approx(150.0)  # at ITS open — gap eaten honestly


class TestKnownAnswer:
    """Hand-computed expectations on deterministic synthetic data."""

    def test_buy_and_hold_exact_final_equity(self):
        # Constant open 100 / close 101. Buy 50 after session 1 -> fill
        # session 2 open: price 100 * (1 + 7bp) = 100.07, commission $1.
        # cash = 10000 - 50*100.07 - 1 = 4995.50; final equity = cash + 50*101.
        provider = SyntheticProvider(flat_bars())
        engine = BacktestEngine(provider, REAL_COSTS, 10_000)
        result = engine.run(BuyAndHold("TEST", 50), ["TEST"], START, END)
        assert result.fills[0].price == pytest.approx(100.07)
        assert result.final_equity == pytest.approx(4995.50 + 50 * 101.0)
        assert result.metrics["n_trades"] == 0  # never closed -> not a trade yet

    def test_round_trip_trade_pnl_known_answer(self):
        class BuyThenSell(Strategy):
            name = "buy-then-sell"

            def on_session(self, session, view, portfolio):
                bars = view.daily_bars("TEST")
                if len(bars) == 1:
                    return [Order("TEST", "buy", 10)]
                if len(bars) == 5:
                    return [Order("TEST", "sell", 10)]
                return []

        # Rising opens: open = close - 0.5 = 99.5 + i (make_bars ramp).
        provider = SyntheticProvider(make_bars("2024-01-02", "2024-06-28"))
        engine = BacktestEngine(provider, NO_COSTS, 10_000)
        result = engine.run(BuyThenSell(), ["TEST"], START, END)
        assert len(result.trades) == 1
        trade = result.trades[0]
        # Entry at session-2 open (100.5), exit at session-6 open (104.5).
        assert trade.entry_price == pytest.approx(100.5)
        assert trade.exit_price == pytest.approx(104.5)
        assert trade.pnl == pytest.approx(40.0)
        assert result.metrics["n_trades"] == 1
        assert result.metrics["win_rate"] == 1.0

    def test_ma_cross_trades_where_constructed(self):
        # 120 flat sessions, then a strong ramp: the 5/20 cross must go long
        # only after the ramp starts, never before.
        flat = [100.0] * 120
        ramp = [100.0 + 2 * i for i in range(1, 61)]
        closes = flat + ramp
        dates = pd.bdate_range("2024-01-02", periods=len(closes))
        bars = pd.DataFrame(
            {
                "open": closes, "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes], "close": closes,
                "adj_close": closes, "volume": 1e6,
            },
            index=dates,
        )
        engine = BacktestEngine(SyntheticProvider(bars), NO_COSTS, 10_000)
        result = engine.run(
            MovingAverageCross("TEST", 5, 20), ["TEST"],
            datetime(2024, 1, 2), datetime.combine(dates[-1].date(), datetime.min.time()),
        )
        buys = [f for f in result.fills if f.side == "buy"]
        assert len(buys) == 1
        assert buys[0].date > dates[119]  # strictly after the flat regime
        assert result.final_equity > 10_000  # rode the ramp


class TestCostsApplied:
    """Principle 4: costs are real, and provably applied."""

    def test_costs_reduce_final_equity_by_exact_amount(self):
        provider = SyntheticProvider(flat_bars())
        free = BacktestEngine(provider, NO_COSTS, 10_000).run(
            BuyAndHold("TEST", 50), ["TEST"], START, END
        )
        costed = BacktestEngine(provider, REAL_COSTS, 10_000).run(
            BuyAndHold("TEST", 50), ["TEST"], START, END
        )
        # 7 bps on 50 shares @ $100 = $3.50 slippage+spread, + $1 commission.
        assert free.final_equity - costed.final_equity == pytest.approx(4.50)

    def test_every_fill_carries_costs(self):
        provider = SyntheticProvider(flat_bars())
        result = BacktestEngine(provider, REAL_COSTS, 10_000).run(
            BuyAndHold("TEST", 50), ["TEST"], START, END
        )
        for fill in result.fills:
            assert fill.price != fill.reference_price  # never filled at raw price
            assert fill.cost_drag > 0

    def test_sell_side_costs_cut_against_the_seller(self):
        class Flip(Strategy):
            name = "flip"

            def on_session(self, session, view, portfolio):
                if len(view.daily_bars("TEST")) == 1:
                    return [Order("TEST", "buy", 10)]
                if portfolio.qty("TEST") and len(view.daily_bars("TEST")) == 2:
                    return [Order("TEST", "sell", 10)]
                return []

        provider = SyntheticProvider(flat_bars())
        result = BacktestEngine(provider, REAL_COSTS, 10_000).run(
            Flip(), ["TEST"], START, END
        )
        sell = next(f for f in result.fills if f.side == "sell")
        assert sell.price < sell.reference_price  # seller receives LESS


class TestHonestRejections:
    def test_unaffordable_buy_rejected_with_warning(self):
        provider = SyntheticProvider(flat_bars())
        result = BacktestEngine(provider, NO_COSTS, 1_000).run(
            BuyAndHold("TEST", 500), ["TEST"], START, END  # needs $50k
        )
        assert result.fills == []
        assert any("rejected (no leverage)" in w for w in result.warnings)

    def test_short_sale_rejected_with_warning(self):
        class ShortSeller(Strategy):
            name = "short"

            def on_session(self, session, view, portfolio):
                if len(view.daily_bars("TEST")) == 1:
                    return [Order("TEST", "sell", 10)]
                return []

        provider = SyntheticProvider(flat_bars())
        result = BacktestEngine(provider, NO_COSTS, 10_000).run(
            ShortSeller(), ["TEST"], START, END
        )
        assert result.fills == []
        assert any("no shorting" in w for w in result.warnings)
