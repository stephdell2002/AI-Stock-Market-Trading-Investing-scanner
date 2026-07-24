# Scheduling Watchman

Watchman is a set of CLI commands; a scheduler runs them on the market's
rhythm. This guide covers **cron** (Linux / macOS) and **Windows Task
Scheduler**. Both drive the same helper scripts in `scripts/`, so the daily
logic lives in one place.

Everything here still runs on **paper only** — nothing you schedule can place a
real order (the interface has no order method). Scheduling just automates the
scan → signal → log → report loop so you build a live track record without
babysitting a terminal.

## The daily rhythm

| Phase | When (ET) | What runs | Why |
|-------|-----------|-----------|-----|
| `morning` | ~08:05, weekdays | `scan`, `report --brief morning` | Build the ≤10-name focus list before the open; print the plan. |
| `intraday` | every 5 min, 09:35–15:55 | `signals` | Evaluate ORB / VWAP / rel-vol setups on each completed bar; auto-take paper trades; resolve open ones. |
| `evening` | ~16:15, weekdays | `signals`, `report --brief evening`, `report` | Final resolve/expire; write the recap and the HTML dashboard. |
| `weekly` | Sun evening | `screen` | Refresh the Module A watchlist and deteriorator flags. |
| `debuts` | weekday pre-open (optional) | `debuts` | New/upcoming listings + data-driven verdicts. **Needs a Finnhub or FMP key**; on yfinance the step just fails and is tallied. |
| `monthly` | 1st of month | `report --rebalance-longterm` | Equal-weight the long-term paper book into the screener top-N. |

The intraday cadence matches the bar interval (5 min by default). A setup only
fires on the **last completed bar**, so running every 5 minutes catches each
bar's trigger exactly once; the ledger's dedup prevents re-entering the same
setup, and the circuit breaker reads live paper P&L — so frequent runs are
safe.

> **Timezone.** The US market runs on `America/New_York`. Cron and Task
> Scheduler fire in the machine's *local* time. Either tell the scheduler to
> use ET (shown below) or convert the ET times in the table to your own zone.
> During US daylight-saving changes, ET-aware scheduling keeps you aligned to
> the market automatically; hard-coded local times do not.

---

## Linux / macOS (cron)

### One command (recommended)

`scripts/install_cron.sh` reads the canonical ET rhythm, converts it to **your
machine's timezone**, and merges it into your crontab — touching only Watchman's
own block (between marker comments), never your other entries. Re-run it any
time to update.

```bash
scripts/install_cron.sh                 # convert ET → your local zone, install
scripts/install_cron.sh --print         # show the lines, don't install
scripts/install_cron.sh --et            # use CRON_TZ=America/New_York (Linux, DST-safe)
scripts/install_cron.sh --tz America/Chicago   # a specific zone
scripts/install_cron.sh --with-debuts   # also schedule the new-listing scan
```

Preview first with `--print`, then run it for real. Verify with `crontab -l`.

- **On EST/`America/New_York`** the times come out as the raw ET rhythm (08:05
  morning, 09:35–15:55 intraday, 16:15 evening, Sun 18:00 weekly, 1st 18:30
  monthly) because your clock *is* the market clock — nothing to convert.
- **`--et`** bakes in `CRON_TZ=America/New_York` instead of local times, so the
  schedule tracks the market through US daylight-saving changes automatically.
  It only works on cron builds that honor `CRON_TZ` (Linux cronie yes;
  macOS/BSD cron no — use the default local-baked output there).
- Any fixed-offset North American zone stays correct year-round because it
  shifts with US DST in lockstep. For zones that *don't* (Europe/Asia), prefer
  `--et` on Linux, or re-run after a DST change.

Under the hood the installer calls `scripts/gen_schedule.py`, which you can run
directly to inspect the block without touching your crontab:

```bash
.venv/bin/python scripts/gen_schedule.py --repo "$(pwd)" --tz America/New_York
```

### By hand

Prefer to paste it yourself? Edit `scripts/crontab.example` (set
`WATCHMAN_HOME` to your checkout's absolute path) and `crontab -e`, or:

```bash
chmod +x scripts/watchman_run.sh
scripts/watchman_run.sh weekly      # smoke-test one phase → data/logs/
crontab scripts/crontab.example     # replaces your whole crontab; back up first
```

The example sets `CRON_TZ=America/New_York`; delete that line and convert to
local if your cron is too old to support it.

**macOS caveat.** `cron` still works but Apple prefers `launchd`, and cron needs
Full Disk Access (System Settings → Privacy & Security) to run unattended. A
laptop that sleeps will miss fires — a Mac mini or always-on box is better for
the intraday cadence. If you only want the daily bookends, keep `morning` and
`evening` and drop the `intraday` lines.

Logs accumulate under `data/logs/<phase>-<date>.log` (gitignored). A phase
exits non-zero if any step failed, so you can wire it to your own alerting.

---

## Windows (Task Scheduler)

The PowerShell runner `scripts\watchman_run.ps1` mirrors the bash script.

### One command (recommended)

`scripts\register_tasks.ps1` converts the ET rhythm to this machine's local
time and prints the exact `schtasks` commands to create every task. It **prints
by default** so you can review them; re-run with `-Run` to actually register.

```powershell
# Preview the commands (nothing is created):
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1

# Create the tasks for real:
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Run

# Include the optional new-listing scan (needs a Finnhub/FMP key):
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -IncludeDebuts -Run
```

It reads your machine's local timezone via `[TimeZoneInfo]::Local` and
converts each ET time for you, so on an EST box the tasks fire at the natural ET
times and on a Pacific box they fire three hours earlier — no arithmetic on your
part. `-ExecutionPolicy Bypass` on the command line avoids changing the
machine-wide policy. Run it from an **Administrator** PowerShell so `schtasks`
can create the tasks.

The tasks it creates: Morning & Evening (weekly, Mon–Fri), Weekly (Sun),
Monthly (1st of month), and Intraday (every 5 minutes between the converted
open and close). The intraday task, like cron's `*/5` schedule, also ticks on
weekends — the runner just no-ops when the market is closed.

### By hand

Prefer the GUI or want to tweak the triggers? Try a phase first:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\watchman_run.ps1 weekly
```

Then create one task per phase (**Task Scheduler → Create Task**, or
`Register-ScheduledTask` / `schtasks` in an Administrator PowerShell). Remember
Task Scheduler triggers are always **local** time — convert from the ET column
in the daily-rhythm table, and note you'll be one hour off for the ~3 weeks a
year when US and your DST transitions don't line up. In each task's
**Conditions** tab, tick *Wake the computer to run this task* (and untick
*Start only on AC power* on a laptop) if you want it to fire while the machine
sleeps.

---

## Not using a scheduler?

You don't have to automate anything. The whole point of v1 is a paper track
record, and you can build one by hand — run `watchman scan` in the morning,
`watchman signals` when you check in, and `watchman report` after the close.
The scheduler just removes the discipline tax.

## What to watch

- `data/logs/` — every scheduled run leaves a dated log. Skim `evening-*.log`
  weekly; a phase that exits non-zero had at least one failed step.
- `data/reports/report-*.html` — the dashboard. The **setup scoreboard** and
  any red **DIVERGENCE** banner are the numbers that decide real capital (see
  [first-90-days.md](first-90-days.md)).
- Data freshness. On free yfinance every signal is `DELAYED — NOT ACTIONABLE`.
  Automating it still builds an honest paper record; just don't hand-trade off
  a delayed signal. Add a real-time provider key (`.env`) to lift the label.
