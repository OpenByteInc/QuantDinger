from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.live_trading.bybit import BybitClient


def test_bybit_spot_get_positions_delegates_to_wallet_holdings():
    client = BybitClient(api_key="k", secret_key="s", category="spot")
    with patch.object(client, "get_spot_holdings", return_value={"result": {"list": []}}) as mock_hold:
        out = client.get_positions(symbol="SOL/USDT")
    mock_hold.assert_called_once_with(symbol="SOL/USDT")
    assert out == {"result": {"list": []}}


@patch.object(BybitClient, "get_instrument_info")
def test_bybit_normalize_quantity_floors_to_qty_step(mock_info):
    mock_info.return_value = {
        "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
    }
    client = BybitClient(api_key="k", secret_key="s", category="spot")
    dec, prec = client._normalize_quantity(symbol="SOL/USDT", quantity=0.06173602)
    assert dec == Decimal("0.061")
    assert prec is not None


@patch.object(BybitClient, "get_instrument_info")
def test_bybit_place_market_order_qty_string_respects_step(mock_info):
    mock_info.return_value = {
        "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
    }
    client = BybitClient(api_key="k", secret_key="s", category="spot")
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"result": {"orderId": "1"}}
        client.place_market_order(symbol="SOL/USDT", side="buy", qty=0.06173602)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["category"] == "spot"
    assert body["qty"] == "0.061"
