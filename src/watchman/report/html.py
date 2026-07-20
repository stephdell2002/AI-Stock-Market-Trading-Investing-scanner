"""Self-contained HTML daily report. No external scripts, fonts, or images —
equity curves are inline SVG. Every number's caveat is printed next to it.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime
from pathlib import Path

from watchman.config import WatchmanConfig
from watchman.data.provider import AsOfView
from watchman.paper import (
    DAY,
    LONGTERM,
    PaperBook,
    SignalLedger,
    last_prices,
    watchlist_changes,
)

WINDOWS = (30, 90)


def svg_equity_curve(points: list[tuple[datetime, float]], width=640, height=160) -> str:
    if len(points) < 2:
        return "<p class='muted'>Not enough equity marks yet for a curve.</p>"
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 8
    xs = [pad + i * (width - 2 * pad) / (len(values) - 1) for i in range(len(values))]
    ys = [height - pad - (v - lo) * (height - 2 * pad) / span for v in values]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
    color = "#1a7f37" if values[-1] >= values[0] else "#b42318"
    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' "
        f"aria-label='equity curve'>"
        f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{pts}'/>"
        f"<text x='{pad}' y='14' class='axis'>${hi:,.0f}</text>"
        f"<text x='{pad}' y='{height - 2}' class='axis'>${lo:,.0f}</text>"
        "</svg>"
    )


def _fmt_pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def _fmt_r(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.2f}R"


def build_report(
    cfg: WatchmanConfig,
    conn: sqlite3.Connection,
    view: AsOfView,
    now: datetime,
) -> str:
    esc = html.escape
    interval = cfg.settings.signals.intraday_interval
    day_book = PaperBook(conn, DAY, cfg.settings.costs,
                         cfg.settings.accounts.day_trading_equity)
    lt_book = PaperBook(conn, LONGTERM, cfg.settings.costs,
                        cfg.settings.accounts.long_term_equity)
    ledger = SignalLedger(conn)

    day_symbols = sorted({p.symbol for p in day_book.open_positions()}
                         | {p.symbol for p in lt_book.open_positions()})
    prices = last_prices(view, day_symbols, interval)
    day_equity = day_book.equity(prices)
    day_pnl = day_book.day_pnl_pct(prices, now.date(),
                                   cfg.settings.accounts.day_trading_equity)
    lt_equity = lt_book.equity(prices)

    sections: list[str] = []

    # -- paper P&L ----------------------------------------------------------
    day_curve = svg_equity_curve(day_book.equity_curve())
    lt_curve = svg_equity_curve(lt_book.equity_curve())
    sections.append(f"""
<section><h2>Paper P&amp;L</h2>
<div class='cards'>
 <div class='card'><h3>Day-trading book</h3>
  <p class='big'>${day_equity:,.2f}</p>
  <p>day P&amp;L {day_pnl:+.2f}% · {len(day_book.open_positions())} open ·
     breaker at {cfg.risk.daily_circuit_breaker_pct:+.1f}%</p>{day_curve}</div>
 <div class='card'><h3>Long-term book</h3>
  <p class='big'>${lt_equity:,.2f}</p>
  <p>{len(lt_book.open_positions())} holdings · monthly rebalance ·
     dividends not credited</p>{lt_curve}</div>
