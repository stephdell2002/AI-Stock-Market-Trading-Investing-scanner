"""Intraday/daily indicator helpers for Module B. Pure functions; None or
empty output means honestly unknown, never a guessed default."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

import pandas as pd

from watchman.data.provider import ET, MARKET_CLOSE, MARKET_OPEN

REGULAR_SESSION_MINUTES = 390  # 9:30 -> 16:00 ET


def atr(daily_bars: pd.DataFrame, n: int = 14) -> float | None:
    """Average True Range over the last n complete daily bars (simple mean)."""
    if daily_bars is None or len(daily_bars) < n + 1:
        return None
    high, low = daily_bars["high"], daily_bars["low"]
    prev_close = daily_bars["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    value = tr.dropna().tail(n).mean()
    return float(value) if pd.notna(value) else None


def session_slice(
    intraday: pd.DataFrame, day: date_type, start_t=MARKET_OPEN, end_t=MARKET_CLOSE
) -> pd.DataFrame:
    """Bars of one session date within [start_t, end_t) ET, by bar START time."""
    if intraday.empty:
        return intraday
    idx = intraday.index
    mask = (
        (idx.date == day)
        & (idx.time >= start_t)
        & (idx.time < end_t)
    )
    return intraday.loc[mask]


def session_vwap(session_bars: pd.DataFrame) -> pd.Series:
    """Cumulative volume-weighted average price over the given session bars
    (typical price = (H+L+C)/3). Aligned to the input index."""
    typical = (session_bars["high"] + session_bars["low"] + session_bars["close"]) / 3
    vol = session_bars["volume"]
    cum_vol = vol.cumsum()
    vwap = (typical * vol).cumsum() / cum_vol.where(cum_vol > 0)
    return vwap


def elapsed_session_fraction(now: datetime) -> float:
    """Fraction of the regular session elapsed at `now` (ET), clamped to
    [1/390, 1]. Used to scale average daily volume for relative volume."""
    now_et = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    open_dt = now_et.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
                             second=0, microsecond=0)
    minutes = (now_et - open_dt).total_seconds() / 60
    return min(max(minutes, 1.0), REGULAR_SESSION_MINUTES) / REGULAR_SESSION_MINUTES


def relative_volume(cum_volume: float, avg_daily_volume: float, now: datetime) -> float | None:
    """Session volume so far vs. what's typical by this time of day
    (avg daily volume scaled by elapsed session fraction)."""
    if avg_daily_volume <= 0:
        return None
    return cum_volume / (avg_daily_volume * elapsed_session_fraction(now))
