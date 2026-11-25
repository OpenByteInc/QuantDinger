from unittest.mock import MagicMock

import pytest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.open_orders import fetch_exchange_open_orders


def native_row(exchange, oid):
    common = {"symbol": "ETHUSDT", "side": "Buy", "price": "2600.01", "orderId": str(oid)}
    if exchange == "bybit":
        return {**common, "qty": "0.004", "cumExecQty": "0.001", "orderStatus": "PartiallyFilled", "orderType": "Limit"}
    if exchange == "bitget":
        return {**common, "qty": "0.004", "cumExecQty": "0.001", "orderStatus": "partially_filled", "orderType": "limit"}
    if exchange == "binance":
        return {**common, "origQty": "0.004", "executedQty": "0.001", "status": "PARTIALLY_FILLED", "type": "LIMIT"}
    if exchange == "okx":
        return {"instId": "ETH-USDT", "ordId": str(oid), "side": "buy", "px": "2600.01", "sz": "0.004", "accFillSz": "0.001", "state": "partially_filled"}
    return {"id": str(oid), "currency_pair": "ETH_USDT", "side": "buy", "amount": "0.004", "left": "0.003", "price": "2600.01", "status": "open"}


def payload(exchange, rows, cursor="", market_type="spot"):
    if exchange == "bybit":
        return {"result": {"list": rows, "nextPageCursor": cursor}}
    if exchange == "bitget":
        return {"data": {"list": rows, "cursor": cursor}}
    if exchange == "okx":
        return {"data": rows}
    return rows


@pytest.mark.parametrize("exchange", ["binance", "okx", "gate", "bybit", "bitget"])
def test_all_five_spot_exchanges_preserve_identity_and_actual_order_values(exchange):
    client = MagicMock()
    client._signed_request.return_value = payload(exchange, [native_row(exchange, "2307363740691205376")])
    orders = fetch_exchange_open_orders(client, exchange_id=exchange, market_type="spot", symbol="ETH/USDT")
    assert len(orders) == 1
    assert orders[0]["exchange_order_id"] == "2307363740691205376"
    assert orders[0]["symbol"] == "ETH/USDT"
    assert orders[0]["price"] == 2600.01
    assert orders[0]["amount"] == .004
    assert orders[0]["filled"] == pytest.approx(.001)
    assert client._signed_request.call_args.args[0] == "GET"


@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_bybit_reads_every_cursor_page_and_sets_category(market_type):
    client = MagicMock()
    client._signed_request.side_effect = [
        payload("bybit", [native_row("bybit", n) for n in range(1, 51)], "next"),
        payload("bybit", [native_row("bybit", 51)]),
    ]
    orders = fetch_exchange_open_orders(client, exchange_id="bybit", market_type=market_type)
    assert len(orders) == 51
    params = client._signed_request.call_args.kwargs["params"]
    assert params["cursor"] == "next"
    assert params["openOnly"] == 0
    assert params["category"] == ("spot" if market_type == "spot" else "linear")
    assert params.get("settleCoin") == (None if market_type == "spot" else "USDT")


@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_bitget_reads_more_than_one_hundred_orders(market_type):
    client = MagicMock()
    client._signed_request.side_effect = [
        payload("bitget", [native_row("bitget", n) for n in range(200, 100, -1)], "101", market_type),
        payload("bitget", [native_row("bitget", 100)], "", market_type),
    ]
    orders = fetch_exchange_open_orders(client, exchange_id="bitget", market_type=market_type)
    assert len(orders) == 101
    params = client._signed_request.call_args.kwargs["params"]
    assert params["cursor"] == "101"
    assert params["category"] == ("USDT-FUTURES" if market_type == "swap" else "SPOT")


def test_bitget_spot_open_order_price_is_price_avg_not_execution_price():
    client = MagicMock()
    row = native_row("bitget", 1)
    row.pop("price")
    row.update(avgPrice="3474.75")
    client._signed_request.return_value = {"data": {"list": [row], "cursor": ""}}
    order = fetch_exchange_open_orders(client, exchange_id="bitget", market_type="spot")[0]
    assert order["price"] == 3474.75


def test_bitget_null_pending_order_list_is_empty_success():
    client = MagicMock()
    client._signed_request.return_value = {
        "code": "00000",
        "data": {"list": [], "cursor": ""},
    }
    assert fetch_exchange_open_orders(client, exchange_id="bitget", market_type="swap") == []


def test_bitget_missing_pending_order_list_is_rejected():
    client = MagicMock()
    client._signed_request.return_value = {"code": "00000", "data": {"cursor": ""}}
    with pytest.raises(LiveTradingError, match="invalid_open_orders_response"):
        fetch_exchange_open_orders(client, exchange_id="bitget", market_type="swap")


