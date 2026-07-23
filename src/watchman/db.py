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

CREATE TABLE IF NOT EXISTS signal_ledger (
    signal_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    session     TEXT NOT NULL,           -- trading date the signal belongs to
    symbol      TEXT NOT NULL,
    setup       TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry       REAL NOT NULL,
    stop        REAL NOT NULL,
    target1     REAL NOT NULL,
    target2     REAL,
    risk_reward REAL NOT NULL,
    shares      INTEGER NOT NULL,
    freshness   TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',  -- open|target|stopped|expired
    resolved_at TEXT,
    exit_price  REAL,
    pnl         REAL,
    r_multiple  REAL
);

CREATE TABLE IF NOT EXISTS setup_baselines (
    setup       TEXT PRIMARY KEY,        -- backtest baseline for divergence checks
    win_rate    REAL NOT NULL,
    sample      INTEGER NOT NULL,
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_accounts (
    account     TEXT PRIMARY KEY,        -- 'day' | 'longterm'
    cash        REAL NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    direction   TEXT NOT NULL DEFAULT 'long',
    qty         REAL NOT NULL,
    avg_cost    REAL NOT NULL,           -- all-in fill price
    opened_at   TEXT NOT NULL,
    signal_id   INTEGER,                 -- ledger link for day trades
    stop        REAL,
    target      REAL,
    closed_at   TEXT,
    exit_price  REAL,
    realized_pnl REAL
);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,           -- buy|sell
    qty         REAL NOT NULL,
    price       REAL NOT NULL,           -- all-in after slippage/spread
    reference   REAL NOT NULL,           -- pre-cost reference price
    commission  REAL NOT NULL,
    signal_id   INTEGER,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS equity_marks (
    account     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    equity      REAL NOT NULL,
    cash        REAL NOT NULL,
    PRIMARY KEY (account, ts)
);

CREATE TABLE IF NOT EXISTS longterm_rebalances (
    rebalance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    holdings     TEXT NOT NULL           -- JSON symbol -> target weight
);

CREATE TABLE IF NOT EXISTS debut_events (
    run_at        TEXT NOT NULL,          -- when the debuts scan ran
    symbol        TEXT NOT NULL,
    name          TEXT,
    ipo_date      TEXT NOT NULL,
    status        TEXT,                   -- expected|priced|withdrawn|filed
    is_trading    INTEGER NOT NULL,
    days_since_ipo INTEGER,
    current_price REAL,
    vs_offer_pct  REAL,
    cohort_basis  TEXT,
    cohort_sample INTEGER,
    cohort_median_90d REAL,
    verdict_label TEXT NOT NULL,
    verdict_score REAL NOT NULL,
    reasons       TEXT NOT NULL,          -- JSON list
    cautions      TEXT NOT NULL,          -- JSON list
    PRIMARY KEY (run_at, symbol)
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
