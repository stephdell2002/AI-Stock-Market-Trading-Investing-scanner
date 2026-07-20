"""Synthetic-company builders for screener tests. Offline by construction."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from tests.conftest import SyntheticProvider

from watchman.data.provider import DataProvider, Freshness, Fundamentals, StatementSet

FISCAL_ENDS = pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"])


def _frame(rows: dict[str, list[float]]) -> pd.DataFrame:
    """Statement frame in yfinance layout: rows = items, columns = periods
    (descending, most recent first). Input lists are oldest-first."""
    df = pd.DataFrame(rows, index=FISCAL_ENDS).T
    return df[df.columns[::-1]]


def make_statements(
    symbol: str = "TEST",
    revenue: list[float] | None = None,
    gross_profit: list[float] | None = None,
    operating_income: list[float] | None = None,
    diluted_eps: list[float] | None = None,
    ebit: float = 200.0,
    pretax_income: float = 190.0,
    tax_provision: float = 47.5,
    invested_capital: float = 1000.0,
    total_debt: float = 300.0,
    ebitda: float = 150.0,
    net_income: float = 100.0,
    free_cash_flow: float = 90.0,
) -> StatementSet:
    revenue = revenue or [100.0, 120.0, 144.0, 172.8]
    gross_profit = gross_profit or [40.0, 50.4, 63.4, 79.5]
    operating_income = operating_income or [20.0, 25.2, 31.7, 39.7]
    diluted_eps = diluted_eps or [1.0, 1.2, 1.44, 1.728]
    n = len(FISCAL_ENDS)
    income = _frame(
        {
            "Total Revenue": revenue,
            "Gross Profit": gross_profit,
            "Operating Income": operating_income,
            "Diluted EPS": diluted_eps,
            "EBIT": [ebit] * n,
            "Pretax Income": [pretax_income] * n,
            "Tax Provision": [tax_provision] * n,
            "EBITDA": [ebitda] * n,
            "Net Income": [net_income] * n,
        }
    )
    balance = _frame(
        {
            "Total Debt": [total_debt] * n,
            "Invested Capital": [invested_capital] * n,
            "Stockholders Equity": [invested_capital - total_debt] * n,
            "Cash And Cash Equivalents": [50.0] * n,
        }
    )
    cashflow = _frame(
        {
            "Free Cash Flow": [free_cash_flow] * n,
            "Operating Cash Flow": [free_cash_flow + 30.0] * n,
            "Capital Expenditure": [-30.0] * n,
        }
    )
    return StatementSet(
        symbol=symbol, income=income, balance=balance, cashflow=cashflow,
        fetched_at=datetime.now(), point_in_time=False,
    )


def make_fundamentals(
    symbol: str = "TEST",
    sector: str = "Information Technology",
    market_cap: float = 3000.0,
    trailing_pe: float = 20.0,
    ev_to_ebitda: float = 15.0,
    free_cash_flow: float = 90.0,
    **overrides,
) -> Fundamentals:
    return Fundamentals(
        symbol=symbol,
        fetched_at=datetime.now(),
        point_in_time=False,
        sector=sector,
        market_cap=market_cap,
        trailing_pe=trailing_pe,
        ev_to_ebitda=ev_to_ebitda,
        free_cash_flow=free_cash_flow,
        **overrides,
    )


def make_price_history(
    daily_return: float, days: int = 300, base: float = 100.0, end: str = "2026-07-17"
) -> pd.DataFrame:
    """Constant-daily-return price path (business days, tz-naive index)."""
    dates = pd.bdate_range(end=end, periods=days)
    close = base * (1 + daily_return) ** np.arange(days)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 1_000_000.0,
        },
        index=dates,
    )


class CompanyProvider(DataProvider):
    """Serves a configured set of synthetic companies (plus SPY bars)."""

    name = "company-synthetic"

    def __init__(self, companies: dict[str, dict]):
        """companies: symbol -> {fundamentals, statements, bars} (any optional)."""
        self.companies = companies

    def daily_bars(self, symbol, start, end) -> pd.DataFrame:
        bars = self.companies[symbol].get("bars")
        if bars is None:
            raise ValueError(f"no bars for {symbol}")
        mask = (bars.index >= pd.Timestamp(start.date())) & (
            bars.index <= pd.Timestamp(end.date())
        )
        return bars.loc[mask]

    def fundamentals(self, symbol) -> Fundamentals:
        f = self.companies[symbol].get("fundamentals")
        if f is None:
            raise ValueError(f"no fundamentals for {symbol}")
        return f

    def financial_statements(self, symbol) -> StatementSet:
        s = self.companies[symbol].get("statements")
        if s is None:
            raise ValueError(f"no statements for {symbol}")
        return s

    def quote_freshness(self) -> Freshness:
        return Freshness.EOD


def three_company_provider(end: str | None = None) -> CompanyProvider:
    """GOOD > MEDIocre > BAD on every pillar, plus SPY as the benchmark.

    Price paths end today-ish so AsOfView(now) sees a full momentum window.
    """
    end = end or pd.Timestamp.now().normalize().strftime("%Y-%m-%d")
    spy = make_price_history(0.0004, days=300, end=end)
    good = {
        "fundamentals": make_fundamentals(
            "GOOD", market_cap=2000.0, trailing_pe=12.0, ev_to_ebitda=8.0,
            free_cash_flow=150.0,
        ),
        "statements": make_statements(
            "GOOD",
            revenue=[100, 125, 156, 195],          # ~25% CAGR
            diluted_eps=[1.0, 1.3, 1.7, 2.2],
            gross_profit=[40, 52.5, 68.6, 89.7],   # margin 40 -> 46%
            operating_income=[20, 27.5, 37.4, 50.7],
            ebit=300.0, invested_capital=800.0,    # high ROIC
            total_debt=100.0, ebitda=350.0,        # low leverage
            net_income=150.0, free_cash_flow=160.0,
        ),
        "bars": make_price_history(0.0012, days=300, end=end),  # beats SPY
    }
    med = {
        "fundamentals": make_fundamentals(
            "MED", market_cap=2000.0, trailing_pe=22.0, ev_to_ebitda=14.0,
            free_cash_flow=60.0,
        ),
        "statements": make_statements(
            "MED",
            revenue=[100, 106, 112, 119],          # ~6% CAGR
            diluted_eps=[1.0, 1.05, 1.1, 1.16],
            ebit=150.0, invested_capital=1200.0,
            total_debt=400.0, ebitda=180.0,
            net_income=80.0, free_cash_flow=70.0,
        ),
        "bars": make_price_history(0.0004, days=300, end=end),  # tracks SPY
    }
    bad = {
        "fundamentals": make_fundamentals(
            "BAD", market_cap=2000.0, trailing_pe=45.0, ev_to_ebitda=30.0,
            free_cash_flow=10.0,
        ),
        "statements": make_statements(
            "BAD",
            revenue=[120, 115, 110, 105],          # shrinking
            diluted_eps=[1.5, 1.2, 0.9, 0.7],
            gross_profit=[54, 48, 42, 36],         # margins collapsing
            operating_income=[24, 20, 15, 10],
            ebit=40.0, invested_capital=1500.0,    # weak ROIC
            total_debt=900.0, ebitda=100.0,        # heavy leverage
            net_income=20.0, free_cash_flow=5.0,
        ),
        "bars": make_price_history(-0.0006, days=300, end=end),  # lags SPY
    }
    return CompanyProvider({"GOOD": good, "MED": med, "BAD": bad, "SPY": {"bars": spy}})


def universe_frame(provider: CompanyProvider) -> pd.DataFrame:
    rows = [
        {
            "symbol": sym,
            "name": sym.title() + " Corp",
            "sector": "Information Technology",
            "indices": "test",
        }
        for sym in provider.companies
        if sym != "SPY"
    ]
    return pd.DataFrame(rows)


__all__ = [
    "CompanyProvider",
    "SyntheticProvider",
    "make_fundamentals",
    "make_price_history",
    "make_statements",
    "three_company_provider",
    "universe_frame",
]
