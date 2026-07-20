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

1. Make the runner executable and try a phase by hand first:

   ```bash
   chmod +x scripts/watchman_run.sh
   scripts/watchman_run.sh weekly      # runs `watchman screen`, logs to data/logs/
   ```

   Confirm it printed a `... done -> data/logs/weekly-YYYY-MM-DD.log` line and
   that the log looks sane.

2. Edit `scripts/crontab.example`: set `WATCHMAN_HOME` to your checkout's
   absolute path. Then install it:

   ```bash
   crontab scripts/crontab.example      # replaces your crontab; back up first if needed
   # or: crontab -e  and paste the lines
   crontab -l                           # verify
   ```

3. **Timezone.** The example sets `CRON_TZ=America/New_York`. If your cron is
   too old to support `CRON_TZ` (some BSD/Vixie builds), delete that line and
   convert the times to local yourself.

4. **macOS caveat.** `cron` still works but Apple prefers `launchd`, and cron
   needs Full Disk Access (System Settings → Privacy & Security) to run
   unattended. A laptop that sleeps will miss fires — a Mac mini or always-on
   box is better for the intraday cadence. If you only want the daily
   bookends, keep `morning` and `evening` and drop the `intraday` lines.

Logs accumulate under `data/logs/<phase>-<date>.log` (gitignored). A phase
exits non-zero if any step failed, so you can wire it to your own alerting.

---

## Windows (Task Scheduler)

The PowerShell runner `scripts\watchman_run.ps1` mirrors the bash script.

1. Try a phase by hand (from the repo root, in PowerShell):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\watchman_run.ps1 weekly
   ```

   If PowerShell blocks the script, the `-ExecutionPolicy Bypass` flag on the
   command line (as above and in the tasks below) is the least-invasive fix —
   you don't have to change the machine-wide policy.

2. Create one task per phase. Either use the GUI (**Task Scheduler → Create
   Task**) or run these once in an **Administrator** PowerShell, editing
   `$Home` to your checkout path:

   ```powershell
   $Repo = "C:\path\to\AI-Stock-Market-Trading-Investing-scanner"
   $Ps   = "powershell.exe"

   function New-WatchmanTask($Name, $Phase, $Trigger) {
     $action = New-ScheduledTaskAction -Execute $Ps `
       -Argument "-ExecutionPolicy Bypass -File `"$Repo\scripts\watchman_run.ps1`" $Phase" `
       -WorkingDirectory $Repo
     Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
       -Description "Watchman $Phase phase" -Force
   }

   # Times below are LOCAL — convert from the ET column in the daily-rhythm
   # table to your timezone (e.g. ET+3 for US Pacific would be 05:05 for morning).
   New-WatchmanTask "Watchman Morning" "morning" `
     (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:05am)

   New-WatchmanTask "Watchman Evening" "evening" `
     (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 4:15pm)

   New-WatchmanTask "Watchman Weekly" "weekly" `
     (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 6:00pm)
   ```

3. **Intraday every 5 minutes.** Task Scheduler can repeat a task on an
   interval within a window. Create the task, then on its **Triggers** tab edit
   the daily trigger: *Repeat task every 5 minutes for a duration of 7 hours*,
   starting at 09:35 (local-adjusted from ET). Or in PowerShell:

   ```powershell
   $t = New-ScheduledTaskTrigger -Daily -At 9:35am
   $t.Repetition = (New-ScheduledTaskTrigger -Once -At 9:35am `
     -RepetitionInterval (New-TimeSpan -Minutes 5) `
     -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition
   New-WatchmanTask "Watchman Intraday" "intraday" $t
   ```

4. **Timezone.** Task Scheduler triggers are always local time — there is no
   ET option. Convert the ET times yourself, and note you'll be one hour off
   for the ~3 weeks a year when US and your DST transitions don't line up. If
   that bothers you, run only `morning`/`evening` and check intraday manually.

5. **Wake / power.** In each task's **Conditions** tab, tick *Wake the
   computer to run this task* (and untick *Start only on AC power* on a laptop)
   if you want it to fire while the machine sleeps.

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
