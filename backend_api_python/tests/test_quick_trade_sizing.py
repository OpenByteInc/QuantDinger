import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask, g

from app.routes import quick_trade
from app.services.quick_trade.sizing import QuickTradeSizingError, convert_usdt_to_base_qty


@pytest.mark.parametrize("price", [None, 0, -1, "invalid", "NaN", "Infinity", "-Infinity"])
def test_unusable_ticker_never_returns_quote_amount(price):
    client = SimpleNamespace(get_ticker=lambda **kwargs: {"last": price})
    with pytest.raises(QuickTradeSizingError):
        convert_usdt_to_base_qty(client, "BTC/USDT", 5000, "swap")


@pytest.mark.parametrize("amount,price", [
    (0, 60000), (-1, 60000), (float("nan"), 60000), (float("inf"), 60000),
    (5000, float("nan")), (5000, float("inf")), (5000, -1),
    (1e308, 1e-308), (1e-308, 1e308),
])
def test_invalid_inputs_and_nonrepresentable_quantities_are_rejected(amount, price):
    with pytest.raises(QuickTradeSizingError):
        convert_usdt_to_base_qty(object(), "BTC/USDT", amount, "swap", price)


@pytest.mark.parametrize("price,expected", [(60000, 5000 / 60000), (1, 5000)])
def test_valid_reference_price_sizes_without_network(price, expected):
    ticker = Mock(side_effect=AssertionError("reference price requires no lookup"))
    assert convert_usdt_to_base_qty(SimpleNamespace(get_ticker=ticker), "BTC/USDT", 5000, "swap", price) == expected
    ticker.assert_not_called()


def test_valid_alternate_ticker_field_can_replace_invalid_last():
    client = SimpleNamespace(get_ticker=lambda **kwargs: {"last": "NaN", "lastPrice": "60000"})
    assert convert_usdt_to_base_qty(client, "BTC/USDT", 5000, "spot") == pytest.approx(5000 / 60000)


VENUES = [
    ("binance", "BinanceFuturesClient", "swap"),
    ("binance_spot", "BinanceSpotClient", "spot"),
    ("okx", "OkxClient", "swap"),
    ("bybit", "BybitClient", "swap"),
    ("bitget", "BitgetMixClient", "swap"),
    ("bitget_classic", "BitgetClassicMixClient", "swap"),
    ("bitget_spot", "BitgetSpotClient", "spot"),
    ("bitget_spot_classic", "BitgetClassicSpotClient", "spot"),
    ("gate", "GateUsdtFuturesClient", "swap"),
    ("gate", "GateSpotClient", "spot"),
    ("htx", "HtxClient", "swap"),
]


@pytest.mark.parametrize("module,class_name,market", VENUES)
@pytest.mark.parametrize("failure", ["timeout", "empty"])
def test_venue_price_failures_are_fail_closed(module, class_name, market, failure):
    cls = getattr(importlib.import_module(f"app.services.live_trading.{module}"), class_name)
    client = object.__new__(cls)
    client.base_url = "https://api.binance.com" if module.startswith("binance") else "https://example.invalid"
    client.get_ticker = Mock(side_effect=TimeoutError("price timed out")) if failure == "timeout" else Mock(return_value={})
    client._request = Mock(side_effect=TimeoutError("price timed out")) if failure == "timeout" else Mock(return_value=(200, {}, {}))
    with pytest.raises(QuickTradeSizingError):
        convert_usdt_to_base_qty(client, "BTC/USDT", 5000, market)


