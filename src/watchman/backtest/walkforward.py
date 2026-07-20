"""Walk-forward validation: optimize on window N, test on window N+1.

Out-of-sample results are the product; in-sample results exist only to show
how much the strategy degrades out of sample. The two are never blended.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from watchman.backtest.engine import BacktestResult
from watchman.backtest.metrics import honesty_flags, performance_metrics, trade_metrics

#: run_fn(params, start_session, end_session) -> BacktestResult
RunFn = Callable[[dict, pd.Timestamp, pd.Timestamp], BacktestResult]


@dataclass(frozen=True)
class Window:
    opt_start: pd.Timestamp
    opt_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass
class WindowResult:
    window: Window
    best_params: dict
    in_sample_metrics: dict
    out_of_sample_metrics: dict
    out_of_sample_result: BacktestResult


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    oos_equity: pd.Series          # stitched out-of-sample curve (normalized to 1.0)
    oos_metrics: dict              # THE headline numbers
    in_sample_objective_avg: float # what optimization saw (for degradation context)
    oos_objective: float
    objective: str
    warnings: list[str] = field(default_factory=list)


def day_windows(
    calendar: pd.DatetimeIndex, opt_days: int, test_days: int
) -> list[Window]:
    """Rolling windows over trading sessions: [opt_days][test_days], stepping
    forward by test_days. Windows never overlap their own test data."""
    if opt_days < 2 or test_days < 1:
        raise ValueError("opt_days must be >=2 and test_days >=1")
    windows: list[Window] = []
    i = 0
    while i + opt_days + test_days <= len(calendar):
        windows.append(
            Window(
                opt_start=calendar[i],
                opt_end=calendar[i + opt_days - 1],
                test_start=calendar[i + opt_days],
                test_end=calendar[i + opt_days + test_days - 1],
            )
        )
        i += test_days
    return windows


def _objective_value(metrics: dict, objective: str) -> float:
    v = metrics.get(objective)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return -math.inf
    return float(v)


def walk_forward(
    run_fn: RunFn,
    param_grid: list[dict],
    windows: list[Window],
    objective: str = "sharpe",
) -> WalkForwardResult:
    if not param_grid:
        raise ValueError("param_grid is empty")
    if not windows:
        raise ValueError("no walk-forward windows — period too short for the window sizes")

    window_results: list[WindowResult] = []
    oos_return_chunks: list[pd.Series] = []
    is_objectives: list[float] = []

    for window in windows:
        best_params: dict | None = None
        best_value = -math.inf
        best_is_metrics: dict = {}
        for params in param_grid:
            result = run_fn(params, window.opt_start, window.opt_end)
            value = _objective_value(result.metrics, objective)
            if value > best_value:
                best_value, best_params, best_is_metrics = value, params, result.metrics
        assert best_params is not None
        oos_result = run_fn(best_params, window.test_start, window.test_end)
        window_results.append(
            WindowResult(
                window=window,
                best_params=best_params,
                in_sample_metrics=best_is_metrics,
                out_of_sample_metrics=oos_result.metrics,
                out_of_sample_result=oos_result,
            )
        )
        is_objectives.append(best_value)
        oos_return_chunks.append(oos_result.equity_curve.pct_change().dropna())

    oos_returns = pd.concat(oos_return_chunks)
    oos_equity = (1 + oos_returns).cumprod()
    oos_trades = [t for wr in window_results for t in wr.out_of_sample_result.trades]
    oos_metrics = {**performance_metrics(oos_equity), **trade_metrics(oos_trades)}
    oos_objective = _objective_value(oos_metrics, objective)
    is_avg = (
        sum(v for v in is_objectives if not math.isinf(v)) / len(is_objectives)
        if is_objectives
        else math.nan
    )

    warnings = honesty_flags(oos_metrics)
    degraded = (
        not math.isnan(is_avg)
        and not math.isinf(oos_objective)
        and is_avg > 0
        and oos_objective < 0.5 * is_avg
    )
    if degraded:
        warnings.append(
                f"OVERFITTING SIGNAL: out-of-sample {objective} ({oos_objective:.2f}) is "
                f"less than half the in-sample average ({is_avg:.2f}). The optimizer "
                "fit noise; distrust the in-sample numbers."
            )

    return WalkForwardResult(
        windows=window_results,
        oos_equity=oos_equity,
        oos_metrics=oos_metrics,
        in_sample_objective_avg=is_avg,
        oos_objective=oos_objective,
        objective=objective,
        warnings=warnings,
    )
