from __future__ import annotations

from app.services.broker_market_policy import allowed_market_types
from app.services.live_trading.capabilities import (
    CRYPTO_VENUE_CAPABILITIES,
    canonical_exchange_id,
    crypto_exchange_ids_for_market_type,
    supported_crypto_exchange_ids,
)


def test_supported_crypto_exchange_ids_are_canonical():
    assert supported_crypto_exchange_ids() == set(CRYPTO_VENUE_CAPABILITIES)
    assert "coinbase_exchange" not in supported_crypto_exchange_ids()
    assert "coinbase_exchange" in supported_crypto_exchange_ids(include_aliases=True)


def test_exchange_aliases_canonicalize():
    assert canonical_exchange_id("coinbase_exchange") == "coinbaseexchange"
    assert canonical_exchange_id("CoinbaseExchange") == "coinbaseexchange"
    assert canonical_exchange_id("binance") == "binance"


def test_policy_uses_capability_matrix():
    for exchange_id, capability in CRYPTO_VENUE_CAPABILITIES.items():
        assert allowed_market_types(exchange_id, "Crypto") == set(capability.market_types)


def test_market_type_filters():
    assert "coinbaseexchange" in crypto_exchange_ids_for_market_type("spot")
    assert "coinbaseexchange" not in crypto_exchange_ids_for_market_type("swap")
    assert "kraken" in crypto_exchange_ids_for_market_type("futures")