@pytest.fixture
def order_route(monkeypatch):
    from app.services.live_trading import execution, spot_sizing

    client = SimpleNamespace(get_ticker=Mock(side_effect=TimeoutError("price timed out")), place_limit_order=Mock())
    submit = Mock(return_value=SimpleNamespace(exchange_order_id="order-1", filled=0, avg_price=0, raw={}))
    client.place_limit_order = submit
    configure = Mock()
    normalize = Mock(side_effect=lambda *args, **kwargs: (kwargs["requested"], {}))
    monkeypatch.setattr(quick_trade, "_build_product_aware_config", lambda *a, **k: ({"exchange_id": "binance"}, {}))
    monkeypatch.setattr(quick_trade, "create_exchange_client", lambda *a, **k: client)
    monkeypatch.setattr(quick_trade, "_recent_strategy_conflict", lambda *a, **k: None)
    monkeypatch.setattr(quick_trade, "configure_quick_trade_derivatives", configure)
    monkeypatch.setattr(quick_trade, "normalized_submission_quantity", normalize)
    monkeypatch.setattr(execution, "place_order_from_signal", submit)
    monkeypatch.setattr(quick_trade, "limit_order_kwargs", lambda client, symbol, qty, price, *a: {"qty": qty, "price": price})
    monkeypatch.setattr(quick_trade, "enrich_fill", lambda *a, **k: {})
    monkeypatch.setattr(quick_trade, "fee_update_from_fill", lambda *a, **k: {"commission": 0, "commission_ccy": "", "commission_quote": 0})
    monkeypatch.setattr(quick_trade, "attach_quick_trade_protection", lambda *a, **k: [])
    monkeypatch.setattr(quick_trade, "_record_quick_trade", lambda **k: 1)
    monkeypatch.setattr(spot_sizing, "normalize_spot_base_quantity", lambda *a, **k: k["quantity"])
    monkeypatch.setattr(spot_sizing, "fetch_spot_last_price", lambda *a, **k: 60000)

    def request(**overrides):
        body = dict(credential_id=9, symbol="BTC/USDT", side="buy", market_type="swap", amount=1000, leverage=5)
        body.update(overrides)
        app = Flask(__name__)
        with app.test_request_context():
            g.user_id = 101
            result = inspect.unwrap(quick_trade.place_order)(body)
            return (result[0].json, result[1]) if isinstance(result, tuple) else (result.json, 200)

    return SimpleNamespace(request=request, submit=submit, configure=configure, normalize=normalize, client=client)


@pytest.mark.parametrize("market,side", [("swap", "buy"), ("swap", "sell"), ("spot", "buy"), ("spot", "sell")])
def test_route_returns_400_without_submission_or_account_changes(order_route, market, side):
    body, status = order_route.request(market_type=market, side=side)
    assert status == 400
    assert body == {"code": 0, "msg": "quickTrade.errorHints.sizingUnavailable", "error_hint": "quickTrade.errorHints.sizingUnavailable"}
    order_route.submit.assert_not_called()
    order_route.configure.assert_not_called()
    order_route.normalize.assert_not_called()


@pytest.mark.parametrize("reference", [{}, {"reference_price": 60000, "reference_price_at_ms": 1}])
def test_expired_or_missing_reference_price_cannot_bypass_failure(order_route, reference):
    assert order_route.request(**reference)[1] == 400
    order_route.submit.assert_not_called()


def test_sizing_response_does_not_expose_exception_details(order_route, monkeypatch):
    error = QuickTradeSizingError()
    error.args = ("internal-only traceback /srv/private/config",)
    monkeypatch.setattr(quick_trade, "_convert_usdt_to_base_qty", Mock(side_effect=error))
    body, status = order_route.request()
    assert status == 400
    assert body == {
        "code": 0,
        "msg": "quickTrade.errorHints.sizingUnavailable",
        "error_hint": "quickTrade.errorHints.sizingUnavailable",
    }
    order_route.submit.assert_not_called()
    order_route.configure.assert_not_called()
    order_route.normalize.assert_not_called()


@pytest.mark.parametrize("price", [60000, 1])
@pytest.mark.parametrize("order_type", ["market", "limit"])
def test_valid_orders_still_submit_correct_notional_quantity(order_route, price, order_type):
    body, status = order_route.request(order_type=order_type, price=price, reference_price=price,
                                     reference_price_at_ms=int(quick_trade.time.time() * 1000))
    assert status == 200 and body["code"] == 1
    quantity_key = "amount" if order_type == "market" else "qty"
    assert order_route.submit.call_args.kwargs[quantity_key] == pytest.approx(5000 / price)
    order_route.client.get_ticker.assert_not_called()


def test_spot_base_quantity_sell_does_not_require_quote_conversion(order_route):
    body, status = order_route.request(market_type="spot", side="sell", amount=0, quantity=.01)
    assert status == 200 and body["code"] == 1
    assert order_route.submit.call_args.kwargs["amount"] == .01
    order_route.client.get_ticker.assert_not_called()
