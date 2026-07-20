"""Module D end-to-end: signals auto-taken, outcomes resolved pessimistically,
and the risk gates running LIVE against paper P&L across runs."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
import yaml
from tests.intraday_fixtures import IntradayProvider, bars_from_closes, daily_history

from watchman.data.provider import ET, AsOfView
from watchman.db import connect
from watchman.paper import DAY, PaperBook, SignalLedger, resolve_open_signals
from watchman.signals import run_signals

TODAY = date.today()


def at(hh: int, mm: int) -> datetime:
    return datetime.combine(TODAY, time(hh, mm), tzinfo=ET)


@pytest.fixture
def cfg(tmp_path):
    from watchman.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    with open("config/settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    settings["data"]["db_path"] = str(tmp_path / "flow.db")
    (cfg_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
    with open("config/risk.yaml", encoding="utf-8") as f:
        (cfg_dir / "risk.yaml").write_text(f.read())
    return load_config(cfg_dir)


def orb_provider(post_breakout: list[float]) -> IntradayProvider:
    """ORB long triggers at 9:55 (close 107 > OR high 106.2); bars after that
    follow `post_breakout` so tests control the outcome."""
    session = bars_from_closes(
        [105.0, 106.0, 105.5, 105.8, 107.0, *post_breakout],
        TODAY, start=time(9, 30), interval_minutes=5, volume=800_000.0,
    )
    return IntradayProvider(
        {"GAP": {"daily": daily_history(prev_close=100.0), "intraday": session}}
    )


class TestAutoTakeAndResolve:
    def test_signal_taken_then_target_hit(self, cfg):
        # Entry 107, stop 104.8 (risk 2.2) -> target1 111.4. Bar closing 111.6
        # (high 111.8) crosses it.
        provider = orb_provider([109.0, 110.5, 111.6])
        conn = connect(cfg.settings.data.db_path)

        r1 = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        assert len(r1.signals) == 1
        assert r1.open_day_positions == 1
        assert any("taken @" in n for n in r1.taken_notes)

        # Immediate re-run: the same trigger fires again, but the ledger's
        # cross-run dedup blocks a second paper entry.
        r1b = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        assert r1b.signals == []
        assert any("duplicate" in rej.reason for rej in r1b.rejected)
        assert r1b.open_day_positions == 1

        r2 = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(10, 30))
        assert any("TARGET" in n for n in r2.resolution_notes)
        assert r2.open_day_positions == 0
        ledger = SignalLedger(conn)
        row = ledger.session_signals(TODAY)[0]
        assert row.status == "target"
        assert row.pnl is not None and row.pnl > 0

    def test_stop_hit_books_a_loss(self, cfg):
        provider = orb_provider([106.0, 104.5])  # low 104.3 < stop 104.8
        conn = connect(cfg.settings.data.db_path)
        run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        r2 = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(10, 30))
        assert any("STOPPED" in n for n in r2.resolution_notes)
        row = SignalLedger(conn).session_signals(TODAY)[0]
        assert row.status == "stopped"
        assert row.pnl is not None and row.pnl < 0

    def test_same_bar_stop_and_target_counts_as_stopped(self, cfg):
        # ONE bar (after entry) spans both stop (104.8) and target (111.4):
        # the pessimistic rule must book it as stopped, not targeted.
        session = bars_from_closes(
            [105.0, 106.0, 105.5, 105.8, 107.0], TODAY, start=time(9, 30),
            interval_minutes=5, volume=800_000.0,
        )
        import pandas as pd

        wild = pd.DataFrame(
            {"open": [107.0], "high": [112.0], "low": [104.0], "close": [108.0],
             "volume": [900_000.0]},
            index=pd.DatetimeIndex(
                [datetime.combine(TODAY, time(10, 0), tzinfo=ET)], name="timestamp"
            ),
        )
        provider = IntradayProvider(
            {"GAP": {"daily": daily_history(prev_close=100.0),
                     "intraday": pd.concat([session, wild])}}
        )
        conn = connect(cfg.settings.data.db_path)
        run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        r2 = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(10, 30))
        assert any("STOPPED" in n for n in r2.resolution_notes)  # pessimistic

    def test_expiry_at_session_close(self, cfg):
        # Price meanders: neither stop nor target by 16:00 -> expired at close.
        provider = orb_provider([107.5, 107.2, 107.8])
        conn = connect(cfg.settings.data.db_path)
        run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        book = PaperBook(conn, DAY, cfg.settings.costs,
                         cfg.settings.accounts.day_trading_equity)
        ledger = SignalLedger(conn)
        view = AsOfView(provider, at(16, 30))
        notes = resolve_open_signals(book, ledger, view,
                                     cfg.settings.signals.intraday_interval)
        assert any("EXPIRED" in n for n in notes)
        assert SignalLedger(conn).session_signals(TODAY)[0].status == "expired"


class TestLiveGates:
    def test_circuit_breaker_trips_on_real_paper_loss(self, cfg, monkeypatch):
        """A big stopped loss must push day P&L past -2% and block the next
        signal with a CIRCUIT BREAKER rejection — live, not hypothetical."""
        provider = orb_provider([106.0, 104.5])
        conn = connect(cfg.settings.data.db_path)
        run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))

        # Seed yesterday's closing mark well above today's equity, so the
        # stopped loss reads as > 2% on the day. (The 104.5 bar also freshly
        # breaks the OR low, so a new short draft exists for the breaker to
        # reject.)
        from datetime import timedelta

        yesterday = at(16, 0) - timedelta(days=1)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO equity_marks (account, ts, equity, cash)"
                " VALUES ('day', ?, ?, ?)",
                (yesterday.isoformat(), 10_200.0, 10_200.0),
            )

        r2 = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(10, 30))
        assert r2.day_pnl_pct is not None and r2.day_pnl_pct <= -2.0
        breaker_rejects = [r for r in r2.rejected if "CIRCUIT BREAKER" in r.reason]
        assert breaker_rejects, f"expected breaker rejection, got {r2.rejected}"
        assert r2.unenforced_gates == set()  # gates are LIVE now

    def test_max_positions_gate_counts_real_positions(self, cfg):
        provider = orb_provider([109.0, 110.0])
        conn = connect(cfg.settings.data.db_path)
        # Occupy all 3 slots with dummy open positions.
        book = PaperBook(conn, DAY, cfg.settings.costs,
                         cfg.settings.accounts.day_trading_equity)
        for i, sym in enumerate(["AAA", "BBB", "CCC"]):
            book.open_position(at(9, 40), sym, "long", 1, 10.0 + i)
        r = run_signals(cfg, provider, conn, symbols=["GAP"], now=at(9, 56))
        assert r.signals == []
        assert any("max concurrent" in rej.reason for rej in r.rejected)
