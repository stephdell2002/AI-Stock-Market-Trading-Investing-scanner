"""Module A's simulated long-term portfolio: monthly rebalance into the
latest screener run's top names, equal weight, with costs on every fill.

Stated limitations: fills at the last EOD close (+slippage); dividends are
not credited (the book therefore understates total return for payers);
existing holdings are NOT resized between membership changes — weights drift
with performance, and a new name gets min(equal-weight budget, available
cash). Full weight-targeting rebalancing is a later refinement.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from watchman.data.provider import AsOfView
from watchman.paper.book import PaperBook

REBALANCE_EVERY_DAYS = 28
#: keep a cash cushion so slippage/rounding never blocks the last buy
TARGET_INVESTED_FRACTION = 0.98


def last_rebalance_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT ts FROM longterm_rebalances ORDER BY rebalance_id DESC LIMIT 1"
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def latest_watchlist(conn: sqlite3.Connection, top: int) -> list[str]:
    row = conn.execute(
        "SELECT run_id FROM screener_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    rows = conn.execute(
        "SELECT symbol FROM screener_scores WHERE run_id = ? AND rank IS NOT NULL"
        " ORDER BY rank LIMIT ?",
        (int(row[0]), top),
    ).fetchall()
    return [r[0] for r in rows]


def rebalance_longterm(
    book: PaperBook,
    conn: sqlite3.Connection,
    view: AsOfView,
    top: int,
    now: datetime,
    force: bool = False,
) -> list[str]:
    """Monthly equal-weight rebalance to the latest screener top-N.
    Returns human-readable notes (empty when it's not time yet)."""
    last = last_rebalance_at(conn)
    if not force and last is not None and (now - last).days < REBALANCE_EVERY_DAYS:
        return []
    targets = latest_watchlist(conn, top)
    if not targets:
        return ["no screener run stored yet — run `watchman screen` first"]

    notes: list[str] = []
    prices: dict[str, float] = {}
    held = {p.symbol: p for p in book.open_positions()}
    for symbol in set(targets) | set(held):
        try:
            daily = view.daily_bars(symbol, start=view.as_of - timedelta(days=10))
            prices[symbol] = float(daily["close"].iloc[-1])
        except Exception as exc:
            notes.append(f"{symbol}: no price ({exc}) — skipped this rebalance")

    # 1) Sell names that fell off the watchlist (or lost pricing).
    for symbol, pos in held.items():
        if symbol not in targets and symbol in prices:
            pnl = book.close_position(pos, now, prices[symbol], note="rebalance-exit")
            notes.append(f"sold {symbol} x{pos.qty:.0f} @ ~{prices[symbol]:.2f} "
                         f"(PnL ${pnl:+,.2f})")

    # 2) Buy new names equal-weight from available cash (capped by cash:
    #    existing holdings are not trimmed to fund new ones in v1).
    buyable = [s for s in targets if s in prices and s not in held]
    if buyable:
        equity = book.equity(prices)
        per_name = equity * TARGET_INVESTED_FRACTION / len(targets)
        bps = book.costs.total_bps_per_side / 1e4
        for symbol in buyable:
            fill_estimate = prices[symbol] * (1 + bps)
            budget = min(per_name, book.cash - book.costs.commission_per_trade)
            qty = int(budget // fill_estimate)
            if qty <= 0:
                notes.append(f"{symbol}: no budget left at ~{fill_estimate:.2f}/share")
                continue
            outcome = book.open_position(
                ts=now, symbol=symbol, direction="long", qty=qty,
                reference_price=prices[symbol],
            )
            if isinstance(outcome, str):
                notes.append(f"{symbol}: not bought — {outcome}")
            else:
                notes.append(f"bought {symbol} x{qty} @ {outcome.avg_cost:.2f}")

    book.mark(now, prices)
    with conn:
        conn.execute(
            "INSERT INTO longterm_rebalances (ts, holdings) VALUES (?, ?)",
            (now.isoformat(), json.dumps(targets)),
        )
    return notes or ["holdings already match the watchlist"]


def watchlist_changes(conn: sqlite3.Connection, top: int) -> dict[str, list[str]]:
    """Entered/exited the top-N between the two most recent screener runs."""
    rows = conn.execute(
        "SELECT run_id FROM screener_runs ORDER BY run_id DESC LIMIT 2"
    ).fetchall()
    if len(rows) < 2:
        return {"entered": [], "exited": []}

    def top_syms(run_id: int) -> list[str]:
        got = conn.execute(
            "SELECT symbol FROM screener_scores WHERE run_id = ? AND rank IS NOT NULL"
            " ORDER BY rank LIMIT ?",
            (run_id, top),
        ).fetchall()
        return [g[0] for g in got]

    current, previous = top_syms(int(rows[0][0])), top_syms(int(rows[1][0]))
    return {
        "entered": [s for s in current if s not in previous],
        "exited": [s for s in previous if s not in current],
    }


def positions_frame(book: PaperBook, prices: dict[str, float]) -> pd.DataFrame:
    rows = []
    for pos in book.open_positions():
        price = prices.get(pos.symbol, pos.avg_cost)
        if pos.direction == "long":
            unrealized = (price - pos.avg_cost) * pos.qty
        else:
            unrealized = (pos.avg_cost - price) * pos.qty
        rows.append(
            {
                "symbol": pos.symbol, "direction": pos.direction, "qty": pos.qty,
                "avg_cost": pos.avg_cost, "last": price, "unrealized": unrealized,
            }
        )
    return pd.DataFrame(rows)
