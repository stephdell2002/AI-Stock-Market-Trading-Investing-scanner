"""Module A orchestration: fetch (cached) -> metrics -> scores -> theses ->
deteriorators -> persist run.

All market data flows through AsOfView pinned at run time, so the lookahead
discipline holds even here. Symbols whose data cannot be fetched are recorded
as failures and shown — never silently dropped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from watchman.config import WatchmanConfig
from watchman.data.cache import CachedProvider
from watchman.data.provider import AsOfView, DataProvider
from watchman.data.universe import load_universe
from watchman.screener import metrics as metrics_mod
from watchman.screener.scoring import ScoreOutput, score_universe
from watchman.screener.store import Deterioration, find_deteriorators, latest_run, save_run
from watchman.screener.thesis import build_thesis

BENCHMARK = "SPY"
PRICE_LOOKBACK_DAYS = 420  # ~252 trading days + slack for the 12m window


@dataclass
class ScreenResult:
    run_at: datetime
    run_id: int
    universe_size: int
    scored: ScoreOutput
    theses: dict[str, str]  # top-N symbol -> thesis
    deteriorators: list[Deterioration]
    failures: dict[str, str] = field(default_factory=dict)
    freshness: str = "EOD"
    previous_run_at: datetime | None = None

    @property
    def watchlist(self) -> pd.DataFrame:
        return self.scored.scores[self.scored.scores["rank"].notna()].head(len(self.theses))


def run_screen(
    cfg: WatchmanConfig,
    provider: DataProvider,
    conn: sqlite3.Connection,
    universe: pd.DataFrame | None = None,
    top: int | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ScreenResult:
    run_at = datetime.now()
    top = top or cfg.settings.screener.watchlist_size

    if universe is None:
        universe = load_universe(cfg.settings.universe)
    if limit:
        universe = universe.head(limit)
    universe = universe.set_index("symbol") if "symbol" in universe.columns else universe

    cached = CachedProvider(
        provider,
        conn,
        fundamentals_max_age=timedelta(days=cfg.settings.screener.data_max_age_days),
    )
    view = AsOfView(cached, run_at)
    price_start = run_at - timedelta(days=PRICE_LOOKBACK_DAYS)

    failures: dict[str, str] = {}
    progress(f"Fetching benchmark {BENCHMARK} bars...")
    try:
        spy_bars = view.daily_bars(BENCHMARK, start=price_start)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot screen without benchmark bars ({BENCHMARK}): {exc}"
        ) from exc

    rows: dict[str, dict] = {}
    symbols = list(universe.index)
    for i, symbol in enumerate(symbols, 1):
        if i == 1 or i % 25 == 0 or i == len(symbols):
            progress(f"[{i}/{len(symbols)}] fetching {symbol}...")
        fund = stmts = bars = None
        errors: list[str] = []
        try:
            fund = view.fundamentals(symbol)
        except Exception as exc:
            errors.append(f"fundamentals: {exc}")
        try:
            stmts = view.financial_statements(symbol)
        except Exception as exc:
            errors.append(f"statements: {exc}")
        try:
            bars = view.daily_bars(symbol, start=price_start)
        except Exception as exc:
            errors.append(f"bars: {exc}")
        if fund is None and stmts is None and bars is None:
            failures[symbol] = "; ".join(errors)
            continue
        if errors:
            failures[symbol] = "; ".join(errors) + " (partial data used)"
        raw = metrics_mod.collect_raw_metrics(fund, stmts, bars, spy_bars)
        raw["name"] = universe.loc[symbol].get("name", "")
        raw["sector"] = universe.loc[symbol].get("sector", "") or (
            fund.sector if fund else ""
        )
        rows[symbol] = raw

    if not rows:
        raise RuntimeError(
            f"No symbol produced any data ({len(failures)} failures). "
            f"First failure: {next(iter(failures.items()), None)}"
        )

    progress(f"Scoring {len(rows)} symbols...")
    raw_frame = pd.DataFrame.from_dict(rows, orient="index")
    scored = score_universe(raw_frame, cfg.settings.screener.weights)

    ranked = scored.scores[scored.scores["rank"].notna()]
    theses: dict[str, str] = {}
    for symbol, score_row in ranked.head(top).iterrows():
        sector = score_row["sector"]
        sector_pe = None
        if sector in scored.sector_medians.index:
            sector_pe = scored.sector_medians.loc[sector, "trailing_pe"]
        theses[symbol] = build_thesis(
            symbol, score_row, scored.metrics.loc[symbol], len(ranked), sector_pe
        )

    previous = latest_run(conn)
    deteriorators: list[Deterioration] = []
    previous_run_at = None
    if previous is not None:
        _, previous_run_at, prev_scores = previous
        current_for_cmp = scored.scores.copy()
        deteriorators = find_deteriorators(
            prev_scores, current_for_cmp, cfg.settings.screener.watchlist_size
        )

    run_id = save_run(
        conn, run_at, scored.scores, scored.metrics, cfg.settings.screener.weights,
        len(universe),
    )

    return ScreenResult(
        run_at=run_at,
        run_id=run_id,
        universe_size=len(universe),
        scored=scored,
        theses=theses,
        deteriorators=deteriorators,
        failures=failures,
        freshness=provider.quote_freshness().value,
        previous_run_at=previous_run_at,
    )
