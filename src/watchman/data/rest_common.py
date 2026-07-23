"""Shared helpers for the REST data providers (Finnhub / Polygon / FMP)."""

from __future__ import annotations

import re

import pandas as pd

from watchman.data.provider import (
    INTRADAY_INTERVALS,
    MARKET_CLOSE,
    MARKET_OPEN,
    Freshness,
)

# The three real-time-capable providers default to DELAYED unless the user
# asserts otherwise: free tiers are typically delayed, and it is safer to
# under-claim freshness (a DELAYED — NOT ACTIONABLE label) than to over-claim.
REALTIME_DEFAULT_FRESHNESS = Freshness.DELAYED


def resolve_freshness(configured: str | None) -> Freshness:
    """Turn an optional config string into a Freshness, defaulting to DELAYED
    for the real-time providers. Raises on an unrecognized value."""
    if configured is None:
        return REALTIME_DEFAULT_FRESHNESS
    try:
        return Freshness(configured.strip().upper())
    except ValueError as exc:
        valid = ", ".join(f.value for f in Freshness)
        raise ValueError(
            f"data.freshness must be one of {valid} (got {configured!r})"
        ) from exc


def check_interval(interval: str) -> None:
    if interval not in INTRADAY_INTERVALS:
        raise ValueError(
            f"unsupported interval {interval!r}; use one of {sorted(INTRADAY_INTERVALS)}"
        )


def parse_price_range(value) -> tuple[float | None, float | None]:
    """Parse an IPO price field into (low, high).

    Handles a single number, a numeric value, or strings like '20.00-24.00',
    '$20.00 - $24.00', '20'. Returns (None, None) when nothing parses; a single
    price yields (price, price)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return (v, v) if v > 0 else (None, None)
    if not isinstance(value, str):
        return (None, None)
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return (None, None)
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (min(nums), max(nums))


def regular_session_only(bars: pd.DataFrame) -> pd.DataFrame:
    """Keep only bars whose START time is within the 09:30-16:00 ET regular
    session. Used when include_premarket is False so extended-hours bars from
    providers that return them don't leak into the setups."""
    if bars.empty:
        return bars
    times = bars.index.time
    mask = [(MARKET_OPEN <= t < MARKET_CLOSE) for t in times]
    return bars.loc[mask]
