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
  data/provider.py        DataProvider ABC, Freshness, Fundamentals, NewsItem,
                          AsOfView, LookaheadError, normalize_* — the honesty core
  data/yfinance_provider.py  EOD bars + latest-snapshot fundamentals
  data/http.py            stdlib JSON-over-HTTPS helper (retries, proxy-aware)
  data/rest_common.py     shared REST bits: freshness, session filter, intervals
  data/finnhub_provider.py / polygon_provider.py / fmp_provider.py
                          real-time REST providers (bars/news/fundamentals)
  data/providers.py       make_provider factory + FundamentalsFallbackProvider
                          (bars-only source + yfinance fundamentals)
  data/cache.py           CachedBars: SQLite bar cache with coverage tracking
  data/universe.py        index membership from bundled snapshots + refresh
  data/snapshots/         sp500.csv, nasdaq100.csv, meta.yaml (provenance)
  db.py                   SQLite schema/connection (cache now; ledger later)
  risk.py                 shares_for_risk, risk_reward
  cli.py                  argparse CLI: universe/fetch/screen/backtest/scan/
                          signals live; report stubbed (Phase 5)
  signals/                Module B (Phase 4):
    model.py              Signal (all spec fields), RejectedSignal, GateState,
                          ConfidenceSource protocol (ledger plugs in Phase 5)
    indicators.py         ATR, session VWAP, relative volume, session slicing
    scanner.py            pre-market gates + ranking -> focus list (max 10)
    setups.py             ORB / VWAP reclaim-reject / rel-vol continuation
    engine.py             SignalEngine.finalize: every gate in one place
    runner.py             run_scan (persists focus list) + run_signals
  backtest/               Module C (Phase 3):
    engine.py             event-driven loop: pinned views, next-open fills, costs
    metrics.py            CAGR/Sharpe/Sortino/maxDD/win rate/expectancy +
                          honesty_flags (Sharpe>3, win>75% => probable bug)
    walkforward.py        opt window N -> test window N+1; OOS is the product
    decile.py             momentum-only decile backtest w/ mandatory disclaimers
    strategies.py         BuyAndHold + MACross demo strategies
  broker/adapter.py       Module E contract: READ-ONLY ABC (no order methods,
                          by design) + BROKER_REGISTRY official-API gate
  debuts/                 Module F (new-listing scanner):
    comparables.py        size buckets, post-IPO performance, cohort builder
    analysis.py           deterministic verdict scorecard (pure; documented
                          constants; skeptical prior — never says BUY)
    runner.py             run_debuts: calendar -> cohorts -> verdicts, persisted
  screener/               Module A (Phase 2):
    metrics.py            pure metric extraction; None = honestly unknown
    scoring.py            sector-relative valuation, percentile ranks, composite
    thesis.py             plain-English theses citing actual numbers
    store.py              run persistence + deteriorator rules (explicit consts)
    runner.py             orchestration; all data via AsOfView(now)
  paper/                  Module D (Phase 5):
    book.py               PaperBook: slippage-honest fills, cash reconciles
    ledger.py             SignalLedger: outcomes, 30/90d stats, ConfidenceSource,
                          baselines + divergence warnings
    simulate.py           auto_take + resolve (pessimistic same-bar rule)
    longterm.py           monthly Module A rebalance + watchlist_changes
  report/                 Module D reporting (Phase 5):
    html.py               self-contained HTML dashboard (inline SVG curves)
    briefs.py             dated morning brief / evening recap text files
tests/                    ALL tests run offline; yfinance is mocked
  screener_fixtures.py    synthetic-company builders (CompanyProvider)
  intraday_fixtures.py    intraday bar / IntradayProvider builders (Module B/D)
