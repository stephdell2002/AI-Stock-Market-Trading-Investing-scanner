"""Position sizing and risk math. The hard limits themselves live in
config/risk.yaml; this module turns them into share counts."""

from __future__ import annotations

import math


def shares_for_risk(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
) -> int:
    """Shares such that (entry - stop) * shares <= equity * risk_pct / 100.

    Works for longs (stop below entry) and shorts (stop above entry).
    Capped so gross exposure never exceeds account equity — v1 uses no leverage.
    Returns 0 when the trade cannot be sized within the risk budget.
    """
    if equity <= 0 or risk_pct <= 0 or entry <= 0:
        return 0
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0:
        return 0  # a trade with no stop distance cannot be sized honestly
    risk_dollars = equity * risk_pct / 100.0
    shares = math.floor(risk_dollars / per_share_risk)
    max_affordable = math.floor(equity / entry)
    return max(0, min(shares, max_affordable))


def risk_reward(entry: float, stop: float, target: float) -> float:
    """Reward:risk ratio. Positive only when target and stop sit on opposite
    sides of entry (i.e., the trade direction is coherent)."""
    risk = entry - stop
    reward = target - entry
    if risk == 0:
        return 0.0
    ratio = reward / risk
    return ratio if ratio > 0 else 0.0
