"""Guardrails for the timezone-aware crontab generator (scripts/gen_schedule.py).

The whole point of this generator is that ET market times land in the user's
crontab as *correct local times*. These tests pin that: Eastern reproduces the
ET rhythm verbatim, a westward zone shifts by the right number of hours, every
emitted schedule line is a syntactically valid 5-field cron entry, and the
marker block the installer keys off of is always present. All offline."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
GEN_PATH = REPO / "scripts" / "gen_schedule.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_schedule", GEN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


gen = _load_gen()


def _schedule_lines(block: str) -> list[str]:
    """The cron entries only — skip markers, comments, and env assignments."""
    out = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "watchman_run.sh" not in stripped:  # env lines like WATCHMAN_HOME=...
            continue
        out.append(stripped)
    return out


class TestMarkers:
    def test_block_is_delimited_for_the_installer(self):
        block = gen.crontab("/repo")
        assert block.startswith(gen.MARKER_START)
        assert block.rstrip().endswith(gen.MARKER_END)

    def test_repo_path_is_pinned(self):
        block = gen.crontab("/home/me/watchman")
        assert "WATCHMAN_HOME=/home/me/watchman" in block


class TestEasternIsTheCanonicalRhythm:
    """EST == America/New_York == the market's own zone, so the generated times
    must equal the canonical ET schedule with no shift."""

    def setup_method(self):
        self.block = gen.crontab("/repo", tz_name="America/New_York")

    def test_morning_is_0805_weekdays(self):
        assert re.search(r"^5 8 \* \* 1-5\s+\S+watchman_run\.sh\" morning",
                         self.block, re.MULTILINE)

    def test_intraday_opening_hour_and_rest(self):
        assert "35,40,45,50,55 9 * * 1-5" in self.block
        assert "*/5 10-15 * * 1-5" in self.block

    def test_evening_weekly_monthly(self):
        assert re.search(r"^15 16 \* \* 1-5\s+\S+ evening", self.block, re.MULTILINE)
        assert re.search(r"^0 18 \* \* 0\s+\S+ weekly", self.block, re.MULTILINE)
        assert re.search(r"^30 18 1 \* \*\s+\S+ monthly", self.block, re.MULTILINE)


class TestWestwardZoneShifts:
    def test_pacific_is_three_hours_earlier(self):
        block = gen.crontab("/repo", tz_name="America/Los_Angeles")
        # 08:05 ET -> 05:05 PT; 16:15 ET -> 13:15 PT; 18:00 ET -> 15:00 PT.
        assert re.search(r"^5 5 \* \* 1-5\s+\S+ morning", block, re.MULTILINE)
        assert re.search(r"^15 13 \* \* 1-5\s+\S+ evening", block, re.MULTILINE)
        assert re.search(r"^0 15 \* \* 0\s+\S+ weekly", block, re.MULTILINE)
        # Intraday opening hour follows the open into 06:xx PT.
        assert "35,40,45,50,55 6 * * 1-5" in block


class TestCronTzMode:
    def test_et_flag_emits_cron_tz_and_keeps_et_times(self):
        block = gen.crontab("/repo", use_cron_tz=True)
        assert "CRON_TZ=America/New_York" in block
        # Under CRON_TZ the times are the raw ET ones (no local conversion).
        assert re.search(r"^5 8 \* \* 1-5\s+\S+ morning", block, re.MULTILINE)


class TestDebutsToggle:
    def test_absent_by_default(self):
        assert "debuts" not in gen.crontab("/repo", tz_name="America/New_York")

    def test_present_when_requested(self):
        block = gen.crontab("/repo", tz_name="America/New_York", with_debuts=True)
        assert re.search(r"^30 7 \* \* 1-5\s+\S+ debuts", block, re.MULTILINE)


class TestEveryLineIsValidCron:
    @pytest.mark.parametrize("kwargs", [
        {"tz_name": "America/New_York"},
        {"tz_name": "America/Los_Angeles"},
        {"tz_name": "America/Chicago"},
        {"use_cron_tz": True},
        {"tz_name": "America/New_York", "with_debuts": True},
    ])
    def test_five_fields_and_a_real_phase(self, kwargs):
        block = gen.crontab("/repo", **kwargs)
        for line in _schedule_lines(block):
            fields = line.split()
            minute, hour, dom, month, dow = fields[:5]
            # Each field is only digits and cron punctuation.
            for f in (minute, hour, dom, month, dow):
                assert re.fullmatch(r"[\d,*/-]+", f), f"bad cron field {f!r} in {line!r}"
            # Hours and minutes stay in range even after conversion.
            for token in re.split(r"[,/-]", hour.replace("*", "")):
                if token:
                    assert 0 <= int(token) <= 23
            phase = line.rsplit("watchman_run.sh\"", 1)[1].split()[0]
            assert phase in {"morning", "intraday", "evening", "weekly",
                             "monthly", "debuts"}


class TestWeekdayField:
    def test_no_shift_is_monday_to_friday(self):
        assert gen._weekday_field(0) == "1-5"

    def test_positive_shift_wraps(self):
        # Shifting Mon-Fri forward a day gives Tue-Sat (2,3,4,5,6).
        assert gen._weekday_field(1) == "2,3,4,5,6"

    def test_negative_shift_wraps_through_sunday(self):
        # Shifting back a day gives Sun-Thu (0,1,2,3,4).
        assert gen._weekday_field(-1) == "0,1,2,3,4"
