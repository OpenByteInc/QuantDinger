import pytest

from app.services.quick_trade.orderbook import (
    OrderBookError,
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
