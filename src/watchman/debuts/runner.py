"""Module F orchestration: detect new/upcoming listings, build comparable
cohorts from past IPOs, analyze each, persist, and return results.

All market data flows through AsOfView(now); the IPO calendar is a live
facility (upcoming listings are legitimately known in advance). New-since-last-
run listings are flagged so the daily scheduler can alert on debuts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from watchman.config import WatchmanConfig
from watchman.data.cache import CachedProvider
from watchman.data.provider import ET, AsOfView, DataProvider, IpoEvent
from watchman.debuts.analysis import STANDING_CAUTIONS, analyze_debut
from watchman.debuts.comparables import CandidateData, build_cohort, size_bucket
from watchman.debuts.model import DebutAnalysis

REFERENCE_LOOKBACK_DAYS = 900   # ~2.5y of past IPOs to draw comparables from
MIN_REFERENCE_SESSIONS = 21     # a comparable needs at least ~1mo of history
CANDIDATE_HISTORY_DAYS = 400    # how much post-IPO history to pull per comparable


@dataclass
class DebutsResult:
    now: datetime
    freshness: str
    window: tuple[str, str]
    analyses: list[DebutAnalysis] = field(default_factory=list)
    new_since_last: list[str] = field(default_factory=list)
    reference_pool_size: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    standing_cautions: tuple[str, ...] = STANDING_CAUTIONS


def _now_et(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=ET)
    return now if now.tzinfo else now.replace(tzinfo=ET)


def _candidate_fetch(view: AsOfView) -> Callable[[IpoEvent], CandidateData | None]:
    """Return a fetch(event) -> CandidateData for building cohorts. Bars and
    fundamentals go through the cached view; a symbol with no usable bars is
    skipped (returns None)."""

    def fetch(event: IpoEvent) -> CandidateData | None:
        try:
            bars = view.daily_bars(
                event.symbol,
                start=datetime.combine(event.ipo_date, datetime.min.time()),
                end=datetime.combine(
                    event.ipo_date + timedelta(days=CANDIDATE_HISTORY_DAYS),
                    datetime.min.time(),
                ),
            )
        except Exception:
            return None
        if bars is None or len(bars) < MIN_REFERENCE_SESSIONS:
            return None
        sector = market_cap = None
        try:
            fund = view.fundamentals(event.symbol)
            sector, market_cap = fund.sector, fund.market_cap
        except Exception:
            pass  # fundamentals optional — performance still counts for all-recent
        return CandidateData(
            sector=sector, market_cap=market_cap, bars=bars,
            offer_price=event.offer_price or event.expected_price,
        )

    return fetch


def run_debuts(
    cfg: WatchmanConfig,
    provider: DataProvider,
    conn: sqlite3.Connection,
    now: datetime | None = None,
    lookback_days: int | None = None,
    horizon_days: int | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> DebutsResult:
    now = _now_et(now)
    dcfg = cfg.settings.debuts
    lookback_days = lookback_days if lookback_days is not None else dcfg.lookback_days
    horizon_days = horizon_days if horizon_days is not None else dcfg.horizon_days

    view = AsOfView(CachedProvider(provider, conn), now)
    win_start = (now - timedelta(days=lookback_days)).date()
    win_end = (now + timedelta(days=horizon_days)).date()

    progress(f"Fetching IPO calendar {win_start}..{win_end}...")
    try:
        events = view.ipo_calendar(win_start, win_end)
    except NotImplementedError as exc:
        raise RuntimeError(
            "The debuts scanner needs an IPO calendar, which only the Finnhub or "
            "FMP providers offer. Set data.provider to finnhub or fmp (with the key "
            "in .env) and try again."
        ) from exc

    # Dedup by symbol (keep the latest-dated entry), drop withdrawn, newest first.
    by_symbol: dict[str, IpoEvent] = {}
    for e in sorted(events, key=lambda e: e.ipo_date):
        if e.status != "withdrawn":
            by_symbol[e.symbol] = e
    targets = sorted(by_symbol.values(), key=lambda e: e.ipo_date, reverse=True)
    if limit:
        targets = targets[:limit]

    progress("Fetching reference pool of past IPOs for cohorts...")
    try:
        reference = view.ipo_calendar(
            (now - timedelta(days=REFERENCE_LOOKBACK_DAYS)).date(),
            (now - timedelta(days=MIN_REFERENCE_SESSIONS * 2)).date(),
        )
    except NotImplementedError:
        reference = []
    reference = [e for e in reference if e.status != "withdrawn"]

    fetch = _candidate_fetch(view)
    result = DebutsResult(
        now=now,
        freshness=provider.quote_freshness().value,
        window=(win_start.isoformat(), win_end.isoformat()),
        reference_pool_size=len(reference),
    )

    previously_seen = _seen_symbols(conn)
    today = now.date()

    for i, event in enumerate(targets, 1):
        progress(f"[{i}/{len(targets)}] analyzing {event.symbol}...")
        is_trading = event.ipo_date <= today
        current_price = vs_offer = first_pop = days_since = None
        try:
            fundamentals = view.fundamentals(event.symbol)
        except Exception:
            fundamentals = None

        if is_trading:
            try:
                bars = view.daily_bars(
                    event.symbol,
                    start=datetime.combine(event.ipo_date, datetime.min.time()),
                )
            except Exception as exc:
                result.failures[event.symbol] = f"bars: {exc}"
                bars = None
            if bars is not None and not bars.empty:
                closes = bars["close"]
                current_price = float(closes.iloc[-1])
                days_since = len(closes)
                offer = event.offer_price or event.expected_price
                if offer and offer > 0:
                    vs_offer = (current_price / offer - 1) * 100
                    first_pop = (float(closes.iloc[0]) / offer - 1) * 100

        target_sector = fundamentals.sector if fundamentals else None
        target_bucket = size_bucket(fundamentals.market_cap if fundamentals else None)
        cohort = build_cohort(
            target_sector, target_bucket, reference, fetch,
            max_fetch=dcfg.max_cohort_fetch,
        )

        analysis = analyze_debut(
            event,
            freshness=provider.quote_freshness(),
            is_trading=is_trading,
            days_since_ipo=days_since,
            current_price=current_price,
            vs_offer_pct=vs_offer,
            first_day_pop_pct=first_pop,
            cohort=cohort,
            fundamentals=fundamentals,
            coverage_note=("fundamentals unavailable" if fundamentals is None else ""),
        )
        result.analyses.append(analysis)
        if event.symbol not in previously_seen:
            result.new_since_last.append(event.symbol)

    _persist(conn, now, result.analyses)
    return result


def _seen_symbols(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT DISTINCT symbol FROM debut_events").fetchall()
    return {r[0] for r in rows}


def _persist(conn: sqlite3.Connection, now: datetime, analyses: list[DebutAnalysis]) -> None:
    with conn:
        for a in analyses:
            cohort = a.cohort
            conn.execute(
                "INSERT OR REPLACE INTO debut_events (run_at, symbol, name, ipo_date,"
                " status, is_trading, days_since_ipo, current_price, vs_offer_pct,"
                " cohort_basis, cohort_sample, cohort_median_90d, verdict_label,"
                " verdict_score, reasons, cautions)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(), a.event.symbol, a.event.name,
                    a.event.ipo_date.isoformat(), a.event.status, int(a.is_trading),
                    a.days_since_ipo, a.current_price, a.vs_offer_pct,
                    cohort.basis if cohort else None,
                    cohort.sample if cohort else None,
                    cohort.median_90d if cohort else None,
                    a.verdict.label, a.verdict.score,
                    json.dumps(a.verdict.reasons), json.dumps(a.verdict.cautions),
                ),
            )
