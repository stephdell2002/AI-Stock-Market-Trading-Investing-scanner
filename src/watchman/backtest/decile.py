"""Decile backtest of the Module A composite score — the honest version.

What this actually tests, stated up front:

- MOMENTUM PILLAR ONLY. yfinance fundamentals are latest-snapshot, not
  point-in-time; backtesting today's quality/growth/valuation numbers against
  old prices would be lookahead bias, and AsOfView refuses to serve them
  (tests/test_lookahead_canary.py). So this backtests the one pillar whose
  history is honestly reconstructible from prices: 6m/12m relative strength.
  It is NOT a backtest of the full four-pillar composite.
- SURVIVORSHIP BIAS. The universe is CURRENT index membership; names that
  fell out (often the losers) are absent, which inflates every decile's
  return. The spread between deciles is more trustworthy than any absolute
  number.
- Costs are applied: each rebalance charges turnover x (slippage + spread)
  on both sides. Dividends are included via adjusted closes.

Every run of this backtest prints these caveats. That is deliberate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from itertools import pairwise

import numpy as np
import pandas as pd

from watchman.config import CostsConfig
from watchman.data.provider import ET, AsOfView, DataProvider, normalize_bars
from watchman.screener.metrics import momentum_metrics

MOMENTUM_LOOKBACK_DAYS = 420  # calendar days of history each rebalance looks back
MONTHS_PER_YEAR = 12

DISCLAIMERS = [
    "MOMENTUM ONLY: fundamentals are not point-in-time on free data, so this "
    "backtests the momentum pillar, not the full composite.",
    "SURVIVORSHIP BIAS: universe = current index membership; departed losers are "
    "missing, inflating absolute returns. Trust the decile SPREAD more than levels.",
]


@dataclass
class DecileResult:
    n_deciles: int
    rebalance_dates: list[pd.Timestamp]
    #: monthly return per decile (columns 1..n, 1 = highest momentum)
    decile_returns: pd.DataFrame
    equity_curves: pd.DataFrame          # cumulative, starting at 1.0
    metrics_by_decile: dict[int, dict]
    top_bottom_spread_pct: float         # annualized top minus bottom CAGR
    monotonic_fraction: float            # how often decile k beat decile k+1
    avg_names_per_decile: float
    costs_bps_per_side: float
    warnings: list[str] = field(default_factory=list)


def _month_end_sessions(calendar: pd.DatetimeIndex) -> list[pd.Timestamp]:
    by_month = pd.Series(calendar, index=calendar).groupby(calendar.to_period("M"))
    return [g.iloc[-1] for _, g in by_month]


def momentum_decile_backtest(
    provider: DataProvider,
    symbols: list[str],
    start: datetime,
    end: datetime,
    costs: CostsConfig,
    n_deciles: int = 10,
    benchmark: str = "SPY",
    progress=lambda _msg: None,
) -> DecileResult:
    padded_start = start - timedelta(days=MOMENTUM_LOOKBACK_DAYS)
    progress(f"Loading bars for {len(symbols)} symbols + {benchmark}...")
    bars: dict[str, pd.DataFrame] = {}
    failures: list[str] = []
    for symbol in [benchmark, *symbols]:
        try:
            bars[symbol] = normalize_bars(provider.daily_bars(symbol, padded_start, end))
        except Exception:
            failures.append(symbol)
    if benchmark not in bars:
        raise RuntimeError(f"cannot run decile backtest without benchmark {benchmark}")

    calendar = bars[benchmark].loc[pd.Timestamp(start.date()) :].index
    rebalances = _month_end_sessions(calendar)
    if len(rebalances) < 13:
        raise ValueError(
            f"only {len(rebalances)} month-ends in range; need >= 13 (about a year) "
            "for a meaningful decile backtest"
        )

    decile_rows: list[dict[int, float]] = []
    period_index: list[pd.Timestamp] = []
    holdings: dict[int, set[str]] = {}
    names_per_decile: list[float] = []
    cost_per_unit_turnover = 2 * costs.total_bps_per_side / 1e4

    for i in range(len(rebalances) - 1):
        d, d_next = rebalances[i], rebalances[i + 1]
        progress(f"[{i + 1}/{len(rebalances) - 1}] rebalance {d.date()}...")
        pin = datetime.combine(d.date(), time(16, 0), tzinfo=ET)
        view = AsOfView(provider, pin)
        spy_view_bars = view.daily_bars(
            benchmark, start=pin - timedelta(days=MOMENTUM_LOOKBACK_DAYS)
        )

        scores: dict[str, float] = {}
        fwd_returns: dict[str, float] = {}
        for symbol in symbols:
            sym_bars = bars.get(symbol)
            if sym_bars is None:
                continue
            visible = sym_bars.loc[sym_bars.index <= d]
            m = momentum_metrics(visible, spy_view_bars)
            vals = [v for v in (m["rel_strength_6m"], m["rel_strength_12m"]) if v is not None]
            if not vals:
                continue
            window = sym_bars.loc[(sym_bars.index >= d) & (sym_bars.index <= d_next)]
            if len(window) < 2:
                continue
            scores[symbol] = float(np.mean(vals))
            fwd_returns[symbol] = float(
                window["adj_close"].iloc[-1] / window["adj_close"].iloc[0] - 1
            )

        if len(scores) < n_deciles * 2:
            continue  # not enough scoreable names this month to form deciles

        score_series = pd.Series(scores)
        pct = score_series.rank(pct=True, ascending=False, method="first")
        assignment = np.ceil(pct * n_deciles).clip(1, n_deciles).astype(int)

        row: dict[int, float] = {}
        for decile in range(1, n_deciles + 1):
            members = set(assignment.index[assignment == decile])
            if not members:
                continue
            gross = float(np.mean([fwd_returns[s] for s in members]))
            previous = holdings.get(decile)
            turnover = (
                1.0 if previous is None else 1 - len(members & previous) / max(len(members), 1)
            )
            row[decile] = gross - turnover * cost_per_unit_turnover
            holdings[decile] = members
        names_per_decile.append(len(scores) / n_deciles)
        decile_rows.append(row)
        period_index.append(d_next)

    if not decile_rows:
        raise RuntimeError("no rebalance produced enough scoreable names")

    returns = pd.DataFrame(decile_rows, index=pd.DatetimeIndex(period_index))
    returns = returns[sorted(returns.columns)]
    equity = (1 + returns.fillna(0.0)).cumprod()

    metrics_by_decile = {
        int(d): _monthly_metrics(equity[d]) for d in returns.columns
    }
    top, bottom = min(returns.columns), max(returns.columns)
    spread = (
        metrics_by_decile[top]["cagr_pct"] - metrics_by_decile[bottom]["cagr_pct"]
    )

    adjacent_wins = 0
    adjacent_total = 0
    cagrs = [metrics_by_decile[int(d)]["cagr_pct"] for d in sorted(returns.columns)]
    for a, b in pairwise(cagrs):
        if not (math.isnan(a) or math.isnan(b)):
            adjacent_total += 1
            adjacent_wins += a > b

    warnings = list(DISCLAIMERS)
    if failures:
        warnings.append(
            f"DATA GAPS: {len(failures)} symbols had no usable bars and were skipped "
            f"(e.g. {', '.join(failures[:8])})."
        )
    for d, m in metrics_by_decile.items():
        if not math.isnan(m["sharpe"]) and m["sharpe"] > 3.0:
            warnings.append(
                f"SUSPECT RESULT: decile {d} Sharpe {m['sharpe']:.2f} > 3 — treat as a "
                "probable bug (lookahead/survivorship/overfitting), not brilliance."
            )

    return DecileResult(
        n_deciles=n_deciles,
        rebalance_dates=rebalances,
        decile_returns=returns,
        equity_curves=equity,
        metrics_by_decile=metrics_by_decile,
        top_bottom_spread_pct=spread,
        monotonic_fraction=adjacent_wins / adjacent_total if adjacent_total else math.nan,
        avg_names_per_decile=float(np.mean(names_per_decile)),
        costs_bps_per_side=costs.total_bps_per_side,
        warnings=warnings,
    )


def _monthly_metrics(equity: pd.Series) -> dict:
    from watchman.backtest.metrics import performance_metrics

    return performance_metrics(equity, periods_per_year=MONTHS_PER_YEAR)
