"""SQLite plumbing. One file holds the price cache now; the signal ledger and
paper portfolios (Module D) will live in the same database later."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,           -- ISO date of the US trading session
    open       REAL NOT NULL,
    high       REAL NOT NULL,
    low        REAL NOT NULL,
    close      REAL NOT NULL,
    adj_close  REAL NOT NULL,
    volume     REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS bar_coverage (
    symbol      TEXT PRIMARY KEY,       -- requested range we have fetched for this symbol
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamentals_cache (
    symbol      TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL            -- Fundamentals as JSON
);

CREATE TABLE IF NOT EXISTS statements_cache (
    symbol      TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    income      TEXT NOT NULL,           -- DataFrames as JSON (orient=split)
    balance     TEXT NOT NULL,
    cashflow    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_lists (
    scan_date   TEXT PRIMARY KEY,        -- ISO date of the pre-market scan
    created_at  TEXT NOT NULL,
    symbols     TEXT NOT NULL,           -- JSON list, ranked
    details     TEXT NOT NULL            -- JSON per-candidate numbers
);

CREATE TABLE IF NOT EXISTS screener_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL,
    universe_size  INTEGER NOT NULL,
    weights        TEXT NOT NULL         -- pillar weights used, as JSON
);

CREATE TABLE IF NOT EXISTS screener_scores (
    run_id     INTEGER NOT NULL REFERENCES screener_runs(run_id),
    symbol     TEXT NOT NULL,
    rank       INTEGER,                  -- NULL when composite could not be scored
    composite  REAL,
    quality    REAL,
    growth     REAL,
    valuation  REAL,
    momentum   REAL,
    coverage   REAL NOT NULL,
    metrics    TEXT NOT NULL,            -- raw metric values, as JSON
    PRIMARY KEY (run_id, symbol)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the Watchman database with schema applied."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
