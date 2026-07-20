"""End-to-end Module B: scan -> persisted focus list -> signals, through the
real CLI with a synthetic delayed provider. Verifies the honesty labeling."""

from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest
import yaml
from tests.intraday_fixtures import IntradayProvider, bars_from_closes, daily_history

from watchman.data.provider import ET, Fundamentals, NewsItem
from watchman.db import connect
from watchman.signals import run_scan, run_signals


def gapper_provider() -> IntradayProvider:
    """GAP: +5% pre-market gap on heavy volume then an ORB long trigger.
    DULL: no gap, no volume. SPY-like daily history everywhere."""
    day = date.today()
    # Published 6:00 ET today: before the 9:00 scan pin and within 18h of it
    # (wall-clock-relative timestamps would drift past the pin).
    fresh_news = [
        NewsItem("GAP", "contract win", datetime.combine(day, time(6, 0), tzinfo=ET))
    ]
    gap_premarket = bars_from_closes(
        [104.0, 104.5, 105.0], day, start=time(8, 0), interval_minutes=5,
        volume=300_000.0,
    )
    # Regular session: OR forms 105..106.2 in first 3 bars, breakout on bar 5.
    gap_session = bars_from_closes(
        [105.0, 106.0, 105.5, 105.8, 107.0], day, start=time(9, 30),
        interval_minutes=5, volume=800_000.0,
    )
    dull_premarket = bars_from_closes(
        [100.1, 100.0, 100.1], day, start=time(8, 0), interval_minutes=5,
        volume=5_000.0,
    )
    return IntradayProvider(
        {
            "GAP": {
                "daily": daily_history(prev_close=100.0, avg_volume=5_000_000.0),
                "premarket": gap_premarket,
                "intraday": gap_session,
                "fundamentals": Fundamentals(
                    symbol="GAP", fetched_at=datetime.now(), float_shares=30e6
                ),
                "news": fresh_news,
            },
            "DULL": {
                "daily": daily_history(prev_close=100.0, avg_volume=5_000_000.0),
                "premarket": dull_premarket,
                "fundamentals": Fundamentals(symbol="DULL", fetched_at=datetime.now()),
            },
        }
    )


@pytest.fixture
def cfg(tmp_path):
    from watchman.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    with open("config/settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    settings["data"]["db_path"] = str(tmp_path / "signals.db")
    (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
    with open("config/risk.yaml", encoding="utf-8") as f:
        (cfg_dir / "risk.yaml").write_text(f.read())
    return load_config(cfg_dir)


@pytest.fixture
def universe(monkeypatch):
    frame = pd.DataFrame(
        [
            {"symbol": "GAP", "name": "Gapper", "sector": "Tech", "indices": "test"},
            {"symbol": "DULL", "name": "Dull", "sector": "Tech", "indices": "test"},
        ]
    )
    monkeypatch.setattr("watchman.signals.runner.load_universe", lambda _cfg: frame)
    return frame


class TestScanToSignals:
    def test_scan_builds_and_persists_focus_list(self, cfg, universe):
        provider = gapper_provider()
        conn = connect(cfg.settings.data.db_path)
        scan_time = datetime.combine(date.today(), time(9, 0), tzinfo=ET)
        outcome = run_scan(cfg, provider, conn, now=scan_time)
        assert [c.symbol for c in outcome.candidates] == ["GAP"]
        gap = outcome.candidates[0]
        assert gap.gap_pct == pytest.approx(5.0)
        assert gap.catalyst is True

        from watchman.signals import load_focus_list

        assert load_focus_list(conn, scan_time.date()) == ["GAP"]

    def test_signals_from_focus_list_orb_long(self, cfg, universe):
        provider = gapper_provider()
        conn = connect(cfg.settings.data.db_path)
        scan_time = datetime.combine(date.today(), time(9, 0), tzinfo=ET)
        run_scan(cfg, provider, conn, now=scan_time)

        signal_time = datetime.combine(date.today(), time(9, 56), tzinfo=ET)
        result = run_signals(cfg, provider, conn, now=signal_time)
        assert result.actionable is False
        assert "NOT ACTIONABLE" in result.freshness_label
        orb = [s for s in result.signals if s.setup.startswith("orb")]
        assert len(orb) == 1
        s = orb[0]
        assert s.direction == "long"
        assert s.entry == pytest.approx(107.0)
        assert s.stop == pytest.approx(104.8)   # OR low w/ fixture wrap
        assert s.risk_reward == pytest.approx(2.0)
        assert s.shares > 0
        assert "opening range" in s.rationale
        # Phase 5: gates run LIVE against the paper book, nothing unenforced.
        assert result.unenforced_gates == set()
        assert result.open_day_positions == 1  # the signal was auto-taken
        assert result.day_equity is not None

    def test_signals_without_scan_demands_one(self, cfg, universe):
        provider = gapper_provider()
        conn = connect(cfg.settings.data.db_path)
        with pytest.raises(RuntimeError, match="run `watchman scan` first"):
            run_signals(cfg, provider, conn)

    def test_explicit_symbols_bypass_focus_list(self, cfg, universe):
        provider = gapper_provider()
        conn = connect(cfg.settings.data.db_path)
        signal_time = datetime.combine(date.today(), time(9, 56), tzinfo=ET)
        result = run_signals(cfg, provider, conn, symbols=["GAP"], now=signal_time)
        assert result.symbols_evaluated == ["GAP"]


class TestSignalsCLI:
    def test_cli_scan_and_signals_print_honestly(
        self, cfg, universe, monkeypatch, capsys, tmp_path
    ):
        import watchman.cli as cli

        provider = gapper_provider()
        monkeypatch.setattr(cli, "_make_provider", lambda _cfg: provider)

        # Freeze 'now' inside the runner to a mid-morning moment.
        signal_time = datetime.combine(date.today(), time(9, 56), tzinfo=ET)
        monkeypatch.setattr(
            "watchman.signals.runner._now_et", lambda now: now or signal_time
        )

        rc = cli.main(["--config-dir", str(cfg.config_dir), "scan"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "NOT ACTIONABLE" in out
        assert "GAP" in out
        assert "Focus list saved" in out

        rc = cli.main(["--config-dir", str(cfg.config_dir), "signals"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "orb-15m" in out
        assert "entry 107.00" in out
        assert "R:R 2.0" in out
        assert "confidence: n/a (no live signals logged yet)" in out
        assert "delayed data" in out  # the study-don't-chase reminder
        assert "Paper day book:" in out
        assert "Paper trades:" in out
