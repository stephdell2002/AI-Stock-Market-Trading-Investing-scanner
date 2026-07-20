"""Watchman command-line interface.

Working today (Phase 1): universe, fetch.
Stubs that name their phase: screen, scan, signals, backtest, report.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from watchman import __version__
from watchman.config import WatchmanConfig, load_config
from watchman.data.cache import CachedBars
from watchman.data.universe import load_universe, refresh_snapshots, snapshot_meta
from watchman.data.yfinance_provider import YFinanceProvider
from watchman.db import connect

_PHASE_STUBS = {
    "backtest": ("Module C backtesting engine", 3),
    "scan": ("Module B pre-market scanner", 4),
    "signals": ("Module B day-trading signal engine", 4),
    "report": ("Module D daily report", 5),
}


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
    return 0


_PILLARS = ["quality", "growth", "valuation", "momentum"]


def _stub(command: str) -> int:
    what, phase = _PHASE_STUBS[command]
    print(f"`watchman {command}` ({what}) arrives in Phase {phase}. "
          f"Phase 1 is the skeleton: config, data layer, tests.")
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

    for name in _PHASE_STUBS:
        sub.add_parser(name, help=f"{_PHASE_STUBS[name][0]} (Phase {_PHASE_STUBS[name][1]})")

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
    return _stub(args.command)


if __name__ == "__main__":
    sys.exit(main())
