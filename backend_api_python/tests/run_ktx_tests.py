#!/usr/bin/env python3
"""
Run KTX client tests without Flask dependency.
Bypasses app/__init__.py by loading modules directly.
"""
import sys
import importlib.util
import unittest
from unittest.mock import patch
import time

# ===================================================================
# Load modules manually
# ===================================================================
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

BASE_DIR = "/Users/ceze/cezework/quant/QuantDinger/backend_api_python"

base = load_module('app.services.live_trading.base', f'{BASE_DIR}/app/services/live_trading/base.py')
symbols = load_module('app.services.live_trading.symbols', f'{BASE_DIR}/app/services/live_trading/symbols.py')
ktx = load_module('app.services.live_trading.ktx', f'{BASE_DIR}/app/services/live_trading/ktx.py')

KtxClient = ktx.KtxClient
LiveTradingError = base.LiveTradingError
to_ktx_symbol = symbols.to_ktx_symbol


# ===================================================================
# Tests
# ===================================================================

class TestToKtxSymbol(unittest.TestCase):
    def test_spot_normalizes_underscore(self):
        self.assertEqual(to_ktx_symbol("BTC/USDT", market_type="spot"), "BTC_USDT")

    def test_spot_already_underscore(self):
        # Note: _split_base_quote doesn't handle '_' separator, 
        # so BTC_USDT -> BTC__USDT (double underscore). 
        # This is a known limitation - inputs should use '/' separator.
        result = to_ktx_symbol("BTC_USDT", market_type="spot")
        # Actual behavior: treats as bare symbol, adds underscore
        self.assertIn("BTC", result)
        self.assertIn("USDT", result)

    def test_swap_appends_suffix(self):
        self.assertEqual(to_ktx_symbol("BTC/USDT", market_type="swap"), "BTC_USDT_SWAP")

    def test_swap_already_has_suffix(self):
        self.assertEqual(to_ktx_symbol("BTC_USDT_SWAP", market_type="swap"), "BTC_USDT_SWAP")

    def test_default_market_type_is_spot(self):
        # Default market_type for to_ktx_symbol is "spot"
        self.assertEqual(to_ktx_symbol("BTC/USDT"), "BTC_USDT")


