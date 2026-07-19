# Watchman

Personal AI stock analysis and signal system. Two jobs:

1. **Find and rank opportunities** — a fundamentals-driven long-term screener
   (Module A) and an intraday signal engine (Module B).
2. **Prove whether its own signals actually work** — rigorous event-driven
   backtesting (Module C) and live paper trading with a permanent signal
   ledger (Module D) — *before any real money is ever involved*.

Its credibility comes from its honesty, not its optimism.

## Non-negotiables

- **No real-money execution.** v1 trades a simulated (paper) portfolio only.
  Broker integration, when it arrives, starts read-only.
- **Statistical honesty.** Every signal type always shows its backtested AND
  live-paper win rate, expectancy, and sample size. Backtests that look too
  good (Sharpe > 3, win rate > 75%) are treated as probable bugs, not genius.
- **No lookahead bias.** Signals may only use data that existed at signal
  time. `tests/test_lookahead_canary.py` proves this mechanically and is
  never skipped.
- **Costs are real.** Slippage (default 5 bps), spread, and commissions are
  applied to every backtest fill and every paper fill.
- **Risk first.** Hard limits live in `config/risk.yaml`: max 1% of the
  account risked per trade, max 3 concurrent day-trade positions, a daily
  circuit breaker, and 2:1 minimum reward:risk on any emitted signal.
- **Stay legitimate.** Official APIs and licensed/free data only. No broker
  scraping, no reverse-engineered private APIs, no credentials in the repo
  (env vars only — see `.env.example`).

## A note on day trading for real

Real-money day trading in the US requires **$25,000+ equity in a margin
account** under FINRA's pattern day trader rule. Paper trading has no such
limit — one more reason Watchman proves everything on paper first.

## Quickstart

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

watchman universe            # show the resolved screening universe
watchman universe --refresh  # rebuild index membership from Wikipedia (needs internet)
watchman fetch AAPL          # pull + cache a year of daily bars via yfinance
pytest                       # the whole suite runs offline
```

`watchman screen`, `scan`, `signals`, `backtest`, and `report` arrive in
later phases and currently say so instead of pretending.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Skeleton, config, data layer (yfinance), tests | ✅ done |
| 2 | Module A screener + ranked watchlist | pending sign-off |
| 3 | Module C backtester + honest Module A backtest | — |
| 4 | Module B scanner + three intraday setups | — |
| 5 | Module D paper engine, signal ledger, HTML report | — |
| 6 | Polish, scheduling, first-90-days checklist | — |

Architecture and conventions live in [CLAUDE.md](CLAUDE.md).
