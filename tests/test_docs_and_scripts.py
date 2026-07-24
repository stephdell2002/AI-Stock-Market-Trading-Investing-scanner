"""Phase 6 guardrails: the scheduling scripts stay valid and the docs stay
consistent with the real CLI. These catch drift (a renamed command, a broken
script) before it reaches a user's crontab."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from watchman import __version__
from watchman.cli import build_parser

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
DOCS = REPO / "docs"

# The subcommands the CLI actually exposes (source of truth for the docs).
CLI_COMMANDS = set(
    build_parser()._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
)
# The phases the runner scripts dispatch on.
PHASES = {"morning", "intraday", "evening", "weekly", "debuts", "monthly"}


class TestVersion:
    def test_v1_shipped(self):
        # Phase 6 is the last phase; v1 is complete.
        assert __version__ == "1.0.0"


class TestFilesExist:
    @pytest.mark.parametrize(
        "rel",
        [
            "scripts/watchman_run.sh",
            "scripts/watchman_run.ps1",
            "scripts/crontab.example",
            "scripts/gen_schedule.py",
            "scripts/install_cron.sh",
            "scripts/register_tasks.ps1",
            "docs/scheduling.md",
            "docs/first-90-days.md",
            "docs/realtime-data.md",
            "docs/debuts.md",
        ],
    )
    def test_present_and_nonempty(self, rel):
        path = REPO / rel
        assert path.exists(), f"missing {rel}"
        assert path.stat().st_size > 200, f"{rel} looks empty"


class TestShellScript:
    @pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
    @pytest.mark.parametrize("script", ["watchman_run.sh", "install_cron.sh"])
    def test_bash_syntax_is_valid(self, script):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS / script)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_script_handles_every_phase(self):
        text = (SCRIPTS / "watchman_run.sh").read_text()
        for phase in PHASES:
            assert re.search(rf"^\s*{phase}\)", text, re.MULTILINE), (
                f"watchman_run.sh has no case branch for '{phase}'"
            )

    def test_powershell_validates_phases(self):
        text = (SCRIPTS / "watchman_run.ps1").read_text()
        # ValidateSet must list exactly the phases the bash script handles.
        match = re.search(r"ValidateSet\(([^)]*)\)", text)
        assert match is not None
        listed = set(re.findall(r"'([a-z]+)'", match.group(1)))
        assert listed == PHASES


class TestDocsMatchCli:
    def test_scheduling_only_references_real_commands(self):
        """Every `run <cmd>` invocation in the runner is a real command."""
        text = (SCRIPTS / "watchman_run.sh").read_text()
        invoked = re.findall(r"^\s*run (\w+)", text, re.MULTILINE)
        assert invoked, "no `run <cmd>` invocations found — regex drifted"
        for cmd in invoked:
            assert cmd in CLI_COMMANDS, f"runner calls unknown command '{cmd}'"

    def test_crontab_points_at_the_runner(self):
        text = (SCRIPTS / "crontab.example").read_text()
        assert "watchman_run.sh" in text
        for phase in PHASES:
            assert phase in text, f"crontab.example never schedules '{phase}'"

    def test_first_90_days_states_the_pdt_rule(self):
        text = (DOCS / "first-90-days.md").read_text()
        assert "$25,000" in text or "$25k" in text
        assert "pattern day trader" in text.lower() or "PDT" in text

    def test_first_90_days_keeps_the_honesty_thresholds(self):
        text = (DOCS / "first-90-days.md").read_text().lower()
        # The scorecard must keep its go/no-go spine.
        assert "expectancy" in text
        assert "divergence" in text
        assert "sample size" in text
        assert "paper only" in text or "paper-only" in text

    def test_debuts_doc_keeps_its_honesty_spine(self):
        text = (DOCS / "debuts.md").read_text().lower()
        # Module F's whole point is honesty about a thin-data case.
        assert "not investment advice" in text or "not advice" in text
        assert "underperform" in text          # documented IPO reality
        assert "base rate" in text
        assert "skeptical" in text             # the verdict never just says BUY

    def test_readme_marks_all_phases_done(self):
        text = (REPO / "README.md").read_text()
        # No phase should still read "pending" once v1 ships.
        assert "pending sign-off" not in text
        assert "docs/first-90-days.md" in text
        assert "docs/scheduling.md" in text
