"""Bitget grid fill polling and initial market execution tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.okx import OkxClient


def test_query_grid_order_fill_bitget_filled():
    client = MagicMock()
    client.__class__ = BitgetMixClient
    client.get_order.return_value = {
        "filled": 0.0042,
        "avg_price": 73472.95,
        "status": "filled",
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="3616035301446492160",
        exchange_config={"product_type": "USDT-FUTURES"},
    )
    assert status == "filled"
    assert filled == 0.0042
    assert avg == 73472.95


def test_query_grid_order_fill_bitget_open():
    client = MagicMock()
    client.__class__ = BitgetMixClient
    client.get_order.return_value = {
        "filled": 0.0,
        "avg_price": 0.0,
        "status": "live",
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="123",
        exchange_config={"product_type": "USDT-FUTURES"},
    )
    assert status == "open"
    assert filled == 0.0


def test_query_grid_order_fill_okx_filled():
    client = MagicMock()
    client.__class__ = OkxClient
    client.get_order.return_value = {
        "state": "filled",
        "accFillSz": "5",
        "avgPx": "65000.1",
    }
    client.get_instrument.return_value = {"ctVal": "0.01"}
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="okx-oid-1",
    )
    assert status == "filled"
    assert filled == pytest.approx(0.05)
    assert avg == 65000.1
    client.get_order.assert_called_once()
    call_kw = client.get_order.call_args.kwargs
    assert call_kw["inst_id"] == "BTC-USDT-SWAP"
    assert call_kw["ord_id"] == "okx-oid-1"


def test_query_grid_order_fill_bybit_filled():
    client = MagicMock()
    client.__class__ = BybitClient
    client.get_order.return_value = {
        "orderStatus": "Filled",
        "cumExecQty": "0.012",
        "avgPrice": "72000",
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="bybit-oid-1",
    )
    assert status == "filled"
    assert filled == 0.012
    assert avg == 72000.0


def test_execute_grid_market_order_requires_fill(monkeypatch):
    from app.services.grid.exchange_orders import execute_grid_market_order

    class FakeResult:
        exchange_order_id = "oid1"

    client = MagicMock()
    monkeypatch.setattr(
        "app.services.live_trading.execution.place_order_from_signal",
        lambda *a, **k: FakeResult(),
    )
    monkeypatch.setattr(
        "app.services.grid.exchange_orders.wait_grid_market_fill",
        lambda *a, **k: (0.0, 0.0),
    )
    ok, filled, avg = execute_grid_market_order(
        client,
        symbol="BTC/USDT",
        signal_type="open_long",
        quantity=0.01,
        market_type="swap",
        exchange_config={},
    )
    assert ok is False
    assert filled == 0.0

    monkeypatch.setattr(
        "app.services.grid.exchange_orders.wait_grid_market_fill",
        lambda *a, **k: (0.004, 73000.0),
    )
    ok2, filled2, avg2 = execute_grid_market_order(
        client,
        symbol="BTC/USDT",
        signal_type="open_long",
        quantity=0.01,
        market_type="swap",
        exchange_config={},
    )
    assert ok2 is True
    assert filled2 == 0.004
    assert avg2 == 73000.0
