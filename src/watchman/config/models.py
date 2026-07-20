"""Pydantic models for Watchman configuration.

Two YAML files, one purpose each:
  - config/settings.yaml : tunables (universe, weights, costs, accounts)
  - config/risk.yaml     : hard limits, enforced in code

Secrets (API keys) come from environment variables only.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class DataConfig(BaseModel):
    provider: str = "yfinance"
    db_path: Path = Path("data/watchman.db")


class UniverseConfig(BaseModel):
    indices: list[str] = Field(default_factory=lambda: ["sp500", "nasdaq100"])
    extra_symbols: list[str] = Field(default_factory=list)
    exclude_symbols: list[str] = Field(default_factory=list)

    @field_validator("extra_symbols", "exclude_symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]


class AccountsConfig(BaseModel):
    day_trading_equity: float = Field(10_000.0, gt=0)
    long_term_equity: float = Field(10_000.0, gt=0)


class CostsConfig(BaseModel):
    """Trading costs applied to every backtest fill and every paper fill."""

    slippage_bps: float = Field(5.0, ge=0)
    spread_bps: float = Field(2.0, ge=0)
    commission_per_trade: float = Field(0.0, ge=0)

    @property
    def total_bps_per_side(self) -> float:
        return self.slippage_bps + self.spread_bps


class ScreenerConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "quality": 0.30,
            "growth": 0.25,
            "valuation": 0.25,
            "momentum": 0.20,
        }
    )
    watchlist_size: int = Field(15, ge=5, le=50)
    #: Cached fundamentals/statements older than this are refetched.
    data_max_age_days: int = Field(3, ge=1, le=30)

    @model_validator(mode="after")
    def _weights_valid(self) -> ScreenerConfig:
        expected = {"quality", "growth", "valuation", "momentum"}
        if set(self.weights) != expected:
            raise ValueError(f"screener weights must be exactly {sorted(expected)}")
        total = sum(self.weights.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"screener weights must sum to 1.0, got {total}")
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("screener weights must be non-negative")
        return self


class SignalsConfig(BaseModel):
    """Module B scanner/setup tunables. The hard risk limits stay in risk.yaml."""

    focus_size: int = Field(10, ge=1, le=10)  # spec: max 10 tickers per day
    min_price: float = Field(5.0, gt=0)
    min_avg_dollar_volume: float = Field(20_000_000.0, gt=0)
    min_gap_pct: float = Field(2.0, gt=0)
    min_rel_vol: float = Field(1.5, gt=0)
    intraday_interval: str = Field("5m")
    orb_minutes: int = Field(15, ge=5, le=60)

    @model_validator(mode="after")
    def _valid_interval(self) -> SignalsConfig:
        allowed = {"1m", "5m", "15m", "30m", "60m"}
        if self.intraday_interval not in allowed:
            raise ValueError(f"intraday_interval must be one of {sorted(allowed)}")
        minutes = int(self.intraday_interval.rstrip("m"))
        if self.orb_minutes % minutes != 0:
            raise ValueError(
                f"orb_minutes ({self.orb_minutes}) must be a multiple of the "
                f"intraday interval ({minutes}m)"
            )
        return self


class ReportConfig(BaseModel):
    output_dir: Path = Path("data/reports")


class Settings(BaseModel):
    data: DataConfig = Field(default_factory=DataConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    screener: ScreenerConfig = Field(default_factory=ScreenerConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


class RiskConfig(BaseModel):
    """Hard limits. Enforced in code, not suggestions."""

    max_risk_per_trade_pct: float = Field(1.0, gt=0, le=5.0)
    max_concurrent_day_positions: int = Field(3, ge=1, le=10)
    daily_circuit_breaker_pct: float = Field(-2.0, lt=0)
    min_risk_reward: float = Field(2.0, ge=1.0)


class ProviderKeys(BaseModel):
    """API keys, read from environment variables only. May all be empty."""

    polygon: str | None = None
    finnhub: str | None = None
    fmp: str | None = None

    @classmethod
    def from_env(cls) -> ProviderKeys:
        def get(name: str) -> str | None:
            v = os.environ.get(name, "").strip()
            return v or None

        return cls(
            polygon=get("WATCHMAN_POLYGON_KEY"),
            finnhub=get("WATCHMAN_FINNHUB_KEY"),
            fmp=get("WATCHMAN_FMP_KEY"),
        )


class WatchmanConfig(BaseModel):
    settings: Settings
    risk: RiskConfig
    keys: ProviderKeys
    config_dir: Path


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


def load_config(config_dir: str | Path | None = None) -> WatchmanConfig:
    """Load settings.yaml + risk.yaml from config_dir and keys from the environment.

    Resolution order for config_dir: explicit argument, $WATCHMAN_CONFIG_DIR,
    ./config relative to the current working directory. Missing files fall back
    to model defaults so a bare checkout still runs.
    """
    if config_dir is None:
        config_dir = os.environ.get("WATCHMAN_CONFIG_DIR") or "config"
    config_dir = Path(config_dir)

    settings = Settings.model_validate(_read_yaml(config_dir / "settings.yaml"))
    risk = RiskConfig.model_validate(_read_yaml(config_dir / "risk.yaml"))
    keys = ProviderKeys.from_env()
    return WatchmanConfig(settings=settings, risk=risk, keys=keys, config_dir=config_dir)
