"""The three starter intraday setups, each its own class.

Semantics shared by all three: a setup examines the session's COMPLETED bars
(supplied through AsOfView, so nothing in-progress or future is visible) and
emits a draft only when the trigger condition happened on the LAST completed
bar — a stale trigger from an hour ago is not a fresh signal. Entries are the
triggering bar's close (the actionable price when the signal is generated);
stops come from structure. The SignalEngine applies every gate afterward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta

import pandas as pd

from watchman.signals.indicators import relative_volume, session_vwap
from watchman.signals.model import SignalDraft

#: relative-volume trigger for the continuation setup (explicit, arguable)
RELVOL_TRIGGER = 2.0
#: minimum move on the day for continuation (absolute %)
CONTINUATION_MIN_DAY_MOVE_PCT = 2.0
#: bars of prior structure used for stops
STOP_LOOKBACK_BARS = 3
CONTINUATION_STOP_LOOKBACK = 5
#: a reclaim/rejection needs this many of the prior VWAP_WINDOW bars on the
#: wrong side of VWAP — alternating chop (~half and half) never qualifies
VWAP_WINDOW = 6
VWAP_SIDE_BARS_REQUIRED = 4


@dataclass
class SetupContext:
    """Everything a setup may look at. Built from a pinned AsOfView."""

    symbol: str
    session_date: date_type
    bars: pd.DataFrame          # completed regular-session bars so far today
    interval_minutes: int
    orb_minutes: int
    prev_close: float
    avg_daily_volume: float
    atr: float | None
    now: datetime               # the view's pin (ET)

    @property
    def vwap(self) -> pd.Series:
        return session_vwap(self.bars)


class Setup(ABC):
    name: str = "setup"

    @abstractmethod
    def evaluate(self, ctx: SetupContext) -> SignalDraft | None:
        """A draft if the last completed bar triggers this setup, else None."""


class OpeningRangeBreakout(Setup):
    """ORB: range = first `orb_minutes` of the session; long on the first bar
    CLOSING above the range high (short: below the low), stop at the opposite
    side of the range."""

    name = "orb"

    def evaluate(self, ctx: SetupContext) -> SignalDraft | None:
        bars_needed = ctx.orb_minutes // ctx.interval_minutes
        if len(ctx.bars) <= bars_needed:
            return None  # opening range not complete, or nothing after it yet
        or_bars = ctx.bars.iloc[:bars_needed]
        post = ctx.bars.iloc[bars_needed:]
        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        last = post.iloc[-1]
        prior = post.iloc[:-1]

        if float(last["close"]) > or_high and not (prior["close"] > or_high).any():
            return SignalDraft(
                symbol=ctx.symbol,
                setup=f"orb-{ctx.orb_minutes}m",
                direction="long",
                entry=float(last["close"]),
                stop=or_low,
                rationale=(
                    f"first close {last['close']:.2f} above the {ctx.orb_minutes}m "
                    f"opening range {or_low:.2f}-{or_high:.2f}"
                ),
            )
        if float(last["close"]) < or_low and not (prior["close"] < or_low).any():
            return SignalDraft(
                symbol=ctx.symbol,
                setup=f"orb-{ctx.orb_minutes}m",
                direction="short",
                entry=float(last["close"]),
                stop=or_high,
                rationale=(
                    f"first close {last['close']:.2f} below the {ctx.orb_minutes}m "
                    f"opening range {or_low:.2f}-{or_high:.2f}"
                ),
            )
        return None


class VwapReclaimReject(Setup):
    """Long when price closes back above session VWAP after holding below it
    (reclaim); short when it closes back below after holding above (reject).
    Stops from the recent bars' extreme."""

    name = "vwap"

    def evaluate(self, ctx: SetupContext) -> SignalDraft | None:
        if len(ctx.bars) < VWAP_WINDOW + 1:
            return None
        vwap = ctx.vwap
        closes = ctx.bars["close"]
        last_close, last_vwap = float(closes.iloc[-1]), float(vwap.iloc[-1])
        prev_close, prev_vwap = float(closes.iloc[-2]), float(vwap.iloc[-2])
        window = ctx.bars.iloc[-(VWAP_WINDOW + 1) : -1]
        window_vwap = vwap.loc[window.index]
        below = int((window["close"] < window_vwap).sum())
        above = int((window["close"] > window_vwap).sum())

        if prev_close < prev_vwap and last_close > last_vwap and below >= VWAP_SIDE_BARS_REQUIRED:
            stop = float(ctx.bars["low"].iloc[-STOP_LOOKBACK_BARS:].min())
            return SignalDraft(
                symbol=ctx.symbol,
                setup="vwap-reclaim",
                direction="long",
                entry=last_close,
                stop=stop,
                rationale=(
                    f"reclaimed VWAP {last_vwap:.2f} (close {last_close:.2f}) after "
                    f"{below} bars below; stop at recent low {stop:.2f}"
                ),
            )
        if prev_close > prev_vwap and last_close < last_vwap and above >= VWAP_SIDE_BARS_REQUIRED:
            stop = float(ctx.bars["high"].iloc[-STOP_LOOKBACK_BARS:].max())
            return SignalDraft(
                symbol=ctx.symbol,
                setup="vwap-reject",
                direction="short",
                entry=last_close,
                stop=stop,
                rationale=(
                    f"rejected at VWAP {last_vwap:.2f} (close {last_close:.2f}) after "
                    f"{above} bars above; stop at recent high {stop:.2f}"
                ),
            )
        return None


