# The First 90 Days — has Watchman earned real capital?

This is the evaluation instrument. Watchman v1 trades **paper only**, on
purpose. The question this document answers is narrow and serious: *after a
disciplined trial, is there honest statistical evidence that any of these
signals deserve real money?* The default answer is **no** — and the burden of
proof is on the strategy, not on your patience.

Read this the way you'd read a business case you were about to fund with your
own savings. Because that is exactly what it is.

---

## The premise

Paper results almost always look better than live money feels. Watchman models
slippage, spread, and commissions on every fill, and resolves same-bar
stop+target ambiguity pessimistically — but a simulator still gives you perfect
fills, infinite liquidity, no partial executions, and, crucially, **no fear or
greed**. On top of that, on free yfinance data every intraday signal is
`DELAYED — NOT ACTIONABLE`: a delayed signal is a study specimen, not a trade
you could actually have taken. So the paper record is the *optimistic* case.
Real money will be worse. Plan around that, not around the equity curve.

Ninety days is a **first gate, not a verdict.** With ~1–3 day trades a day you
might log 60–180 resolved signals — enough to notice a disaster, not enough to
prove an edge. Distinguishing a genuine 55% setup from a 50% coin flip takes on
the order of a few hundred trades. Treat 90 days as "has it earned the right to
keep being evaluated (and maybe a token real allocation)?" — not "is it
proven?"

---

## What you are actually testing

Three hypotheses, each falsifiable from the signal ledger and reports:

1. **The day-trade setups have positive expectancy after costs.** Not "win
   often" — *make money per trade* once slippage/spread/commission are paid.
2. **Live matches backtest.** If the live win rate craters below the backtest
   baseline (Watchman shows a red `DIVERGENCE` banner), the backtest was
   optimistic — that is information, and it disqualifies the setup until
   understood.
3. **The risk framework holds under fire.** 1% max risk per trade, ≤3 day
   positions, the −2% daily circuit breaker, 2:1 minimum reward:risk — enforced
   in code, but you must confirm they were never quietly overridden.

The long-term screener (Module A) is a *separate, slower* test — see the note
at the end. Ninety days cannot evaluate a multi-year strategy.

---

## Timeline

### Days 1–15 — plumbing and data integrity
Before any result means anything, prove the machine is honest.

- [ ] Real data flows: `watchman fetch SPY` returns recent bars; `watchman
      universe` resolves ~500 names. If you refreshed membership, `watchman
      universe --refresh` succeeded.
- [ ] `watchman screen --limit 30` completes and the theses cite real numbers.
- [ ] `pytest` is green on your machine (the lookahead canary especially).
- [ ] Scheduling fires: `data/logs/` fills with dated logs; the morning brief
      and evening HTML report generate. (See [scheduling.md](scheduling.md).)
- [ ] You understand the freshness label on your data. If it says
      `DELAYED — NOT ACTIONABLE`, you're studying, not trading — that's fine
      for the trial, but be honest about it.
- [ ] **Record your baselines now.** Note the SPY price, your paper start
      equity ($10k day / $10k long-term by default), and today's date. You'll
      compare against a plain SPY buy-and-hold at the end.

### Days 16–60 — accumulate, don't judge
Let the ledger fill. **Do not** tune parameters mid-trial to chase results —
that is in-sample overfitting wearing a disguise, and it invalidates the whole
test. Change nothing but watch everything.

- [ ] The focus list looks sane most mornings (liquid names, real gaps).
- [ ] Signals carry every field: entry, stop, targets, R:R ≥ 2, size, rationale.
- [ ] Trades resolve: the evening recap shows target / stopped / expired
      outcomes, not a growing pile of unresolved "open" signals.
- [ ] Per-setup sample sizes are climbing (evening recap, 30d/90d columns).
- [ ] No honesty flag is firing unexplained (Sharpe > 3, win rate > 75%). If
      one is, **stop and find the bug** before trusting anything — that is the
      system telling you a number is too good to be true.
- [ ] You are journaling the *misses*: setups you'd have overridden, days the
      circuit breaker saved you, fills you doubt were realistic.

### Days 61–90 — evaluate against the scorecard
Now the ledger has enough in it to grade. Open the HTML report and work the
scorecard below honestly.

---

## The graduation scorecard

Score the **day-trading module** against every row. This is go / no-go: a
single unmet hard criterion means **not yet** — keep it on paper. There is no
partial credit and no "but the last week was great."

### Hard criteria (all must hold)

