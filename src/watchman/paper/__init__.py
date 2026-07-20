"""Module D — paper trading engine and the signal ledger (the honesty
mechanism). Simulated fills with slippage, persistent positions/P&L/equity in
SQLite, every signal's outcome logged, rolling live win rates fed back into
signal confidence. No real-money execution, ever, in v1.
"""

from watchman.paper.book import DAY, LONGTERM, PaperBook, PaperPosition
from watchman.paper.ledger import LedgerRow, SetupStats, SignalLedger
from watchman.paper.longterm import rebalance_longterm, watchlist_changes
from watchman.paper.simulate import auto_take, last_prices, resolve_open_signals

__all__ = [
    "DAY",
    "LONGTERM",
    "LedgerRow",
    "PaperBook",
    "PaperPosition",
    "SetupStats",
    "SignalLedger",
    "auto_take",
    "last_prices",
    "rebalance_longterm",
    "resolve_open_signals",
    "watchlist_changes",
]