scripts/                  watchman_run.sh + .ps1 (phase runner), crontab.example
docs/                     scheduling.md, first-90-days.md
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
- **IPOs are the thinnest-data case (Module F, debuts/).** A brand-new ticker
  has ~one filing and no track record — the AsOfView/backtest/ledger discipline
  barely applies. The debut verdict must therefore stay a DETERMINISTIC
  scorecard over the comparable-cohort base rate + the listing's own numbers
  (analysis.py, pure function), never a model narrative. It encodes documented
  IPO regularities as explicit constants (skeptical prior; post-pop penalty;
  unprofitable penalty), always shows cohort sample size + match basis, caps at
  CAUTION when data is absent, and NEVER emits "BUY". The IPO calendar
  (Finnhub/FMP `ipo_calendar`) is a live-only facility — AsOfView passes it
  through unclipped (upcoming listings are legitimately known in advance) and
  the runner always pins at now. Keep the standing cautions load-bearing.

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
4. ✅ Module B pre-market scanner + ORB / VWAP reclaim-reject / rel-vol
   continuation setup classes; every signal: entry/stop/targets, R:R ≥ 2,
   size per risk config, confidence = rolling live win rate, rationale.
   Module B notes: setups only trigger on the LAST completed intraday bar
   (stale triggers are not fresh signals); AsOfView hides in-progress bars
   (a 5m bar starting 10:00 exists only from 10:05). SignalEngine.finalize
   is the single gate path — R:R, sizing, circuit breaker, max positions,
   dedup — with breaker/positions reported as UNENFORCED until Module D's
   paper book supplies live P&L. Scanner rel-vol uses an explicit
   TYPICAL_PREMARKET_FRACTION=0.05 approximation (no free historical
   premarket baseline); news catalyst is None (shown '?') when the provider
   can't serve news. ConfidenceSource protocol is how the Phase 5 ledger
   plugs rolling live win rates into signals.
5. ✅ Module D paper engine + signal ledger (every signal's outcome logged;
   30/90-day live win rates shown everywhere) + self-contained HTML report.
   Module D notes: run_signals resolves open positions FIRST, then gates run
   LIVE (breaker vs real day P&L, real open-position count), then finalized
   signals are auto-taken with slippage; ledger dedup prevents re-entry
   across runs. Same-bar stop+target resolves as STOPPED (pessimistic) —
   keep it that way. Realized PnL carries both round-trip commissions so
   cash always reconciles to start + sum(pnl). Long-term book: monthly
   equal-weight into screener top-N; existing holdings are NOT resized
   (documented drift). Divergence warnings need a recorded baseline
   (setup_baselines) + >=20 resolved signals. Reports are self-contained
   HTML (inline SVG, no scripts) + dated text briefs.
6. ✅ Polish: full README (daily rhythm, layout), scheduling scripts
   (scripts/watchman_run.sh + .ps1, crontab.example) with docs/scheduling.md,
   and docs/first-90-days.md (the go/no-go checklist for real capital).
   Scheduler notes: one runner script, phases morning/intraday/evening/weekly/
   monthly; each phase runs all its steps even if one fails (tallied into the
   exit code) and logs to data/logs/<phase>-<date>.log; ET timezone is the
   documented gotcha. v1 is complete (version 1.0.0) and paper-only — the
   real-money decision is docs/first-90-days.md, never a code change.

## Integration roadmap (stay legitimate)

Requests to hook up external platforms are answered by principle 6 and the
BROKER_REGISTRY in broker/adapter.py — that registry is the single source of
truth, keep it accurate:

- **Yahoo Finance**: connected today via yfinance (free, EOD; quotes delayed).
- **Real-time data**: IMPLEMENTED for Polygon/Finnhub/FMP (data/*_provider.py,
  selected via settings.data.provider, keys from env). make_provider composes a
  bars-only source (Polygon) with yfinance fundamentals via
  FundamentalsFallbackProvider. Freshness is settings.data.freshness (default
  DELAYED — the safe under-claim); set REALTIME only when the plan truly
  delivers it, since the label flows straight onto every signal. Adding a
  provider = a DataProvider subclass + a factory branch + a mocked-HTTP test;
  never touch the AsOfView honesty core. See docs/realtime-data.md.
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
watchman scan [--limit K]       # pre-market focus list (run 8:00-9:25 ET)
watchman signals [--symbols A,B]  # evaluate setups + auto-take paper trades
watchman report [--brief morning|evening] [--rebalance-longterm [--force]]
watchman debuts [--why] [--lookback D] [--horizon D]  # Module F new listings
```
