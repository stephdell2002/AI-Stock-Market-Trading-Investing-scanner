"""Module E contract: read-only by construction, official-API gate enforced."""

from __future__ import annotations

from watchman.broker import BROKER_REGISTRY, BrokerAdapter, adapter_allowed
from watchman.data.universe import to_tradingview_symbol


class TestReadOnlyByConstruction:
    def test_adapter_interface_has_no_order_methods(self):
        forbidden = [
            name for name in dir(BrokerAdapter)
            if any(word in name.lower() for word in ("order", "trade", "buy", "sell"))
        ]
        assert forbidden == [], (
            f"BrokerAdapter grew order-shaped methods {forbidden}; v1 is read-only "
            "by construction — see broker/adapter.py docstring before changing this."
        )

    def test_read_only_flag_default(self):
        assert BrokerAdapter.read_only is True


class TestOfficialApiGate:
    def test_official_api_brokers_allowed(self):
        for key in ("ibkr", "tradier", "schwab", "alpaca"):
            assert adapter_allowed(key), key

    def test_no_official_api_brokers_blocked(self):
        for key in ("wealthsimple", "robinhood", "webull"):
            assert not adapter_allowed(key), key

    def test_unknown_broker_blocked_by_default(self):
        assert not adapter_allowed("sketchy-broker-x")

    def test_wealthsimple_entry_explains_the_dashboard_path(self):
        note = BROKER_REGISTRY["wealthsimple"].note
        assert "NO official" in note
        assert "manually" in note


class TestTradingViewExport:
    def test_share_class_symbols_use_dots(self):
        assert to_tradingview_symbol("BRK-B") == "BRK.B"
        assert to_tradingview_symbol("brk.b") == "BRK.B"
        assert to_tradingview_symbol("AAPL") == "AAPL"
