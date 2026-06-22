from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.spot_sizing import (
    fetch_spot_last_price,
    normalize_spot_quote_amount,
)


def test_fetch_spot_last_price_uses_bitget_last_pr():
    client = MagicMock()
    client.get_ticker.return_value = {"lastPr": "0.12345", "symbol": "DOGEUSDT"}
    assert fetch_spot_last_price(client, symbol="DOGE/USDT") == 0.12345


def test_normalize_spot_quote_respects_min_trade_usdt():
    client = MagicMock(spec=BitgetSpotClient)
    client._normalize_quote_size.return_value = (Decimal("10"), 2)
    out = normalize_spot_quote_amount(client, symbol="DOGE/USDT", quote_amount=9.5)
    assert out == 10.0
    client._normalize_quote_size.assert_called_once()


@patch.object(BitgetSpotClient, "get_symbol_meta")
def test_bitget_market_buy_normalizes_quote_not_base(mock_meta):
    mock_meta.return_value = {
        "quantityPrecision": "0",
        "quotePrecision": "2",
        "minTradeUSDT": "5",
    }
    client = BitgetSpotClient(
        api_key="k",
        secret_key="s",
        passphrase="p",
    )
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"data": {"orderId": "1"}}
        client.place_market_order(symbol="DOGE/USDT", side="buy", size=12.3456)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["size"] == "12.34"
    assert body["side"] == "buy"


@patch.object(BitgetSpotClient, "get_symbol_meta")
def test_bitget_market_sell_normalizes_base(mock_meta):
    mock_meta.return_value = {
        "quantityPrecision": "1",
        "minTradeAmount": "1",
    }
    client = BitgetSpotClient(
        api_key="k",
        secret_key="s",
        passphrase="p",
    )
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"data": {"orderId": "2"}}
        client.place_market_order(symbol="DOGE/USDT", side="sell", size=10.99)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["size"] == "10.9"
    assert body["side"] == "sell"


@patch.object(BitgetSpotClient, "get_symbol_meta")
def test_bitget_limit_order_normalizes_price_precision(mock_meta):
    mock_meta.return_value = {
        "quantityPrecision": "6",
        "pricePrecision": "2",
        "minTradeAmount": "0.001",
    }
    client = BitgetSpotClient(
        api_key="k",
        secret_key="s",
        passphrase="p",
    )
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"data": {"orderId": "3"}}
        client.place_limit_order(symbol="ETH/USDT", side="buy", size=0.013333333, price=1200.009)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["size"] == "0.013333"
    assert body["price"] == "1200"
    assert body["side"] == "buy"
