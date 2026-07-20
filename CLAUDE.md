# Watchman — architecture and conventions

Personal AI stock analysis and signal system for a user with an accounting /
business-management / finance background. Paper trading only. Credibility
through statistical honesty. Read this before changing anything.

## Non-negotiable principles (enforced in code, not vibes)

1. **No real-money execution.** v1 is paper-only; the broker layer (Module E)
   starts read-only when it exists at all.
2. **Statistical honesty.** Every signal type displays backtested AND
   live-paper win rate, expectancy, and sample size wherever it appears.
   A backtest with Sharpe > 3 or win rate > 75% is treated as a probable bug
   (lookahead, survivorship, overfitting) and flagged to the user — never
   celebrated.
3. **No lookahead bias.** All strategy/screener/backtest code reaches market
   data ONLY through `AsOfView` (src/watchman/data/provider.py), which
   physically clips reads to what was knowable at its pinned moment.
   `tests/test_lookahead_canary.py` proves this. Never delete, skip, or
   weaken the canary to make something else pass.
4. **Costs are real.** `costs` in settings.yaml (slippage default 5 bps,
   spread, commission) must be applied to every backtest and paper fill.
   Phase 3 adds a test proving costs are applied; keep it green.
5. **Risk first.** Hard limits live in `config/risk.yaml` and are enforced by
   `RiskConfig` validation + `watchman.risk` sizing math. Position size =
   risk dollars / (entry − stop), capped at account equity (no leverage).
6. **Stay legitimate.** Official/free data sources only (yfinance now;
   Polygon/Finnhub/FMP via env keys later). Never scrape a broker, never use
   reverse-engineered private APIs, never hardcode credentials. Keys come
   from env vars only (`ProviderKeys.from_env`; see .env.example).

## Layout

```
config/settings.yaml      tunables: universe, weights, costs, accounts, provider
config/risk.yaml          hard limits (loosening one deserves a written why)
src/watchman/
  config/models.py        pydantic v2 models; load_config(config_dir)
  data/provider.py        DataProvider ABC, Freshness, Fundamentals, AsOfView,
                          LookaheadError, normalize_bars — the honesty core
  data/yfinance_provider.py  EOD bars + latest-snapshot fundamentals
  data/cache.py           CachedBars: SQLite bar cache with coverage tracking
  data/universe.py        index membership from bundled snapshots + refresh
  data/snapshots/         sp500.csv, nasdaq100.csv, meta.yaml (provenance)
  db.py                   SQLite schema/connection (cache now; ledger later)
  risk.py                 shares_for_risk, risk_reward
  cli.py                  argparse CLI: universe/fetch/screen/backtest live
  backtest/               Module C (Phase 3):
    engine.py             event-driven loop: pinned views, next-open fills, costs
    metrics.py            CAGR/Sharpe/Sortino/maxDD/win rate/expectancy +
                          honesty_flags (Sharpe>3, win>75% => probable bug)
    walkforward.py        opt window N -> test window N+1; OOS is the product
    decile.py             momentum-only decile backtest w/ mandatory disclaimers
    strategies.py         BuyAndHold + MACross demo strategies
  broker/adapter.py       Module E contract: READ-ONLY ABC (no order methods,
                          by design) + BROKER_REGISTRY official-API gate
  screener/               Module A (Phase 2):
    metrics.py            pure metric extraction; None = honestly unknown
    scoring.py            sector-relative valuation, percentile ranks, composite
    thesis.py             plain-English theses citing actual numbers
    store.py              run persistence + deteriorator rules (explicit consts)
    runner.py             orchestration; all data via AsOfView(now)
  signals|backtest|paper|broker|report/   Phase 3–5 modules (docstring stubs)
tests/                    ALL tests run offline; yfinance is mocked
  screener_fixtures.py    synthetic-company builders (CompanyProvider)
```

## Conventions

- Python 3.11+, type hints everywhere, pydantic v2 (`model_validate`, not v1
  idioms), ruff (line length 100) clean before commit: `ruff check .`
- Symbols are yfinance-style uppercase: `BRK-B`, never `BRK.B`
  (`universe.normalize_symbol`).
- Daily bars: DataFrame `[open, high, low, close, adj_close, volume]`,
  tz-naive normalized DatetimeIndex named `date` = the US trading session
  date. `normalize_bars` coerces any provider output into this shape.
- A daily bar for date D does not exist until D's session closes (16:00 ET).
  `AsOfView` pinned intraday on D therefore serves through D−1. Naive
  datetimes are interpreted as US/Eastern.
- Tests must run offline. Anything that would touch the network gets a fake
  (see `FakeTicker` in tests/test_yfinance_provider.py, `SyntheticProvider`
  in tests/conftest.py).
