"""Pre-market scanner (8:00-9:25 ET): gap %, relative volume, float, ATR,
news-catalyst flag -> focus list of at most `focus_size` (<=10) tickers.

Honesty notes:
- Free data has no historical pre-market volume, so relative volume is
  approximated as premarket volume vs. TYPICAL_PREMARKET_FRACTION of the
  20-day average daily volume. The constant is explicit and shown; a paid
  real-time feed can replace it with a real baseline later.
- Catalyst is None (shown as '?') when the provider can't serve news —
  unknown is unknown.
- The scan universe is the configured index universe: gappy micro-caps
  outside it will NOT appear until a broader real-time feed exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from watchman.config import SignalsConfig
from watchman.data.provider import MARKET_OPEN, AsOfView
from watchman.signals.indicators import atr

TYPICAL_PREMARKET_FRACTION = 0.05  # premarket usually ~5% of a day's volume
AVG_VOLUME_WINDOW = 20
NEWS_CATALYST_HOURS = 18


@dataclass(frozen=True)
class ScannerInput:
    """Everything the scanner needs about one symbol, pre-fetched."""

    symbol: str
    prev_close: float
    last_price: float           # latest pre-market trade price
    premarket_volume: float
    avg_daily_volume: float     # 20-day average shares/day
    atr: float | None
    float_shares: float | None
    catalyst: bool | None       # None = news unavailable, shown as '?'


@dataclass(frozen=True)
class ScannerCandidate:
    symbol: str
    gap_pct: float
    rel_vol: float
    premarket_volume: float
    last_price: float
    atr_pct: float | None       # ATR as % of prev close
    float_shares: float | None
    catalyst: bool | None
    score: float                # attention score = |gap%| x rel_vol


@dataclass
class ScanOutcome:
    candidates: list[ScannerCandidate]   # ranked, len <= focus_size
    scanned: int
    rejected: dict[str, int]             # reason -> count
    failures: dict[str, str]             # symbol -> error


def evaluate_candidate(
    inp: ScannerInput, cfg: SignalsConfig
) -> ScannerCandidate | str:
    """One symbol through the gates; returns a candidate or the reject reason."""
    if inp.prev_close <= 0 or inp.last_price <= 0:
        return "bad price data"
    if inp.last_price < cfg.min_price:
        return f"price < ${cfg.min_price:.0f}"
    dollar_volume = inp.avg_daily_volume * inp.prev_close
    if dollar_volume < cfg.min_avg_dollar_volume:
        return "illiquid (avg $ volume)"
    gap_pct = (inp.last_price / inp.prev_close - 1) * 100
    if abs(gap_pct) < cfg.min_gap_pct:
        return f"|gap| < {cfg.min_gap_pct}%"
    if inp.avg_daily_volume <= 0:
        return "no volume history"
    rel_vol = inp.premarket_volume / (inp.avg_daily_volume * TYPICAL_PREMARKET_FRACTION)
    if rel_vol < cfg.min_rel_vol:
        return f"rel vol < {cfg.min_rel_vol}x"
    return ScannerCandidate(
        symbol=inp.symbol,
        gap_pct=gap_pct,
        rel_vol=rel_vol,
        premarket_volume=inp.premarket_volume,
        last_price=inp.last_price,
        atr_pct=(inp.atr / inp.prev_close * 100) if inp.atr else None,
        float_shares=inp.float_shares,
        catalyst=inp.catalyst,
        score=abs(gap_pct) * rel_vol,
    )


def scan_premarket(
    inputs: list[ScannerInput],
    cfg: SignalsConfig,
    failures: dict[str, str] | None = None,
) -> ScanOutcome:
    candidates: list[ScannerCandidate] = []
    rejected: dict[str, int] = {}
    for inp in inputs:
        outcome = evaluate_candidate(inp, cfg)
        if isinstance(outcome, str):
            rejected[outcome] = rejected.get(outcome, 0) + 1
        else:
            candidates.append(outcome)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return ScanOutcome(
        candidates=candidates[: cfg.focus_size],
        scanned=len(inputs),
        rejected=rejected,
        failures=failures or {},
    )


def build_scanner_input(view: AsOfView, symbol: str) -> ScannerInput:
    """Assemble one symbol's scanner input through the pinned view."""
    daily = view.daily_bars(symbol, start=view.as_of - timedelta(days=90))
    if len(daily) < AVG_VOLUME_WINDOW + 1:
        raise ValueError(f"not enough daily history ({len(daily)} bars)")
    prev_close = float(daily["close"].iloc[-1])
    avg_volume = float(daily["volume"].tail(AVG_VOLUME_WINDOW).mean())

    intraday = view.intraday_bars(symbol, "1m", days=1, include_premarket=True)
    today = view.as_of.date()
    today_bars = intraday.loc[intraday.index.date == today] if not intraday.empty else intraday
    if today_bars.empty:
        raise ValueError("no bars yet today (pre-market may not have started)")
    last_price = float(today_bars["close"].iloc[-1])
    pre_bars = today_bars.loc[today_bars.index.time < MARKET_OPEN]
    premarket_volume = float(pre_bars["volume"].sum())

    catalyst: bool | None = None
    try:
        items = view.news(symbol)
        cutoff = view.as_of - timedelta(hours=NEWS_CATALYST_HOURS)
        catalyst = any(item.published_at.astimezone(cutoff.tzinfo) >= cutoff for item in items)
    except NotImplementedError:
        catalyst = None

    float_shares: float | None = None
    try:
        float_shares = view.fundamentals(symbol).float_shares
    except Exception:
        float_shares = None  # fundamentals are optional for the scan

    return ScannerInput(
        symbol=symbol,
        prev_close=prev_close,
        last_price=last_price,
        premarket_volume=premarket_volume,
        avg_daily_volume=avg_volume,
        atr=atr(daily),
        float_shares=float_shares,
        catalyst=catalyst,
    )