@pytest.mark.parametrize("exchange,path,symbol_key,native_symbol", [
    ("binance", "/fapi/v1/openOrders", "symbol", "ETHUSDT"),
    ("okx", "/api/v5/trade/orders-pending", "instId", "ETH-USDT-SWAP"),
    ("gate", "/api/v4/futures/usdt/orders", "contract", "ETH_USDT"),
    ("bybit", "/v5/order/realtime", "symbol", "ETHUSDT"),
    ("bitget", "/api/v3/trade/unfilled-orders", "symbol", "ETHUSDT"),
])
def test_swap_routes_use_contract_endpoints_and_strip_settlement_suffix(exchange, path, symbol_key, native_symbol):
    client = MagicMock()
    client._signed_request.return_value = payload(exchange, [], market_type="swap")
    assert fetch_exchange_open_orders(client, exchange_id=exchange, market_type="swap", symbol="ETH/USDT:USDT") == []
    assert client._signed_request.call_args.args == ("GET", path)
    assert client._signed_request.call_args.kwargs["params"][symbol_key] == native_symbol


def test_okx_reads_after_cursor():
    client = MagicMock()
    client._signed_request.side_effect = [payload("okx", [native_row("okx", n) for n in range(1, 101)]), payload("okx", [native_row("okx", 101)])]
    assert len(fetch_exchange_open_orders(client, exchange_id="okx", market_type="spot")) == 101
    assert client._signed_request.call_args.kwargs["params"]["after"] == "100"


def test_gate_account_wide_pagination_is_per_pair():
    client = MagicMock()
    client._signed_request.side_effect = [
        [{"currency_pair": "ETH_USDT", "orders": [native_row("gate", n) for n in range(1, 101)]}],
        [{"currency_pair": "ETH_USDT", "orders": [native_row("gate", 101)]}],
    ]
    assert len(fetch_exchange_open_orders(client, exchange_id="gate", market_type="spot")) == 101
    assert client._signed_request.call_args.kwargs["params"]["page"] == 2


@pytest.mark.parametrize("exchange", ["binance", "okx", "gate", "bybit", "bitget"])
def test_malformed_response_never_becomes_successful_empty_list(exchange):
    client = MagicMock()
    client._signed_request.return_value = {}
    with pytest.raises(LiveTradingError):
        fetch_exchange_open_orders(client, exchange_id=exchange, market_type="spot", symbol="ETH/USDT")


def test_pagination_failure_never_returns_partial_success():
    client = MagicMock()
    client._signed_request.side_effect = [payload("bybit", [native_row("bybit", 1)], "next"), RuntimeError("timeout")]
    with pytest.raises(RuntimeError):
        fetch_exchange_open_orders(client, exchange_id="bybit", market_type="spot")


def test_repeated_cursor_fails_instead_of_claiming_complete_snapshot():
    client = MagicMock()
    client._signed_request.side_effect = [payload("bybit", [native_row("bybit", 1)], "next"), payload("bybit", [native_row("bybit", 2)], "next")]
    with pytest.raises(LiveTradingError, match="pagination_stalled"):
        fetch_exchange_open_orders(client, exchange_id="bybit", market_type="spot")


def test_budget_is_checked_before_each_snapshot_page():
    client = MagicMock()
    client._signed_request.return_value = payload('bybit', [native_row('bybit', 1)], 'next')
    budget = MagicMock(side_effect=[True, False])
    with pytest.raises(LiveTradingError, match='request_budget_exhausted'):
        fetch_exchange_open_orders(client, exchange_id='bybit', market_type='spot', request_allowed=budget)
    assert client._signed_request.call_count == 1


@pytest.mark.parametrize("exchange", ["bybit", "bitget"])
def test_account_snapshot_actually_queries_both_order_markets(monkeypatch, exchange):
    from app.services.live_trading import account_snapshot as snapshots
    clients = {mt: MagicMock() for mt in ("spot", "swap")}
    for mt, client in clients.items():
        client._signed_request.return_value = payload(exchange, [native_row(exchange, mt)], market_type=mt)
    monkeypatch.setattr(snapshots, "create_client", lambda _config, *, market_type: clients[market_type])
    monkeypatch.setattr(snapshots, "_fetch_spot_wallet", lambda *a, **kw: [])
    monkeypatch.setattr(snapshots, "_fetch_swap_positions_snapshot", lambda *a: [])
    errors = []
    _, _, orders = snapshots._fetch_multi_crypto_snapshot({}, exchange, errors)
    assert not errors
    assert {o["market_type"] for o in orders} == {"spot", "swap"}
    clients["spot"]._signed_request.side_effect = RuntimeError("spot failed")
    _, _, orders = snapshots._fetch_multi_crypto_snapshot({}, exchange, errors)
    assert [o["market_type"] for o in orders] == ["swap"]
    assert errors == ["brokerAccounts.snapshotSpotOrdersFailed"]
