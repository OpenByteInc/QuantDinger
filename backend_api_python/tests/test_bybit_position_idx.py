from unittest.mock import MagicMock, patch

from app.services.live_trading.bybit import BybitClient


def test_bybit_detect_one_way_from_position_list():
    client = BybitClient(api_key="k", secret_key="s", category="linear", hedge_mode=True)
    with patch.object(
        client,
        "get_positions",
        return_value={"result": {"list": [{"positionIdx": 0, "size": "0"}]}},
    ):
        assert client.is_hedge_position_mode(symbol="DOGE/USDT") is False


def test_bybit_detect_hedge_from_position_list():
    client = BybitClient(api_key="k", secret_key="s", category="linear", hedge_mode=False)
    with patch.object(
        client,
        "get_positions",
        return_value={
            "result": {
                "list": [
                    {"positionIdx": 1, "size": "0"},
                    {"positionIdx": 2, "size": "0"},
                ]
            }
        },
    ):
        assert client.is_hedge_position_mode(symbol="DOGE/USDT") is True


def test_bybit_resolve_position_idx_one_way_always_zero():
    client = BybitClient(api_key="k", secret_key="s", category="linear")
    with patch.object(client, "is_hedge_position_mode", return_value=False):
        assert client._resolve_position_idx("long", symbol="DOGE/USDT") == 0
        assert client._resolve_position_idx("short", symbol="DOGE/USDT") == 0


def test_bybit_resolve_position_idx_hedge_long_short():
    client = BybitClient(api_key="k", secret_key="s", category="linear")
    with patch.object(client, "is_hedge_position_mode", return_value=True):
        assert client._resolve_position_idx("long", symbol="DOGE/USDT") == 1
        assert client._resolve_position_idx("short", symbol="DOGE/USDT") == 2


@patch.object(BybitClient, "get_instrument_info")
def test_bybit_linear_market_order_sets_position_idx_zero(mock_info):
    mock_info.return_value = {
        "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"},
    }
    client = BybitClient(api_key="k", secret_key="s", category="linear")
    with patch.object(client, "is_hedge_position_mode", return_value=False):
        with patch.object(client, "_signed_request") as mock_req:
            mock_req.return_value = {"result": {"orderId": "1"}}
            client.place_market_order(symbol="DOGE/USDT", side="buy", qty=100, pos_side="long")
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["category"] == "linear"
    assert body["positionIdx"] == 0


@patch.object(BybitClient, "get_instrument_info")
def test_bybit_linear_market_order_hedge_open_long(mock_info):
    mock_info.return_value = {
        "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"},
    }
    client = BybitClient(api_key="k", secret_key="s", category="linear")
    with patch.object(client, "is_hedge_position_mode", return_value=True):
        with patch.object(client, "_signed_request") as mock_req:
            mock_req.return_value = {"result": {"orderId": "1"}}
            client.place_market_order(symbol="DOGE/USDT", side="buy", qty=100, pos_side="long")
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["positionIdx"] == 1
