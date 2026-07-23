#!/usr/bin/env bash
# Watchman daily orchestration for cron (Linux / macOS).
#
# One script, four phases — schedule each phase in cron at the right ET time.
# Every run is logged to data/logs/<phase>-YYYY-MM-DD.log so a headless machine
# leaves an audit trail.
#
#   scripts/watchman_run.sh morning     # ~08:05 ET  scan + morning brief
#   scripts/watchman_run.sh intraday    # every 5m 09:35-16:00 ET  signals
#   scripts/watchman_run.sh evening     # ~16:15 ET  final resolve + reports
#   scripts/watchman_run.sh weekly      # e.g. Sun    refresh screener watchlist
#   scripts/watchman_run.sh monthly     # 1st of month  long-term rebalance
#
# See docs/scheduling.md for crontab lines (including the ET timezone note).
set -euo pipefail

PHASE="${1:-}"
if [[ -z "$PHASE" ]]; then
  echo "usage: $0 {morning|intraday|evening|weekly|debuts|monthly}" >&2
  exit 2
fi

# Resolve the repo root from this script's location, so cron's bare $PWD
# (usually $HOME) never matters.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"
cd "$REPO_ROOT"

# Prefer the project virtualenv; fall back to a `watchman` on PATH.
if [[ -x "$REPO_ROOT/.venv/bin/watchman" ]]; then
  WATCHMAN=("$REPO_ROOT/.venv/bin/watchman")
elif command -v watchman &>/dev/null; then
  WATCHMAN=(watchman)
else
  echo "watchman not found (no .venv/bin/watchman and none on PATH)" >&2
  exit 1
fi

# Load API keys from .env if present (keys live in env vars only, never YAML).
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

LOG_DIR="$REPO_ROOT/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${PHASE}-$(date +%F).log"

# Run a watchman command, logging it, but do NOT abort the phase if it fails —
# a transient error in one step (e.g. `signals`) must not skip the reports.
# Failures are tallied and surfaced in the phase's exit code instead.
FAILURES=0
run() {
  echo "[$(date '+%F %T %Z')] watchman $*" | tee -a "$LOG_FILE"
  if ! "${WATCHMAN[@]}" "$@" >>"$LOG_FILE" 2>&1; then
    echo "[$(date '+%F %T %Z')] FAILED: watchman $*" | tee -a "$LOG_FILE" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

case "$PHASE" in
  morning)
    run scan
    run report --brief morning
    ;;
  intraday)
    # Evaluate setups on the latest completed bar; open positions from earlier
    # runs are resolved first, and dedup prevents re-entry. Safe to run often.
    run signals
    ;;
  evening)
    run signals               # final pass: resolve / expire the day's trades
    run report --brief evening
    run report                # self-contained HTML dashboard
    ;;
  weekly)
    run screen                # refresh the Module A watchlist + deteriorators
    ;;
  debuts)
    # New/upcoming listings + data-driven verdicts. Needs a Finnhub or FMP key
    # (yfinance has no IPO calendar); on yfinance this step just fails, is
    # tallied, and the runner moves on.
    run debuts
    ;;
  monthly)
    run report --rebalance-longterm   # equal-weight into the screener top-N
    ;;
  *)
    echo "unknown phase '$PHASE' (want morning|intraday|evening|weekly|debuts|monthly)" >&2
    exit 2
    ;;
esac

if [[ "$FAILURES" -gt 0 ]]; then
  echo "[$(date '+%F %T %Z')] $PHASE finished with $FAILURES failure(s) -> $LOG_FILE" >&2
  exit 1
fi
echo "[$(date '+%F %T %Z')] $PHASE done -> $LOG_FILE"
