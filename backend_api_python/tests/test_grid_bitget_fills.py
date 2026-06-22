"""Bitget grid fill polling and initial market execution tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
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


def test_query_grid_order_fill_bitget_spot_unwraps_order_info():
    client = MagicMock()
    client.__class__ = BitgetSpotClient
    client.get_order.return_value = {
        "code": "00000",
        "data": {
            "orderId": "spot-oid-1",
            "status": "filled",
            "baseVolume": "0.25",
            "priceAvg": "123.45",
        },
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="ABC/USDT",
        market_type="spot",
        exchange_order_id="spot-oid-1",
    )
    assert status == "filled"
    assert filled == 0.25
    assert avg == 123.45


def test_query_grid_order_fill_bitget_spot_uses_fills_when_order_info_lags():
    client = MagicMock()
    client.__class__ = BitgetSpotClient
    client.get_order.return_value = {
        "code": "00000",
        "data": {
            "orderId": "spot-oid-2",
            "status": "live",
            "baseVolume": "0",
            "priceAvg": "0",
        },
    }
    client.get_fills.return_value = {
        "code": "00000",
        "data": [
            {"orderId": "spot-oid-2", "tradeId": "t1", "size": "0.1", "priceAvg": "10"},
            {"orderId": "spot-oid-2", "tradeId": "t2", "size": "0.2", "priceAvg": "11"},
        ],
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="ABC/USDT",
        market_type="spot",
        exchange_order_id="spot-oid-2",
    )
    assert status == "filled"
    assert filled == pytest.approx(0.3)
    assert avg == pytest.approx((0.1 * 10 + 0.2 * 11) / 0.3)
    client.get_fills.assert_called_once_with(symbol="ABC/USDT", order_id="spot-oid-2")


def test_query_grid_order_fill_bitget_mix_uses_fills_when_order_detail_lags():
    client = MagicMock()
    client.__class__ = BitgetMixClient
    client.get_order.return_value = {
        "filled": 0.0,
        "avg_price": 0.0,
        "status": "live",
        "raw": {"data": {"orderId": "mix-oid-1"}},
    }
    client.get_order_fills.return_value = {
        "code": "00000",
        "data": {
            "fillList": [
                {"orderId": "mix-oid-1", "tradeId": "t1", "baseVolume": "0.004", "fillPrice": "70000"},
            ],
        },
    }
    filled, avg, status = query_grid_order_fill(
        client,
        symbol="BTC/USDT",
        market_type="swap",
        exchange_order_id="mix-oid-1",
        exchange_config={"product_type": "USDT-FUTURES"},
    )
    assert status == "filled"
    assert filled == 0.004
    assert avg == 70000.0
    client.get_order_fills.assert_called_once_with(
        symbol="BTC/USDT",
        product_type="USDT-FUTURES",
        order_id="mix-oid-1",
    )


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
