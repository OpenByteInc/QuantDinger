from unittest.mock import patch

from app.services.live_trading.gate import GateSpotClient


def test_gate_spot_market_order_uses_ioc_time_in_force():
    client = GateSpotClient(api_key="k", secret_key="s")
    with patch.object(client, "_signed_request") as mock_req:
        mock_req.return_value = {"id": "123"}
        client.place_market_order(symbol="BTC/USDT", side="buy", size=0.01)
    body = mock_req.call_args.kwargs.get("json_body") or mock_req.call_args[1].get("json_body")
    assert body["type"] == "market"
    assert body["time_in_force"] == "ioc"
