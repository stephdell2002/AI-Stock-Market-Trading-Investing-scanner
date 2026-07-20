"""The daily HTML report and briefs: right sections, honest labels, dated
files, divergence warnings surfaced."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
import yaml
from tests.intraday_fixtures import IntradayProvider, bars_from_closes, daily_history

from watchman.data.provider import ET, AsOfView
from watchman.db import connect
from watchman.paper import SignalLedger
from watchman.report import build_report, write_brief, write_report
from watchman.signals import run_signals

TODAY = date.today()
NOW = datetime.combine(TODAY, time(16, 30), tzinfo=ET)


@pytest.fixture
def env(tmp_path):
    from watchman.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    with open("config/settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    settings["data"]["db_path"] = str(tmp_path / "r.db")
    settings["report"] = {"output_dir": str(tmp_path / "reports")}
    (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
    with open("config/risk.yaml", encoding="utf-8") as f:
        (cfg_dir / "risk.yaml").write_text(f.read())
    cfg = load_config(cfg_dir)

    session = bars_from_closes(
        [105.0, 106.0, 105.5, 105.8, 107.0, 109.0, 110.5, 111.6],
        TODAY, start=time(9, 30), interval_minutes=5, volume=800_000.0,
    )
    provider = IntradayProvider(
        {"GAP": {"daily": daily_history(prev_close=100.0), "intraday": session}}
    )
    conn = connect(cfg.settings.data.db_path)
    # Produce one full signal lifecycle: taken at 9:56, target by 10:30.
    run_signals(cfg, provider, conn,
                symbols=["GAP"], now=datetime.combine(TODAY, time(9, 56), tzinfo=ET))
    run_signals(cfg, provider, conn,
                symbols=["GAP"], now=datetime.combine(TODAY, time(10, 30), tzinfo=ET))
    view = AsOfView(provider, NOW)
    return cfg, conn, provider, view


class TestHtmlReport:
    def test_report_has_all_sections_and_honesty_footer(self, env):
        cfg, conn, _provider, view = env
        html = build_report(cfg, conn, view, NOW)
        assert "Paper P&amp;L" in html
        assert "Today's signals" in html
        assert "live vs backtest" in html
        assert "Watchlist changes" in html
        assert "Read this before believing anything above" in html
        assert "NOT ACTIONABLE" in html          # DELAYED provider
        assert "pessimistic" in html             # same-bar rule disclosed
        assert "$25k" in html                    # PDT reminder

    def test_report_shows_the_resolved_signal(self, env):
        cfg, conn, _provider, view = env
        html = build_report(cfg, conn, view, NOW)
        assert "GAP" in html
        assert "orb-15m" in html
        assert "target" in html                  # resolved status appears

    def test_no_baseline_reads_as_no_evidence_not_zero(self, env):
        cfg, conn, _provider, view = env
        html = build_report(cfg, conn, view, NOW)
        assert "no honest intraday backtest on free data" in html

    def test_divergence_warning_surfaces_loudly(self, env):
        cfg, conn, _provider, view = env
        ledger = SignalLedger(conn)
        ledger.set_baseline("orb-15m", 0.90, 500, "hypothetical backtest")
        # 21 losing signals over recent sessions -> far below the 90% baseline.
        from tests.test_ledger import make_signal

        for i in range(21):
            sid = ledger.record(
                make_signal(symbol=f"L{i}"), (NOW - timedelta(days=5)).date()
            )
            ledger.resolve(sid, "stopped", NOW, 98.0, -100.0)
        html = build_report(cfg, conn, view, NOW)
        assert "DIVERGENCE" in html
        assert "class='alert'" in html

    def test_report_is_self_contained(self, env):
        cfg, conn, _provider, view = env
        html = build_report(cfg, conn, view, NOW)
        assert "<script" not in html
        assert "http://" not in html and "https://" not in html
        assert "<svg" in html                    # equity curve is inline

    def test_write_report_dates_the_file(self, env):
        cfg, conn, _provider, view = env
        path = write_report(cfg, conn, view, NOW)
        assert path.name == f"report-{NOW:%Y-%m-%d}.html"
        assert path.exists()


class TestBriefs:
    def test_morning_brief_written_and_warns_when_unscanned(self, env):
        cfg, conn, _provider, view = env
        path = write_brief("morning", cfg, conn, view, NOW)
        text = path.read_text()
        assert path.name == f"morning-{NOW:%Y-%m-%d}.txt"
        assert "MORNING BRIEF" in text
        assert "not scanned yet" in text         # we never ran scan in this env
        assert "DELAYED" in text

    def test_evening_recap_shows_outcomes_and_stats(self, env):
        cfg, conn, _provider, view = env
        path = write_brief("evening", cfg, conn, view, NOW)
        text = path.read_text()
        assert "EVENING RECAP" in text
        assert "GAP" in text and "TARGET" in text
        assert "30d" in text and "90d" in text
        assert "Paper results always read better" in text

    def test_unknown_brief_kind_refused(self, env):
        cfg, conn, _provider, view = env
        with pytest.raises(ValueError, match=r"morning\|evening"):
            write_brief("midnight", cfg, conn, view, NOW)


class TestReportCLI:
    def test_cli_report_end_to_end(self, env, monkeypatch, capsys):
        cfg, _conn, provider, _view = env
        import watchman.cli as cli

        monkeypatch.setattr(cli, "_make_provider", lambda _cfg: provider)
        rc = cli.main(["--config-dir", str(cfg.config_dir), "report"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Daily HTML report written to" in out
        assert "self-contained" in out
