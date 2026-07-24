#!/usr/bin/env python3
"""Generate a Watchman crontab, wired to a timezone.

The market runs on America/New_York (ET). This converts the canonical ET daily
rhythm to a target timezone (the machine's local zone by default) and prints a
ready-to-install crontab block. Used by scripts/install_cron.sh; also runnable
by hand to review the lines before installing.

  python3 scripts/gen_schedule.py --repo /path/to/checkout            # local tz
  python3 scripts/gen_schedule.py --repo /path/to/checkout --tz America/Chicago
  python3 scripts/gen_schedule.py --repo /path/to/checkout --et       # CRON_TZ, Linux
  python3 scripts/gen_schedule.py --repo /path/to/checkout --with-debuts

Notes:
- Every North American zone keeps a fixed offset to ET year-round, so baked
  local times stay correct through DST. For zones that don't shift with US DST
  (e.g. Europe/Asia) prefer --et (Linux cron), or re-run after a DST change.
- --et emits `CRON_TZ=America/New_York`, which is DST-safe and timezone-
  independent but only honored by cron implementations that support it (Linux
  cronie yes; macOS/BSD cron no — use the default local-baked output there).
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKER_START = "# >>> watchman schedule >>>"
MARKER_END = "# <<< watchman schedule <<<"

# Reference dates (stable, unambiguous) for computing any day shift on
# conversion: a Wednesday for weekday jobs, a Sunday, and a 1st-of-month.
_REF_WEEKDAY = date(2025, 6, 4)   # Wednesday
_REF_SUNDAY = date(2025, 6, 8)    # Sunday
_REF_MONTH1 = date(2025, 6, 1)    # 1st


def _local(et_h: int, et_m: int, ref: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(ref, time(et_h, et_m), tzinfo=ET).astimezone(tz)


def _weekday_field(delta_days: int) -> str:
    """cron day-of-week for Mon-Fri shifted by delta days (0=Sun..6=Sat)."""
    shifted = sorted((d + delta_days) % 7 for d in (1, 2, 3, 4, 5))
    if shifted == [1, 2, 3, 4, 5]:
        return "1-5"
    return ",".join(str(d) for d in shifted)


def crontab(repo: str, tz_name: str | None = None, use_cron_tz: bool = False,
            with_debuts: bool = False) -> str:
    lines: list[str] = [MARKER_START,
                        "# Watchman daily rhythm. Regenerate with scripts/install_cron.sh.",
                        f'WATCHMAN_HOME={repo}']
    runner = '"$WATCHMAN_HOME/scripts/watchman_run.sh"'

    if use_cron_tz:
        lines.append("CRON_TZ=America/New_York   # schedule below is ET (DST-safe)")
        tz = ET
    else:
        tz = ZoneInfo(tz_name) if tz_name else _machine_tz()
        lines.append(f"# Times below are {tz.key} local, converted from ET.")

    def at(et_h: int, et_m: int, kind: str, phase: str, note: str) -> str:
        ref = {"weekdays": _REF_WEEKDAY, "sunday": _REF_SUNDAY,
               "month1": _REF_MONTH1}[kind]
        loc = _local(et_h, et_m, ref, tz)
        delta = (loc.date() - datetime.combine(ref, time(et_h, et_m),
                                               tzinfo=ET).astimezone(ET).date()).days
        if kind == "weekdays":
            dow, dom = _weekday_field(delta), "*"
        elif kind == "sunday":
            dow, dom = str((0 + delta) % 7), "*"
        else:  # month1
            dow, dom = "*", "1"
            if delta:
                lines.append(f"# NOTE: {phase} shifts a day in {tz.key}; verify the "
                             "day-of-month by hand.")
        return f"{loc.minute} {loc.hour} {dom} * {dow}   {runner} {phase}   # {note}"

    lines.append(at(8, 5, "weekdays", "morning", "pre-open scan + morning brief"))

    # Intraday: every 5 min 09:35-15:55 ET. Convert the open/close hours.
    open_loc = _local(9, 35, _REF_WEEKDAY, tz)
    close_loc = _local(15, 55, _REF_WEEKDAY, tz)
    if open_loc.date() == close_loc.date():
        dow = _weekday_field((open_loc.date() - _REF_WEEKDAY).days)
        oh, ch = open_loc.hour, close_loc.hour
        lines.append(f"35,40,45,50,55 {oh} * * {dow}   {runner} intraday   "
                     "# session, opening hour")
        if ch > oh:
            lines.append(f"*/5 {oh + 1}-{ch} * * {dow}   {runner} intraday   "
                         "# session, rest of the day")
    else:
        lines.append("# NOTE: the intraday session crosses local midnight in "
                     f"{tz.key}; split the */5 lines by hand or use --et.")

    lines.append(at(16, 15, "weekdays", "evening", "resolve + evening report + dashboard"))
    lines.append(at(18, 0, "sunday", "weekly", "refresh the Module A watchlist"))
    lines.append(at(18, 30, "month1", "monthly", "long-term paper rebalance"))
    if with_debuts:
        lines.append(at(7, 30, "weekdays", "debuts", "new-listing scan (needs Finnhub/FMP key)"))

    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def _machine_tz() -> ZoneInfo:
    from datetime import datetime as _dt

    local = _dt.now().astimezone().tzinfo
    key = getattr(local, "key", None)
    if key:
        return ZoneInfo(key)
    # Fall back to reading /etc/localtime's target, else ET.
    try:
        import os

        link = os.readlink("/etc/localtime")
        return ZoneInfo(link.split("zoneinfo/")[-1])
    except (OSError, ValueError):
        return ET


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a Watchman crontab block.")
    ap.add_argument("--repo", required=True, help="Absolute path to the checkout")
    ap.add_argument("--tz", default=None, help="IANA timezone (default: machine local)")
    ap.add_argument("--et", action="store_true", help="Emit CRON_TZ=ET (Linux, DST-safe)")
    ap.add_argument("--with-debuts", action="store_true",
                    help="Include the debuts phase (needs a Finnhub/FMP key)")
    args = ap.parse_args()
    print(crontab(args.repo, tz_name=args.tz, use_cron_tz=args.et,
                  with_debuts=args.with_debuts), end="")


if __name__ == "__main__":
    main()
