"""Reporting: self-contained HTML dashboard, morning brief, evening recap.
Everything reads from the same SQLite the ledger and paper books write to.
"""

from watchman.report.briefs import evening_recap, morning_brief, write_brief
from watchman.report.html import build_report, write_report

__all__ = [
    "build_report",
    "evening_recap",
    "morning_brief",
    "write_brief",
    "write_report",
]
