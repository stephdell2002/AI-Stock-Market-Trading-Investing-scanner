# Connecting real-time data

Watchman runs on free yfinance end-of-day data out of the box, which is why
every intraday signal is labeled **`DELAYED — NOT ACTIONABLE`**. To lift that
label — so Module B signals reflect live prices — point Watchman at a data
provider with an official API. Three are wired in: **Finnhub**, **Polygon**,
and **FMP**. All are legitimate, official REST APIs with published terms; none
of this scrapes a broker or uses a private endpoint (principle 6).

## The two things you change

1. **Add the key to your environment** (never to YAML). Copy `.env.example` to
   `.env` and fill in one line, or export it in your shell:

   ```bash
   # .env  (gitignored)
   WATCHMAN_FMP_KEY=your_key_here
   ```

2. **Select the provider** in `config/settings.yaml`:

   ```yaml
   data:
     provider: fmp          # yfinance | finnhub | polygon | fmp
     # freshness: REALTIME  # see "Honesty about freshness" below
   ```

That's it. `watchman scan`, `watchman signals`, and `watchman report` now pull
from that source. If the key is missing, Watchman stops with a clear message
naming the env var — it never silently falls back.

## Which provider?

| Provider | Bars (daily / intraday) | Fundamentals & statements | News | Notes |
|----------|------------------------|---------------------------|------|-------|
| **FMP** | ✅ / ✅ | ✅ full | ✅ | Most complete — drives the screener **and** the signal engine alone. Best single choice. |
| **Polygon** | ✅ / ✅ (clean aggregates, incl. extended hours) | ✕ (uses yfinance) | ✅ | Best bars API. Free tier delayed; paid tiers 15-min or real-time. |
| **Finnhub** | ✅ / ⚠️ candles are paid-tier | ⚠️ partial (uses yfinance for statements) | ✅ real-time | Great free real-time quotes + news; intraday candles need a paid plan. |

Watchman **composes** automatically: if the selected provider can't serve
fundamentals or financial statements (Polygon has none; Finnhub has no
statements), those calls fall back to yfinance, so the Module A screener keeps
working. Bars, news, and — critically — the freshness label always come from
the provider you selected.

For most users the simplest path to actionable intraday signals is **FMP**
(one key, everything works) or **Polygon** (best bars, fundamentals via the
automatic yfinance fallback).

## Honesty about freshness

This is the part that matters, and it's deliberately your call — only you know
your subscription tier.

- By default a real-time provider is labeled **`DELAYED`**. Free tiers are
  usually delayed, and Watchman would rather under-claim than pretend a signal
  was actionable when it wasn't.
- Set `data.freshness: REALTIME` **only if your plan genuinely delivers
  real-time data for the symbols you trade.** That label flows straight onto
  every emitted signal and into the report — claiming REALTIME on delayed data
  is exactly the kind of self-deception this whole project exists to prevent.
- Valid values: `REALTIME`, `DELAYED`, `EOD` (or leave it unset for the safe
  `DELAYED` default). yfinance ignores the setting — it is always EOD.

When freshness is `REALTIME`, signals drop the `NOT ACTIONABLE` tag and the
"study, don't chase" reminder disappears from `watchman signals` and the HTML
report. Everything else — the risk gates, the ledger, the pessimistic
resolution rules — is unchanged. Real-time data makes the signals *actionable*;
it does not make them *proven*. The [first-90-days checklist](first-90-days.md)
still governs whether any of this earns real money.

## A note on rate limits and caching

Free tiers cap requests per minute. Watchman caches daily bars, fundamentals,
and statements in SQLite (`data/watchman.db`), so a screener run hits each
symbol once per cache window. Intraday bars are intentionally **not** cached
(they're perishable), so the intraday `signals` loop makes one call per symbol
per run — keep your focus list small (the scanner caps it at 10) and the
5-minute cadence comfortably fits typical free limits. On a `429`, the HTTP
layer backs off and retries automatically; a persistent failure is reported
per-symbol, never silently swallowed.

## Adding another provider later

Every provider is just a `DataProvider` (see `src/watchman/data/provider.py`):
implement `daily_bars`, `intraday_bars`, `fundamentals`, `quote_freshness`, and
optionally `news` / `financial_statements`, then register it in the factory
(`src/watchman/data/providers.py`). The point-in-time `AsOfView`, the caches,
the scanner, the setups, and the paper engine all work through that interface
unchanged — a new source is a few hundred lines and a mocked-HTTP test file,
never a change to the honesty core.
