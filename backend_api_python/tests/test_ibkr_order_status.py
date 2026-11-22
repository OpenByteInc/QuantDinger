from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.ibkr_trading.client import IBKRClient


def test_ibkr_get_order_status_queries_completed_orders_after_restart():
    order = SimpleNamespace(orderId=123, permId=987)
    status = SimpleNamespace(status="Filled", filled=4, remaining=0, avgFillPrice=205.25)
    trade = SimpleNamespace(order=order, orderStatus=status)
    ib = SimpleNamespace(
        openTrades=lambda: [],
        trades=lambda: [],
        reqCompletedOrders=lambda api_only: [trade],
    )
    client = object.__new__(IBKRClient)
    client._ib = ib
    client._ensure_connected = lambda: None

    result = client.get_order_status(123)

    assert result.success is True
    assert result.status == "Filled"
    assert result.filled == 4
    assert result.avg_price == 205.25
    assert result.raw["permId"] == 987


def test_ibkr_strict_position_snapshot_propagates_transport_failure():
    def fail_positions(_account):
        raise TimeoutError("temporary timeout")

    client = object.__new__(IBKRClient)
    client._ib = SimpleNamespace(positions=fail_positions)
    client._account = "DU123"
    client._ensure_connected = lambda: None

    with pytest.raises(TimeoutError, match="temporary timeout"):
        client.get_positions(raise_on_error=True)

    assert client.get_positions() == []


def test_ibkr_get_order_status_uses_account_wide_open_orders_and_commission():
    order = SimpleNamespace(orderId=12, permId=9988)
    status = SimpleNamespace(status="Filled", filled=2, remaining=0, avgFillPrice=100)
    fill = SimpleNamespace(commissionReport=SimpleNamespace(commission=0.35, currency="USD"))
    trade = SimpleNamespace(order=order, orderStatus=status, fills=[fill])
    calls = []
    ib = SimpleNamespace(
        reqAllOpenOrders=lambda: calls.append("all") or [trade],
        openTrades=lambda: [],
        trades=lambda: [],
        reqCompletedOrders=lambda api_only: [],
    )
    client = object.__new__(IBKRClient)
    client._ib = ib
    client._ensure_connected = lambda: None

    result = client.get_order_status(9988)

    assert calls == ["all"]
    assert result.order_id == 9988
    assert result.raw["commission"] == 0.35
    assert result.raw["commission_ccy"] == "USD"


def test_ibkr_completed_order_recovers_commission_from_executions():
    order = SimpleNamespace(orderId=12, permId=9988)
    status = SimpleNamespace(status="Filled", filled=2, remaining=0, avgFillPrice=100)
    trade = SimpleNamespace(order=order, orderStatus=status, fills=[])
    execution_fill = SimpleNamespace(
        execution=SimpleNamespace(orderId=77, permId=9988),
        commissionReport=SimpleNamespace(commission=0.42, currency="USD"),
    )
    ib = SimpleNamespace(
        reqAllOpenOrders=lambda: [],
        openTrades=lambda: [],
        trades=lambda: [],
        reqCompletedOrders=lambda api_only: [trade],
        reqExecutions=lambda: [execution_fill],
    )
    client = object.__new__(IBKRClient)
    client._ib = ib
    client._ensure_connected = lambda: None

    result = client.get_order_status(9988)

    assert result.raw["commission"] == 0.42
    assert result.raw["commission_ccy"] == "USD"


def test_ibkr_get_order_status_recovers_by_order_ref():
    order = SimpleNamespace(orderId=123, permId=987, orderRef="qd_9_51")
    status = SimpleNamespace(status="Filled", filled=4, remaining=0, avgFillPrice=205.25)
    trade = SimpleNamespace(order=order, orderStatus=status, fills=[])
    ib = SimpleNamespace(
        reqAllOpenOrders=lambda: [],
        openTrades=lambda: [],
        trades=lambda: [],
        reqCompletedOrders=lambda api_only: [trade],
        reqExecutions=lambda: [],
    )
    client = object.__new__(IBKRClient)
    client._ib = ib
    client._ensure_connected = lambda: None

    result = client.get_order_status_by_client_id("qd_9_51")

    assert result.success is True
    assert result.order_id == 987
    assert result.status == "Filled"
    assert result.raw["orderRef"] == "qd_9_51"


def test_ibkr_get_executions_normalizes_fill_and_commission():
    fill = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL"),
        execution=SimpleNamespace(
            execId="e-1",
            permId=9988,
            orderId=12,
            orderRef="qd_9_52",
            side="SLD",
            shares=2,
            price=205.5,
            time=None,
        ),
        commissionReport=SimpleNamespace(commission=0.42, currency="USD"),
        time=datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc),
    )
    client = object.__new__(IBKRClient)
    client._ib = SimpleNamespace(reqExecutions=lambda: [fill])
    client._ensure_connected = lambda: None

    rows = client.get_executions("AAPL")

    assert rows == [
        {
            "execution_id": "e-1",
            "order_id": 9988,
            "local_order_id": 12,
            "client_order_id": "qd_9_52",
            "symbol": "AAPL",
            "side": "SLD",
            "quantity": 2.0,
            "price": 205.5,
            "executed_at": datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc),
            "commission": 0.42,
            "commission_ccy": "USD",
        }
    ]
