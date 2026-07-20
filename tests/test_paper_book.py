"""PaperBook: slippage-honest fills, cash accounting, shorts, equity marks.
Every expected number is hand-computable."""

from __future__ import annotations

from datetime import datetime

import pytest

from watchman.config import CostsConfig
from watchman.db import connect
from watchman.paper import DAY, PaperBook

COSTS = CostsConfig(slippage_bps=5.0, spread_bps=2.0, commission_per_trade=1.0)
NO_COSTS = CostsConfig(slippage_bps=0.0, spread_bps=0.0, commission_per_trade=0.0)
TS = datetime(2026, 7, 20, 10, 0)


@pytest.fixture
def book(tmp_path):
    return PaperBook(connect(tmp_path / "b.db"), DAY, COSTS, 10_000.0)


class TestFills:
    def test_long_open_pays_costs_exactly(self, book):
        pos = book.open_position(TS, "TEST", "long", 50, 100.0)
        assert pos.avg_cost == pytest.approx(100.07)  # +7 bps
        # cash: 10000 - 50*100.07 - $1 commission
        assert book.cash == pytest.approx(10_000 - 5_003.50 - 1.0)

    def test_long_close_known_pnl(self, book):
        pos = book.open_position(TS, "TEST", "long", 50, 100.0)
        pnl = book.close_position(pos, TS, 104.0, "target")
        # Exit at 104 * (1 - 7bp) = 103.9272; PnL carries BOTH round-trip
        # commissions: (103.9272 - 100.07) * 50 - $2.
        assert pnl == pytest.approx((103.9272 - 100.07) * 50 - 2.0)
        assert book.open_positions() == []
        # Cash conservation: end cash = start + realized pnl, exactly.
        assert book.cash == pytest.approx(10_000 + pnl)

    def test_short_round_trip_profits_when_price_falls(self, book):
        pos = book.open_position(TS, "TEST", "short", 50, 100.0)
        assert pos.avg_cost == pytest.approx(99.93)  # sells at -7 bps
        pnl = book.close_position(pos, TS, 96.0, "target")
        # Cover at 96 * (1 + 7bp) = 96.0672; both commissions in the PnL.
        assert pnl == pytest.approx((99.93 - 96.0672) * 50 - 2.0)
        assert book.cash == pytest.approx(10_000 + pnl)

    def test_unaffordable_open_rejected_with_reason(self, book):
        outcome = book.open_position(TS, "TEST", "long", 500, 100.0)  # $50k
        assert isinstance(outcome, str)
        assert "insufficient paper cash" in outcome
        assert book.cash == pytest.approx(10_000)  # untouched


class TestEquityAndMarks:
    def test_equity_marks_persist_and_curve_grows(self, tmp_path):
        conn = connect(tmp_path / "b.db")
        book = PaperBook(conn, DAY, NO_COSTS, 10_000.0)
        book.mark(datetime(2026, 7, 17, 16, 0), {})
        book.open_position(TS, "TEST", "long", 100, 50.0)
        book.mark(datetime(2026, 7, 20, 16, 0), {"TEST": 55.0})
        curve = book.equity_curve()
        assert len(curve) == 2
        assert curve[0][1] == pytest.approx(10_000)
        assert curve[1][1] == pytest.approx(10_000 + 100 * 5.0)  # +$500 unrealized

    def test_day_pnl_uses_prior_mark_as_base(self, tmp_path):
        conn = connect(tmp_path / "b.db")
        book = PaperBook(conn, DAY, NO_COSTS, 10_000.0)
        book.mark(datetime(2026, 7, 17, 16, 0), {})
        book.open_position(TS, "TEST", "long", 100, 50.0)
        pnl = book.day_pnl_pct({"TEST": 48.5}, TS.date(), 10_000.0)
        assert pnl == pytest.approx(-1.5)  # -$150 on $10k

    def test_missing_price_values_position_at_cost(self, tmp_path):
        conn = connect(tmp_path / "b.db")
        book = PaperBook(conn, DAY, NO_COSTS, 10_000.0)
        book.open_position(TS, "TEST", "long", 100, 50.0)
        assert book.equity({}) == pytest.approx(10_000)  # no phantom gains

    def test_book_state_survives_reconnection(self, tmp_path):
        conn = connect(tmp_path / "b.db")
        PaperBook(conn, DAY, NO_COSTS, 10_000.0).open_position(
            TS, "TEST", "long", 100, 50.0
        )
        book2 = PaperBook(connect(tmp_path / "b.db"), DAY, NO_COSTS, 10_000.0)
        assert book2.cash == pytest.approx(5_000)
        assert len(book2.open_positions()) == 1
