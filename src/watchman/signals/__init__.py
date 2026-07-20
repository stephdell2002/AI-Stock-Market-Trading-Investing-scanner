"""Module B — Signals: intraday setups on liquid US equities.

Pre-market scanner (focus list <= 10), three setup classes (Opening Range
Breakout, VWAP reclaim/rejection, high-relative-volume continuation), and the
SignalEngine that enforces every gate: R:R >= 2, 1%-risk sizing, circuit
breaker, max concurrent positions, confidence from the live ledger. Signals
built on non-realtime data are labeled DELAYED — NOT ACTIONABLE.
"""

from watchman.signals.engine import SignalEngine
from watchman.signals.model import (
    ConfidenceSource,
    GateState,
    NoLiveHistory,
    RejectedSignal,
    Signal,
    SignalDraft,
)
from watchman.signals.runner import SignalsResult, load_focus_list, run_scan, run_signals
from watchman.signals.scanner import ScannerCandidate, ScannerInput, ScanOutcome, scan_premarket
from watchman.signals.setups import (
    ALL_SETUPS,
    OpeningRangeBreakout,
    RelVolContinuation,
    Setup,
    SetupContext,
    VwapReclaimReject,
)

__all__ = [
    "ALL_SETUPS",
    "ConfidenceSource",
    "GateState",
    "NoLiveHistory",
    "OpeningRangeBreakout",
    "RejectedSignal",
    "RelVolContinuation",
    "ScanOutcome",
    "ScannerCandidate",
    "ScannerInput",
    "Setup",
    "SetupContext",
    "Signal",
    "SignalDraft",
    "SignalEngine",
    "SignalsResult",
    "VwapReclaimReject",
    "load_focus_list",
    "run_scan",
    "run_signals",
    "scan_premarket",
]
