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
watchman screen              # Module A: ranked watchlist with theses (first run
                             #   fetches the whole universe: 10-20 min, then cached)
watchman screen --limit 30   # quick trial over the first 30 universe names
watchman screen --export-tv tv.txt   # also write a TradingView-importable watchlist
watchman backtest ma-cross           # walk-forward demo (SPY, out-of-sample first)
watchman backtest momentum-decile    # honest decile backtest of the Module A score
pytest                       # the whole suite runs offline
```

`watchman scan`, `signals`, and `report` arrive in later phases and
currently say so instead of pretending.

## Connecting other platforms (the legitimate paths)

- **Yahoo Finance** — already the default data source (free, end-of-day).
- **Real-time data** — add a Polygon/Finnhub/FMP key in `.env`; Module B will
  use it and stop labeling signals `DELAYED — NOT ACTIONABLE`.
- **TradingView** — has no public data API, so Watchman never scrapes it.
  Instead: `watchman screen --export-tv` writes a watchlist file you import
  into TradingView; TradingView alert webhooks (official feature) can feed
  Watchman later.
- **Wealthsimple** — has NO official trading API. Per this project's rules
  Watchman will never scrape it; it stays a dashboard whose signals you act
  on manually in Wealthsimple. Same for Robinhood/Webull.
- **IBKR / Tradier / Schwab / Alpaca** — official APIs; these are the Module E
  adapter candidates, read-only first, and only when you provide credentials.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Skeleton, config, data layer (yfinance), tests | ✅ done |
| 2 | Module A screener + ranked watchlist | ✅ done |
| 3 | Module C backtester + honest Module A backtest | ✅ done |
| 4 | Module B scanner + three intraday setups | pending sign-off |
| 5 | Module D paper engine, signal ledger, HTML report | — |
| 6 | Polish, scheduling, first-90-days checklist | — |

Architecture and conventions live in [CLAUDE.md](CLAUDE.md).
