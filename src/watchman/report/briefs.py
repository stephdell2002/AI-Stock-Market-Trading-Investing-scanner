"""Morning brief and evening recap: dated plain-text files.

Morning (pre-open): focus list, open positions, gate state, watchlist moves.
Evening (post-close): the day's signal outcomes, P&L, rolling setup stats.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from watchman.config import WatchmanConfig
from watchman.data.provider import AsOfView
from watchman.paper import DAY, PaperBook, SignalLedger, last_prices, watchlist_changes
from watchman.signals.runner import load_focus_list


def _book_lines(cfg, conn, view, now) -> list[str]:
    book = PaperBook(conn, DAY, cfg.settings.costs,
                     cfg.settings.accounts.day_trading_equity)
    positions = book.open_positions()
    prices = last_prices(view, sorted({p.symbol for p in positions}),
                         cfg.settings.signals.intraday_interval)
    equity = book.equity(prices)
    pnl = book.day_pnl_pct(prices, now.date(), cfg.settings.accounts.day_trading_equity)
    lines = [
        f"Day book: ${equity:,.2f} ({pnl:+.2f}% today), "
        f"{len(positions)}/{cfg.risk.max_concurrent_day_positions} positions, "
        f"breaker at {cfg.risk.daily_circuit_breaker_pct:+.1f}%",
    ]
    for p in positions:
        lines.append(f"  open: {p.symbol} {p.direction} x{p.qty:.0f} "
                     f"@ {p.avg_cost:.2f} stop {p.stop} target {p.target}")
    return lines


def morning_brief(cfg: WatchmanConfig, conn: sqlite3.Connection, view: AsOfView,
                  now: datetime) -> str:
    lines = [f"WATCHMAN MORNING BRIEF — {now:%A %Y-%m-%d %H:%M} ET",
             f"Data: {view.quote_freshness().value}"
             + ("" if view.quote_freshness().value == "REALTIME"
                else " — signals will be DELAYED, NOT ACTIONABLE"),
             ""]
    focus = load_focus_list(conn, now.date())
    if focus:
        lines.append(f"Focus list ({len(focus)}): {', '.join(focus)}")
    else:
        lines.append("Focus list: not scanned yet — run `watchman scan` 8:00-9:25 ET")
    lines.append("")
    lines.extend(_book_lines(cfg, conn, view, now))
    changes = watchlist_changes(conn, cfg.settings.screener.watchlist_size)
    lines.append("")
    lines.append(f"Watchlist: entered {', '.join(changes['entered']) or '—'}; "
                 f"exited {', '.join(changes['exited']) or '—'}")
    lines.append("")
    lines.append("Plan the trade before the open; the stop is decided now, not at -5%.")
    return "\n".join(lines) + "\n"


def evening_recap(cfg: WatchmanConfig, conn: sqlite3.Connection, view: AsOfView,
                  now: datetime) -> str:
    ledger = SignalLedger(conn)
    lines = [f"WATCHMAN EVENING RECAP — {now:%A %Y-%m-%d} ", ""]
    rows = ledger.session_signals(now.date())
    if rows:
        lines.append(f"Signals today ({len(rows)}):")
        for r in rows:
            outcome = r.status.upper()
            pnl = "" if r.pnl is None else f"  PnL ${r.pnl:+,.2f}"
            r_mult = "" if r.r_multiple is None else f" ({r.r_multiple:+.2f}R)"
            lines.append(f"  {r.symbol:<6} {r.setup:<22} {r.direction:<5} "
                         f"{outcome:<8}{pnl}{r_mult}")
    else:
        lines.append("No signals today.")
    lines.append("")
    lines.extend(_book_lines(cfg, conn, view, now))
    lines.append("")
    for setup in ledger.known_setups():
        s30 = ledger.setup_stats(setup, 30, now)
        s90 = ledger.setup_stats(setup, 90, now)
        w30 = "n/a" if s30.win_rate is None else f"{s30.win_rate:.0%}"
        w90 = "n/a" if s90.win_rate is None else f"{s90.win_rate:.0%}"
        lines.append(f"  {setup}: 30d {w30} ({s30.sample})  90d {w90} ({s90.sample})")
        warning = ledger.divergence_warning(setup)
        if warning:
            lines.append(f"  !! {warning}")
    lines.append("")
    lines.append("Paper results always read better than live money feels. Stay honest.")
    return "\n".join(lines) + "\n"


def write_brief(kind: str, cfg: WatchmanConfig, conn: sqlite3.Connection,
                view: AsOfView, now: datetime) -> Path:
    if kind == "morning":
        text = morning_brief(cfg, conn, view, now)
    elif kind == "evening":
        text = evening_recap(cfg, conn, view, now)
    else:
        raise ValueError(f"brief kind must be morning|evening, got {kind!r}")
    out_dir = Path(cfg.settings.report.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{kind}-{now:%Y-%m-%d}.txt"
    path.write_text(text, encoding="utf-8")
    return path
