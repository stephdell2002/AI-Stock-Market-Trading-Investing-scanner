"""Universe resolution from bundled snapshots."""

from __future__ import annotations

from watchman.config import UniverseConfig
from watchman.data.universe import load_index_snapshot, load_universe, normalize_symbol


def test_normalize_symbol():
    assert normalize_symbol(" brk.b ") == "BRK-B"
    assert normalize_symbol("AAPL") == "AAPL"


def test_snapshots_load_and_look_sane():
    sp500 = load_index_snapshot("sp500")
    ndx = load_index_snapshot("nasdaq100")
    assert 450 <= len(sp500) <= 510, f"sp500 snapshot has {len(sp500)} rows"
    assert 95 <= len(ndx) <= 105, f"nasdaq100 snapshot has {len(ndx)} rows"
    for df in (sp500, ndx):
        assert df["symbol"].is_unique
        assert (df["symbol"] == df["symbol"].str.upper()).all()
        assert not df["symbol"].str.contains(r"\.").any()  # yfinance style, no dots


def test_default_universe_unions_and_dedupes():
    cfg = UniverseConfig()  # sp500 + nasdaq100
    uni = load_universe(cfg)
    assert uni["symbol"].is_unique
    # Mega-caps sit in both indices and must appear once, tagged with both.
    row = uni.set_index("symbol").loc["AAPL"]
    assert row["indices"] == "nasdaq100+sp500"
    # Union is bigger than either index alone.
    assert len(uni) > len(load_index_snapshot("sp500"))


def test_extra_and_exclude_symbols():
    cfg = UniverseConfig(extra_symbols=["shop"], exclude_symbols=["AAPL", "msft"])
    uni = load_universe(cfg)
    symbols = set(uni["symbol"])
    assert "SHOP" in symbols
    assert "AAPL" not in symbols
    assert "MSFT" not in symbols
    assert uni.set_index("symbol").loc["SHOP", "indices"] == "extra"


def test_single_index_universe():
    cfg = UniverseConfig(indices=["nasdaq100"])
    uni = load_universe(cfg)
    assert (uni["indices"] == "nasdaq100").all()
