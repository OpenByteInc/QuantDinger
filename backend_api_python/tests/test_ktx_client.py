"""Smoke tests for KTX client (no real API calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.ktx import KtxClient
from app.services.live_trading.symbols import to_ktx_symbol


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------


def test_to_ktx_symbol_spot():
    assert to_ktx_symbol("BTC/USDT", market_type="spot") == "BTC_USDT"
    assert to_ktx_symbol("ETH/USDT", market_type="spot") == "ETH_USDT"


def test_to_ktx_symbol_swap():
    assert to_ktx_symbol("BTC/USDT", market_type="swap") == "BTC_USDT_SWAP"
    assert to_ktx_symbol("ETH/USDT", market_type="futures") == "ETH_USDT_SWAP"
    assert to_ktx_symbol("BTC/USDT", market_type="perp") == "BTC_USDT_SWAP"


def test_to_ktx_symbol_already_normalized():
    # When the input already contains an underscore separator, ensure no double-suffix.
    assert to_ktx_symbol("BTC_USDT_SWAP", market_type="swap") == "BTC_USDT_SWAP"


# ---------------------------------------------------------------------------
# Client init / config
# ---------------------------------------------------------------------------


def test_ktx_client_init_requires_keys():
    with pytest.raises(LiveTradingError):
        KtxClient(api_key="", secret_key="")
    with pytest.raises(LiveTradingError):
        KtxClient(api_key="k", secret_key="")


def test_ktx_client_default_is_swap():
    c = KtxClient(api_key="k", secret_key="s")
    assert c.market_type == "swap"
    assert c.api_key == "k"


def test_ktx_client_market_type_aliases():
    for alias in ("futures", "future", "perp", "perpetual", "lpc"):
        c = KtxClient(api_key="k", secret_key="s", market_type=alias)
        assert c.market_type == "swap"
    c2 = KtxClient(api_key="k", secret_key="s", market_type="spot")
    assert c2.market_type == "spot"


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------


def test_signed_post_uses_raw_body_to_match_signature():
    """Regression: POST must send ``data=body_str`` so wire bytes equal signed bytes."""
    c = KtxClient(api_key="k", secret_key="s")
    captured = {}

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["data"] = kwargs.get("data")
        captured["headers"] = kwargs.get("headers") or {}
        return 200, {"result": {"id": "1"}}, "{}"

    with patch.object(c, "_request", side_effect=fake_request):
        c._signed_request("POST", "/v1/order", json_body={"symbol": "BTC_USDT_SWAP", "side": "buy"})

    # JSON dumped via _json_dumps is compact (no spaces).
    assert captured["data"] == '{"symbol":"BTC_USDT_SWAP","side":"buy"}'
    assert captured["headers"].get("Content-Type") == "application/json"
    assert "api-key" in captured["headers"]
    assert "api-sign" in captured["headers"]
    assert "api-expire-time" in captured["headers"]


def test_signed_get_appends_sorted_query_string():
    c = KtxClient(api_key="k", secret_key="s")
    captured = {}

    def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        return 200, {"result": []}, "{}"

    with patch.object(c, "_request", side_effect=fake_request):
        c._signed_request("GET", "/v1/orders", params={"symbol": "BTC_USDT", "limit": 10})

    assert captured["method"] == "GET"
    # Sorted params: limit before symbol
    assert captured["path"] == "/papi/v1/orders?limit=10&symbol=BTC_USDT"
