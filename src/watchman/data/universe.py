"""Screening universe: S&P 500 + Nasdaq 100 membership.

Membership comes from bundled snapshot CSVs (symbol, name, sector) so Watchman
works offline and tests never need the network. Snapshots drift as indices
rebalance — `watchman universe --refresh` rebuilds them from Wikipedia's public
constituent lists (free, CC-licensed reference data; not a broker, not a
private API). snapshots/meta.yaml records when and how each snapshot was made.

Survivorship-bias note (also flagged in backtest output): a *current*
membership list applied to *historical* backtests overstates results, because
losers that left the index are missing. Free data cannot fully fix this; the
Module A backtest states it loudly rather than hiding it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from watchman.config import UniverseConfig

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

WIKIPEDIA_SOURCES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "nasdaq100": "https://en.wikipedia.org/wiki/Nasdaq-100",
}


def normalize_symbol(symbol: str) -> str:
    """Canonical yfinance-style symbol: uppercase, dashes for share classes
    (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-")


def to_tradingview_symbol(symbol: str) -> str:
    """TradingView's import format uses dots for share classes (BRK.B).
    Used by `watchman screen --export-tv` to write importable watchlists."""
    return normalize_symbol(symbol).replace("-", ".")


def load_index_snapshot(index: str, snapshot_dir: Path = SNAPSHOT_DIR) -> pd.DataFrame:
    path = snapshot_dir / f"{index}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No snapshot for index {index!r} at {path}. "
            f"Known indices: {sorted(p.stem for p in snapshot_dir.glob('*.csv'))}"
        )
    df = pd.read_csv(path)
    df["symbol"] = df["symbol"].map(normalize_symbol)
    df["index"] = index
    return df[["symbol", "name", "sector", "index"]]


def snapshot_meta(snapshot_dir: Path = SNAPSHOT_DIR) -> dict:
    meta_path = snapshot_dir / "meta.yaml"
    if not meta_path.exists():
        return {}
    with meta_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_universe(cfg: UniverseConfig, snapshot_dir: Path = SNAPSHOT_DIR) -> pd.DataFrame:
    """Resolve the configured universe to a DataFrame of
    [symbol, name, sector, indices], deduplicated and sorted by symbol."""
    frames = [load_index_snapshot(ix, snapshot_dir) for ix in cfg.indices]
    merged = pd.concat(frames, ignore_index=True)
    grouped = (
        merged.groupby("symbol")
        .agg(
            name=("name", "first"),
            sector=("sector", "first"),
            indices=("index", lambda s: "+".join(sorted(set(s)))),
        )
        .reset_index()
    )
    for sym in cfg.extra_symbols:
        sym = normalize_symbol(sym)
        if sym not in set(grouped["symbol"]):
            grouped.loc[len(grouped)] = {
                "symbol": sym,
                "name": "",
                "sector": "",
                "indices": "extra",
            }
    excluded = {normalize_symbol(s) for s in cfg.exclude_symbols}
    grouped = grouped[~grouped["symbol"].isin(excluded)]
    return grouped.sort_values("symbol").reset_index(drop=True)


def refresh_snapshots(snapshot_dir: Path = SNAPSHOT_DIR) -> dict[str, int]:
    """Rebuild snapshot CSVs from Wikipedia's constituent tables.

    Needs outbound network access. Returns {index: row_count}.
    """
    counts: dict[str, int] = {}
    results: dict[str, pd.DataFrame] = {}
    for index, url in WIKIPEDIA_SOURCES.items():
        tables = pd.read_html(url)
        results[index] = _extract_constituents(index, tables)

    # Only write once BOTH fetches parsed, so a failure can't leave one file stale.
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for index, df in results.items():
        df.to_csv(snapshot_dir / f"{index}.csv", index=False)
        counts[index] = len(df)
    meta = {
        "as_of": date.today().isoformat(),
        "source": "Wikipedia constituent lists (public reference data)",
        "urls": WIKIPEDIA_SOURCES,
        "note": "Symbols normalized to yfinance style (dashes for share classes).",
    }
    with (snapshot_dir / "meta.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(meta, f, sort_keys=False)
    return counts


def _extract_constituents(index: str, tables: list[pd.DataFrame]) -> pd.DataFrame:
    table = None
    for t in tables:
        cols = [str(c) for c in t.columns]
        if any(("Symbol" in c or "Ticker" in c) for c in cols) and len(t) > 80:
            table = t
            break
    if table is None:
        raise ValueError(f"Could not find a constituents table for {index}")
    sym_col = next(c for c in table.columns if "Symbol" in str(c) or "Ticker" in str(c))
    name_col = next(
        (c for c in table.columns if "Security" in str(c) or "Company" in str(c)),
        table.columns[0],
    )
    sector_col = next((c for c in table.columns if "Sector" in str(c)), None)
    out = pd.DataFrame(
        {
            "symbol": table[sym_col].astype(str).map(normalize_symbol),
            "name": table[name_col].astype(str).str.strip(),
            "sector": (
                table[sector_col].astype(str).str.strip() if sector_col is not None else ""
            ),
        }
    )
    return out.drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True)
