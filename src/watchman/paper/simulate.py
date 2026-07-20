"""Auto-take Module B signals as paper trades and resolve their outcomes.

Resolution rules (explicit, pessimistic where ambiguous):
- A long is STOPPED when a completed bar's low <= stop; it hits TARGET when a
  bar's high >= target1. If the SAME bar touches both, it counts as STOPPED —
  the pessimistic reading. Optimistic tie-breaks are how paper results end up
  better than reality.
- Exits fill at the stop/target price with slippage against the trader
  (worse than the trigger level, never better).
- Day trades EXPIRE at session close: any signal still open at 16:00 ET is
  closed at the last completed bar's close.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from watchman.data.provider import MARKET_CLOSE, AsOfView
from watchman.paper.book import PaperBook
from watchman.paper.ledger import SignalLedger
from watchman.signals.model import Signal


def auto_take(
    book: PaperBook,
    ledger: SignalLedger,
    signals: list[Signal],
    now: datetime,
) -> list[str]:
    """Open a paper position for every finalized signal (paper takes them all,
    delayed or not — that IS the experiment). Returns human-readable notes."""
    notes: list[str] = []
    for signal in signals:
        signal_id = ledger.record(signal, now.date())
        outcome = book.open_position(
            ts=now,
            symbol=signal.symbol,
            direction=signal.direction,
            qty=signal.shares,
            reference_price=signal.entry,
            signal_id=signal_id,
            stop=signal.stop,
            target=signal.targets[0],
        )
        if isinstance(outcome, str):
            # Cash-blocked after gates passed (e.g. capital tied up): the
            # ledger row expires immediately as untaken rather than vanishing.
            ledger.resolve(signal_id, "expired", now, signal.entry, 0.0)
            notes.append(f"{signal.symbol} {signal.setup}: NOT taken — {outcome}")
        else:
            notes.append(
                f"{signal.symbol} {signal.setup} {signal.direction} x{signal.shares} "
                f"taken @ {outcome.avg_cost:.2f} (entry {signal.entry:.2f} + costs)"
            )
    return notes


def resolve_open_signals(
    book: PaperBook,
    ledger: SignalLedger,
    view: AsOfView,
    interval: str,
) -> list[str]:
    """Walk each open day-trade position's completed bars since entry and
    settle stop/target/expiry. Returns human-readable notes."""
    notes: list[str] = []
    for pos in book.open_positions():
        if pos.signal_id is None or pos.stop is None or pos.target is None:
            continue  # not a signal-linked day trade
        try:
            bars = view.intraday_bars(pos.symbol, interval, days=2)
        except Exception as exc:
            notes.append(f"{pos.symbol}: cannot resolve (no bars: {exc})")
            continue
        bars = bars.loc[bars.index > pos.opened_at]
        if bars.empty:
            continue

        status = exit_reference = None
        exit_ts = None
        for ts, bar in bars.iterrows():
            if pos.direction == "long":
                stopped = float(bar["low"]) <= pos.stop
                targeted = float(bar["high"]) >= pos.target
            else:
                stopped = float(bar["high"]) >= pos.stop
                targeted = float(bar["low"]) <= pos.target
            if stopped:  # pessimistic: stop wins any same-bar tie
                status, exit_reference, exit_ts = "stopped", pos.stop, ts
                break
            if targeted:
                status, exit_reference, exit_ts = "target", pos.target, ts
                break

        if status is None:
            # Expire at session close: position still open after 16:00 ET.
            last_ts = bars.index[-1]
            session_close = datetime.combine(
                last_ts.date(), MARKET_CLOSE, tzinfo=last_ts.tzinfo
            )
            if view.as_of >= session_close or last_ts.date() < view.as_of.date():
                status = "expired"
                exit_reference = float(bars["close"].iloc[-1])
                exit_ts = last_ts
            else:
                continue  # still live intraday

        exit_dt = exit_ts.to_pydatetime() + _interval_delta(interval)
        pnl = book.close_position(pos, exit_dt, exit_reference, note=status)
        ledger.resolve(pos.signal_id, status, exit_dt, exit_reference, pnl)
        notes.append(
            f"{pos.symbol} {pos.direction} x{pos.qty:.0f}: {status.upper()} "
            f"@ {exit_reference:.2f} -> PnL ${pnl:+,.2f}"
        )
    return notes


def _interval_delta(interval: str) -> timedelta:
    from watchman.data.provider import INTRADAY_INTERVALS

    return INTRADAY_INTERVALS[interval]


def last_prices(view: AsOfView, symbols: list[str], interval: str) -> dict[str, float]:
    """Latest completed-bar close for each symbol (skips symbols w/o data)."""
    prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            bars = view.intraday_bars(symbol, interval, days=2)
            if not bars.empty:
                prices[symbol] = float(bars["close"].iloc[-1])
                continue
        except Exception:
            pass
        try:
            daily = view.daily_bars(symbol, start=view.as_of - timedelta(days=10))
            if not daily.empty:
                prices[symbol] = float(daily["close"].iloc[-1])
        except Exception:
            continue
    return prices