class RelVolContinuation(Setup):
    """High-relative-volume momentum continuation: heavy tape (rel vol >= 2x),
    a >=2% day move with price on the right side of VWAP, and the last bar
    making a fresh session extreme after at least a few bars of pause."""

    name = "relvol-continuation"

    def evaluate(self, ctx: SetupContext) -> SignalDraft | None:
        if len(ctx.bars) < CONTINUATION_STOP_LOOKBACK + 2:
            return None
        rel_vol = relative_volume(
            float(ctx.bars["volume"].sum()), ctx.avg_daily_volume, ctx.now
        )
        if rel_vol is None or rel_vol < RELVOL_TRIGGER:
            return None
        last = ctx.bars.iloc[-1]
        prior = ctx.bars.iloc[:-1]
        day_move_pct = (float(last["close"]) / ctx.prev_close - 1) * 100
        last_vwap = float(ctx.vwap.iloc[-1])
        pause = prior.iloc[-STOP_LOOKBACK_BARS:]

        if (
            day_move_pct >= CONTINUATION_MIN_DAY_MOVE_PCT
            and float(last["close"]) > last_vwap
            and float(last["high"]) > float(prior["high"].max())
            and float(pause["high"].max()) < float(prior["high"].max())
        ):
            stop = float(ctx.bars["low"].iloc[-CONTINUATION_STOP_LOOKBACK:].min())
            return SignalDraft(
                symbol=ctx.symbol,
                setup=self.name,
                direction="long",
                entry=float(last["close"]),
                stop=stop,
                rationale=(
                    f"new session high on {rel_vol:.1f}x rel vol, +{day_move_pct:.1f}% "
                    f"day above VWAP {last_vwap:.2f}; stop {stop:.2f}"
                ),
            )
        if (
            day_move_pct <= -CONTINUATION_MIN_DAY_MOVE_PCT
            and float(last["close"]) < last_vwap
            and float(last["low"]) < float(prior["low"].min())
            and float(pause["low"].min()) > float(prior["low"].min())
        ):
            stop = float(ctx.bars["high"].iloc[-CONTINUATION_STOP_LOOKBACK:].max())
            return SignalDraft(
                symbol=ctx.symbol,
                setup=self.name,
                direction="short",
                entry=float(last["close"]),
                stop=stop,
                rationale=(
                    f"new session low on {rel_vol:.1f}x rel vol, {day_move_pct:.1f}% "
                    f"day below VWAP {last_vwap:.2f}; stop {stop:.2f}"
                ),
            )
        return None


def build_context(view, symbol: str, cfg_signals) -> SetupContext | None:
    """Assemble a SetupContext from a pinned view; None when the session has
    no completed bars yet."""
    from watchman.data.provider import MARKET_OPEN
    from watchman.signals.indicators import atr as compute_atr

    daily = view.daily_bars(symbol, start=view.as_of - timedelta(days=90))
    if daily.empty:
        return None
    intraday = view.intraday_bars(
        symbol, cfg_signals.intraday_interval, days=1, include_premarket=False
    )
    today = view.as_of.date()
    session = (
        intraday.loc[
            (intraday.index.date == today) & (intraday.index.time >= MARKET_OPEN)
        ]
        if not intraday.empty
        else intraday
    )
    if session.empty:
        return None
    return SetupContext(
        symbol=symbol,
        session_date=today,
        bars=session,
        interval_minutes=int(cfg_signals.intraday_interval.rstrip("m")),
        orb_minutes=cfg_signals.orb_minutes,
        prev_close=float(daily["close"].iloc[-1]),
        avg_daily_volume=float(daily["volume"].tail(20).mean()),
        atr=compute_atr(daily),
        now=view.as_of,
    )


ALL_SETUPS: list[type[Setup]] = [OpeningRangeBreakout, VwapReclaimReject, RelVolContinuation]
