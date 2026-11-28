import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask, g

from app.routes import alpaca, ibkr, openapi_brokers
from app.services.alpaca_trading.client import AlpacaClient
from app.services.ibkr_trading.client import IBKRClient
from app.services.openapi_brokers import ForthrightClient, TigerClient, WebullClient
from app.utils.broker_order_validation import BrokerOrderValidationError, order_number


INVALID = ["NaN", "Infinity", "-Infinity", "1e999", -1, True, "invalid"]
BROKERS = ["alpaca", "ibkr", "tiger", "webull", "forthright"]


@pytest.fixture
def broker_route(monkeypatch):
    result = SimpleNamespace(success=True, message="mock", order_id="mock", filled=0, avg_price=0, status="submitted", raw={})
    client = SimpleNamespace(place_market_order=Mock(return_value=result), place_limit_order=Mock(return_value=result))
    monkeypatch.setattr(alpaca, "_require_connected_client", lambda: (client, None))
    monkeypatch.setattr(ibkr, "_require_connected_client", lambda: (client, None))
    monkeypatch.setattr(openapi_brokers, "_connected_client", lambda broker: (client, None))

    def send(broker, **overrides):
        body = dict(symbol="AAPL", side="buy", quantity=2, marketType="USStock")
        body.update(overrides)
        app = Flask(__name__)
        with app.test_request_context(json=body):
            g.user_id = 1
            if broker == "alpaca":
                result = inspect.unwrap(alpaca.place_order)()
            elif broker == "ibkr":
                result = inspect.unwrap(ibkr.place_order)()
            else:
                result = inspect.unwrap(openapi_brokers.place_order)(broker)
            return (result[0].json, result[1]) if isinstance(result, tuple) else (result.json, 200)

    return send, client


@pytest.mark.parametrize("broker", BROKERS)
@pytest.mark.parametrize("value", INVALID + [0, None])
def test_route_rejects_invalid_quantity_without_calling_broker(broker_route, broker, value):
    send, client = broker_route
    body, status = send(broker, quantity=value)
    assert status == 400 and body["success"] is False
    client.place_market_order.assert_not_called()
    client.place_limit_order.assert_not_called()


@pytest.mark.parametrize("broker", BROKERS)
@pytest.mark.parametrize("value", INVALID + [0, None])
def test_route_rejects_invalid_limit_price_without_calling_broker(broker_route, broker, value):
    send, client = broker_route
    body, status = send(broker, orderType="limit", price=value)
    assert status == 400 and body["success"] is False
    client.place_market_order.assert_not_called()
    client.place_limit_order.assert_not_called()


@pytest.mark.parametrize("field", ["notional", "amount", "take_profit_price", "stop_loss_price"])
@pytest.mark.parametrize("value", INVALID)
def test_alpaca_route_validates_notional_and_optional_protection(broker_route, field, value):
    send, client = broker_route
    overrides = {field: value}
    if field in ("notional", "amount"):
        overrides["quantity"] = 0
    assert send("alpaca", **overrides)[1] == 400
    client.place_market_order.assert_not_called()


@pytest.mark.parametrize("notional,price", [(1e308, 1e-308), (1e300, 1), (1e-300, 1e300)])
def test_alpaca_rejects_overflow_or_underflow_in_limit_conversion(broker_route, notional, price):
    send, client = broker_route
    assert send("alpaca", quantity=0, notional=notional, price=price, orderType="limit")[1] == 400
    client.place_limit_order.assert_not_called()


@pytest.mark.parametrize("broker", BROKERS)
@pytest.mark.parametrize("kind", ["market", "limit"])
def test_valid_share_quantity_and_price_are_preserved(broker_route, broker, kind):
    send, client = broker_route
    body, status = send(broker, quantity=2.5, orderType=kind, price=100)
    assert status == 200 and body["success"] is True
    submit = client.place_market_order if kind == "market" else client.place_limit_order
    assert submit.call_args.kwargs["quantity"] == 2.5
    if kind == "limit":
        assert submit.call_args.kwargs["price"] == 100


@pytest.mark.parametrize("kind", ["market", "limit"])
def test_alpaca_keeps_notional_sizing_and_legacy_amount_alias(broker_route, kind):
    send, client = broker_route
    assert send("alpaca", quantity=0, notional=0, amount=5000, orderType=kind, price=60000)[1] == 200
    if kind == "market":
        assert client.place_market_order.call_args.kwargs["notional"] == 5000
    else:
        assert client.place_limit_order.call_args.kwargs["quantity"] == .083333333


@pytest.mark.parametrize("broker", ["tiger", "webull", "forthright"])
def test_legacy_size_and_limit_price_aliases_still_work(broker_route, broker):
    send, client = broker_route
    assert send(broker, quantity=0, size=100, orderType="limit", price=0, limitPrice=50)[1] == 200
    assert client.place_limit_order.call_args.kwargs["quantity"] == 100


CLIENTS = [AlpacaClient, IBKRClient, TigerClient, WebullClient, ForthrightClient]


@pytest.mark.parametrize("cls", CLIENTS)
@pytest.mark.parametrize("field", ["quantity", "price"])
@pytest.mark.parametrize("value", INVALID + [0])
def test_adapter_rejects_invalid_input_before_sdk_or_transport(cls, field, value):
    client = object.__new__(cls)
    guard = Mock(side_effect=AssertionError("Invalid input reached external boundary"))
    for name in ("_ensure_connected", "_ensure_client", "_sdk", "_request", "_instrument"):
        setattr(client, name, guard)
    kwargs = dict(symbol="AAPL", side="buy", quantity=2, market_type="USStock")
    kwargs[field] = value
    method = client.place_limit_order if field == "price" else client.place_market_order
    if cls in (AlpacaClient, IBKRClient):
        result = method(**kwargs)
        assert result.success is False
        if cls is AlpacaClient and field == "quantity" and value == 0:
            assert result.message == "Provide exactly one of quantity or notional"
        else:
            assert "brokerAccounts.invalidOrder" in result.message
    else:
        with pytest.raises(BrokerOrderValidationError):
            method(**kwargs)
    guard.assert_not_called()


@pytest.mark.parametrize("cls", [AlpacaClient, IBKRClient])
@pytest.mark.parametrize("field", ["take_profit_price", "stop_loss_price"])
@pytest.mark.parametrize("value", INVALID)
def test_invalid_protection_never_submits_parent_order(cls, field, value):
    client = object.__new__(cls)
    client._ensure_connected = Mock(side_effect=AssertionError("Invalid protection reached connection"))
    method = client.place_bracket_order if cls is IBKRClient else client.place_market_order
    result = method(symbol="AAPL", side="buy", quantity=2, **{field: value})
    assert result.success is False
    assert "brokerAccounts.invalidOrder" in result.message
    client._ensure_connected.assert_not_called()


@pytest.mark.parametrize("value", INVALID)
def test_forthright_rejects_invalid_reference_before_instrument_lookup(value):
    client = object.__new__(ForthrightClient)
    client._instrument = Mock()
    with pytest.raises(BrokerOrderValidationError):
        client.place_market_order(symbol="AAPL", side="buy", quantity=2, reference_price=value)
    client._instrument.assert_not_called()


def test_optional_zero_is_allowed_but_required_zero_is_rejected():
    assert order_number(None, allow_zero=True) == 0
    assert order_number(0, allow_zero=True) == 0
    assert order_number("0.125") == .125
    with pytest.raises(BrokerOrderValidationError):
        order_number(0)
