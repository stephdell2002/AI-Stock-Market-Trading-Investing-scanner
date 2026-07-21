"""Provider factory: selection, key requirement, freshness, and the
yfinance-fundamentals fallback for bars-only providers. Plus the integration
proof that a REALTIME provider makes signals actionable end-to-end."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
import yaml
from tests.intraday_fixtures import IntradayProvider, bars_from_closes, daily_history

from watchman.config import load_config
from watchman.data.provider import ET, DataProvider, Freshness, Fundamentals, StatementSet
from watchman.data.providers import (
    FundamentalsFallbackProvider,
    ProviderConfigError,
    make_provider,
)
from watchman.db import connect
from watchman.signals import run_signals


def cfg_with(tmp_path, **data_overrides):
    cfg = load_config("config")
    cfg.settings.data.provider = data_overrides.get("provider", "yfinance")
    cfg.settings.data.freshness = data_overrides.get("freshness")
    for attr in ("polygon", "finnhub", "fmp"):
        if attr in data_overrides:
            setattr(cfg.keys, attr, data_overrides[attr])
    return cfg


class TestSelection:
    def test_default_is_yfinance(self, tmp_path):
        assert make_provider(cfg_with(tmp_path)).name == "yfinance"

    def test_unknown_provider_rejected(self, tmp_path):
        with pytest.raises(ProviderConfigError, match="unknown data provider"):
            make_provider(cfg_with(tmp_path, provider="bogus"))

    @pytest.mark.parametrize(
        ("provider", "env_var"),
        [("polygon", "WATCHMAN_POLYGON_KEY"),
         ("finnhub", "WATCHMAN_FINNHUB_KEY"),
         ("fmp", "WATCHMAN_FMP_KEY")],
    )
    def test_missing_key_names_the_env_var(self, tmp_path, provider, env_var):
        with pytest.raises(ProviderConfigError, match=env_var):
            make_provider(cfg_with(tmp_path, provider=provider))

    def test_each_realtime_provider_constructs_with_a_key(self, tmp_path):
        for provider, key_attr in [("polygon", "polygon"), ("finnhub", "finnhub"),
                                   ("fmp", "fmp")]:
            p = make_provider(cfg_with(tmp_path, provider=provider, **{key_attr: "k"}))
            assert p.name.startswith(provider)


class TestFreshness:
    def test_defaults_to_delayed(self, tmp_path):
        p = make_provider(cfg_with(tmp_path, provider="polygon", polygon="k"))
        assert p.quote_freshness() == Freshness.DELAYED

    def test_realtime_override_flows_through(self, tmp_path):
        p = make_provider(
            cfg_with(tmp_path, provider="polygon", polygon="k", freshness="REALTIME")
        )
        assert p.quote_freshness() == Freshness.REALTIME

    def test_invalid_freshness_rejected_at_config_load(self, tmp_path):
        from pydantic import ValidationError

        from watchman.config import DataConfig

        with pytest.raises(ValidationError, match="freshness"):
            DataConfig(freshness="soon-ish")


class TestFundamentalsFallback:
    def _bars_only(self):
        """A provider that serves bars but raises NotImplementedError for
        fundamentals/statements — like Polygon."""

        class BarsOnly(DataProvider):
            name = "bars-only"

            def daily_bars(self, symbol, start, end):
                return daily_history(prev_close=100.0)

            def fundamentals(self, symbol):
                raise NotImplementedError

            def quote_freshness(self):
                return Freshness.REALTIME

        class FakeFundamentals(DataProvider):
            name = "fake-fundamentals"

            def daily_bars(self, symbol, start, end):
                raise AssertionError("bars must come from the primary")

            def fundamentals(self, symbol):
                return Fundamentals(symbol=symbol, fetched_at=datetime.now())

            def financial_statements(self, symbol):
                return StatementSet(
                    symbol=symbol, income=daily_history(100.0),
                    balance=daily_history(100.0), cashflow=daily_history(100.0),
                    fetched_at=datetime.now(),
                )

            def quote_freshness(self):
                return Freshness.EOD

        return FundamentalsFallbackProvider(BarsOnly(), fallback_factory=FakeFundamentals)

    def test_bars_and_freshness_come_from_primary(self):
        composite = self._bars_only()
        assert not composite.daily_bars("X", datetime(2024, 1, 1), datetime(2024, 2, 1)).empty
        assert composite.quote_freshness() == Freshness.REALTIME  # primary's

    def test_fundamentals_fall_back_when_primary_cant(self):
        composite = self._bars_only()
        assert composite.fundamentals("X").symbol == "X"          # from fallback
        assert composite.financial_statements("X").symbol == "X"  # from fallback

    def test_fallback_is_lazy(self):
        composite = self._bars_only()
        assert composite._fallback is None            # not built yet
        composite.fundamentals("X")
        assert composite._fallback is not None         # built on first need


class TestRealtimeMakesSignalsActionable:
    """The point of the whole feature: with a REALTIME provider, Module B
    signals lose the DELAYED — NOT ACTIONABLE label."""

    def _provider(self, freshness):
        today = date.today()
        session = bars_from_closes(
            [105.0, 106.0, 105.5, 105.8, 107.0], today, start=time(9, 30),
            interval_minutes=5, volume=800_000.0,
        )
        return IntradayProvider(
            {"GAP": {"daily": daily_history(prev_close=100.0), "intraday": session}},
            freshness=freshness,
        )

    def test_delayed_provider_labels_not_actionable(self, tmp_path):
        conn = connect(tmp_path / "d.db")
        provider = self._provider(Freshness.DELAYED)
        now = datetime.combine(date.today(), time(9, 56), tzinfo=ET)
        result = run_signals(load_config("config"), provider, conn,
                             symbols=["GAP"], now=now)
        assert result.actionable is False
        assert "NOT ACTIONABLE" in result.freshness_label
        assert result.signals and result.signals[0].actionable is False

    def test_realtime_provider_makes_signals_actionable(self, tmp_path):
        conn = connect(tmp_path / "r.db")
        provider = self._provider(Freshness.REALTIME)
        now = datetime.combine(date.today(), time(9, 56), tzinfo=ET)
        result = run_signals(load_config("config"), provider, conn,
                             symbols=["GAP"], now=now)
        assert result.actionable is True
        assert result.freshness_label == "REALTIME"
        assert result.signals and result.signals[0].actionable is True
        assert result.signals[0].freshness_label == "REALTIME"


class TestReportReflectsRealtime:
    def test_report_drops_not_actionable_when_realtime(self, tmp_path):
        from watchman.data.provider import AsOfView
        from watchman.report import build_report

        cfg = load_config("config")
        settings_dir = tmp_path / "config"
        settings_dir.mkdir()
        with open("config/settings.yaml", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        settings["data"]["db_path"] = str(tmp_path / "r.db")
        (settings_dir / "settings.yaml").write_text(yaml.safe_dump(settings))
        with open("config/risk.yaml", encoding="utf-8") as f:
            (settings_dir / "risk.yaml").write_text(f.read())
        cfg = load_config(settings_dir)

        today = date.today()
        session = bars_from_closes(
            [105.0, 106.0, 105.5, 105.8, 107.0, 109.0, 110.5, 111.6], today,
            start=time(9, 30), interval_minutes=5, volume=800_000.0,
        )
        provider = IntradayProvider(
            {"GAP": {"daily": daily_history(prev_close=100.0), "intraday": session}},
            freshness=Freshness.REALTIME,
        )
        conn = connect(cfg.settings.data.db_path)
        run_signals(cfg, provider, conn, symbols=["GAP"],
                    now=datetime.combine(today, time(9, 56), tzinfo=ET))
        now = datetime.combine(today, time(16, 30), tzinfo=ET)
        html = build_report(cfg, conn, AsOfView(provider, now), now)
        assert "REALTIME" in html
        assert "NOT ACTIONABLE" not in html  # the delayed caveat is gone
