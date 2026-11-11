from unittest.mock import patch

from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.execution import place_order_from_signal


def test_gate_spot_market_order_uses_ioc_time_in_force():
    client = GateSpotClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"id": "123"}
        client.place_market_order(symbol="BTC/USDT", side="buy", size=0.01)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["type"] == "market"
    assert body["time_in_force"] == "ioc"


def test_gate_spot_limit_order_serializes_small_amount_without_exponent():
    client = GateSpotClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"id": "123"}
        client.place_limit_order(
            symbol="BTC/USDT",
            side="sell",
            size=0.00005,
            price=79_038.4,
        )
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["amount"] == "0.00005"
    assert "e" not in body["amount"].lower()
    assert body["price"] == "79038.4"


def test_gate_spot_market_order_serializes_small_amount_without_exponent():
    client = GateSpotClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"id": "123"}
        client.place_market_order(symbol="BTC/USDT", side="sell", size=5.2e-05)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["amount"] == "0.000052"
    assert "e" not in body["amount"].lower()


def test_quantity_sized_market_buy_converts_to_gate_quote_value():
    client = GateSpotClient(api_key="k", secret_key="s")
    with (
        patch.object(client, "get_ticker", return_value={"last": "100"}),
        patch.object(client, "_signed_request", return_value={"id": "124"}) as request,
    ):
        place_order_from_signal(
            client,
            signal_type="open_long",
            symbol="DOGE/USDT",
            amount=0.1,
            market_type="spot",
            spot_buy_by_quantity=True,
        )

    body = request.call_args.kwargs["json_body"]
    assert body["amount"] == "10.0"


def test_gate_futures_limit_order_serializes_small_price_without_exponent():
    client = GateUsdtFuturesClient(api_key="k", secret_key="s")
    with (
        patch.object(client, "_resolve_order_size", return_value=("1", None)),
        patch.object(client, "_signed_request", return_value={"id": "123"}) as mock_req,
    ):
        client.place_limit_order(
            symbol="SHIB/USDT",
            side="buy",
            size=1,
            price=1e-05,
        )

    body = mock_req.call_args.kwargs["json_body"]
    assert body["price"] == "0.00001"
    assert "e" not in body["price"].lower()


def test_gate_futures_limit_order_snaps_price_to_contract_tick():
    client = GateUsdtFuturesClient(api_key="k", secret_key="s")
    with (
        patch.object(client, "_resolve_order_size", return_value=("1", None)),
        patch.object(client, "get_contract", return_value={"order_price_round": "0.1"}),
        patch.object(client, "_signed_request", return_value={"id": "123"}) as mock_req,
    ):
        client.place_limit_order(
            symbol="BTC/USDT",
            side="buy",
            size=1,
            price=77951.14749999999,
        )

    body = mock_req.call_args.kwargs["json_body"]
    assert body["price"] == "77951.1"


def test_gate_spot_open_orders_uses_account_wide_endpoint():
    client = GateSpotClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request", return_value=[]) as mock_req:
        client.get_open_orders(limit=100)

    assert mock_req.call_args.args == ("GET", "/api/v4/spot/open_orders")
    assert mock_req.call_args.kwargs["params"] == {
        "page": 1,
        "limit": 100,
        "account": "spot",
    }


def test_gate_futures_open_orders_requests_open_status():
    client = GateUsdtFuturesClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request", return_value=[]) as mock_req:
        client.get_open_orders(limit=100)

    assert mock_req.call_args.args == ("GET", "/api/v4/futures/usdt/orders")
    assert mock_req.call_args.kwargs["params"] == {
        "status": "open",
        "limit": 100,
        "offset": 0,
    }
