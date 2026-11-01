import inspect

import pytest
from flask import g

from app.routes import quick_trade
from app.services.quick_trade.orderbook import (
    OrderBookError,
    _contract_request_spec,
    _extract_contract_multiplier,
    _request_spec,
    normalize_order_book,
    normalize_symbol,
)


def test_normalize_symbol_supports_common_formats():
    assert normalize_symbol("BTC/USDT").compact == "BTCUSDT"
    assert normalize_symbol("Crypto:ETH-USDT-SWAP").dashed == "ETH-USDT"
    assert normalize_symbol("SOL_USDC").underscored == "SOL_USDC"


@pytest.mark.parametrize(
    ("exchange_id", "payload"),
    [
        ("binance", {"bids": [["100", "2"]], "asks": [["101", "3"]]}),
        ("okx", {"data": [{"bids": [["100", "2", "0", "1"]], "asks": [["101", "3", "0", "1"]], "ts": "10"}]}),
        ("bitget", {"data": {"bids": [["100", "2"]], "asks": [["101", "3"]], "ts": "10"}}),
        ("bybit", {"result": {"b": [["100", "2"]], "a": [["101", "3"]], "ts": "10"}}),
        ("gate", {"bids": [{"p": "100", "s": 2}], "asks": [{"p": "101", "s": 3}], "current": 10}),
        ("htx", {"tick": {"bids": [["100", "2"]], "asks": [["101", "3"]]}, "ts": 10}),
    ],
)
def test_normalize_order_book_for_supported_exchanges(exchange_id, payload):
    result = normalize_order_book(exchange_id, payload, symbol="BTC/USDT", market_type="swap", limit=10)
    assert result["best_bid"] == 100
    assert result["best_ask"] == 101
    assert result["mid"] == 100.5
    assert result["spread"] == 1
    assert result["bid_share"] == pytest.approx(39.761431, rel=1e-5)
    assert result["buy_wall"]["price"] == 100
    assert result["sell_wall"]["price"] == 101


@pytest.mark.parametrize(
    ("exchange_id", "expected_unit"),
    [
        ("binance", "base"),
        ("okx", "contracts"),
        ("bitget", "base"),
        ("bybit", "base"),
        ("gate", "contracts"),
        ("htx", "contracts"),
    ],
)
def test_derivative_quantity_units_are_explicit_for_every_supported_exchange(exchange_id, expected_unit):
    payloads = {
        "binance": {"bids": [["100", "2"]], "asks": [["101", "3"]]},
        "okx": {"data": [{"bids": [["100", "2"]], "asks": [["101", "3"]]}]},
        "bitget": {"data": {"b": [["100", "2"]], "a": [["101", "3"]]}},
        "bybit": {"result": {"b": [["100", "2"]], "a": [["101", "3"]]}},
        "gate": {"bids": [{"p": "100", "s": 2}], "asks": [{"p": "101", "s": 3}]},
        "htx": {"tick": {"bids": [["100", "2"]], "asks": [["101", "3"]]}},
    }
    result = normalize_order_book(
        exchange_id,
        payloads[exchange_id],
        symbol="SOL/USDT",
        market_type="swap",
        limit=10,
    )
    assert result["base_asset"] == "SOL"
    assert result["quantity_unit"] == expected_unit


def test_empty_order_book_fails_closed():
    with pytest.raises(OrderBookError, match="empty order book"):
        normalize_order_book("binance", {"bids": [], "asks": []}, symbol="BTC/USDT", market_type="spot", limit=10)


@pytest.mark.parametrize(
    ("market_type", "category"),
    [("spot", "SPOT"), ("swap", "USDT-FUTURES")],
)
def test_bitget_order_book_uses_unified_v3_endpoint(market_type, category):
    url, params = _request_spec("bitget", normalize_symbol("BTC/USDT"), market_type, 25)
    assert url == "https://api.bitget.com/api/v3/market/orderbook"
    assert params == {"category": category, "symbol": "BTCUSDT", "limit": 25}


