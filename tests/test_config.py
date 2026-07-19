"""Config loading: YAML tunables, hard risk limits, env-only secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from watchman.config import ProviderKeys, RiskConfig, ScreenerConfig, load_config

REPO_CONFIG = Path(__file__).parent.parent / "config"


class TestRepoConfigFiles:
    """The checked-in YAML must always load and carry the agreed defaults."""

    def test_loads(self):
        cfg = load_config(REPO_CONFIG)
        assert cfg.settings.data.provider == "yfinance"

    def test_agreed_defaults(self):
        cfg = load_config(REPO_CONFIG)
        assert cfg.settings.accounts.day_trading_equity == 10_000.0
        assert cfg.settings.accounts.long_term_equity == 10_000.0
        assert cfg.settings.universe.indices == ["sp500", "nasdaq100"]
        assert cfg.settings.costs.slippage_bps == 5.0

    def test_hard_risk_limits(self):
        cfg = load_config(REPO_CONFIG)
        assert cfg.risk.max_risk_per_trade_pct == 1.0
        assert cfg.risk.max_concurrent_day_positions == 3
        assert cfg.risk.daily_circuit_breaker_pct == -2.0
        assert cfg.risk.min_risk_reward == 2.0


class TestValidation:
    def test_screener_weights_must_sum_to_one(self):
        with pytest.raises(ValidationError, match=r"sum to 1\.0"):
            ScreenerConfig(
                weights={"quality": 0.5, "growth": 0.5, "valuation": 0.5, "momentum": 0.5}
            )

    def test_screener_weights_must_name_all_pillars(self):
        with pytest.raises(ValidationError, match="exactly"):
            ScreenerConfig(weights={"quality": 1.0})

    def test_circuit_breaker_must_be_negative(self):
        with pytest.raises(ValidationError):
            RiskConfig(daily_circuit_breaker_pct=2.0)

    def test_risk_per_trade_capped(self):
        with pytest.raises(ValidationError):
            RiskConfig(max_risk_per_trade_pct=10.0)  # 10% per trade is not a tweak


class TestMissingFilesFallBackToDefaults:
    def test_empty_dir_loads_defaults(self, tmp_path):
        cfg = load_config(tmp_path)
        assert cfg.settings.accounts.day_trading_equity == 10_000.0
        assert cfg.risk.max_concurrent_day_positions == 3


class TestSecretsFromEnvOnly:
    def test_keys_read_from_env(self, monkeypatch):
        monkeypatch.setenv("WATCHMAN_POLYGON_KEY", "pk_test_123")
        monkeypatch.delenv("WATCHMAN_FINNHUB_KEY", raising=False)
        keys = ProviderKeys.from_env()
        assert keys.polygon == "pk_test_123"
        assert keys.finnhub is None

    def test_blank_env_value_is_none(self, monkeypatch):
        monkeypatch.setenv("WATCHMAN_FMP_KEY", "   ")
        assert ProviderKeys.from_env().fmp is None

    def test_no_key_material_in_yaml(self):
        """Nothing that looks like a credential may live in the config files."""
        for name in ("settings.yaml", "risk.yaml"):
            text = (REPO_CONFIG / name).read_text(encoding="utf-8").lower()
            for needle in ("api_key", "apikey", "secret", "token", "password"):
                assert needle not in text, f"{needle!r} found in {name}"
