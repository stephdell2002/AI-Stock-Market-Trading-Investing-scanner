"""PaperBook: a SQLite-backed simulated account. Broker-independent.

Fill honesty: every fill pays slippage + spread (CostsConfig) against the
trader plus commission, exactly like the backtester. Shorts are supported for
day trades; cash accounting reserves qty x entry as collateral on open so a
short can never lever the account either.

Known limitation (stated): dividends are not credited to paper cash.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from watchman.config import CostsConfig

DAY = "day"
LONGTERM = "longterm"


@dataclass(frozen=True)
class PaperPosition:
    position_id: int
    account: str
    symbol: str
    direction: str  # long|short
    qty: float
    avg_cost: float
    opened_at: datetime
    signal_id: int | None
    stop: float | None
    target: float | None


class PaperBook:
    def __init__(self, conn: sqlite3.Connection, account: str, costs: CostsConfig,
                 initial_cash: float):
        self.conn = conn
        self.account = account
        self.costs = costs
        row = conn.execute(
            "SELECT cash FROM paper_accounts WHERE account = ?", (account,)
        ).fetchone()
        if row is None:
            with conn:
                conn.execute(
                    "INSERT INTO paper_accounts (account, cash, created_at) VALUES (?, ?, ?)",
                    (account, initial_cash, datetime.now().isoformat()),
                )
            self.cash = initial_cash
        else:
            self.cash = float(row[0])

    # -- costs --------------------------------------------------------------
    def _fill_price(self, reference: float, side: str) -> float:
        bps = self.costs.total_bps_per_side / 1e4
        return reference * (1 + bps) if side == "buy" else reference * (1 - bps)

    # -- state --------------------------------------------------------------
    def open_positions(self) -> list[PaperPosition]:
        rows = self.conn.execute(
            "SELECT position_id, account, symbol, direction, qty, avg_cost, opened_at,"
            " signal_id, stop, target FROM paper_positions"
            " WHERE account = ? AND closed_at IS NULL",
            (self.account,),
        ).fetchall()
        return [
            PaperPosition(
                position_id=r[0], account=r[1], symbol=r[2], direction=r[3],
                qty=float(r[4]), avg_cost=float(r[5]),
                opened_at=datetime.fromisoformat(r[6]), signal_id=r[7],
                stop=r[8], target=r[9],
            )
            for r in rows
        ]

    def equity(self, prices: dict[str, float]) -> float:
        """Cash + open position value. Positions lacking a live price are
        valued at cost (and that's a data gap, not a profit)."""
        value = self.cash
        for pos in self.open_positions():
            price = prices.get(pos.symbol, pos.avg_cost)
            if pos.direction == "long":
                value += pos.qty * price
            else:
                # Collateral (qty x avg_cost) was reserved at open; the short
                # is worth collateral + accrued pnl.
                value += pos.qty * pos.avg_cost + pos.qty * (pos.avg_cost - price)
        return value

    def day_start_equity(self, session_date) -> float | None:
        """Last equity mark strictly before the session, or None if no marks."""
        row = self.conn.execute(
            "SELECT equity FROM equity_marks WHERE account = ? AND ts < ?"
            " ORDER BY ts DESC LIMIT 1",
            (self.account, session_date.isoformat()),
        ).fetchone()
        return float(row[0]) if row else None

    def day_pnl_pct(self, prices: dict[str, float], session_date,
                    initial_cash: float) -> float:
        base = self.day_start_equity(session_date)
        if base is None:
            base = initial_cash
        if base <= 0:
            return 0.0
        return (self.equity(prices) / base - 1) * 100

    def mark(self, ts: datetime, prices: dict[str, float]) -> float:
        equity = self.equity(prices)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO equity_marks (account, ts, equity, cash)"
                " VALUES (?, ?, ?, ?)",
                (self.account, ts.isoformat(), equity, self.cash),
            )
        return equity

    def equity_curve(self) -> list[tuple[datetime, float]]:
        rows = self.conn.execute(
            "SELECT ts, equity FROM equity_marks WHERE account = ? ORDER BY ts",
            (self.account,),
        ).fetchall()
        return [(datetime.fromisoformat(r[0]), float(r[1])) for r in rows]

    # -- trading ------------------------------------------------------------
    def open_position(
        self,
        ts: datetime,
        symbol: str,
        direction: str,
        qty: float,
        reference_price: float,
        signal_id: int | None = None,
        stop: float | None = None,
        target: float | None = None,
    ) -> PaperPosition | str:
        """Open with slippage; returns the position or a rejection reason."""
        side = "buy" if direction == "long" else "sell"
        price = self._fill_price(reference_price, side)
        # Longs: cash pays for the shares. Shorts: qty x fill price is held as
        # collateral (same figure close_position returns), so round-trip cash
        # always reconciles to start + realized pnl exactly.
        required = qty * price + self.costs.commission_per_trade
        if required > self.cash + 1e-9:
            return (f"insufficient paper cash: need ${required:,.2f}, "
                    f"have ${self.cash:,.2f}")
        with self.conn:
            self.cash -= required
            self.conn.execute(
                "UPDATE paper_accounts SET cash = ? WHERE account = ?",
                (self.cash, self.account),
            )
            cur = self.conn.execute(
                "INSERT INTO paper_positions (account, symbol, direction, qty, avg_cost,"
                " opened_at, signal_id, stop, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.account, symbol, direction, qty, price, ts.isoformat(),
                 signal_id, stop, target),
            )
            self._record_fill(ts, symbol, side, qty, price, reference_price,
                              signal_id, "open")
        return PaperPosition(
            position_id=int(cur.lastrowid), account=self.account, symbol=symbol,
            direction=direction, qty=qty, avg_cost=price, opened_at=ts,
            signal_id=signal_id, stop=stop, target=target,
        )

    def close_position(
        self, pos: PaperPosition, ts: datetime, reference_price: float, note: str
    ) -> float:
        """Close with slippage against the trader; returns realized PnL."""
        side = "sell" if pos.direction == "long" else "buy"
        price = self._fill_price(reference_price, side)
        if pos.direction == "long":
            gross = (price - pos.avg_cost) * pos.qty
            proceeds = pos.qty * price
        else:
            gross = (pos.avg_cost - price) * pos.qty
            proceeds = pos.qty * pos.avg_cost + gross  # collateral back + pnl
        # Realized PnL carries the whole round trip's commissions (one open
        # fill + one close fill), so cash always equals start + sum(pnl).
        pnl = gross - 2 * self.costs.commission_per_trade
        proceeds -= self.costs.commission_per_trade
        with self.conn:
            self.cash += proceeds
            self.conn.execute(
                "UPDATE paper_accounts SET cash = ? WHERE account = ?",
                (self.cash, self.account),
            )
            self.conn.execute(
                "UPDATE paper_positions SET closed_at = ?, exit_price = ?,"
                " realized_pnl = ? WHERE position_id = ?",
                (ts.isoformat(), price, pnl, pos.position_id),
            )
            self._record_fill(ts, pos.symbol, side, pos.qty, price,
                              reference_price, pos.signal_id, note)
        return pnl

    def _record_fill(self, ts, symbol, side, qty, price, reference, signal_id, note):
        self.conn.execute(
            "INSERT INTO paper_fills (account, ts, symbol, side, qty, price,"
            " reference, commission, signal_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.account, ts.isoformat(), symbol, side, qty, price, reference,
             self.costs.commission_per_trade, signal_id, note),
        )

    def realized_pnl_between(self, start: datetime, end: datetime) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM paper_positions"
            " WHERE account = ? AND closed_at >= ? AND closed_at <= ?",
            (self.account, start.isoformat(), end.isoformat()),
        ).fetchone()
        return float(row[0])