| # | Criterion | Threshold | Where to look |
|---|-----------|-----------|---------------|
| 1 | **Sample size** | ≥ 100 resolved signals total; ≥ 30 per setup you'd fund | Evening recap / report scoreboard |
| 2 | **Positive expectancy after costs** | Expectancy > 0 per funded setup *and* overall | Ledger `expectancy`; report |
| 3 | **Profit factor** | > 1.3 overall | Report / backtest metrics |
| 4 | **Live vs backtest** | No standing `DIVERGENCE` banner; live win rate not > ~10pp below any recorded baseline | Report "Setups: live vs backtest" |
| 5 | **Drawdown you can stomach** | Max paper drawdown < 10% of the account — and you honestly believe you'd hold through it with real money | Equity curve |
| 6 | **Risk discipline intact** | Zero breaches: no trade risked > 1%, breaker always respected, never > 3 concurrent, no signal below 2:1 | Ledger; you never overrode a rejection |
| 7 | **No unexplained honesty flag** | Any Sharpe > 3 / win > 75% has a documented, benign explanation — or you don't trust the result | Report warnings |
| 8 | **Edge isn't one lucky trade** | Remove your single best trade; expectancy is still > 0 | Ledger, sort by PnL |

### Soft signals (weigh, don't gate)

- Beat a plain **SPY buy-and-hold** over the same window on a risk-adjusted
  basis (compare Sharpe, not just return). Losing to buy-and-hold isn't
  automatically disqualifying over 90 days, but it's a reason for humility.
- Win rate and average R are stable across the trial, not front- or
  back-loaded.
- The setups you'd fund are the ones with the *most* data, not the least.

---

## Red flags — stop the evaluation

Any of these means pause and diagnose. Do **not** graduate around them.

- A red **DIVERGENCE** banner you can't explain — live is telling you the
  backtest lied.
- A result so good an honesty flag fired and you never found why. Assume a bug
  (lookahead, a resolution quirk, survivorship) until proven otherwise.
- Expectancy carried by one or two outliers; the median trade is a small loss.
- You started overriding the system — taking rejected signals, widening stops,
  sizing up "because you were sure." The moment you trade your feelings instead
  of the rules, the paper record stops measuring the *system*.
- Costs that would be far worse live: signals concentrated in thin-float,
  wide-spread names where the modeled 5 bps is fantasy. Real fills there bleed.
- Your data was delayed the whole time (`NOT ACTIONABLE`). Then you've proven a
  strategy on prices you couldn't have traded — a real-time feed is a
  prerequisite before real money, not an afterthought.

---

## If it passes

Passing earns **one** thing: permission to consider a **small, real
allocation** — not a green light to fund the strategy at size. The discipline
that got you here is the discipline that keeps you solvent:

1. **Respect the PDT rule.** Real-money day trading in the US requires
   **$25,000+ in a margin account**. Below that you are capped at 3 day trades
   per 5 business days — which alone can invalidate a day-trading strategy. This
   is not Watchman's rule; it's FINRA's, and it doesn't care about your backtest.
2. **Start at the floor.** Fund the minimum that lets you feel real P&L — enough
   to trigger the emotions the simulator couldn't. A few hundred dollars of real
   money teaches more than $10k of paper.
3. **Keep paper running in parallel.** The ledger should keep growing. Compare
   real fills to paper fills trade-for-trade; the gap *is* your slippage
   estimate, and it's usually worse than 5 bps.
4. **Broker integration stays read-only first.** Only brokers with official APIs
   are ever candidates (IBKR, Tradier, Schwab, Alpaca — see the registry in
   `broker/adapter.py`). Wealthsimple / Robinhood / Webull have no official
   trading API and will never be automated; you act on those manually. Order
   placement is a deliberate, separate decision — never smuggled into a code
   change.
5. **Re-run this checklist every 90 days.** An edge that was real can decay.
   The evaluation is a habit, not a milestone.

## If it fails

Failing is the system working. Most strategies don't have an edge, and finding
that out on paper cost you nothing but time. Options, in order of honesty:

- Keep it on paper another 90 days and gather more data — *without* tuning to
  fit the trial you just ran.
- Retire the setups that never showed expectancy; keep the ones that did and
  re-evaluate them alone.
- Accept that intraday edge is hard, and lean on the long-term screener, where
  costs matter less and the horizon is friendlier.

---

## A note on the long-term book (Module A)

Ninety days is far too short to judge a fundamentals-driven long-term strategy —
it needs **quarters to years**. Over the trial, just confirm the machinery is
honest: monthly rebalances happen, costs are charged, deteriorators get flagged,
and dropped names are sold. Judge its *returns* on a multi-quarter horizon
against SPY, with the survivorship-bias caveat from the decile backtest firmly
in mind (the current index membership omits the losers that fell out, so every
historical number is flattered). Do not fund it on 90 days of paper either.

---

*Credibility comes from honesty, not optimism. If this document ever feels like
an obstacle between you and "just going live," that is the document doing its
job.*
