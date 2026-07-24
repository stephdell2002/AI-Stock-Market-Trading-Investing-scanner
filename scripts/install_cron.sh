#!/usr/bin/env bash
# Install the Watchman daily rhythm into your crontab, wired to your timezone.
#
#   scripts/install_cron.sh                 # your machine's local timezone
#   scripts/install_cron.sh --et            # CRON_TZ=America/New_York (Linux, DST-safe)
#   scripts/install_cron.sh --tz America/Chicago
#   scripts/install_cron.sh --with-debuts   # also schedule the new-listing scan
#   scripts/install_cron.sh --print         # show the lines, don't install
#
# It replaces only Watchman's own block (between marker comments) and leaves the
# rest of your crontab untouched. Re-run any time to update.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif command -v python3 &>/dev/null; then
  PY=python3
else
  echo "python3 not found — activate the project venv first." >&2
  exit 1
fi

GEN_ARGS=(--repo "$REPO_ROOT")
DO_INSTALL=1
for arg in "$@"; do
  case "$arg" in
    --print) DO_INSTALL=0 ;;
    --et|--with-debuts) GEN_ARGS+=("$arg") ;;
    --tz) : ;;                        # value handled below
    --tz=*) GEN_ARGS+=(--tz "${arg#--tz=}") ;;
    *) if [[ "${prev:-}" == "--tz" ]]; then GEN_ARGS+=(--tz "$arg"); fi ;;
  esac
  prev="$arg"
done

BLOCK="$("$PY" "$SCRIPT_DIR/gen_schedule.py" "${GEN_ARGS[@]}")"

if [[ "$DO_INSTALL" -eq 0 ]]; then
  echo "$BLOCK"
  echo "# (--print: not installed. Drop --print to install.)" >&2
  exit 0
fi

# Merge: keep everything except any previous Watchman block, then append ours.
EXISTING="$(crontab -l 2>/dev/null || true)"
CLEANED="$(printf '%s\n' "$EXISTING" \
  | sed '/# >>> watchman schedule >>>/,/# <<< watchman schedule <<</d')"

{
  printf '%s\n' "$CLEANED" | sed '/^$/N;/^\n$/D'   # collapse blank runs
  printf '%s\n' "$BLOCK"
} | crontab -

echo "Installed. Your Watchman schedule is now live:"
echo "$BLOCK" | sed 's/^/  /'
echo
echo "Review any time with:  crontab -l"
echo "Logs land in:          $REPO_ROOT/data/logs/"
