"""Module E — broker layer (read-only by construction).

The BrokerAdapter contract exists now; concrete adapters arrive when the user
supplies credentials for a broker with an official API (see BROKER_REGISTRY).
Brokers without official APIs (Wealthsimple, Robinhood, Webull) never get
adapters — for those, Watchman remains a signals dashboard acted on manually.
"""

from watchman.broker.adapter import (
    BROKER_REGISTRY,
    Balances,
    BrokerAdapter,
    BrokerPosition,
    BrokerSupport,
    Quote,
    adapter_allowed,
)

__all__ = [
    "BROKER_REGISTRY",
    "Balances",
    "BrokerAdapter",
    "BrokerPosition",
    "BrokerSupport",
    "Quote",
    "adapter_allowed",
]
