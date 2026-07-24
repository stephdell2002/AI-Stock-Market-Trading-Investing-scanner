# Running Watchman on an always-on box

The intraday phase fires every 5 minutes during the session. A laptop that
sleeps, closes, or travels misses those fires — you get a patchy paper record
full of holes. The fix is to run the schedule on a host that stays **on and
online 24/5**, independent of your laptop. Your laptop becomes just a screen you
open to read the reports; the box does the work.

Everything here is still **paper only** — the box places no real orders (the
broker interface has no order method by design), so a remote/unattended host
carries no execution risk. The only secret it ever holds is an *optional*
real-time data key in `.env`, and that file is gitignored.

## Which box

Watchman is light: no GPU, a single core and ~512 MB RAM are plenty, and it
uses a few hundred MB of SQLite + text logs. The heavy moment is the *first*
`screen`, which pulls fundamentals for the whole universe (10–20 min on
yfinance); everything after that is cached. So almost any always-on machine
works:

| Option | Cost | Notes |
|--------|------|-------|
| **Small cloud VPS** (1 vCPU / 1 GB) | ~$5/mo | Simplest reliable choice. Always on, always networked, DST handled by the OS. Recommended. |
| **Raspberry Pi 4/5** | one-time | Runs it comfortably at home; just keep it powered and on Ethernet/Wi-Fi. |
| **Old mini PC / NAS / home server** | one-time | Fine if it's already on 24/5. Containers or a bare venv both work. |
| **Mac mini** (or any always-on Mac) | one-time | Works, but macOS cron ignores `CRON_TZ` — use local-baked times (see below) and mind Full Disk Access. |
| ~~Your laptop~~ | — | The thing you're trying to get away from. It sleeps. |

Pick whatever you'll actually keep running. A $5 Linux VPS is the least fuss.

## One-time setup (Linux box)

```bash
# 1. Clone your checkout onto the box
git clone <your-repo-url> ~/watchman && cd ~/watchman

# 2. Python 3.11+ venv + install
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
watchman --version

# 3. (optional) real-time data key — env vars only, never committed
cp .env.example .env
#    edit .env, set your POLYGON / FINNHUB / FMP key, then set
#    data.provider (and data.freshness) in config/settings.yaml.
#    Skip this and the box runs on free yfinance EOD data — fine for
#    building a paper record, just labeled DELAYED — NOT ACTIONABLE.

# 4. Smoke-test one phase before automating
scripts/watchman_run.sh weekly        # should write data/logs/weekly-<date>.log

# 5. Make sure cron is actually running on this box (fresh servers vary)
sudo systemctl enable --now cron      # Debian/Ubuntu  (RHEL/Fedora: crond)

# 6. Install the schedule — DST-safe on Linux
scripts/install_cron.sh --et          # CRON_TZ=America/New_York; add --with-debuts if you set a key
crontab -l                            # verify the block is there
```

Use **`--et`** on a Linux box: it writes `CRON_TZ=America/New_York`, so the
schedule tracks the US market through daylight-saving flips automatically — no
re-running twice a year. (On a Mac mini, whose cron ignores `CRON_TZ`, drop
`--et` so the installer bakes in local times converted from ET, and re-check
after a DST change.)

That's the whole job. The box now runs morning → intraday → evening → weekly →
monthly on the market's clock whether or not your laptop is awake.

## Keep it healthy

- **Updates.** When you pull new work:
  `cd ~/watchman && git pull && .venv/bin/pip install -e ".[dev]"`. Re-run
  `scripts/install_cron.sh --et` only if the schedule itself changed — it just
  replaces Watchman's own crontab block, leaving your other entries alone.
- **Disk & logs.** Each run appends to `data/logs/<phase>-<date>.log` (small
  text, gitignored). Over months, prune with `logrotate` or a `find … -mtime
  +30 -delete` cron line. On a tiny VPS also watch the SQLite caches under
  `data/`.
- **Secrets.** The only sensitive file is `.env` (an optional data key).
  `.gitignore` already excludes it — keep it that way and never bake it into a
  shared VM image or snapshot.
- **Freshness is unchanged by location.** Running on a server does *not* make a
  delayed feed real-time. On free yfinance every intraday signal is still
  stamped `DELAYED — NOT ACTIONABLE`; only a provider key + `data.freshness:
  REALTIME` lifts that label. The honest paper record builds either way.

## Confirm it's actually firing

- `crontab -l` shows the `# >>> watchman schedule >>>` block.
- After a trading day, `ls data/logs/` shows dated logs for each phase, and a
  phase log ending in a non-zero tally means a step failed — skim it.
- `data/reports/report-*.html` refreshes each evening; scp/rsync it to your
  laptop (or serve `data/reports/` behind something you trust) to read it.

You still decide everything real-money-related by hand — see
[first-90-days.md](first-90-days.md). The always-on box only removes the
"my laptop was asleep" gaps from the paper track record it's grading.
