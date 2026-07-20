"""SignalLedger — the honesty mechanism.

Every signal ever emitted is logged with its full terms; every logged signal
eventually gets an outcome: target / stopped / expired. Rolling 30- and
90-day live win rates per setup are computed from THIS record and nothing
else, and they feed straight back into new signals' confidence via the
ConfidenceSource protocol. If a backtest baseline is recorded for a setup and
live performance diverges badly, the report says so loudly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from watchman.signals.model import Signal

#: live-vs-backtest win-rate gap (percentage points) that triggers the loud
#: divergence warning; only checked once the live sample is meaningful
DIVERGENCE_PP = 20.0
DIVERGENCE_MIN_SAMPLE = 20

OUTCOMES = ("target", "stopped", "expired")


@dataclass(frozen=True)
class SetupStats:
    setup: str
    window_days: int
    sample: int
    wins: int
    win_rate: float | None
    avg_r: float | None          # mean realized R multiple
    total_pnl: float


@dataclass(frozen=True)
class LedgerRow:
    signal_id: int
    session: str
    symbol: str
    setup: str
    direction: str
    entry: float
    stop: float
    target1: float
    shares: int
    status: str
    exit_price: float | None
    pnl: float | None
    r_multiple: float | None
    rationale: str


class SignalLedger:
    """Implements ConfidenceSource: rolling_win_rate(setup, days)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(self, signal: Signal, session_date) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO signal_ledger (created_at, session, symbol, setup,"
                " direction, entry, stop, target1, target2, risk_reward, shares,"
                " freshness, rationale) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal.created_at.isoformat(),
                    session_date.isoformat(),
                    signal.symbol,
                    signal.setup,
                    signal.direction,
                    signal.entry,
                    signal.stop,
                    signal.targets[0],
                    signal.targets[1] if len(signal.targets) > 1 else None,
                    signal.risk_reward,
                    signal.shares,
                    signal.freshness.value,
                    signal.rationale,
                ),
            )
        return int(cur.lastrowid)

    def resolve(
        self,
        signal_id: int,
        status: str,
        resolved_at: datetime,
        exit_price: float,
        pnl: float,
    ) -> None:
        if status not in OUTCOMES:
            raise ValueError(f"status must be one of {OUTCOMES}, got {status!r}")
        row = self.conn.execute(
            "SELECT entry, stop, shares FROM signal_ledger WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no ledger row {signal_id}")
        entry, stop, shares = float(row[0]), float(row[1]), int(row[2])
        initial_risk = abs(entry - stop) * shares
        r_multiple = pnl / initial_risk if initial_risk > 0 else None
        with self.conn:
            self.conn.execute(
                "UPDATE signal_ledger SET status = ?, resolved_at = ?, exit_price = ?,"
                " pnl = ?, r_multiple = ? WHERE signal_id = ?",
                (status, resolved_at.isoformat(), exit_price, pnl, r_multiple, signal_id),
            )

    def open_signals(self) -> list[LedgerRow]:
        return self._rows("status = 'open'")

    def session_signals(self, session_date) -> list[LedgerRow]:
        return self._rows("session = ?", (session_date.isoformat(),))

    def already_emitted(self, session_date) -> set[tuple[str, str, str]]:
        """Keys already in the ledger for this session — cross-run dedup."""
        rows = self.conn.execute(
            "SELECT symbol, setup, direction FROM signal_ledger WHERE session = ?",
            (session_date.isoformat(),),
        ).fetchall()
        return {(r[0], r[1], r[2]) for r in rows}

    def _rows(self, where: str, params: tuple = ()) -> list[LedgerRow]:
        rows = self.conn.execute(
            "SELECT signal_id, session, symbol, setup, direction, entry, stop,"
            f" target1, shares, status, exit_price, pnl, r_multiple, rationale"
            f" FROM signal_ledger WHERE {where} ORDER BY signal_id",
            params,
        ).fetchall()
        return [
            LedgerRow(
                signal_id=r[0], session=r[1], symbol=r[2], setup=r[3], direction=r[4],
                entry=float(r[5]), stop=float(r[6]), target1=float(r[7]),
                shares=int(r[8]), status=r[9],
                exit_price=None if r[10] is None else float(r[10]),
                pnl=None if r[11] is None else float(r[11]),
                r_multiple=None if r[12] is None else float(r[12]),
                rationale=r[13],
            )
            for r in rows
        ]

    # -- statistics ---------------------------------------------------------
    def setup_stats(self, setup: str, days: int, now: datetime | None = None) -> SetupStats:
        now = now or datetime.now()
        cutoff = (now - timedelta(days=days)).date().isoformat()
        rows = self.conn.execute(
            "SELECT status, pnl, r_multiple FROM signal_ledger"
            " WHERE setup = ? AND session >= ? AND status != 'open'",
            (setup, cutoff),
        ).fetchall()
        sample = len(rows)
        wins = sum(1 for r in rows if r[0] == "target" or (r[1] or 0) > 0)
        r_values = [float(r[2]) for r in rows if r[2] is not None]
        return SetupStats(
            setup=setup,
            window_days=days,
            sample=sample,
            wins=wins,
            win_rate=wins / sample if sample else None,
            avg_r=sum(r_values) / len(r_values) if r_values else None,
            total_pnl=sum(float(r[1] or 0) for r in rows),
        )

    def known_setups(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT setup FROM signal_ledger").fetchall()
        return sorted(r[0] for r in rows)

    def rolling_win_rate(self, setup: str, days: int = 30) -> tuple[float | None, int]:
        """ConfidenceSource protocol: (win_rate or None, sample size)."""
        stats = self.setup_stats(setup, days)
        return stats.win_rate, stats.sample

    # -- backtest baselines + divergence ------------------------------------
    def set_baseline(self, setup: str, win_rate: float, sample: int, source: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO setup_baselines (setup, win_rate, sample,"
                " source, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (setup, win_rate, sample, source, datetime.now().isoformat()),
            )

    def baseline(self, setup: str) -> tuple[float, int, str] | None:
        row = self.conn.execute(
            "SELECT win_rate, sample, source FROM setup_baselines WHERE setup = ?",
            (setup,),
        ).fetchone()
        return (float(row[0]), int(row[1]), row[2]) if row else None

    def divergence_warning(self, setup: str, days: int = 90) -> str | None:
        """Loud warning when live results diverge badly from a recorded
        backtest baseline. None when no baseline, thin sample, or no gap."""
        base = self.baseline(setup)
        if base is None:
            return None
        stats = self.setup_stats(setup, days)
        if stats.win_rate is None or stats.sample < DIVERGENCE_MIN_SAMPLE:
            return None
        gap_pp = (stats.win_rate - base[0]) * 100
        if abs(gap_pp) < DIVERGENCE_PP:
            return None
        direction = "BELOW" if gap_pp < 0 else "above"
        return (
            f"DIVERGENCE: {setup} live win rate {stats.win_rate:.0%} "
            f"({stats.sample} signals/{days}d) is {abs(gap_pp):.0f}pp {direction} its "
            f"backtest baseline {base[0]:.0%} ({base[2]}). If live is below backtest, "
            "assume the backtest was too optimistic — not that live is unlucky."
        )
