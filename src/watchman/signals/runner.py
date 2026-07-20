"""Module B orchestration: run_scan (pre-market focus list, persisted for the
day) and run_signals (evaluate the three setups on the focus list or explicit
symbols). All market data flows through an AsOfView pinned at 'now'.

Since Phase 5, run_signals is wired to the Module D paper book: open signals
are resolved first, the circuit breaker and max-position gates run against
LIVE paper P&L, confidence comes from the signal ledger, and every emitted
signal is auto-taken as a paper trade."""

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
    # Module D paper-book state (populated since Phase 5)
    resolution_notes: list[str] = field(default_factory=list)
    taken_notes: list[str] = field(default_factory=list)
    day_equity: float | None = None
    day_pnl_pct: float | None = None
    open_day_positions: int | None = None


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

    from watchman.paper import (
        DAY,
        PaperBook,
        SignalLedger,
        auto_take,
        last_prices,
        resolve_open_signals,
    )

    view = AsOfView(CachedProvider(provider, conn), now)
    freshness = provider.quote_freshness()
    interval = cfg.settings.signals.intraday_interval
    book = PaperBook(conn, DAY, cfg.settings.costs,
                     cfg.settings.accounts.day_trading_equity)
    ledger = SignalLedger(conn)

    # 1) Settle anything already open BEFORE the gates read the book.
    resolution_notes = resolve_open_signals(book, ledger, view, interval)

    # 2) Live gate state from the paper book (spec gates now enforced).
    open_positions = book.open_positions()
    price_symbols = sorted({p.symbol for p in open_positions} | set(symbols))
    prices = last_prices(view, price_symbols, interval)
    day_pnl = book.day_pnl_pct(prices, now.date(),
                               cfg.settings.accounts.day_trading_equity)
    engine = SignalEngine(
        risk=cfg.risk,
        day_equity=cfg.settings.accounts.day_trading_equity,
        freshness=freshness,
        confidence=ledger,
    )
    state = GateState(
        day_pnl_pct=day_pnl,
        open_day_positions=len(open_positions),
        already_emitted=ledger.already_emitted(now.date()),
        now=now,
    )

    result = SignalsResult(
        now=now,
        freshness_label=(
            "REALTIME" if freshness.value == "REALTIME"
            else f"{freshness.value} — NOT ACTIONABLE"
        ),
        actionable=freshness.value == "REALTIME",
        symbols_evaluated=symbols,
        resolution_notes=resolution_notes,
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

    # 3) Auto-take emitted signals as paper trades, then mark the book.
    result.taken_notes = auto_take(book, ledger, result.signals, now)
    prices = last_prices(view, sorted({p.symbol for p in book.open_positions()}
                                      | set(prices)), interval)
    result.day_equity = book.mark(now, prices)
    result.day_pnl_pct = book.day_pnl_pct(
        prices, now.date(), cfg.settings.accounts.day_trading_equity
    )
    result.open_day_positions = len(book.open_positions())
    return result
