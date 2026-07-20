"""CLI smoke tests (offline: only commands that don't need a provider)."""

from __future__ import annotations

from pathlib import Path

import pytest

from watchman.cli import main

REPO_CONFIG = str(Path(__file__).parent.parent / "config")


def test_universe_lists_symbols(capsys):
    rc = main(["--config-dir", REPO_CONFIG, "universe"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "unique symbols" in out
    assert "AAPL" in out


def test_stub_commands_name_their_phase(capsys):
    stubs = [("report", 5)]
    for command, phase in stubs:
        rc = main(["--config-dir", REPO_CONFIG, command])
        assert rc == 0
        assert f"Phase {phase}" in capsys.readouterr().out


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main(["--config-dir", REPO_CONFIG, "yolo-trade"])