def test_bitget_v3_order_book_fields_are_normalized():
    result = normalize_order_book(
        "bitget",
        {"data": {"b": [["100", "2"]], "a": [["101", "3"]], "ts": "1730969017964"}},
        symbol="BTC/USDT",
        market_type="spot",
        limit=10,
    )
    assert result["best_bid"] == 100
    assert result["best_ask"] == 101
    assert result["timestamp"] == 1730969017964


@pytest.mark.parametrize(
    ("exchange_id", "expected_url"),
    [
        ("okx", "https://www.okx.com/api/v5/public/instruments"),
        ("gate", "https://fx-api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT"),
        ("htx", "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info"),
    ],
)
def test_contract_metadata_uses_public_venue_endpoints(exchange_id, expected_url):
    url, _ = _contract_request_spec(exchange_id, normalize_symbol("BTC/USDT"))
    assert url == expected_url


@pytest.mark.parametrize(
    ("exchange_id", "symbol", "payload", "expected"),
    [
        ("okx", "BTC/USDT", {"data": [{"ctVal": "0.01", "ctValCcy": "BTC", "ctType": "linear"}]}, 0.01),
        ("okx", "ETH/USDT", {"data": [{"ctVal": "0.1", "ctValCcy": "ETH", "ctType": "linear"}]}, 0.1),
        ("okx", "DOGE/USDT", {"data": [{"ctVal": "1000", "ctValCcy": "DOGE", "ctType": "linear"}]}, 1000),
        ("gate", "BTC/USDT", {"quanto_multiplier": "0.0001"}, 0.0001),
        ("gate", "DOGE/USDT", {"quanto_multiplier": "10"}, 10),
        ("htx", "BTC/USDT", {"data": [{"contract_size": "0.001"}]}, 0.001),
        ("htx", "SOL/USDT", {"data": [{"contract_size": "1"}]}, 1),
    ],
)
def test_contract_multiplier_is_normalized_to_base_asset(exchange_id, symbol, payload, expected):
    assert _extract_contract_multiplier(exchange_id, payload, normalize_symbol(symbol)) == pytest.approx(expected)


def test_contract_metadata_requests_are_symbol_specific():
    okx_url, okx_params = _contract_request_spec("okx", normalize_symbol("SOL/USDT"))
    gate_url, gate_params = _contract_request_spec("gate", normalize_symbol("DOGE/USDT"))
    htx_url, htx_params = _contract_request_spec("htx", normalize_symbol("ETH/USDT"))
    assert okx_url == "https://www.okx.com/api/v5/public/instruments"
    assert okx_params["instId"] == "SOL-USDT-SWAP"
    assert gate_url.endswith("/DOGE_USDT")
    assert gate_params == {}
    assert htx_url == "https://api.hbdm.com/linear-swap-api/v1/swap_contract_info"
    assert htx_params["contract_code"] == "ETH-USDT"


def test_inverse_contract_multiplier_fails_closed():
    payload = {"data": [{"ctVal": "100", "ctValCcy": "USD", "ctType": "inverse"}]}
    assert _extract_contract_multiplier("okx", payload, normalize_symbol("BTC/USDT")) == 0


def test_order_book_route_rejects_exception_detail_exposure(app, monkeypatch):
    def reject_order_book(*_args, **_kwargs):
        raise OrderBookError("credential=secret")

    monkeypatch.setattr(quick_trade, "fetch_order_book", reject_order_book)

    with app.test_request_context(
        "/api/quick-trade/order-book?exchange_id=binance&symbol=BTC%2FUSDT",
        method="GET",
    ):
        g.user_id = 7
        g.quick_trade_session = object()
        response, status = inspect.unwrap(quick_trade.get_order_book)()

    body = response.get_json()
    assert status == 400
    assert body["msg"] == "quickTrade.orderFailed"
    assert "secret" not in response.get_data(as_text=True)


def test_order_book_reports_native_contract_unit_metadata():
    result = normalize_order_book(
        "okx",
        {"data": [{"bids": [["100", "2"]], "asks": [["101", "3"]]}]},
        symbol="BTC/USDT",
        market_type="swap",
        limit=10,
        contract_multiplier=0.01,
    )
    assert result["quantity_unit"] == "contracts"
    assert result["base_asset"] == "BTC"
    assert result["contract_multiplier"] == pytest.approx(0.01)
