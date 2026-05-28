"""
Comprehensive tests for KTX (direct REST) client.

Covers:
- Symbol normalization (to_ktx_symbol)
- Client initialization & validation
- Numeric helpers (Decimal, step floor, precision)
- Authentication & signing (HMAC-SHA256, raw body)
- Market data endpoints (products, ticker, ping)
- Account endpoints (balance, positions)
- Order lifecycle (place market/limit, get, cancel, open orders)
- wait_for_fill polling
- set_leverage
- get_fee_rate
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.live_trading.ktx import KtxClient
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.symbols import to_ktx_symbol


# ===================================================================
# Symbol normalization
# ===================================================================

class TestToKtxSymbol:
    """Test symbol conversion for spot and swap."""

    def test_spot_normalizes_underscore(self):
        assert to_ktx_symbol("BTC/USDT", market_type="spot") == "BTC_USDT"

    def test_spot_already_underscore(self):
        assert to_ktx_symbol("BTC_USDT", market_type="spot") == "BTC_USDT"

    def test_swap_appends_suffix(self):
        assert to_ktx_symbol("BTC/USDT", market_type="swap") == "BTC_USDT_SWAP"

    def test_swap_already_has_suffix(self):
        assert to_ktx_symbol("BTC_USDT_SWAP", market_type="swap") == "BTC_USDT_SWAP"

    def test_swap_removes_then_readds_suffix(self):
        # If input already has _SWAP, it should be idempotent
        assert to_ktx_symbol("BTC_USDT", market_type="swap") == "BTC_USDT_SWAP"

    def test_default_market_type_is_swap(self):
        assert to_ktx_symbol("BTC/USDT") == "BTC_USDT_SWAP"


# ===================================================================
# Client initialization
# ===================================================================

class TestKtxClientInit:
    """Test client construction & validation."""

    def test_requires_api_key(self):
        with pytest.raises(LiveTradingError, match="Missing KTX api_key/secret_key"):
            KtxClient(api_key="", secret_key="s")

    def test_requires_secret_key(self):
        with pytest.raises(LiveTradingError, match="Missing KTX api_key/secret_key"):
            KtxClient(api_key="k", secret_key="")

    def test_default_market_type_is_swap(self):
        c = KtxClient(api_key="k", secret_key="s")
        assert c.market_type == "swap"

    def test_market_type_aliases(self):
        for alias in ("futures", "future", "perp", "perpetual", "lpc"):
            c = KtxClient(api_key="k", secret_key="s", market_type=alias)
            assert c.market_type == "swap"

    def test_market_type_spot(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        assert c.market_type == "spot"

    def test_base_url_strips_trailing_slash(self):
        c = KtxClient(api_key="k", secret_key="s", base_url="https://api.ktx.app/")
        assert c.base_url == "https://api.ktx.app"


# ===================================================================
# Numeric helpers
# ===================================================================

class TestNumericHelpers:
    """Test Decimal conversion, step flooring, precision."""

    def test_to_dec_float(self):
        assert KtxClient._to_dec(1.5) == Decimal("1.5")

    def test_to_dec_string(self):
        assert KtxClient._to_dec("0.001") == Decimal("0.001")

    def test_to_dec_invalid(self):
        assert KtxClient._to_dec("abc") == Decimal("0")

    def test_dec_str_zero(self):
        assert KtxClient._dec_str(Decimal("0")) == "0"

    def test_dec_str_with_precision(self):
        # strict_precision=2 should round to 2 decimals
        assert KtxClient._dec_str(Decimal("1.234"), strict_precision=2) == "1.23"

    def test_dec_str_strips_trailing_zeros(self):
        assert KtxClient._dec_str(Decimal("1.500")) == "1.5"

    def test_floor_to_step(self):
        assert KtxClient._floor_to_step(Decimal("1.7"), Decimal("0.5")) == Decimal("1.5")

    def test_floor_to_step_zero_step(self):
        assert KtxClient._floor_to_step(Decimal("1.7"), Decimal("0")) == Decimal("1.7")

    def test_floor_to_step_exact(self):
        assert KtxClient._floor_to_step(Decimal("2.0"), Decimal("0.5")) == Decimal("2.0")


# ===================================================================
# Authentication & signing
# ===================================================================

class TestSigning:
    """Test HMAC-SHA256 signature generation."""

    def test_sign_produces_hex(self):
        c = KtxClient(api_key="test_key", secret_key="test_secret")
        sig = c._sign("hello")
        assert len(sig) == 64  # SHA-256 hex length
        assert all(ch in "0123456789abcdef" for ch in sig)

    def test_sign_is_deterministic(self):
        c = KtxClient(api_key="k", secret_key="s")
        assert c._sign("msg1") == c._sign("msg1")
        assert c._sign("msg1") != c._sign("msg2")

    def test_signed_post_uses_raw_body_to_match_signature(self):
        """Critical: POST body must be sent as raw string, not re-serialized by requests."""
        c = KtxClient(api_key="k", secret_key="s")
        captured = {}

        def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["data"] = kwargs.get("data")
            captured["headers"] = kwargs.get("headers") or {}
            return 200, {"result": {"id": "1"}}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            c._signed_request(
                "POST",
                "/v1/order",
                json_body={"symbol": "BTC_USDT_SWAP", "side": "buy"},
            )

        # Body must be the exact string used for signing
        assert captured["data"] == '{"symbol":"BTC_USDT_SWAP","side":"buy"}'
        assert captured["headers"].get("Content-Type") == "application/json"
        assert captured["headers"].get("api-key") == "k"
        assert "api-sign" in captured["headers"]
        assert "api-expire-time" in captured["headers"]

    def test_signed_get_appends_sorted_query_string(self):
        """GET query params must be sorted and signed."""
        c = KtxClient(api_key="k", secret_key="s")
        captured = {}

        def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["headers"] = kwargs.get("headers") or {}
            return 200, {"result": []}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            c._signed_request(
                "GET",
                "/v1/orders",
                params={"symbol": "BTC_USDT_SWAP", "status": "open"},
            )

        # Path must contain sorted query string
        assert "status=open" in captured["path"]
        assert "symbol=BTC_USDT_SWAP" in captured["path"]
        assert captured["headers"].get("api-key") == "k"


# ===================================================================
# Market data endpoints
# ===================================================================

class TestMarketData:
    """Test public endpoints: ping, get_ticker, get_products."""

    def test_ping_success(self):
        c = KtxClient(api_key="k", secret_key="s")

        def fake_request(method, path, **kwargs):
            return 200, {"result": []}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            assert c.ping() is True

    def test_ping_failure_returns_false(self):
        c = KtxClient(api_key="k", secret_key="s")

        def fake_request(method, path, **kwargs):
            raise Exception("Connection error")

        with patch.object(c, "_request", side_effect=fake_request):
            assert c.ping() is False

    def test_get_ticker_list_result(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        ticker_response = {
            "result": [
                {
                    "last": "50000.0",
                    "high": "51000.0",
                    "low": "49000.0",
                    "volume": "1000.0",
                }
            ]
        }

        def fake_request(method, path, **kwargs):
            return 200, ticker_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_ticker(symbol="BTC/USDT")

        assert result["last"] == "50000.0"
        assert result["high"] == "51000.0"

    def test_get_ticker_empty_result(self):
        c = KtxClient(api_key="k", secret_key="s")

        def fake_request(method, path, **kwargs):
            return 200, {"result": []}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_ticker(symbol="BTC/USDT")

        assert result == {}


# ===================================================================
# Account endpoints
# ===================================================================

class TestAccountEndpoints:
    """Test get_account, get_balance, get_positions."""

    def test_get_account(self):
        c = KtxClient(api_key="k", secret_key="s")
        account_data = {"result": {"total_equity": "10000.0"}}

        def fake_request(method, path, **kwargs):
            return 200, account_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_account()

        assert result["result"]["total_equity"] == "10000.0"
        # Verify it's a signed request (has api-key header)
        call_kwargs = c._request.call_args
        assert call_kwargs[1].get("headers", {}).get("api-key") == "k"

    def test_get_balance_is_alias(self):
        c = KtxClient(api_key="k", secret_key="s")
        account_data = {"result": {"total_equity": "5000.0"}}

        def fake_request(method, path, **kwargs):
            return 200, account_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_balance()

        assert result["result"]["total_equity"] == "5000.0"

    def test_get_positions_swap(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        positions_data = {
            "result": [
                {
                    "symbol": "BTC_USDT_SWAP",
                    "side": "long",
                    "amount": "0.1",
                    "entry_price": "50000.0",
                }
            ]
        }

        def fake_request(method, path, **kwargs):
            return 200, positions_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_positions()

        assert len(result) == 1
        assert result[0]["symbol"] == "BTC_USDT_SWAP"

    def test_get_positions_spot_returns_empty(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        # Should not make any request
        result = c.get_positions()
        assert result == []


# ===================================================================
# Order lifecycle
# ===================================================================

class TestPlaceMarketOrder:
    """Test market order placement."""

    def test_place_market_order_buy(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        order_response = {"result": {"id": "12345"}}

        def fake_request(method, path, **kwargs):
            return 200, order_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            # Mock _get_product to avoid real API call
            with patch.object(c, "_get_product", return_value={}):
                result = c.place_market_order(
                    symbol="BTC/USDT",
                    side="buy",
                    qty=0.01,
                )

        assert result.exchange_id == "ktx"
        assert result.exchange_order_id == "12345"
        assert result.filled == 0.0
        assert result.avg_price == 0.0

    def test_place_market_order_invalid_side(self):
        c = KtxClient(api_key="k", secret_key="s")
        with pytest.raises(LiveTradingError, match="Invalid side"):
            c.place_market_order(symbol="BTC/USDT", side="invalid", qty=0.01)

    def test_place_market_order_zero_qty(self):
        c = KtxClient(api_key="k", secret_key="s")
        with patch.object(c, "_get_product", return_value={"min_base_amount": "0.001"}):
            with pytest.raises(LiveTradingError, match="Invalid qty"):
                c.place_market_order(symbol="BTC/USDT", side="buy", qty=0.0001)


class TestPlaceLimitOrder:
    """Test limit order placement."""

    def test_place_limit_order(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        order_response = {"result": {"id": "67890"}}

        def fake_request(method, path, **kwargs):
            return 200, order_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            with patch.object(c, "_get_product", return_value={}):
                result = c.place_limit_order(
                    symbol="BTC/USDT",
                    side="sell",
                    qty=0.1,
                    price=50000.0,
                )

        assert result.exchange_id == "ktx"
        assert result.exchange_order_id == "67890"

    def test_place_limit_order_invalid_price(self):
        c = KtxClient(api_key="k", secret_key="s")
        with patch.object(c, "_get_product", return_value={}):
            with pytest.raises(LiveTradingError, match="Invalid price"):
                c.place_limit_order(
                    symbol="BTC/USDT",
                    side="buy",
                    qty=0.01,
                    price=0.0,
                )


class TestGetOrder:
    """Test order query."""

    def test_get_order_by_order_id(self):
        c = KtxClient(api_key="k", secret_key="s")
        order_data = {"result": {"id": "123", "status": "filled"}}

        def fake_request(method, path, **kwargs):
            return 200, order_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_order(symbol="BTC/USDT", order_id="123")

        assert result["result"]["id"] == "123"
        assert result["result"]["status"] == "filled"

    def test_get_order_requires_id(self):
        c = KtxClient(api_key="k", secret_key="s")
        with pytest.raises(LiveTradingError, match="requires order_id or client_order_id"):
            c.get_order(symbol="BTC/USDT")


class TestCancelOrder:
    """Test order cancellation."""

    def test_cancel_order_by_order_id(self):
        c = KtxClient(api_key="k", secret_key="s")
        cancel_response = {"result": {"id": "123", "status": "cancelled"}}

        def fake_request(method, path, **kwargs):
            return 200, cancel_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.cancel_order(symbol="BTC/USDT", order_id="123")

        assert result["result"]["status"] == "cancelled"

    def test_cancel_order_requires_id(self):
        c = KtxClient(api_key="k", secret_key="s")
        with pytest.raises(LiveTradingError, match="requires order_id or client_order_id"):
            c.cancel_order(symbol="BTC/USDT")


class TestGetOpenOrders:
    """Test open orders query."""

    def test_get_open_orders(self):
        c = KtxClient(api_key="k", secret_key="s")
        orders_data = {
            "result": [
                {"id": "1", "symbol": "BTC_USDT_SWAP", "status": "open"},
                {"id": "2", "symbol": "ETH_USDT_SWAP", "status": "open"},
            ]
        }

        def fake_request(method, path, **kwargs):
            return 200, orders_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_open_orders(symbol="BTC/USDT")

        assert len(result) == 2
        assert result[0]["id"] == "1"


# ===================================================================
# wait_for_fill
# ===================================================================

class TestWaitForFill:
    """Test order status polling until fill/cancel/timeout."""

    def test_wait_for_fill_filled(self):
        c = KtxClient(api_key="k", secret_key="s")
        filled_order = {
            "result": {
                "id": "123",
                "status": "filled",
                "filled_amount": "0.1",
                "average_price": "50000.0",
                "fee": "5.0",
                "fee_currency": "USDT",
            }
        }

        call_count = [0]

        def fake_request(method, path, **kwargs):
            call_count[0] += 1
            return 200, filled_order, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.wait_for_fill(
                symbol="BTC/USDT",
                order_id="123",
                max_wait_sec=1.0,
                poll_interval_sec=0.1,
            )

        assert result["status"] == "filled"
        assert result["filled"] == 0.1
        assert result["avg_price"] == 50000.0
        assert result["fee"] == 5.0
        assert result["fee_ccy"] == "USDT"

    def test_wait_for_fill_cancelled(self):
        c = KtxClient(api_key="k", secret_key="s")
        cancelled_order = {"result": {"id": "123", "status": "cancelled"}}

        def fake_request(method, path, **kwargs):
            return 200, cancelled_order, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.wait_for_fill(
                symbol="BTC/USDT",
                order_id="123",
                max_wait_sec=1.0,
                poll_interval_sec=0.1,
            )

        assert result["status"] == "cancelled"
        assert result["filled"] == 0.0

    def test_wait_for_fill_timeout(self):
        c = KtxClient(api_key="k", secret_key="s")
        open_order = {"result": {"id": "123", "status": "open"}}

        def fake_request(method, path, **kwargs):
            return 200, open_order, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            start = time.time()
            result = c.wait_for_fill(
                symbol="BTC/USDT",
                order_id="123",
                max_wait_sec=0.2,
                poll_interval_sec=0.05,
            )
            elapsed = time.time() - start

        # Should timeout after ~0.2s
        assert result["status"] == "open"
        assert elapsed >= 0.15  # Allow some tolerance


# ===================================================================
# set_leverage
# ===================================================================

class TestSetLeverage:
    """Test leverage configuration."""

    def test_set_leverage_spot_noop(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        result = c.set_leverage(symbol="BTC/USDT", leverage=10)
        assert result["skipped"] is True
        assert result["reason"] == "spot"

    def test_set_leverage_invalid_leverage(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        result = c.set_leverage(symbol="BTC/USDT", leverage=0)
        assert result["skipped"] is True
        assert result["reason"] == "invalid_leverage"

    def test_set_leverage_success(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        leverage_response = {"result": {"symbol": "BTC_USDT_SWAP", "leverage": 10}}

        def fake_request(method, path, **kwargs):
            return 200, leverage_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.set_leverage(symbol="BTC/USDT", leverage=10)

        assert result["result"]["leverage"] == 10


# ===================================================================
# get_fee_rate
# ===================================================================

class TestGetFeeRate:
    """Test fee rate retrieval."""

    def test_get_fee_rate(self):
        c = KtxClient(api_key="k", secret_key="s")
        product_info = {"maker_fee": "0.0002", "taker_fee": "0.0005"}

        with patch.object(c, "_get_product", return_value=product_info):
            result = c.get_fee_rate(symbol="BTC/USDT")

        assert result["maker"] == 0.0002
        assert result["taker"] == 0.0005

    def test_get_fee_rate_exception(self):
        c = KtxClient(api_key="k", secret_key="s")

        with patch.object(c, "_get_product", side_effect=Exception("API error")):
            result = c.get_fee_rate(symbol="BTC/USDT")

        assert result is None


# ===================================================================
# Market parameter
# ===================================================================

class TestMarketParam:
    """Test _ktx_market_param helper."""

    def test_spot_param(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        assert c._ktx_market_param() == "spot"

    def test_swap_param(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        assert c._ktx_market_param() == "lpc"
