"""Module F end-to-end: the runner builds cohorts from a synthetic IPO history
and produces persisted, data-driven verdicts; the CLI prints them honestly.
All offline."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import yaml

from watchman.data.provider import (
    ET,
    DataProvider,
    Freshness,
    Fundamentals,
    IpoEvent,
)
from watchman.db import connect
from watchman.debuts import run_debuts

NOW = datetime.now(tz=ET)
TODAY = NOW.date()


def _daily(start: date, days: int, first: float, slope: float) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=days)
    closes = [first + slope * i for i in range(days)]
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "adj_close": closes, "volume": 1e6},
        index=idx,
    )


class DebutProvider(DataProvider):
    """Synthetic provider with an IPO calendar + per-symbol bars/fundamentals."""

    name = "debut-synthetic"

    def __init__(self, calendar, companies, freshness=Freshness.REALTIME):
        self._calendar = calendar
        self._companies = companies
        self._freshness = freshness

    def ipo_calendar(self, start, end):
        return [e for e in self._calendar if start <= e.ipo_date <= end]

    def daily_bars(self, symbol, start, end):
        c = self._companies.get(symbol)
        if c is None or c.get("bars") is None:
            raise ValueError(f"no bars for {symbol}")
        bars = c["bars"]
        mask = (bars.index >= pd.Timestamp(start.date())) & (
            bars.index <= pd.Timestamp(end.date()))
        return bars.loc[mask]

    def fundamentals(self, symbol):
        c = self._companies.get(symbol)
        if c is None or "fundamentals" not in c:
            raise ValueError(f"no fundamentals for {symbol}")
        return c["fundamentals"]

    def quote_freshness(self):
        return self._freshness


def _fundamentals(symbol, sector, cap, margin=0.1):
    return Fundamentals(symbol=symbol, fetched_at=datetime.now(), sector=sector,
                        market_cap=cap, profit_margin=margin, trailing_pe=25.0)


def build_world(target_slope: float):
    """A target IPO trading now + 8 past Tech mid-cap comparables that rose."""
    target_ipo = TODAY - timedelta(days=10)
    calendar = [IpoEvent("TGT", "Target Co", target_ipo, offer_price=20.0,
                         status="priced")]
    companies = {
        "TGT": {
            "bars": _daily(target_ipo, 8, 22.0, target_slope),   # +10% pop from 20
            "fundamentals": _fundamentals("TGT", "Tech", 5e9),
        }
    }
    # Past comparables: IPO'd ~1 year ago, Tech mid-cap, rose ~+12% by 90d.
    past_ipo = TODAY - timedelta(days=400)
    for i in range(8):
        sym = f"CMP{i}"
        calendar.append(IpoEvent(sym, f"Cmp {i}", past_ipo, offer_price=20.0,
                                 status="priced"))
        companies[sym] = {
            "bars": _daily(past_ipo, 200, 20.0, 0.02),           # steady riser
            "fundamentals": _fundamentals(sym, "Tech", 5e9),
        }
    return DebutProvider(calendar, companies), calendar


@pytest.fixture
def cfg(tmp_path):
    from watchman.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    with open("config/settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    settings["data"]["db_path"] = str(tmp_path / "debuts.db")
    (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
    with open("config/risk.yaml", encoding="utf-8") as f:
        (cfg_dir / "risk.yaml").write_text(f.read())
    return load_config(cfg_dir)


class TestRunner:
    def test_analyzes_target_with_cohort_and_persists(self, cfg):
        provider, _ = build_world(target_slope=0.1)
        conn = connect(cfg.settings.data.db_path)
        result = run_debuts(cfg, provider, conn, now=NOW)

        tgt = next(a for a in result.analyses if a.event.symbol == "TGT")
        assert tgt.is_trading
        assert tgt.cohort is not None
        assert tgt.cohort.sample == 8
        assert tgt.cohort.basis == "sector+size"          # matched tightly
        assert tgt.vs_offer_pct is not None
        assert tgt.verdict.label in ("AVOID", "CAUTION", "NEUTRAL", "LEAN_FAVORABLE")
        # Every reason cites a number/fact — none is empty.
        assert all(tgt.verdict.reasons)

        # Persisted for tracking.
        row = conn.execute(
            "SELECT verdict_label, cohort_sample FROM debut_events WHERE symbol='TGT'"
        ).fetchone()
        assert row[0] == tgt.verdict.label
        assert row[1] == 8

    def test_new_since_last_flag(self, cfg):
        provider, _ = build_world(0.1)
        conn = connect(cfg.settings.data.db_path)
        first = run_debuts(cfg, provider, conn, now=NOW)
        assert "TGT" in first.new_since_last
        second = run_debuts(cfg, provider, conn, now=NOW + timedelta(hours=1))
        assert "TGT" not in second.new_since_last     # already seen

    def test_missing_calendar_raises_helpful_error(self, cfg):
        class NoCalendar(DataProvider):
            name = "nocal"

            def daily_bars(self, s, a, b):
                raise ValueError

            def fundamentals(self, s):
                raise ValueError

            def quote_freshness(self):
                return Freshness.EOD

        conn = connect(cfg.settings.data.db_path)
        with pytest.raises(RuntimeError, match="Finnhub or FMP"):
            run_debuts(cfg, NoCalendar(), conn, now=NOW)

    def test_withdrawn_listings_excluded(self, cfg):
        provider, calendar = build_world(0.1)
        calendar.append(IpoEvent("DEAD", "Dead Co", TODAY - timedelta(days=3),
                                 offer_price=15.0, status="withdrawn"))
        conn = connect(cfg.settings.data.db_path)
        result = run_debuts(cfg, provider, conn, now=NOW)
        assert "DEAD" not in [a.event.symbol for a in result.analyses]


class TestDebutsCLI:
    def test_cli_prints_verdict_and_disclaimers(self, cfg, monkeypatch, capsys):
        import watchman.cli as cli

        provider, _ = build_world(0.1)
        monkeypatch.setattr(cli, "_make_provider", lambda _cfg: provider)
        rc = cli.main(["--config-dir", str(cfg.config_dir), "debuts", "--why"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "TGT" in out
        assert "VERDICT:" in out
        assert "not advice" in out.lower() or "not investment advice" in out.lower()
        assert "underperform" in out.lower()          # standing caveat surfaced
        # The verdict shown is one of the hedged labels, never "BUY".
        assert "BUY" not in out
