"""Backtest performance metrics — and the honesty flags that treat
too-good-to-be-true results as probable bugs, per the project principles."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252

#: Results beyond these look like bugs (lookahead, survivorship, overfitting),
#: not genius. They are flagged loudly wherever metrics are shown.
SHARPE_SUSPECT = 3.0
WIN_RATE_SUSPECT = 0.75


def performance_metrics(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> dict:
    """Equity-curve metrics. Returns NaN (not fake zeros) when undefined."""
    out = {
        "cagr_pct": math.nan,
        "sharpe": math.nan,
        "sortino": math.nan,
        "max_drawdown_pct": math.nan,
        "total_return_pct": math.nan,
        "n_periods": len(equity),
    }
    if len(equity) < 2 or float(equity.iloc[0]) <= 0:
        return out
    returns = equity.pct_change().dropna()
    years = len(returns) / periods_per_year
    end_over_start = float(equity.iloc[-1]) / float(equity.iloc[0])
    out["total_return_pct"] = (end_over_start - 1) * 100
    if end_over_start > 0 and years > 0:
        out["cagr_pct"] = (end_over_start ** (1 / years) - 1) * 100
    std = float(returns.std())
    if std > 0:
        out["sharpe"] = float(returns.mean()) / std * math.sqrt(periods_per_year)
    downside = returns[returns < 0]
    downside_std = float(downside.std()) if len(downside) > 1 else 0.0
    if downside_std > 0:
        out["sortino"] = float(returns.mean()) / downside_std * math.sqrt(periods_per_year)
    running_max = equity.cummax()
    out["max_drawdown_pct"] = float(((equity / running_max) - 1).min()) * 100
    return out


def trade_metrics(trades: list) -> dict:
    """Round-trip trade metrics. Sample size is always reported — a 90% win
    rate over 10 trades is noise, and the reader deserves to see the 10."""
    out = {
        "n_trades": len(trades),
        "win_rate": math.nan,
        "profit_factor": math.nan,
        "expectancy_usd": math.nan,
        "expectancy_pct": math.nan,
        "avg_win_usd": math.nan,
        "avg_loss_usd": math.nan,
    }
    if not trades:
        return out
    pnls = np.array([t.pnl for t in trades], dtype=float)
    rets = np.array([t.ret_pct for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    out["win_rate"] = len(wins) / len(pnls)
    gross_loss = float(-losses.sum())
    if gross_loss > 0:
        out["profit_factor"] = float(wins.sum()) / gross_loss
    elif len(wins):
        out["profit_factor"] = math.inf
    out["expectancy_usd"] = float(pnls.mean())
    out["expectancy_pct"] = float(rets.mean())
    if len(wins):
        out["avg_win_usd"] = float(wins.mean())
    if len(losses):
        out["avg_loss_usd"] = float(losses.mean())
    return out


def honesty_flags(metrics: dict) -> list[str]:
    """Loud warnings for implausibly good results. These are shown wherever
    the metrics are shown — never filtered out to make a report look better."""
    flags: list[str] = []
    sharpe = metrics.get("sharpe")
    if sharpe is not None and not math.isnan(sharpe) and sharpe > SHARPE_SUSPECT:
        flags.append(
            f"SUSPECT RESULT: Sharpe {sharpe:.2f} > {SHARPE_SUSPECT:.0f}. Treat as a "
            "probable bug (lookahead, survivorship, or overfitting) until proven otherwise."
        )
    win_rate = metrics.get("win_rate")
    n_trades = metrics.get("n_trades", 0)
    if (
        win_rate is not None
        and not math.isnan(win_rate)
        and win_rate > WIN_RATE_SUSPECT
        and n_trades >= 10
    ):
        flags.append(
            f"SUSPECT RESULT: win rate {win_rate:.0%} > {WIN_RATE_SUSPECT:.0%} over "
            f"{n_trades} trades. Treat as a probable bug until proven otherwise."
        )
    if 0 < n_trades < 30:
        flags.append(
            f"SMALL SAMPLE: only {n_trades} closed trades — every statistic above "
            "is noise-dominated; do not act on it."
        )
    return flags


def format_metrics(metrics: dict) -> str:
    """Compact human-readable metrics block."""

    def fmt(key: str, spec: str = ".2f") -> str:
        v = metrics.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "n/a"
        if isinstance(v, float) and math.isinf(v):
            return "inf"
        return f"{v:{spec}}"

    lines = [
        f"CAGR {fmt('cagr_pct')}%  |  total return {fmt('total_return_pct')}%",
        f"Sharpe {fmt('sharpe')}  |  Sortino {fmt('sortino')}  |  "
        f"max drawdown {fmt('max_drawdown_pct')}%",
        f"trades {metrics.get('n_trades', 0)}  |  win rate {fmt('win_rate', '.1%')}  |  "
        f"profit factor {fmt('profit_factor')}",
        f"expectancy ${fmt('expectancy_usd')} / {fmt('expectancy_pct')}% per trade",
    ]
    return "\n".join(lines)
