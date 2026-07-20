"""Watchman command-line interface. All commands live:

universe/fetch (data), screen (Module A), backtest (Module C),
scan/signals (Module B, auto-taken as paper trades), report (Module D).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from watchman import __version__
from watchman.config import WatchmanConfig, load_config
from watchman.data.cache import CachedBars
from watchman.data.universe import load_universe, refresh_snapshots, snapshot_meta
from watchman.data.yfinance_provider import YFinanceProvider
from watchman.db import connect


def _make_provider(cfg: WatchmanConfig) -> YFinanceProvider:
    provider_name = cfg.settings.data.provider
    if provider_name != "yfinance":
        raise SystemExit(
            f"Provider {provider_name!r} is not implemented yet. "
            f"Available: yfinance. (Polygon/Finnhub/FMP slots arrive with Module B.)"
        )
    return YFinanceProvider()


def cmd_universe(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    if args.refresh:
        print("Refreshing index snapshots from Wikipedia constituent lists...")
        try:
            counts = refresh_snapshots()
        except Exception as exc:
            print(f"Refresh failed ({exc}). Existing snapshots left untouched.")
            return 1
        for index, n in counts.items():
            print(f"  {index}: {n} constituents")
    universe = load_universe(cfg.settings.universe)
    meta = snapshot_meta()
    print(f"Universe: {', '.join(cfg.settings.universe.indices)} "
          f"-> {len(universe)} unique symbols")
    if meta.get("as_of"):
        print(f"Snapshot as of: {meta['as_of']}")
    with_both = universe[universe["indices"].str.contains(r"\+", regex=True)]
    print(f"In more than one index: {len(with_both)}")
    print()
    print(universe.to_string(index=False, max_rows=len(universe) if args.all else 20))
    if not args.all and len(universe) > 20:
        print(f"... ({len(universe) - 20} more; use --all to list every symbol)")
    return 0


def cmd_fetch(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    provider = _make_provider(cfg)
    conn = connect(cfg.settings.data.db_path)
    cached = CachedBars(provider, conn)
    end = datetime.now()
    start = end - timedelta(days=args.days)
    symbol = args.symbol.strip().upper()
    print(f"Fetching {symbol} daily bars, last {args.days} days, "
          f"provider={provider.name} (freshness: {provider.quote_freshness().value})")
    try:
        bars = cached.daily_bars(symbol, start, end)
    except Exception as exc:
        print(f"Fetch failed: {exc}")
        print("If this is a network/proxy error, check internet access and try again.")
        return 1
    if bars.empty:
        print("No bars returned.")
        return 1
    print(f"{len(bars)} bars cached in {cfg.settings.data.db_path}")
    print(bars.tail(args.tail).to_string())
    return 0


def cmd_screen(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    from watchman.screener import run_screen

    provider = _make_provider(cfg)
    conn = connect(cfg.settings.data.db_path)
    print(
        f"Watchman screen | universe: {', '.join(cfg.settings.universe.indices)}"
        f"{f' (limit {args.limit})' if args.limit else ''} | provider: {provider.name} "
        f"(freshness: {provider.quote_freshness().value})"
    )
    print("First run fetches fundamentals for the whole universe and can take "
          "10-20 minutes on yfinance; later runs use the cache.\n")
    try:
        result = run_screen(
            cfg, provider, conn, top=args.top, limit=args.limit, progress=print,
        )
    except Exception as exc:  # CLI boundary: report, don't trace-dump
        print(f"Screen failed: {exc}")
        return 1

    scores = result.scored.scores
    ranked = scores[scores["rank"].notna()]
    top_n = ranked.head(args.top or cfg.settings.screener.watchlist_size)

    print(f"\n=== Watchlist (top {len(top_n)} of {len(ranked)} scored; "
          f"{result.universe_size} in universe) ===")
    display = top_n[["name", "sector", "composite", *_PILLARS, "coverage"]].copy()
    display["composite"] = display["composite"].round(1)
    for pillar in _PILLARS:
        display[pillar] = display[pillar].round(0)
    display["coverage"] = (display["coverage"] * 100).round(0).astype(int).astype(str) + "%"
    display.index.name = "symbol"
    print(display.to_string())

    print("\n=== Theses ===")
    for symbol, thesis in result.theses.items():
        print(f"\n{symbol}: {thesis}")

    print("\n=== Deteriorators ===")
    if result.previous_run_at is None:
        print("First stored run - nothing to compare against yet.")
    elif not result.deteriorators:
        print(f"None flagged vs previous run ({result.previous_run_at:%Y-%m-%d %H:%M}).")
    else:
        for d in result.deteriorators:
            curr_rank = d.curr_rank if d.curr_rank is not None else "unranked"
            print(f"  {d.symbol}: rank {d.prev_rank} -> {curr_rank}; {d.reason}")

    if result.failures:
        print(f"\nData failures ({len(result.failures)} symbols): "
              f"{', '.join(sorted(result.failures)[:15])}"
              f"{'...' if len(result.failures) > 15 else ''}")

    print(f"\nHonesty notes: prices are {result.freshness} (as of last close); "
          "fundamentals are the latest snapshot, NOT point-in-time; statement "
          "history is ~4 fiscal years, so CAGRs are ~3-year figures. "
          f"Run saved as #{result.run_id} for deteriorator tracking.")

    if args.export_tv:
        from watchman.data.universe import to_tradingview_symbol

        path = Path(args.export_tv)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(to_tradingview_symbol(s) for s in top_n.index) + "\n",
            encoding="utf-8",
        )
        print(f"TradingView watchlist written to {path} "
              "(import via TradingView watchlist menu).")
    return 0


_PILLARS = ["quality", "growth", "valuation", "momentum"]


def cmd_backtest(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    from watchman.data.cache import CachedProvider

    provider = CachedProvider(_make_provider(cfg), connect(cfg.settings.data.db_path))
    end = datetime.now()
    start = end - timedelta(days=int(args.years * 365.25))
    try:
        if args.mode == "ma-cross":
            return _backtest_ma_cross(args, cfg, provider, start, end)
        return _backtest_decile(args, cfg, provider, start, end)
    except Exception as exc:  # CLI boundary: report, don't trace-dump
        print(f"Backtest failed: {exc}")
        return 1


def _backtest_ma_cross(args, cfg, provider, start, end) -> int:
    from watchman.backtest import (
        BacktestEngine,
        MovingAverageCross,
        day_windows,
        format_metrics,
        walk_forward,
    )
    from watchman.data.provider import normalize_bars

    symbol = args.symbol.strip().upper()
    print(f"Walk-forward MA-cross backtest on {symbol}, {args.years}y, "
          f"costs {cfg.settings.costs.total_bps_per_side} bps/side "
          f"+ ${cfg.settings.costs.commission_per_trade}/trade\n")
    engine = BacktestEngine(
        provider, cfg.settings.costs, cfg.settings.accounts.day_trading_equity
    )
    calendar = normalize_bars(provider.daily_bars(symbol, start, end)).index
    windows = day_windows(calendar, opt_days=args.opt_days, test_days=args.test_days)
    if not windows:
        print(f"Not enough history: {len(calendar)} sessions < "
              f"opt {args.opt_days} + test {args.test_days}.")
        return 1
    grid = [
        {"fast": f, "slow": s}
        for f in (10, 20, 50)
        for s in (50, 100, 200)
        if f < s
    ]

    def run_fn(params, w_start, w_end):
        return engine.run(
            MovingAverageCross(symbol, **params), [symbol],
            datetime.combine(w_start.date(), datetime.min.time()),
            datetime.combine(w_end.date(), datetime.min.time()),
        )

    wf = walk_forward(run_fn, grid, windows, objective="sharpe")

    print(f"=== OUT-OF-SAMPLE (the number that matters) ===\n{format_metrics(wf.oos_metrics)}")
    print(f"\nIn-sample avg Sharpe {wf.in_sample_objective_avg:.2f} vs "
          f"out-of-sample {wf.oos_objective:.2f}")
    print(f"\n=== Per window ({len(wf.windows)}) ===")
    for wr in wf.windows:
        w = wr.window
        print(f"  opt {w.opt_start.date()}..{w.opt_end.date()} -> "
              f"params {wr.best_params} | test {w.test_start.date()}..{w.test_end.date()}"
              f" OOS sharpe {wr.out_of_sample_metrics.get('sharpe', float('nan')):.2f}")
    for warning in wf.warnings:
        print(f"\n!! {warning}")
    return 0


def _backtest_decile(args, cfg, provider, start, end) -> int:
    from watchman.backtest import momentum_decile_backtest
    from watchman.data.universe import load_universe

    universe = load_universe(cfg.settings.universe)
    if args.limit:
        universe = universe.head(args.limit)
    symbols = list(universe["symbol"])
    print(f"Momentum-decile backtest of the Module A score | {len(symbols)} symbols, "
          f"{args.years}y, {args.deciles} deciles, monthly rebalance\n")
    result = momentum_decile_backtest(
        provider, symbols, start, end, cfg.settings.costs,
        n_deciles=args.deciles, progress=print,
    )
    print(f"\n=== Decile results (1 = highest momentum; {result.avg_names_per_decile:.0f} "
          f"names/decile avg; costs {result.costs_bps_per_side} bps/side on turnover) ===")
    for d in sorted(result.metrics_by_decile):
        m = result.metrics_by_decile[d]
        print(f"  D{d:<2} CAGR {m['cagr_pct']:>7.1f}%  sharpe {m['sharpe']:>5.2f}  "
              f"maxDD {m['max_drawdown_pct']:>6.1f}%")
    print(f"\nTop-minus-bottom CAGR spread: {result.top_bottom_spread_pct:+.1f}pp")
    print(f"Adjacent-decile ordering held {result.monotonic_fraction:.0%} of the time.")
    print("\n=== Read this before believing any of it ===")
    for warning in result.warnings:
        print(f"!! {warning}")
    return 0


def _freshness_banner(provider) -> str:
    freshness = provider.quote_freshness().value
    if freshness == "REALTIME":
        return "data: REALTIME"
    return (f"data: {freshness} — NOT ACTIONABLE (informational only; add a "
            "real-time provider key to change this)")


def cmd_scan(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    from watchman.signals import run_scan

    provider = _make_provider(cfg)
    conn = connect(cfg.settings.data.db_path)
    print(f"Watchman pre-market scan | {_freshness_banner(provider)}")
    print("Best run 8:00-9:25 ET; outside that window pre-market volume "
          "reads low or empty.\n")
    try:
        outcome = run_scan(cfg, provider, conn, limit=args.limit, progress=print)
    except Exception as exc:
        print(f"Scan failed: {exc}")
        return 1
    print(f"\n=== Focus list ({len(outcome.candidates)} of {outcome.scanned} scanned; "
          f"max {cfg.settings.signals.focus_size}) ===")
    if not outcome.candidates:
        print("No candidates passed the gates.")
    for c in outcome.candidates:
        catalyst = {True: "news", False: "-", None: "?"}[c.catalyst]
        float_txt = f"{c.float_shares / 1e6:,.0f}M" if c.float_shares else "?"
        atr_txt = f"{c.atr_pct:.1f}%" if c.atr_pct is not None else "?"
        print(f"  {c.symbol:<6} gap {c.gap_pct:+6.1f}%  relvol {c.rel_vol:4.1f}x  "
              f"px {c.last_price:8.2f}  ATR {atr_txt:>5}  float {float_txt:>7}  "
              f"catalyst {catalyst}")
    if outcome.rejected:
        top = sorted(outcome.rejected.items(), key=lambda kv: -kv[1])[:5]
        print("\nRejected: " + ", ".join(f"{n} x {r}" for r, n in top))
    if outcome.failures:
        print(f"Data failures: {len(outcome.failures)} symbols "
              f"(e.g. {', '.join(list(outcome.failures)[:5])})")
    print("\nFocus list saved for today — `watchman signals` will use it.")
    return 0


def cmd_signals(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    from watchman.signals import run_signals

    provider = _make_provider(cfg)
    conn = connect(cfg.settings.data.db_path)
    symbols = args.symbols.split(",") if args.symbols else None
    print(f"Watchman signals | {_freshness_banner(provider)}\n")
    try:
        result = run_signals(cfg, provider, conn, symbols=symbols, progress=lambda _m: None)
    except Exception as exc:
        print(f"Signals failed: {exc}")
        return 1

    print(f"=== Signals at {result.now:%Y-%m-%d %H:%M ET} "
          f"({len(result.symbols_evaluated)} symbols evaluated) ===")
    if not result.signals:
        print("No setups triggered on the latest completed bar.")
    for s in result.signals:
        targets = " / ".join(f"{t:.2f}" for t in s.targets)
        print(f"\n{s.symbol}  {s.setup}  {s.direction.upper()}  [{s.freshness_label}]")
        print(f"  entry {s.entry:.2f}  stop {s.stop:.2f}  targets {targets}  "
              f"R:R {s.risk_reward:.1f}")
        equity = cfg.settings.accounts.day_trading_equity
        print(f"  size {s.shares} sh (risk ${s.risk_dollars:,.2f} = "
              f"{cfg.risk.max_risk_per_trade_pct}% of ${equity:,.0f})")
        print(f"  confidence: {s.confidence_label}")
        print(f"  why: {s.rationale}")
    if result.rejected:
        print(f"\nRejected ({len(result.rejected)}):")
        for r in result.rejected:
            print(f"  {r.symbol} {r.setup} {r.direction}: {r.reason}")
    if result.failures:
        print(f"\nNo data: {', '.join(f'{s} ({e})' for s, e in result.failures.items())}")
    if result.resolution_notes:
        print("\nResolved since last run:")
        for note in result.resolution_notes:
            print(f"  {note}")
    if result.taken_notes:
        print("\nPaper trades:")
        for note in result.taken_notes:
            print(f"  {note}")
    if result.day_equity is not None:
        print(f"\nPaper day book: ${result.day_equity:,.2f} "
              f"({result.day_pnl_pct:+.2f}% today), "
              f"{result.open_day_positions}/{cfg.risk.max_concurrent_day_positions} "
              f"positions, breaker at {cfg.risk.daily_circuit_breaker_pct:+.1f}%")
    if result.unenforced_gates:
        print("\nGates not enforceable this run:")
        for gate in sorted(result.unenforced_gates):
            print(f"  - {gate}")
    if not result.actionable:
        print("\nREMINDER: these signals are built on delayed data — study them, "
              "do not chase them.")
    return 0


def cmd_report(args: argparse.Namespace, cfg: WatchmanConfig) -> int:
    from watchman.data.cache import CachedProvider
    from watchman.data.provider import ET, AsOfView

    provider = _make_provider(cfg)
    conn = connect(cfg.settings.data.db_path)
    now = datetime.now(tz=ET)
    view = AsOfView(CachedProvider(provider, conn), now)
    try:
        if args.rebalance_longterm:
            from watchman.paper import LONGTERM, PaperBook, rebalance_longterm

            book = PaperBook(conn, LONGTERM, cfg.settings.costs,
                             cfg.settings.accounts.long_term_equity)
            notes = rebalance_longterm(
                book, conn, view, cfg.settings.screener.watchlist_size, now,
                force=args.force,
            )
            print("Long-term rebalance:" if notes else "Long-term rebalance: not due.")
            for note in notes:
                print(f"  {note}")

        if args.brief:
            from watchman.report import write_brief

            path = write_brief(args.brief, cfg, conn, view, now)
            print(f"{args.brief.capitalize()} brief written to {path}")
            print()
            print(path.read_text(encoding="utf-8"))
        else:
            from watchman.report import write_report

            path = write_report(cfg, conn, view, now)
            print(f"Daily HTML report written to {path}")
            print("Open it in a browser — it is fully self-contained.")
    except Exception as exc:  # CLI boundary: report, don't trace-dump
        print(f"Report failed: {exc}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchman",
        description="Personal AI stock analysis and signal system. "
        "Paper trading only; honesty over optimism.",
    )
    parser.add_argument("--version", action="version", version=f"watchman {__version__}")
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Directory holding settings.yaml and risk.yaml (default: ./config "
        "or $WATCHMAN_CONFIG_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_universe = sub.add_parser("universe", help="Show the resolved screening universe")
    p_universe.add_argument("--refresh", action="store_true",
                            help="Rebuild index snapshots from Wikipedia (needs internet)")
    p_universe.add_argument("--all", action="store_true", help="List every symbol")

    p_fetch = sub.add_parser("fetch", help="Fetch and cache daily bars for one symbol")
    p_fetch.add_argument("symbol")
    p_fetch.add_argument("--days", type=int, default=365)
    p_fetch.add_argument("--tail", type=int, default=10, help="Rows to display")

    p_screen = sub.add_parser(
        "screen", help="Module A: rank the universe by four-pillar composite score"
    )
    p_screen.add_argument("--top", type=int, default=None,
                          help="Watchlist size (default: settings.yaml watchlist_size)")
    p_screen.add_argument("--limit", type=int, default=None,
                          help="Screen only the first N universe symbols (quick trial run)")
    p_screen.add_argument("--export-tv", metavar="PATH", default=None,
                          help="Also write the watchlist as a TradingView-importable file")

    p_backtest = sub.add_parser(
        "backtest", help="Module C: event-driven backtests with walk-forward validation"
    )
    p_backtest.add_argument("mode", choices=["ma-cross", "momentum-decile"],
                            help="ma-cross: walk-forward demo strategy; "
                            "momentum-decile: honest Module A score backtest")
    p_backtest.add_argument("--symbol", default="SPY", help="ma-cross symbol (default SPY)")
    p_backtest.add_argument("--years", type=float, default=6.0,
                            help="History length in years (default 6)")
    p_backtest.add_argument("--opt-days", type=int, default=504,
                            help="ma-cross: in-sample window in sessions (default 504 ~ 2y)")
    p_backtest.add_argument("--test-days", type=int, default=126,
                            help="ma-cross: out-of-sample window in sessions (default 126 ~ 6m)")
    p_backtest.add_argument("--deciles", type=int, default=10,
                            help="momentum-decile: number of buckets (default 10)")
    p_backtest.add_argument("--limit", type=int, default=None,
                            help="momentum-decile: only the first N universe symbols")

    p_scan = sub.add_parser(
        "scan", help="Module B: pre-market scanner -> today's focus list (max 10)"
    )
    p_scan.add_argument("--limit", type=int, default=None,
                        help="Scan only the first N universe symbols (trial run)")

    p_signals = sub.add_parser(
        "signals", help="Module B: evaluate ORB / VWAP / rel-vol setups now"
    )
    p_signals.add_argument("--symbols", default=None,
                           help="Comma-separated symbols (default: today's focus list)")

    p_report = sub.add_parser(
        "report", help="Module D: daily HTML dashboard / morning + evening briefs"
    )
    p_report.add_argument("--brief", choices=["morning", "evening"], default=None,
                          help="Write a dated text brief instead of the HTML dashboard")
    p_report.add_argument("--rebalance-longterm", action="store_true",
                          help="Run the monthly Module A paper rebalance first (if due)")
    p_report.add_argument("--force", action="store_true",
                          help="With --rebalance-longterm: rebalance even if not due")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config_dir)
    if args.command == "universe":
        return cmd_universe(args, cfg)
    if args.command == "fetch":
        return cmd_fetch(args, cfg)
    if args.command == "screen":
        return cmd_screen(args, cfg)
    if args.command == "backtest":
        return cmd_backtest(args, cfg)
    if args.command == "scan":
        return cmd_scan(args, cfg)
    if args.command == "signals":
        return cmd_signals(args, cfg)
    if args.command == "report":
        return cmd_report(args, cfg)
    raise SystemExit(f"unhandled command {args.command!r}")  # argparse prevents this


if __name__ == "__main__":
    sys.exit(main())
