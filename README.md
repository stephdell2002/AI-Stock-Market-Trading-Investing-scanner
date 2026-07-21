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

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
watchman --version
pytest                       # the whole suite runs offline
```

Copy `.env.example` to `.env` if you have a real-time data key (Polygon /
Finnhub / FMP) and set `data.provider` in `config/settings.yaml`. Without one,
Watchman runs on free yfinance EOD data and labels every intraday signal
`DELAYED — NOT ACTIONABLE`. See **[docs/realtime-data.md](docs/realtime-data.md)**
for wiring up a live feed and the (deliberately your-call) freshness setting.

## The daily rhythm

Watchman is a set of commands you run on the market's clock. Automate them with
the scheduler ([docs/scheduling.md](docs/scheduling.md)) or run them by hand:

```bash
# Before the open (8:00–9:25 ET)
watchman scan                     # gap/volume/float/ATR/news → focus list (≤10)
watchman report --brief morning   # dated plan for the day

# During the session (every ~5 min)
watchman signals                  # ORB / VWAP / rel-vol setups on completed bars;
                                  #   auto-taken as paper trades, outcomes logged

# After the close (~16:15 ET)
watchman signals                  # final resolve / expire
watchman report --brief evening   # dated recap with per-setup live win rates
watchman report                   # self-contained HTML dashboard → data/reports/

# Weekly / monthly
watchman screen                   # refresh the Module A watchlist + deteriorators
watchman report --rebalance-longterm   # monthly long-term paper rebalance
```

Every signal is logged in the ledger with its eventual outcome (target /
stopped / expired, with same-bar ambiguity resolved pessimistically), and each
setup's rolling 30/90-day **live** win rate feeds back into new signals'
confidence — the system grades its own homework.

## Command reference

```bash
watchman universe [--refresh] [--all]      # resolved screening universe
watchman fetch AAPL [--days 365]           # cache daily bars for one symbol
watchman screen [--top N] [--limit K] [--export-tv FILE]   # Module A watchlist
watchman backtest ma-cross [--symbol SPY] [--years 6]      # walk-forward demo
watchman backtest momentum-decile [--years 6] [--deciles 10] [--limit K]
watchman scan [--limit K]                  # pre-market focus list
watchman signals [--symbols A,B,C]         # evaluate setups + auto-take paper
watchman report [--brief morning|evening] [--rebalance-longterm [--force]]
```

The first `watchman screen` fetches fundamentals for the whole universe (10–20
min on yfinance); later runs use the SQLite cache. Global `--config-dir` points
at a different `config/` directory.

## Scheduling & evaluation

- **[docs/scheduling.md](docs/scheduling.md)** — run the daily rhythm
  unattended via **cron** (Linux/macOS, `scripts/watchman_run.sh` +
  `scripts/crontab.example`) or **Windows Task Scheduler**
  (`scripts/watchman_run.ps1`), including the all-important ET timezone note.
- **[docs/first-90-days.md](docs/first-90-days.md)** — the go/no-go checklist
  for deciding whether any signal has earned real capital. Read this before you
  ever think about funding the strategy; the default answer is "not yet."

## Connecting other platforms (the legitimate paths)

- **Yahoo Finance** — already the default data source (free, end-of-day).
- **Real-time data** — implemented for **Polygon, Finnhub, and FMP**. Add a key
  in `.env`, set `data.provider`, and Module B reads live bars/news; set
  `data.freshness: REALTIME` (when your plan warrants it) to drop the
  `DELAYED — NOT ACTIONABLE` label. Full guide:
  [docs/realtime-data.md](docs/realtime-data.md).
- **TradingView** — has no public data API, so Watchman never scrapes it.
  Instead: `watchman screen --export-tv` writes a watchlist file you import
  into TradingView; TradingView alert webhooks (official feature) can feed
  Watchman later.
- **Wealthsimple** — has NO official trading API. Per this project's rules
  Watchman will never scrape it; it stays a dashboard whose signals you act
  on manually in Wealthsimple. Same for Robinhood/Webull.
- **IBKR / Tradier / Schwab / Alpaca** — official APIs; these are the Module E
  adapter candidates, read-only first, and only when you provide credentials.
  The v1 `BrokerAdapter` interface has no order-placement method by design.

## Project layout

```
config/         settings.yaml (tunables) + risk.yaml (hard limits)
src/watchman/
  config/       pydantic models; keys from env vars only
  data/         DataProvider + AsOfView (the point-in-time honesty core),
                yfinance + Polygon/Finnhub/FMP providers, factory, SQLite
                caches, universe snapshots
  screener/     Module A: four-pillar composite, theses, deteriorators
  backtest/     Module C: event-driven engine, walk-forward, honest decile
  signals/      Module B: scanner, ORB/VWAP/rel-vol setups, gate engine
  paper/        Module D: paper books, signal ledger, auto-take + resolve
  report/       self-contained HTML dashboard + dated text briefs
  broker/       Module E contract (read-only) + official-API registry
scripts/        cron / Task Scheduler runners
docs/           scheduling + first-90-days evaluation
tests/          all offline; yfinance mocked; the lookahead canary
```

Architecture and conventions live in [CLAUDE.md](CLAUDE.md).

## Status — v1 complete

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Skeleton, config, data layer (yfinance), tests | ✅ done |
| 2 | Module A screener + ranked watchlist | ✅ done |
| 3 | Module C backtester + honest Module A backtest | ✅ done |
| 4 | Module B scanner + three intraday setups | ✅ done |
| 5 | Module D paper engine, signal ledger, HTML report | ✅ done |
| 6 | Polish: README, scheduling, first-90-days checklist | ✅ done |

v1 is paper-only by design. The next real-money question is answered by
[docs/first-90-days.md](docs/first-90-days.md), not by a code change.
