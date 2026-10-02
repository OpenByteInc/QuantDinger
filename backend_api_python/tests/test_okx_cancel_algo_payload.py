from __future__ import annotations

from app.services.live_trading.okx import OkxClient


def test_cancel_algo_order_sends_array_body_to_advance_endpoint_for_trailing():
    calls = []

    def fake_signed_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, "json_body": kwargs.get("json_body")})
        return {"code": "0", "data": [{"algoId": "1", "sCode": "0"}]}

    client = OkxClient.__new__(OkxClient)
    client._signed_request = fake_signed_request

    client.cancel_algo_order(
        algo_id="3807971948987043840",
        inst_id="BTC-USDT-SWAP",
        ord_type="move_order_stop",
    )

    assert calls
    assert calls[0]["path"] == "/api/v5/trade/cancel-advance-algos"
    assert isinstance(calls[0]["json_body"], list)
    assert calls[0]["json_body"] == [
        {"algoId": "3807971948987043840", "instId": "BTC-USDT-SWAP"}
    ]


def test_cancel_all_algo_orders_batches_by_type():
    calls = []

    client = OkxClient.__new__(OkxClient)

    def fake_get_algo_orders(*, ord_type: str = "", **_kwargs):
        if ord_type != "move_order_stop":
            return {"data": []}
        return {
            "data": [
                {"algoId": "1", "instId": "BTC-USDT-SWAP"},
                {"algoId": "2", "instId": "BTC-USDT-SWAP"},
            ]
        }

    def fake_signed_request(method, path, **kwargs):
        calls.append({"path": path, "json_body": kwargs.get("json_body")})
        body = kwargs.get("json_body") or []
        return {
            "code": "0",
            "data": [{"algoId": item["algoId"], "sCode": "0"} for item in body],
        }

    client.get_algo_orders = fake_get_algo_orders
    client._signed_request = fake_signed_request

    cancelled = client.cancel_all_algo_orders(inst_id="BTC-USDT-SWAP", inst_type="SWAP")

    assert cancelled == 2
    assert calls
    assert calls[0]["path"] == "/api/v5/trade/cancel-advance-algos"
    assert isinstance(calls[0]["json_body"], list)
    assert len(calls[0]["json_body"]) == 2
