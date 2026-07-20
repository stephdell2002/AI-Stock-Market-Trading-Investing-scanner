"""Module E — the broker layer contract (read-only, deliberately).

The v1 interface has NO order-placement method. That is not an oversight:
principle 1 says no real-money execution, and the interface physically not
having a `place_order` is how the principle is enforced at this layer. When
(much later, after the first-90-days evaluation) execution is considered, it
arrives as a separate, explicitly-opted-into interface.

Which brokers/platforms can ever get an adapter is governed by principle 6
(official APIs only). BROKER_REGISTRY below is the single source of truth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from watchman.data.provider import Freshness


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    as_of: datetime
    freshness: Freshness  # anything non-REALTIME gets labeled, never hidden


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: float
    avg_cost: float


@dataclass(frozen=True)
class Balances:
    cash: float
    equity: float
    currency: str = "USD"


class BrokerAdapter(ABC):
    """Read-only window into a brokerage account: quotes, positions, balances.

    Implementations may only target brokers listed in BROKER_REGISTRY with
    official_api=True, and only when the user supplies credentials via env
    vars. Never scrape, never use reverse-engineered private APIs.
    """

    name: str = "abstract"
    read_only: bool = True  # v1 invariant; there is no order API to disable

    @abstractmethod
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def balances(self) -> Balances: ...


@dataclass(frozen=True)
class BrokerSupport:
    broker: str
    official_api: bool
    note: str


#: Single source of truth for what can ever be integrated, and why/why not.
BROKER_REGISTRY: dict[str, BrokerSupport] = {
    "ibkr": BrokerSupport(
        "Interactive Brokers", True,
        "Official Client Portal / TWS APIs; IBKR Lite is commission-free. "
        "Planned first-class adapter.",
    ),
    "tradier": BrokerSupport(
        "Tradier", True, "Official REST API with a sandbox. Planned adapter."
    ),
    "schwab": BrokerSupport(
        "Charles Schwab", True, "Official developer API. Planned adapter."
    ),
    "alpaca": BrokerSupport(
        "Alpaca", True,
        "Official API, paper-trading native. Strong candidate for the FIRST "
        "adapter: Watchman's paper mode can mirror to Alpaca paper for free.",
    ),
    "wealthsimple": BrokerSupport(
        "Wealthsimple", False,
        "NO official trading/data API. Per principle 6, Watchman will never "
        "scrape it or use reverse-engineered endpoints: it remains a signals "
        "dashboard the user acts on manually in Wealthsimple. Becomes an "
        "adapter candidate only if an official API ships.",
    ),
    "robinhood": BrokerSupport(
        "Robinhood", False, "No official trading API for stocks. Dashboard-only."
    ),
    "webull": BrokerSupport(
        "Webull", False, "No official trading API for stocks. Dashboard-only."
    ),
}


def adapter_allowed(broker_key: str) -> bool:
    """May an adapter be built for this broker at all?"""
    support = BROKER_REGISTRY.get(broker_key.lower())
    return support is not None and support.official_api
