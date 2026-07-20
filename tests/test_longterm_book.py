"""Module A's simulated long-term portfolio: monthly equal-weight rebalance
into the latest screener top-N, with costs, dropped names sold."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tests.screener_fixtures import three_company_provider, universe_frame

from watchman.config import load_config
from watchman.data.provider import ET, AsOfView
from watchman.db import connect
from watchman.paper import LONGTERM, PaperBook, rebalance_longterm
from watchman.paper.longterm import last_rebalance_at, latest_watchlist
from watchman.screener import run_screen

NOW = datetime.now(tz=ET)


@pytest.fixture
def setup(tmp_path):
    cfg = load_config("config")
    conn = connect(tmp_path / "lt.db")
    provider = three_company_provider()
    run_screen(cfg, provider, conn, universe=universe_frame(provider), top=3)
    book = PaperBook(conn, LONGTERM, cfg.settings.costs, 10_000.0)
    view = AsOfView(provider, NOW)
    return cfg, conn, provider, book, view


class TestRebalance:
    def test_first_rebalance_buys_top_names_equal_weight(self, setup):
        _cfg, conn, _provider, book, view = setup
        notes = rebalance_longterm(book, conn, view, top=2, now=NOW)
        held = {p.symbol for p in book.open_positions()}
        assert held == set(latest_watchlist(conn, 2)) == {"GOOD", "MED"}
        assert any("bought GOOD" in n for n in notes)
        # Costs applied on every fill.
        for pos in book.open_positions():
            fills = conn.execute(
                "SELECT price, reference FROM paper_fills WHERE symbol = ?",
                (pos.symbol,),
            ).fetchall()
            assert all(price > reference for price, reference in fills)  # buys pay up

    def test_not_due_again_within_a_month(self, setup):
        _cfg, conn, _provider, book, view = setup
        rebalance_longterm(book, conn, view, top=2, now=NOW)
        assert rebalance_longterm(book, conn, view, top=2, now=NOW) == []
        assert last_rebalance_at(conn) is not None

    def test_dropped_name_sold_next_month(self, setup):
        cfg, conn, provider, book, view = setup
        rebalance_longterm(book, conn, view, top=2, now=NOW)

        # A month later, a new screener run demotes MED below BAD. The data
        # caches must expire first or the swap is invisible to the screener.
        with conn:
            for table in ("fundamentals_cache", "statements_cache", "daily_bars",
                          "bar_coverage"):
                conn.execute(f"DELETE FROM {table}")
        provider.companies["MED"], provider.companies["BAD"] = (
            provider.companies["BAD"], provider.companies["MED"],
        )
        run_screen(cfg, provider, conn, universe=universe_frame(provider), top=3)
        next_month = NOW + timedelta(days=30)
        notes = rebalance_longterm(book, conn, view, top=2, now=next_month)
        held = {p.symbol for p in book.open_positions()}
        assert "MED" not in held
        assert "BAD" in held
        assert any(n.startswith("sold MED") for n in notes)

    def test_force_overrides_the_monthly_gate(self, setup):
        _cfg, conn, _provider, book, view = setup
        rebalance_longterm(book, conn, view, top=2, now=NOW)
        notes = rebalance_longterm(book, conn, view, top=2, now=NOW, force=True)
        assert notes  # ran again despite not being due

    def test_no_screener_run_says_so(self, tmp_path):
        cfg = load_config("config")
        conn = connect(tmp_path / "empty.db")
        provider = three_company_provider()
        book = PaperBook(conn, LONGTERM, cfg.settings.costs, 10_000.0)
        notes = rebalance_longterm(book, conn, AsOfView(provider, NOW), top=2, now=NOW)
        assert any("run `watchman screen` first" in n for n in notes)
