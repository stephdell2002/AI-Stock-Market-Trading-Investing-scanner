"""Module B orchestration: run_scan (pre-market focus list, persisted for the
day) and run_signals (evaluate the three setups on the focus list or explicit
symbols). All market data flows through an AsOfView pinned at 'now'."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from watchman.config import WatchmanConfig
from watchman.data.cache import CachedProvider
from watchman.data.provider import ET, AsOfView, DataProvider
from watchman.data.universe import load_universe
from watchman.signals.engine import SignalEngine
from watchman.signals.model import GateState, RejectedSignal, Signal
from watchman.signals.scanner import ScanOutcome, build_scanner_input, scan_premarket
from watchman.signals.setups import ALL_SETUPS, build_context


@dataclass
class SignalsResult:
    now: datetime
    freshness_label: str
    actionable: bool
    signals: list[Signal] = field(default_factory=list)
    rejected: list[RejectedSignal] = field(default_factory=list)
    unenforced_gates: set[str] = field(default_factory=set)
    failures: dict[str, str] = field(default_factory=dict)
    symbols_evaluated: list[str] = field(default_factory=list)


def _now_et(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=ET)
    return now if now.tzinfo else now.replace(tzinfo=ET)


def run_scan(
    cfg: WatchmanConfig,
    provider: DataProvider,
    conn: sqlite3.Connection,
    now: datetime | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ScanOutcome:
    now = _now_et(now)
    universe = load_universe(cfg.settings.universe)
    if limit:
        universe = universe.head(limit)
    symbols = list(universe["symbol"])
    view = AsOfView(CachedProvider(provider, conn), now)

    inputs = []
    failures: dict[str, str] = {}
    for i, symbol in enumerate(symbols, 1):
        if i == 1 or i % 50 == 0 or i == len(symbols):
            progress(f"[{i}/{len(symbols)}] scanning {symbol}...")
        try:
            inputs.append(build_scanner_input(view, symbol))
        except Exception as exc:
            failures[symbol] = str(exc)
    outcome = scan_premarket(inputs, cfg.settings.signals, failures)

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO focus_lists (scan_date, created_at, symbols, details) "
            "VALUES (?, ?, ?, ?)",
            (
                now.date().isoformat(),
                now.isoformat(),
                json.dumps([c.symbol for c in outcome.candidates]),
                json.dumps(
                    [
                        {
                            "symbol": c.symbol,
                            "gap_pct": c.gap_pct,
                            "rel_vol": c.rel_vol,
                            "score": c.score,
                        }
                        for c in outcome.candidates
                    ]
                ),
            ),
        )
    return outcome


def load_focus_list(conn: sqlite3.Connection, day) -> list[str] | None:
    row = conn.execute(
        "SELECT symbols FROM focus_lists WHERE scan_date = ?", (day.isoformat(),)
    ).fetchone()
    return json.loads(row[0]) if row else None


def run_signals(
    cfg: WatchmanConfig,
    provider: DataProvider,
    conn: sqlite3.Connection,
    symbols: list[str] | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> SignalsResult:
    now = _now_et(now)
    if symbols is None:
        symbols = load_focus_list(conn, now.date())
        if symbols is None:
            raise RuntimeError(
                "no focus list for today — run `watchman scan` first, or pass "
                "--symbols A,B,C"
            )
    symbols = [s.strip().upper() for s in symbols if s.strip()]

    view = AsOfView(CachedProvider(provider, conn), now)
    freshness = provider.quote_freshness()
    engine = SignalEngine(
        risk=cfg.risk,
        day_equity=cfg.settings.accounts.day_trading_equity,
        freshness=freshness,
    )
    # Module D's paper book will supply live day P&L / open positions; until
    # then these gates are explicitly reported as unenforced.
    state = GateState(day_pnl_pct=None, open_day_positions=None, now=now)

    result = SignalsResult(
        now=now,
        freshness_label=(
            "REALTIME" if freshness.value == "REALTIME"
            else f"{freshness.value} — NOT ACTIONABLE"
        ),
        actionable=freshness.value == "REALTIME",
        symbols_evaluated=symbols,
    )
    for symbol in symbols:
        progress(f"evaluating {symbol}...")
        try:
            ctx = build_context(view, symbol, cfg.settings.signals)
        except Exception as exc:
            result.failures[symbol] = str(exc)
            continue
        if ctx is None:
            result.failures[symbol] = "no completed session bars yet"
            continue
        for setup_cls in ALL_SETUPS:
            draft = setup_cls().evaluate(ctx)
            if draft is None:
                continue
            outcome = engine.finalize(draft, state)
            if isinstance(outcome, Signal):
                result.signals.append(outcome)
            else:
                result.rejected.append(outcome)
    result.unenforced_gates = engine.unenforced_gates
    return result
