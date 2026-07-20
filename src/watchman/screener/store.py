"""Run persistence and deteriorator detection.

Every screen run is stored in SQLite so the next run can answer: which
previously high-ranked names have turned? Deterioration rules (explicit so
they can be argued with):

- Only names that ranked inside the prior top 2 x watchlist_size matter.
- Flag when the composite dropped by >= COMPOSITE_DROP points, or the
  fundamental half (mean of quality & growth) dropped by >= FUNDAMENTAL_DROP
  points — the latter catches names whose price momentum still looks fine
  while the business turns.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

COMPOSITE_DROP = 10.0
FUNDAMENTAL_DROP = 12.0


@dataclass
class Deterioration:
    symbol: str
    name: str
    prev_rank: int
    curr_rank: int | None  # None = no longer scoreable this run
    prev_composite: float
    curr_composite: float | None
    reason: str


def save_run(
    conn: sqlite3.Connection,
    run_at: datetime,
    scores: pd.DataFrame,
    metrics: pd.DataFrame,
    weights: dict[str, float],
    universe_size: int,
) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO screener_runs (run_at, universe_size, weights) VALUES (?, ?, ?)",
            (run_at.isoformat(), universe_size, json.dumps(weights)),
        )
        run_id = int(cur.lastrowid)
        rows = []
        for symbol, row in scores.iterrows():
            metric_json = json.dumps(
                {
                    k: (None if pd.isna(v) else float(v))
                    for k, v in metrics.loc[symbol].items()
                    if isinstance(v, (int, float, np.floating)) or pd.isna(v)
                }
            )
            rows.append(
                (
                    run_id,
                    symbol,
                    None if pd.isna(row["rank"]) else int(row["rank"]),
                    _none_if_nan(row["composite"]),
                    _none_if_nan(row["quality"]),
                    _none_if_nan(row["growth"]),
                    _none_if_nan(row["valuation"]),
                    _none_if_nan(row["momentum"]),
                    float(row["coverage"]),
                    metric_json,
                )
            )
        conn.executemany(
            "INSERT INTO screener_scores (run_id, symbol, rank, composite, quality, growth,"
            " valuation, momentum, coverage, metrics) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return run_id


def _none_if_nan(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def latest_run(conn: sqlite3.Connection) -> tuple[int, datetime, pd.DataFrame] | None:
    """Most recent stored run as (run_id, run_at, scores frame indexed by symbol)."""
    row = conn.execute(
        "SELECT run_id, run_at FROM screener_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    run_id, run_at = int(row[0]), datetime.fromisoformat(row[1])
    scores = pd.read_sql_query(
        "SELECT symbol, rank, composite, quality, growth, valuation, momentum, coverage "
        "FROM screener_scores WHERE run_id = ?",
        conn,
        params=(run_id,),
        index_col="symbol",
    )
    return run_id, run_at, scores


def _fundamental_score(row: pd.Series) -> float | None:
    vals = [row.get("quality"), row.get("growth")]
    vals = [v for v in vals if v is not None and not pd.isna(v)]
    return float(np.mean(vals)) if vals else None


def find_deteriorators(
    previous: pd.DataFrame,
    current: pd.DataFrame,
    watchlist_size: int,
) -> list[Deterioration]:
    """Compare a prior run's scores to the current run's (both indexed by symbol)."""
    out: list[Deterioration] = []
    rank_window = 2 * watchlist_size
    for symbol, prev in previous.iterrows():
        if prev.get("rank") is None or pd.isna(prev.get("rank")) or prev["rank"] > rank_window:
            continue
        if pd.isna(prev.get("composite")):
            continue
        if symbol not in current.index:
            continue  # missing data this run is a fetch problem, not a verdict
        curr = current.loc[symbol]
        reasons: list[str] = []
        comp_drop = None
        if not pd.isna(curr.get("composite")):
            comp_drop = float(prev["composite"]) - float(curr["composite"])
            if comp_drop >= COMPOSITE_DROP:
                reasons.append(
                    f"composite fell {comp_drop:.0f}pts "
                    f"({prev['composite']:.0f} -> {curr['composite']:.0f})"
                )
        prev_fund, curr_fund = _fundamental_score(prev), _fundamental_score(curr)
        if prev_fund is not None and curr_fund is not None:
            fund_drop = prev_fund - curr_fund
            if fund_drop >= FUNDAMENTAL_DROP:
                reasons.append(
                    f"fundamentals (quality+growth) fell {fund_drop:.0f}pts "
                    f"({prev_fund:.0f} -> {curr_fund:.0f})"
                )
        if reasons:
            out.append(
                Deterioration(
                    symbol=str(symbol),
                    name=str(current.loc[symbol].get("name", "") or ""),
                    prev_rank=int(prev["rank"]),
                    curr_rank=None if pd.isna(curr.get("rank")) else int(curr["rank"]),
                    prev_composite=float(prev["composite"]),
                    curr_composite=_none_if_nan(curr.get("composite")),
                    reason="; ".join(reasons),
                )
            )
    out.sort(key=lambda d: d.prev_rank)
    return out
