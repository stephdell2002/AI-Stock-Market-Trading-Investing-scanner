"""The three setup classes on constructed bar patterns. Entries/stops are
hand-checkable; 'no signal' cases matter as much as signal cases."""

from __future__ import annotations

from datetime import date, time

import pandas as pd
import pytest
from tests.intraday_fixtures import bars_from_closes

from watchman.signals.setups import (
    OpeningRangeBreakout,
    RelVolContinuation,
    SetupContext,
    VwapReclaimReject,
)

DAY = date(2026, 7, 20)


def ctx_for(closes, volume=50_000.0, prev_close=100.0, avg_vol=5_000_000.0,
            interval=5, orb=15) -> SetupContext:
    bars = bars_from_closes(closes, DAY, time(9, 30), interval, volume=volume)
    last_end = bars.index[-1] + pd.Timedelta(minutes=interval)
    return SetupContext(
        symbol="TEST", session_date=DAY, bars=bars,
        interval_minutes=interval, orb_minutes=orb,
        prev_close=prev_close, avg_daily_volume=avg_vol, atr=2.0,
        now=last_end.to_pydatetime(),
    )


class TestORB:
    # 15m OR on 5m bars = first 3 bars. closes 100,101,100.5: with the
    # fixture's +/-0.2 wrap, OR high = 101.2, OR low = 99.8.

    def test_long_breakout_on_last_bar(self):
        ctx = ctx_for([100, 101, 100.5, 100.8, 101.5])  # last close > 101.2
        draft = OpeningRangeBreakout().evaluate(ctx)
        assert draft is not None
        assert draft.direction == "long"
        assert draft.entry == pytest.approx(101.5)
        assert draft.stop == pytest.approx(99.8)
        assert "opening range" in draft.rationale

    def test_short_breakdown(self):
        ctx = ctx_for([100, 101, 100.5, 100.2, 99.5])  # last close < 99.8
        draft = OpeningRangeBreakout().evaluate(ctx)
        assert draft is not None
        assert draft.direction == "short"
        assert draft.stop == pytest.approx(101.2)

    def test_no_signal_while_range_still_forming(self):
        assert OpeningRangeBreakout().evaluate(ctx_for([100, 101, 100.5])) is None

    def test_no_signal_inside_the_range(self):
        assert OpeningRangeBreakout().evaluate(ctx_for([100, 101, 100.5, 100.9, 101.0])) is None

    def test_stale_breakout_not_reemitted(self):
        # Breakout happened two bars ago; last bar is not the trigger.
        ctx = ctx_for([100, 101, 100.5, 101.5, 101.6, 101.7])
        assert OpeningRangeBreakout().evaluate(ctx) is None


class TestVwapReclaimReject:
    def test_reclaim_long(self):
        # Price sinks below VWAP for several bars, then closes back above.
        closes = [100, 99.5, 99, 98.5, 98, 97.8, 100.5]
        ctx = ctx_for(closes)
        draft = VwapReclaimReject().evaluate(ctx)
        assert draft is not None
        assert draft.setup == "vwap-reclaim"
        assert draft.direction == "long"
        assert draft.entry == pytest.approx(100.5)
        # Stop = min low of last 3 bars = 97.8 - 0.2 wrap = 97.6.
        assert draft.stop == pytest.approx(97.6)

    def test_reject_short(self):
        closes = [100, 100.5, 101, 101.5, 102, 102.2, 99.5]
        ctx = ctx_for(closes)
        draft = VwapReclaimReject().evaluate(ctx)
        assert draft is not None
        assert draft.setup == "vwap-reject"
        assert draft.direction == "short"
        assert draft.stop == pytest.approx(102.4)  # max high of last 3 bars

    def test_chop_produces_nothing(self):
        # Alternating around VWAP: not enough one-sided bars.
        closes = [100, 99.8, 100.2, 99.9, 100.1, 99.95, 100.05]
        assert VwapReclaimReject().evaluate(ctx_for(closes)) is None

    def test_too_few_bars_produces_nothing(self):
        assert VwapReclaimReject().evaluate(ctx_for([100, 99, 100.5])) is None


class TestRelVolContinuation:
    def _heavy_ctx(self, closes):
        # Volume such that rel vol is far above the 2x trigger whatever the
        # elapsed-session fraction: 78 bars/day avg -> give each bar avg*0.1.
        return ctx_for(closes, volume=500_000.0, prev_close=100.0,
                       avg_vol=5_000_000.0)

    def test_long_continuation_on_new_high(self):
        # Up 3%+, pause (three bars below the earlier high), then new high.
        # (Peak early: the fixture's open=prev-close makes the bar right after
        # a peak inherit its high, so the pause window must start later.)
        closes = [102.0, 103.5, 103.0, 102.9, 102.8, 103.0, 104.0]
        ctx = self._heavy_ctx(closes)
        draft = RelVolContinuation().evaluate(ctx)
        assert draft is not None
        assert draft.direction == "long"
        assert draft.entry == pytest.approx(104.0)
        assert "rel vol" in draft.rationale

    def test_short_continuation_on_new_low(self):
        closes = [98.0, 96.5, 97.0, 97.1, 97.2, 97.0, 96.0]
        ctx = self._heavy_ctx(closes)
        draft = RelVolContinuation().evaluate(ctx)
        assert draft is not None
        assert draft.direction == "short"

    def test_light_volume_produces_nothing(self):
        closes = [102.0, 102.8, 103.5, 103.0, 102.9, 103.1, 104.0]
        ctx = ctx_for(closes, volume=1_000.0)  # tiny tape
        assert RelVolContinuation().evaluate(ctx) is None

    def test_small_day_move_produces_nothing(self):
        closes = [100.2, 100.5, 100.8, 100.6, 100.5, 100.7, 101.0]  # +1% only
        assert RelVolContinuation().evaluate(self._heavy_ctx(closes)) is None

    def test_no_new_high_produces_nothing(self):
        closes = [102.0, 103.5, 103.0, 102.9, 102.8, 103.0, 103.2]
        assert RelVolContinuation().evaluate(self._heavy_ctx(closes)) is None
