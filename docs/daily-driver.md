# Watchman — the daily driver cheat sheet

The exact commands to run each day, and what to expect. All output lands in
`data/reports/`; everything persists in `data/watchman.db` (that file **is** your
track record — back it up).

First, know which track you're on — it changes what's actionable:

| | **FREE track** (default) | **REAL-TIME track** |
|---|---|---|
| Data source | yfinance (end-of-day / delayed) | Finnhub / Polygon / FMP key set, `data.freshness: REALTIME` |
| Long-term screener (`screen`) | ✅ fully usable | ✅ fully usable |
| Day-trade signals (`signals`) | ⚠️ `DELAYED — NOT ACTIONABLE` — **study, don't trade** | ✅ actionable (still paper; you execute by hand) |
| New listings (`debuts`) | ✗ needs a Finnhub/FMP key | ✅ available |

> **Golden rules (both tracks):** Watchman is **paper-only** — it never places a
> real order. It tells you the trade and the size; *you* execute manually in your
> broker (Wealthsimple, etc.). Respect the freshness label. Before any real money,
> work through [first-90-days.md](first-90-days.md).

Activate your environment first each session:
```bash
cd AI-Stock-Market-Trading-Investing-scanner
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

---

## ☀️ Morning — before the open (8:00–9:25 ET)

```bash
watchman scan                    # build today's focus list (≤10 tickers)
watchman report --brief morning  # dated plan: focus list, open positions, watchlist moves
```

- **FREE track:** the focus list is built on delayed data, so treat it as a
  *study* list, not a trade list. Still worth reading to learn what the scanner
  flags.
- **REAL-TIME track:** the focus list is live; these are the names to watch.
- `scan` must run before `signals` (signals reads today's focus list). Skipped
  the scan? Run signals with explicit names: `watchman signals --symbols NVDA,AMD`.

---

## 📈 Intraday — during the session (9:35–15:55 ET)

```bash
watchman signals                 # evaluate ORB / VWAP / rel-vol setups on the latest bar
```

- A setup only fires on the **last completed bar**, so run this **every ~5
  minutes** to catch each trigger. Each run also resolves your open paper trades
  (hit target / stopped / expired) and auto-takes new ones.
- **FREE track:** every signal is stamped `DELAYED — NOT ACTIONABLE`. Read them,
  learn the patterns, let the paper ledger fill — **do not chase them**.
- **REAL-TIME track:** signals are actionable. Each one gives entry, stop,
  targets, R:R, and size — place it **by hand** in your broker.
- Don't want to babysit the terminal? Let the scheduler run this loop for you —
  see [scheduling.md](scheduling.md).

---

## 🌙 Evening — after the close (~16:15 ET)

```bash
watchman signals                 # final pass: resolve / expire the day's trades
watchman report --brief evening  # recap: outcomes, P&L, per-setup live win rates
watchman report                  # the self-contained HTML dashboard
```

Open `data/reports/report-YYYY-MM-DD.html` in any browser. This is where you
review honestly: paper P&L and equity curve, today's signals and their outcomes,
and — the important one — each setup's **live win rate next to its backtest win
rate**. A red `DIVERGENCE` banner means live is falling short of backtest; believe
the live number.

---

## 📅 Weekly (e.g. Sunday evening)

```bash
watchman screen                  # refresh the long-term watchlist + deteriorator flags
```

Ranks the S&P 500 + Nasdaq 100 on the four-pillar score with a thesis per name.
The **first run pulls the whole universe (10–20 min)**, then it's cached and fast.
This is the most useful command on the FREE track. Export it to TradingView with
`watchman screen --export-tv watchlist.txt`.

---

## 🗓️ Monthly (1st of the month)

```bash
watchman report --rebalance-longterm    # equal-weight the long-term paper book into the top-N
```

---

## 🆕 When you want it (REAL-TIME track only)

```bash
watchman debuts                  # new/upcoming listings + a data-driven, skeptical verdict
watchman debuts --why            # also print the full caveats per name
```

The "a new stock is tradable" alert with a base-rate read. It never says BUY and
it's not advice — see [debuts.md](debuts.md).

---

## The 30-second version

**Every trading day:**
1. Morning: `watchman scan` → `watchman report --brief morning`
2. Through the day: `watchman signals` every ~5 min (or let the scheduler do it)
3. After close: `watchman signals` → `watchman report --brief evening` → `watchman report`

**Weekly:** `watchman screen`  ·  **Monthly:** `watchman report --rebalance-longterm`

**FREE track:** day-trade signals are for *study*; the screener is your real edge.
**REAL-TIME track:** signals are actionable — but you place every trade by hand,
on paper's evidence, until [first-90-days.md](first-90-days.md) says otherwise.
