# Module F — Debuts (new-listing scanner)

`watchman debuts` detects new and upcoming stock listings and attaches a
**data-driven verdict** to each: how comparable past IPOs performed, where this
one sits, and a deterministic AVOID / CAUTION / NEUTRAL / LEAN_FAVORABLE call —
with every input shown. It is the "a new stock is tradable" alert (the way you
heard about Sandisk when it listed in Feb 2025), plus an honest base-rate read
on whether the data favors it.

> **This is not investment advice, and it is not an oracle.** The verdict is a
> transparent function of numbers, not an opinion — and the numbers for a
> brand-new stock are thin. Read "Why the verdict is deliberately skeptical"
> below before you act on anything here.

## Requirements

The IPO calendar comes from an official API, and only **Finnhub** or **FMP**
provide one. Set a key and select the provider (see
[realtime-data.md](realtime-data.md)):

```bash
# .env
WATCHMAN_FMP_KEY=your_key        # or WATCHMAN_FINNHUB_KEY
```
```yaml
# config/settings.yaml
data:
  provider: fmp                  # or finnhub
```

On plain yfinance (no IPO calendar), `watchman debuts` stops with a clear
message telling you to add a key — it never fabricates a listing.

## Usage

```bash
watchman debuts                  # recent + upcoming listings with verdicts
watchman debuts --why            # also print the full standing caveats per name
watchman debuts --lookback 60 --horizon 45   # widen the window (days)
watchman debuts --limit 10       # analyze at most N listings
```

Tunables live in `config/settings.yaml` under `debuts:` (`lookback_days`,
`horizon_days`, `max_cohort_fetch`). Automate it with the scheduler's `debuts`
phase (see [scheduling.md](scheduling.md)); it's commented out in
`crontab.example` because it needs a key.

## What each verdict is built from

For every listing, the verdict is a **pure function** of these data inputs
(nothing else — no model narrative):

1. **The comparable cohort (the base rate).** Watchman pulls past IPOs from the
   provider's calendar (~2.5 years back), matches ones similar to this listing
   by **sector + size bucket** (falling back to sector-only, then all-recent if
   too few match — the match basis is always shown), and computes how they
   actually performed: median return at ~1/3/6 months from their first close,
   the share that were positive at 3 months, and median max drawdown. Sample
   size is always displayed; a thin cohort is down-weighted and flagged.
2. **This listing's price vs its offer.** If it's already trading, how far it
   is above/below the offer price. Being far above offer (a "hot" open) is a
   *penalty*, not a plus — see below.
3. **Profitability at listing**, if fundamentals exist. Unprofitable IPOs have
   historically underperformed profitable ones, so that subtracts.

The net score maps to a label by fixed thresholds. Every reason printed is a
sentence built from one of these numbers.

## Why the verdict is deliberately skeptical

The scorecard starts **below neutral** and it is hard, by design, to reach
`LEAN_FAVORABLE`. That's not pessimism for its own sake — it encodes two
well-established, documented findings:

- **IPOs underperform the market on average** in the years after listing (the
  long-run IPO underperformance literature).
- **Buying after a large first-day pop underperforms** buying near the offer;
  the first-day retail buyer historically fares worst. So a stock trading +80%
  above its offer gets a *caution*, even if its cohort looks good — because you
  would be buying after the move the cohort captured.

These are the same kind of explicit, arguable constants as the screener's
deteriorator thresholds; they live in `src/watchman/debuts/analysis.py` and you
can change them in the open.

## What it deliberately does NOT do

- It does **not** say "BUY." The strongest label is `LEAN_FAVORABLE`, and it's
  hedged on purpose.
- It does **not** invent a thesis from nothing. If there's no cohort and no
  fundamentals, the verdict is capped at `CAUTION` and says so.
- It does **not** claim precision it can't have. Every run repeats that this is
  weak, base-rate evidence from a small, survivorship-affected sample.

## Honest limitations

- **Thin data is the whole problem.** A company on its first day of trading has
  ~one filing of history and no track record. The point-in-time discipline the
  rest of Watchman relies on (AsOfView, backtesting, the ledger) barely applies
  to a brand-new ticker — there's almost nothing to be point-in-time *about*.
- **The cohort is survivorship- and selection-biased.** The past-IPO list is
  what the provider still carries; de-listed flops may be missing, flattering
  the base rate. Trust the *spread and sample size* more than any single
  number.
- **Lockups.** Insider lockups typically expire ~90-180 days after listing; the
  added share supply is a known headwind you're inside the window for on a
  recent debut. The verdict flags it but can't time it (lockup dates aren't in
  the free calendar).
- **Freshness still applies.** On a `DELAYED` feed the prices feeding the
  verdict are delayed, and it says so. Real-time data makes the read *current*,
  not *proven*.

Bottom line: `watchman debuts` turns "a new stock is available" into "here's
what comparable new stocks actually did, here's where this one sits, and here's
a skeptical, rules-based read — now you decide." That is the honest maximum a
data-driven tool can offer on IPOs.
