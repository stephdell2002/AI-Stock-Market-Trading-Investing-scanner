"""Per-symbol raw metric extraction for the Module A screener.

All functions are pure: they take already-fetched data and return
{metric_name: value | None}. None means "honestly unknown" — scoring excludes
missing metrics instead of guessing.

Free-data honesty notes:
- yfinance serves ~4 annual statement columns, so "3-5 yr CAGR" is in practice
  a ~3-year CAGR. Output labels say "~3y" rather than pretending.
- Statements are the latest restatement, not as-originally-reported.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from watchman.data.provider import Fundamentals, StatementSet

#: Pillar -> metric names, in display order. Valuation's *_vs_sector metrics
#: are derived cross-sectionally in scoring.py from the raw inputs here.
PILLAR_METRICS: dict[str, list[str]] = {
    "quality": [
        "roic",
        "gross_margin_trend",
        "operating_margin_trend",
        "debt_to_ebitda",
        "fcf_conversion",
    ],
    "growth": ["revenue_cagr", "eps_cagr"],
    "valuation": ["fcf_yield", "pe_vs_sector", "ev_ebitda_vs_sector"],
    "momentum": ["rel_strength_6m", "rel_strength_12m"],
}

ALL_METRICS: list[str] = [m for metrics in PILLAR_METRICS.values() for m in metrics]

#: Metrics where a LOWER value is better; scoring negates them before ranking.
LOWER_IS_BETTER: frozenset[str] = frozenset({"debt_to_ebitda"})

#: Raw inputs valuation needs before sector-relative transformation.
VALUATION_RAW = ["fcf_yield", "trailing_pe", "ev_to_ebitda"]


def _ok(v: float | None) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def line(df: pd.DataFrame | None, *aliases: str) -> pd.Series | None:
    """Find a statement line item by any alias (case-insensitive) and return
    it as a numeric Series sorted by period ascending, or None."""
    if df is None or df.empty:
        return None
    lookup = {str(ix).strip().casefold(): ix for ix in df.index}
    for alias in aliases:
        ix = lookup.get(alias.strip().casefold())
        if ix is None:
            continue
        row = df.loc[ix]
        if isinstance(row, pd.DataFrame):  # duplicated label: take the first
            row = row.iloc[0]
        s = pd.to_numeric(row, errors="coerce")
        s.index = pd.to_datetime(s.index)
        s = s.sort_index().dropna()
        if not s.empty:
            return s
    return None


def cagr_pct(s: pd.Series | None, min_points: int = 3) -> float | None:
    """Compound annual growth rate in %, or None when it cannot be computed
    honestly (too few periods, or a non-positive endpoint)."""
    if s is None or len(s) < min_points:
        return None
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    if first <= 0 or last <= 0:
        return None
    years = (s.index[-1] - s.index[0]).days / 365.25
    if years < 1.5:
        return None
    return ((last / first) ** (1 / years) - 1) * 100


def trend_pp_per_year(numer: pd.Series | None, denom: pd.Series | None) -> float | None:
    """Slope of (numer/denom) in percentage points per year over the common
    periods; None with fewer than 3 usable points."""
    if numer is None or denom is None:
        return None
    common = numer.index.intersection(denom.index)
    if len(common) < 3:
        return None
    d = denom.loc[common]
    ratio = (numer.loc[common][d > 0] / d[d > 0]) * 100
    if len(ratio) < 3:
        return None
    x = (ratio.index - ratio.index[0]).days / 365.25
    return float(np.polyfit(x, ratio.to_numpy(dtype=float), 1)[0])


def quality_metrics(stmts: StatementSet | None, fund: Fundamentals | None) -> dict:
    out: dict[str, float | None] = dict.fromkeys(PILLAR_METRICS["quality"])
    income = stmts.income if stmts else None
    balance = stmts.balance if stmts else None
    cashflow = stmts.cashflow if stmts else None

    revenue = line(income, "Total Revenue", "Operating Revenue")
    gross = line(income, "Gross Profit")
    op_income = line(income, "Operating Income", "EBIT")
    out["gross_margin_trend"] = trend_pp_per_year(gross, revenue)
    out["operating_margin_trend"] = trend_pp_per_year(op_income, revenue)

    # ROIC = NOPAT / invested capital, latest fiscal year.
    ebit = line(income, "EBIT", "Operating Income")
    pretax = line(income, "Pretax Income")
    tax = line(income, "Tax Provision", "Income Tax Expense")
    invested = line(balance, "Invested Capital")
    if invested is None:
        debt = line(balance, "Total Debt")
        equity = line(balance, "Stockholders Equity", "Total Equity Gross Minority Interest")
        cash = line(
            balance,
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        )
        if debt is not None and equity is not None:
            common = debt.index.intersection(equity.index)
            if len(common):
                invested = debt.loc[common] + equity.loc[common]
                if cash is not None:
                    invested = invested - cash.reindex(common).fillna(0.0)
    if ebit is not None and invested is not None and len(invested) and invested.iloc[-1] > 0:
        tax_rate = 0.21  # US statutory default when effective rate is unknowable
        if pretax is not None and tax is not None and len(pretax) and pretax.iloc[-1] > 0:
            tax_rate = min(max(float(tax.iloc[-1] / pretax.iloc[-1]), 0.0), 0.5)
        nopat = float(ebit.iloc[-1]) * (1 - tax_rate)
        out["roic"] = nopat / float(invested.iloc[-1]) * 100

    # Debt / EBITDA (lower is better; scoring negates before ranking).
    debt_latest = line(balance, "Total Debt")
    ebitda_series = line(income, "EBITDA", "Normalized EBITDA")
    total_debt = float(debt_latest.iloc[-1]) if debt_latest is not None else _ok(
        fund.total_debt if fund else None
    )
    ebitda = float(ebitda_series.iloc[-1]) if ebitda_series is not None else _ok(
        fund.ebitda if fund else None
    )
    if total_debt is not None and ebitda is not None and ebitda > 0:
        out["debt_to_ebitda"] = max(total_debt, 0.0) / ebitda

    # FCF conversion = free cash flow / net income (profitable companies only).
    fcf = line(cashflow, "Free Cash Flow")
    ocf = line(cashflow, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    capex = line(cashflow, "Capital Expenditure")
    fcf_latest = None
    if fcf is not None:
        fcf_latest = float(fcf.iloc[-1])
    elif ocf is not None and capex is not None and len(ocf) and len(capex):
        fcf_latest = float(ocf.iloc[-1]) + float(capex.iloc[-1])  # capex reported negative
    net_income = line(income, "Net Income", "Net Income Common Stockholders")
    ni_latest = float(net_income.iloc[-1]) if net_income is not None else _ok(
        fund.net_income if fund else None
    )
    if fcf_latest is not None and ni_latest is not None and ni_latest > 0:
        out["fcf_conversion"] = fcf_latest / ni_latest * 100

    return out


def growth_metrics(stmts: StatementSet | None) -> dict:
    out: dict[str, float | None] = dict.fromkeys(PILLAR_METRICS["growth"])
    income = stmts.income if stmts else None
    out["revenue_cagr"] = cagr_pct(line(income, "Total Revenue", "Operating Revenue"))
    out["eps_cagr"] = cagr_pct(line(income, "Diluted EPS", "Basic EPS"))
    return out


def valuation_inputs(fund: Fundamentals | None, stmts: StatementSet | None) -> dict:
    """Raw valuation inputs; sector-relative versions are built in scoring."""
    out: dict[str, float | None] = dict.fromkeys(VALUATION_RAW)
    if fund is None:
        return out
    market_cap = _ok(fund.market_cap)
    fcf = None
    if stmts is not None:
        fcf_series = line(stmts.cashflow, "Free Cash Flow")
        if fcf_series is not None:
            fcf = float(fcf_series.iloc[-1])
    if fcf is None:
        fcf = _ok(fund.free_cash_flow)
    if fcf is not None and market_cap is not None and market_cap > 0:
        out["fcf_yield"] = fcf / market_cap * 100

    pe = _ok(fund.trailing_pe)
    out["trailing_pe"] = pe if pe is not None and pe > 0 else None

    ev_ebitda = _ok(fund.ev_to_ebitda)
    if ev_ebitda is None:
        ev, ebitda = _ok(fund.enterprise_value), _ok(fund.ebitda)
        if ev is not None and ebitda is not None and ebitda > 0:
            ev_ebitda = ev / ebitda
    out["ev_to_ebitda"] = ev_ebitda if ev_ebitda is not None and ev_ebitda > 0 else None
    return out


def momentum_metrics(stock_bars: pd.DataFrame | None, spy_bars: pd.DataFrame | None) -> dict:
    """6m/12m total return minus SPY's, in percentage points (adj_close)."""
    out: dict[str, float | None] = dict.fromkeys(PILLAR_METRICS["momentum"])
    for window, name in ((126, "rel_strength_6m"), (252, "rel_strength_12m")):
        if stock_bars is None or spy_bars is None:
            continue
        if len(stock_bars) <= window or len(spy_bars) <= window:
            continue  # not enough history (e.g. recent IPO) — honestly unknown
        stock_ret = stock_bars["adj_close"].iloc[-1] / stock_bars["adj_close"].iloc[-1 - window] - 1
        spy_ret = spy_bars["adj_close"].iloc[-1] / spy_bars["adj_close"].iloc[-1 - window] - 1
        out[name] = (stock_ret - spy_ret) * 100
    return out


def collect_raw_metrics(
    fund: Fundamentals | None,
    stmts: StatementSet | None,
    stock_bars: pd.DataFrame | None,
    spy_bars: pd.DataFrame | None,
) -> dict:
    """All raw metrics for one symbol (valuation still in raw, pre-sector form)."""
    out: dict[str, float | None] = {}
    out.update(quality_metrics(stmts, fund))
    out.update(growth_metrics(stmts))
    out.update(valuation_inputs(fund, stmts))
    out.update(momentum_metrics(stock_bars, spy_bars))
    return {k: _ok(v) for k, v in out.items()}