- Runtime artifacts (SQLite db, reports) go under `data/` (gitignored);
  path configurable in settings.yaml.

## Data honesty notes (carry these into later phases)

- **yfinance fundamentals are NOT point-in-time** — only the latest snapshot.
  `Fundamentals.point_in_time=False` records this, and `AsOfView.fundamentals`
  raises `LookaheadError` rather than serve them for a historical timestamp.
  Consequence: the Phase 3 backtest of Module A's composite score must either
  use price/momentum pillars only, or clearly disclose the fundamentals
  limitation. Do not quietly backtest current fundamentals against old prices.
- **Universe snapshots are current membership**, so historical backtests over
  them carry survivorship bias. State this prominently in backtest output.
  Snapshots were bundled from model knowledge (build sandbox had no market
  data access) — `watchman universe --refresh` rebuilds from Wikipedia.
- **Freshness labels**: `Freshness.EOD/DELAYED/REALTIME` flows from provider
  to signal. Module B must stamp anything non-realtime
  `DELAYED — NOT ACTIONABLE`, not pretend.

## Build phases (pause for user sign-off after each)

1. ✅ Skeleton, config, data layer + yfinance, CLAUDE.md, tests green.
2. ✅ Module A screener: four-pillar composite (Quality/Growth/Valuation/
   Momentum), ranked watchlist w/ theses citing numbers, deteriorator flags.
   Screener notes: yfinance statements give ~4 fiscal years, so "3-5y CAGR"
   is a ~3y CAGR (labeled "~3y" in output). ROIC prefers the provider's
   Invested Capital line, falls back to debt+equity−cash. Valuation is
   sector-relative (median of ≥5 peers, else universe median). Missing
   pillars renormalize weights rather than scoring zero; coverage is shown.
   Deteriorator thresholds are explicit constants in screener/store.py.
3. ✅ Module C event-driven backtester, walk-forward validation; decile
   backtest of Module A score with honest warts. Lookahead canary at engine
   level, known-answer test on synthetic data, costs-applied test — all in
   tests/test_backtest_engine.py; never weaken them.
   Engine notes: strategies get an AsOfView pinned at each session's close;
   orders fill at the NEXT session's open with costs; long-only, no leverage,
   dividends not credited in engine cash (decile backtest uses adj_close and
   does include them). Decile backtest is MOMENTUM-ONLY (fundamentals aren't
   point-in-time) and prints survivorship/momentum-only disclaimers on every
   run — those disclaimers are load-bearing, keep them.
4. Module B pre-market scanner + ORB / VWAP reclaim-reject / rel-vol
   continuation setup classes; every signal: entry/stop/targets, R:R ≥ 2,
   size per risk config, confidence = rolling live win rate, rationale.
5. Module D paper engine + signal ledger (every signal's outcome logged;
   30/90-day live win rates shown everywhere) + self-contained HTML report.
6. Polish: README, cron/Task Scheduler instructions, first-90-days checklist.

## Integration roadmap (stay legitimate)

Requests to hook up external platforms are answered by principle 6 and the
BROKER_REGISTRY in broker/adapter.py — that registry is the single source of
truth, keep it accurate:

- **Yahoo Finance**: connected today via yfinance (free, EOD; quotes delayed).
- **Real-time data**: Polygon/Finnhub/FMP env-key slots; Module B (Phase 4)
  consumes whatever is configured and labels anything non-realtime
  `DELAYED — NOT ACTIONABLE`.
- **TradingView**: no public consumption API. Legitimate paths only:
  `watchman screen --export-tv FILE` writes an importable watchlist; later,
  inbound TradingView alert webhooks (their official feature) can feed the
  signal ledger. Never scrape TradingView.
- **Wealthsimple / Robinhood / Webull**: NO official trading API. Watchman
  will never scrape them or use reverse-engineered endpoints — they stay
  manual-execution dashboards. If an official API ships, re-evaluate.
- **IBKR / Tradier / Schwab / Alpaca**: official APIs; Module E adapter
  candidates (read-only first; Alpaca is the likely first adapter because its
  paper-trading API matches Watchman's paper-first design).
- **Order placement**: the v1 BrokerAdapter interface has no order methods on
  purpose. Real-money execution is gated behind the first-90-days evaluation
  and an explicit user decision, never a code change smuggled into a phase.

## Commands

```bash
.venv/bin/python -m pytest      # run tests (offline)
.venv/bin/ruff check .          # lint
watchman universe [--refresh]   # resolved universe / rebuild snapshots
watchman fetch AAPL --days 365  # cache daily bars
watchman screen [--top N] [--limit K] [--export-tv FILE]  # Module A watchlist
watchman backtest ma-cross [--symbol SPY] [--years 6]     # walk-forward demo
watchman backtest momentum-decile [--years 6] [--deciles 10] [--limit K]
```
