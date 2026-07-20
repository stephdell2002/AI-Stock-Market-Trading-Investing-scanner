"""End-to-end screener tests: runner orchestration, theses, deteriorator
tracking across runs, run persistence, and the CLI wiring. All offline."""

from __future__ import annotations

import pandas as pd
import pytest
from tests.screener_fixtures import (
    make_fundamentals,
    make_price_history,
    make_statements,
    three_company_provider,
    universe_frame,
)

from watchman.config import load_config
from watchman.db import connect
from watchman.screener import run_screen
from watchman.screener.store import find_deteriorators, latest_run

REPO_CONFIG = "config"


@pytest.fixture
def cfg():
    return load_config(REPO_CONFIG)


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "screen.db")


def run_once(cfg, conn, provider=None):
    provider = provider or three_company_provider()
    return run_screen(
        cfg, provider, conn, universe=universe_frame(provider), top=3
    ), provider


class TestEndToEnd:
    def test_ranking_order_matches_construction(self, cfg, conn):
        result, _ = run_once(cfg, conn)
        ranked = result.scored.scores
        assert list(ranked.index[:3]) == ["GOOD", "MED", "BAD"]
        assert ranked.loc["GOOD", "composite"] > ranked.loc["BAD", "composite"]

    def test_theses_cite_actual_numbers(self, cfg, conn):
        result, _ = run_once(cfg, conn)
        good = result.theses["GOOD"]
        assert "Good Corp" in good
        assert "#1 of" in good
        assert "%" in good  # cites at least one actual figure
        assert "Weak spot" in good  # honesty: even the winner shows its weakness

    def test_run_is_persisted(self, cfg, conn):
        result, _ = run_once(cfg, conn)
        stored = latest_run(conn)
        assert stored is not None
        run_id, _, scores = stored
        assert run_id == result.run_id
        assert set(scores.index) == {"GOOD", "MED", "BAD"}
        assert scores.loc["GOOD", "rank"] == 1

    def test_failures_are_reported_not_hidden(self, cfg, conn):
        provider = three_company_provider()
        del provider.companies["BAD"]["fundamentals"]
        del provider.companies["BAD"]["statements"]
        del provider.companies["BAD"]["bars"]
        result = run_screen(cfg, provider, conn, universe=universe_frame(provider), top=3)
        assert "BAD" in result.failures
        assert "BAD" not in result.scored.scores.index

    def test_freshness_label_propagates(self, cfg, conn):
        result, _ = run_once(cfg, conn)
        assert result.freshness == "EOD"


class TestDeterioratorsAcrossRuns:
    def test_second_run_flags_turned_fundamentals(self, cfg, conn):
        _, provider = run_once(cfg, conn)  # run 1: GOOD is #1

        # Days pass; the data caches expire (deterioration is only visible
        # once fresh data is fetched, so simulate expiry explicitly).
        with conn:
            for table in ("fundamentals_cache", "statements_cache", "daily_bars",
                          "bar_coverage"):
                conn.execute(f"DELETE FROM {table}")

        # GOOD's business turns: growth gone, margins/quality now the worst.
        provider.companies["GOOD"]["statements"] = make_statements(
            "GOOD",
            revenue=[195, 180, 160, 140],
            diluted_eps=[2.2, 1.6, 1.0, 0.5],
            gross_profit=[89, 70, 52, 35],
            operating_income=[50, 35, 20, 8],
            ebit=20.0, invested_capital=1500.0,
            total_debt=1200.0, ebitda=60.0,
            net_income=10.0, free_cash_flow=2.0,
        )
        provider.companies["GOOD"]["fundamentals"] = make_fundamentals(
            "GOOD", market_cap=2000.0, trailing_pe=60.0, ev_to_ebitda=40.0,
            free_cash_flow=2.0,
        )
        provider.companies["GOOD"]["bars"] = make_price_history(
            -0.002, days=300, end=pd.Timestamp.now().normalize().strftime("%Y-%m-%d")
        )

        result2 = run_screen(cfg, provider, conn, universe=universe_frame(provider), top=3)
        flagged = {d.symbol for d in result2.deteriorators}
        assert "GOOD" in flagged
        d = next(d for d in result2.deteriorators if d.symbol == "GOOD")
        assert d.prev_rank == 1
        assert "fell" in d.reason

    def test_first_run_has_no_deteriorators(self, cfg, conn):
        result, _ = run_once(cfg, conn)
        assert result.deteriorators == []
        assert result.previous_run_at is None


class TestDeterioratorRules:
    def _frame(self, rows: dict) -> pd.DataFrame:
        return pd.DataFrame.from_dict(rows, orient="index")

    def test_low_ranked_names_are_ignored(self):
        prev = self._frame(
            {"X": {"rank": 99, "composite": 80.0, "quality": 80.0, "growth": 80.0}}
        )
        curr = self._frame(
            {"X": {"rank": 120, "composite": 40.0, "quality": 40.0, "growth": 40.0}}
        )
        assert find_deteriorators(prev, curr, watchlist_size=15) == []

    def test_small_wobble_not_flagged(self):
        prev = self._frame(
            {"X": {"rank": 5, "composite": 80.0, "quality": 80.0, "growth": 80.0}}
        )
        curr = self._frame(
            {"X": {"rank": 7, "composite": 75.0, "quality": 76.0, "growth": 74.0}}
        )
        assert find_deteriorators(prev, curr, watchlist_size=15) == []

    def test_fundamental_turn_flagged_even_if_composite_holds(self):
        # Momentum keeps the composite afloat while the business turns.
        prev = self._frame(
            {"X": {"rank": 5, "composite": 80.0, "quality": 85.0, "growth": 85.0,
                   "valuation": 70.0, "momentum": 80.0}}
        )
        curr = self._frame(
            {"X": {"rank": 9, "composite": 73.0, "quality": 60.0, "growth": 55.0,
                   "valuation": 70.0, "momentum": 95.0, "name": "X Corp"}}
        )
        found = find_deteriorators(prev, curr, watchlist_size=15)
        assert len(found) == 1
        assert "fundamentals" in found[0].reason


class TestScreenCLI:
    def test_cli_screen_end_to_end(self, cfg, conn, monkeypatch, capsys, tmp_path):
        import watchman.cli as cli

        provider = three_company_provider()
        monkeypatch.setattr(cli, "_make_provider", lambda _cfg: provider)
        monkeypatch.setattr(
            "watchman.screener.runner.load_universe",
            lambda _u: universe_frame(provider),
        )
        # Point the db at tmp so the test never touches the real data dir.
        import yaml

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        with open("config/settings.yaml", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        settings["data"]["db_path"] = str(tmp_path / "cli.db")
        (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
        with open("config/risk.yaml", encoding="utf-8") as f:
            (cfg_dir / "risk.yaml").write_text(f.read())

        tv_path = tmp_path / "tv_watchlist.txt"
        rc = cli.main(
            ["--config-dir", str(cfg_dir), "screen", "--top", "3",
             "--export-tv", str(tv_path)]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Watchlist" in out
        assert "GOOD" in out
        assert "Theses" in out
        assert "NOT point-in-time" in out  # the honesty footer is always printed
        assert tv_path.read_text().splitlines() == ["GOOD", "MED", "BAD"]
