from app.services.live_trading.bitget_compat import _cache_key
from app.services.live_trading.ws_order_transport import _pool_key
from app.services.pending_orders.position_sync_cache import position_sync_cache_key
from app.utils.security_fingerprint import opaque_fingerprint


def test_opaque_fingerprint_is_stable_and_separates_namespaces():
    first = opaque_fingerprint("secret", "value", namespace="first")
    repeated = opaque_fingerprint("secret", "value", namespace="first")
    other_namespace = opaque_fingerprint("secret", "value", namespace="second")

    assert first == repeated
    assert first != other_namespace
    assert "secret" not in first
    assert len(first) == 64


def test_sensitive_cache_keys_do_not_contain_api_key_material():
    api_key = "public-prefix-super-sensitive-api-key-suffix"

    position_key = position_sync_cache_key(
        7,
        "binance",
        "swap",
        {"api_key": api_key},
    )
    bitget_key = _cache_key(
        api_key=api_key,
        base_url="https://api.bitget.com",
        market_type="swap",
        simulated=False,
    )
    websocket_key = _pool_key(
        "binance",
        "wss://ws-api.binance.com/ws-api/v3",
        api_key,
        "live",
    )

    for cache_key in (position_key, bitget_key, websocket_key):
        assert api_key not in cache_key
        assert api_key[-16:] not in cache_key


def test_credential_id_cache_key_remains_readable_without_secret_material():
    assert position_sync_cache_key(
        7,
        "binance",
        "swap",
        {"credential_id": 42, "api_key": "ignored-secret"},
    ) == "u7:binance:swap:cred:42"