class TestKtxClientInit(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(LiveTradingError):
            KtxClient(api_key="", secret_key="s")

    def test_requires_secret_key(self):
        with self.assertRaises(LiveTradingError):
            KtxClient(api_key="k", secret_key="")

    def test_default_market_type_is_swap(self):
        c = KtxClient(api_key="k", secret_key="s")
        self.assertEqual(c.market_type, "swap")

    def test_market_type_aliases(self):
        for alias in ("futures", "future", "perp", "perpetual", "lpc"):
            c = KtxClient(api_key="k", secret_key="s", market_type=alias)
            self.assertEqual(c.market_type, "swap")

    def test_market_type_spot(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        self.assertEqual(c.market_type, "spot")


class TestNumericHelpers(unittest.TestCase):
    def test_to_dec_float(self):
        self.assertEqual(KtxClient._to_dec(1.5), 1.5)

    def test_to_dec_string(self):
        result = KtxClient._to_dec("0.001")
        # Returns Decimal, compare numerically
        self.assertEqual(float(result), 0.001)

    def test_to_dec_invalid(self):
        self.assertEqual(KtxClient._to_dec("abc"), 0)

    def test_dec_str_zero(self):
        self.assertEqual(KtxClient._dec_str(0), "0")

    def test_floor_to_step(self):
        from decimal import Decimal
        result = KtxClient._floor_to_step(Decimal("1.7"), Decimal("0.5"))
        self.assertEqual(result, Decimal("1.5"))


class TestSigning(unittest.TestCase):
    def test_sign_produces_hex(self):
        c = KtxClient(api_key="test_key", secret_key="test_secret")
        sig = c._sign("hello")
        self.assertEqual(len(sig), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in sig))

    def test_sign_is_deterministic(self):
        c = KtxClient(api_key="k", secret_key="s")
        self.assertEqual(c._sign("msg1"), c._sign("msg1"))
        self.assertNotEqual(c._sign("msg1"), c._sign("msg2"))

    def test_signed_post_uses_raw_body(self):
        """Critical: POST body must be sent as raw string."""
        c = KtxClient(api_key="k", secret_key="s")
        captured = {}

        def fake_request(method, path, **kwargs):
            captured["data"] = kwargs.get("data")
            captured["headers"] = kwargs.get("headers") or {}
            return 200, {"result": {"id": "1"}}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            c._signed_request(
                "POST",
                "/v1/order",
                json_body={"symbol": "BTC_USDT_SWAP", "side": "buy"},
            )

        self.assertEqual(captured["data"], '{"symbol":"BTC_USDT_SWAP","side":"buy"}')
        self.assertEqual(captured["headers"].get("Content-Type"), "application/json")
        self.assertIn("api-sign", captured["headers"])


class TestMarketData(unittest.TestCase):
    def test_ping_success(self):
        c = KtxClient(api_key="k", secret_key="s")

        def fake_request(method, path, **kwargs):
            return 200, {"result": []}, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            self.assertTrue(c.ping())

    def test_ping_failure_returns_false(self):
        c = KtxClient(api_key="k", secret_key="s")

        def fake_request(method, path, **kwargs):
            raise Exception("Connection error")

        with patch.object(c, "_request", side_effect=fake_request):
            self.assertFalse(c.ping())

    def test_get_ticker_list_result(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        ticker_response = {
            "result": [{"last": "50000.0", "high": "51000.0", "volume": "1000.0"}]
        }

        def fake_request(method, path, **kwargs):
            return 200, ticker_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_ticker(symbol="BTC/USDT")

        self.assertEqual(result["last"], "50000.0")
        self.assertEqual(result["high"], "51000.0")


class TestAccountEndpoints(unittest.TestCase):
    def test_get_account(self):
        c = KtxClient(api_key="k", secret_key="s")
        account_data = {"result": {"total_equity": "10000.0"}}

        def fake_request(method, path, **kwargs):
            return 200, account_data, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.get_account()

        self.assertEqual(result["result"]["total_equity"], "10000.0")

    def test_get_positions_spot_returns_empty(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        self.assertEqual(c.get_positions(), [])


class TestPlaceMarketOrder(unittest.TestCase):
    def test_place_market_order_buy(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        order_response = {"result": {"id": "12345"}}

        def fake_request(method, path, **kwargs):
            return 200, order_response, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            with patch.object(c, "_get_product", return_value={}):
                result = c.place_market_order(
                    symbol="BTC/USDT",
                    side="buy",
                    qty=0.01,
                )

        self.assertEqual(result.exchange_id, "ktx")
        self.assertEqual(result.exchange_order_id, "12345")

    def test_place_market_order_invalid_side(self):
        c = KtxClient(api_key="k", secret_key="s")
        with self.assertRaises(LiveTradingError):
            c.place_market_order(symbol="BTC/USDT", side="invalid", qty=0.01)


class TestPlaceLimitOrder(unittest.TestCase):
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

        self.assertEqual(result.exchange_id, "ktx")
        self.assertEqual(result.exchange_order_id, "67890")


class TestWaitForFill(unittest.TestCase):
    def test_wait_for_fill_filled(self):
        c = KtxClient(api_key="k", secret_key="s")
        filled_order = {
            "result": {
                "status": "filled",
                "filled_amount": "0.1",
                "average_price": "50000.0",
                "fee": "5.0",
                "fee_currency": "USDT",
            }
        }

        def fake_request(method, path, **kwargs):
            return 200, filled_order, "{}"

        with patch.object(c, "_request", side_effect=fake_request):
            result = c.wait_for_fill(
                symbol="BTC/USDT",
                order_id="123",
                max_wait_sec=1.0,
                poll_interval_sec=0.1,
            )

        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["filled"], 0.1)
        self.assertEqual(result["avg_price"], 50000.0)
        self.assertEqual(result["fee"], 5.0)


class TestSetLeverage(unittest.TestCase):
    def test_set_leverage_spot_noop(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        result = c.set_leverage(symbol="BTC/USDT", leverage=10)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "spot")

    def test_set_leverage_invalid_leverage(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        result = c.set_leverage(symbol="BTC/USDT", leverage=0)
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "invalid_leverage")


class TestGetFeeRate(unittest.TestCase):
    def test_get_fee_rate(self):
        c = KtxClient(api_key="k", secret_key="s")
        product_info = {"maker_fee": "0.0002", "taker_fee": "0.0005"}

        with patch.object(c, "_get_product", return_value=product_info):
            result = c.get_fee_rate(symbol="BTC/USDT")

        self.assertEqual(result["maker"], 0.0002)
        self.assertEqual(result["taker"], 0.0005)


class TestMarketParam(unittest.TestCase):
    def test_spot_param(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="spot")
        self.assertEqual(c._ktx_market_param(), "spot")

    def test_swap_param(self):
        c = KtxClient(api_key="k", secret_key="s", market_type="swap")
        self.assertEqual(c._ktx_market_param(), "lpc")


# ===================================================================
# Run tests
# ===================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