</div></section>""")

    # -- today's signals ----------------------------------------------------
    rows = ledger.session_signals(now.date())
    if rows:
        body = "".join(
            f"<tr><td>{esc(r.symbol)}</td><td>{esc(r.setup)}</td>"
            f"<td>{esc(r.direction)}</td><td>{r.entry:.2f}</td><td>{r.stop:.2f}</td>"
            f"<td>{r.target1:.2f}</td><td>{r.shares}</td>"
            f"<td class='{esc(r.status)}'>{esc(r.status)}</td>"
            f"<td>{'' if r.pnl is None else f'{r.pnl:+,.2f}'}</td>"
            f"<td>{'' if r.r_multiple is None else f'{r.r_multiple:+.2f}R'}</td></tr>"
            for r in rows
        )
        table = ("<table><tr><th>sym</th><th>setup</th><th>dir</th><th>entry</th>"
                 "<th>stop</th><th>target</th><th>shares</th><th>status</th>"
                 "<th>P&amp;L $</th><th>R</th></tr>" + body + "</table>")
    else:
        table = "<p class='muted'>No signals logged today.</p>"
    sections.append(f"<section><h2>Today's signals &amp; outcomes</h2>{table}</section>")

    # -- setup scoreboard: live vs backtest ---------------------------------
    setups = ledger.known_setups()
    if setups:
        rows_html = []
        warnings: list[str] = []
        for setup in setups:
            cells = [f"<td>{esc(setup)}</td>"]
            for days in WINDOWS:
                stats = ledger.setup_stats(setup, days, now)
                cells.append(
                    f"<td>{_fmt_pct(stats.win_rate)} ({stats.sample}) · "
                    f"{_fmt_r(stats.avg_r)}</td>"
                )
            base = ledger.baseline(setup)
            cells.append(
                f"<td>{base[0]:.0%} ({base[1]}) — {esc(base[2])}</td>" if base
                else "<td class='muted'>none — no honest intraday backtest on "
                     "free data; the live record IS the evidence</td>"
            )
            rows_html.append("<tr>" + "".join(cells) + "</tr>")
            warning = ledger.divergence_warning(setup)
            if warning:
                warnings.append(f"<p class='alert'>{esc(warning)}</p>")
        sections.append(
            "<section><h2>Setups: live vs backtest</h2><table>"
            "<tr><th>setup</th><th>live 30d (n) · avg R</th>"
            "<th>live 90d (n) · avg R</th><th>backtest baseline</th></tr>"
            + "".join(rows_html) + "</table>" + "".join(warnings)
            + "<p class='muted'>Win rates come from the signal ledger — every "
            "signal ever emitted is in it, including expired ones.</p></section>"
        )
    else:
        sections.append("<section><h2>Setups: live vs backtest</h2>"
                        "<p class='muted'>No signals in the ledger yet.</p></section>")

    # -- watchlist changes --------------------------------------------------
    changes = watchlist_changes(conn, cfg.settings.screener.watchlist_size)
    entered = ", ".join(map(esc, changes["entered"])) or "—"
    exited = ", ".join(map(esc, changes["exited"])) or "—"
    sections.append(f"""
<section><h2>Watchlist changes (Module A top {cfg.settings.screener.watchlist_size})</h2>
<p>Entered: {entered}<br>Exited: {exited}</p></section>""")

    # -- honesty footer -----------------------------------------------------
    freshness = view.quote_freshness().value
    sections.append(f"""
<section class='footer'><h2>Read this before believing anything above</h2><ul>
<li>Data freshness: <b>{esc(freshness)}</b>{'' if freshness == 'REALTIME'
    else ' — signals are NOT ACTIONABLE for live entries'}.</li>
<li>Every paper fill pays {cfg.settings.costs.total_bps_per_side:.0f} bps
    slippage+spread per side plus
    ${cfg.settings.costs.commission_per_trade:.2f} commission.</li>
<li>Same-bar stop+target ambiguity resolves as STOPPED (pessimistic).</li>
<li>Paper trading has no PDT rule; real US day trading needs $25k+ margin
    equity. Paper results always look better than live money feels.</li>
</ul></section>""")

    style = """
body{font-family:system-ui,-apple-system,sans-serif;max-width:960px;margin:2rem auto;
padding:0 1rem;color:#1f2328;background:#fff}
h1{border-bottom:2px solid #1f2328;padding-bottom:.3rem}
h2{margin-top:1.5rem}table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border:1px solid #d0d7de;padding:.35rem .5rem;text-align:left}
th{background:#f6f8fa}.muted{color:#656d76}.big{font-size:1.6rem;font-weight:700;
margin:.2rem 0}.cards{display:flex;gap:1rem;flex-wrap:wrap}
.card{flex:1;min-width:280px;border:1px solid #d0d7de;border-radius:8px;padding:1rem}
.alert{background:#fff1f0;border:1px solid #b42318;color:#b42318;padding:.6rem;
border-radius:6px;font-weight:600}
.target{color:#1a7f37;font-weight:600}.stopped{color:#b42318;font-weight:600}
.expired{color:#656d76}.axis{font-size:10px;fill:#656d76}
.footer{background:#f6f8fa;border-radius:8px;padding:.5rem 1rem}
svg{width:100%;height:auto}"""

    return f"""<!DOCTYPE html>
<html lang='en'><head><meta charset='utf-8'>
<title>Watchman daily report — {now:%Y-%m-%d}</title>
<style>{style}</style></head><body>
<h1>Watchman — {now:%A %Y-%m-%d %H:%M} ET</h1>
<p class='muted'>Paper trading only. Credibility through statistical honesty.</p>
{''.join(sections)}
</body></html>"""


def write_report(cfg: WatchmanConfig, conn: sqlite3.Connection, view: AsOfView,
                 now: datetime) -> Path:
    out_dir = Path(cfg.settings.report.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report-{now:%Y-%m-%d}.html"
    path.write_text(build_report(cfg, conn, view, now), encoding="utf-8")
    return path
