"""Contract tests for FutuClient with a mocked futu-api SDK."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.services.futu_trading.client import FutuClient
from app.services.futu_trading.config import FutuConfig


class _FakeFT:
    RET_OK = 0
    RET_ERROR = -1

    class TrdEnv:
        SIMULATE = "SIMULATE"
        REAL = "REAL"

    class TrdMarket:
        HK = "HK"
        US = "US"
        NONE = "NONE"

    class SecurityFirm:
        FUTUSECURITIES = "FUTUSECURITIES"

    class TrdSide:
        BUY = "BUY"
        SELL = "SELL"

    class OrderType:
        MARKET = "MARKET"
        NORMAL = "NORMAL"

    class OrderStatus:
        SUBMITTED = "SUBMITTED"
        FILLED_PART = "FILLED_PART"
        FILLED_ALL = "FILLED_ALL"
        WAITING_SUBMIT = "WAITING_SUBMIT"
        SUBMITTING = "SUBMITTING"

    class ModifyOrderOp:
        CANCEL = "CANCEL"

    class KLType:
        K_DAY = "K_DAY"

    class AuType:
        QFQ = "QFQ"

    class SubType:
        QUOTE = "QUOTE"

    class TradeOrderHandlerBase:
        def on_recv_rsp(self, rsp_pb):
            return rsp_pb

    class TradeDealHandlerBase:
        def on_recv_rsp(self, rsp_pb):
            return rsp_pb


def _client_with_mocks():
    cfg = FutuConfig(host="127.0.0.1", port=11111, trade_env="demo", trade_market="HK")
    client = FutuClient(cfg)
    quote = MagicMock()
    trade = MagicMock()
    quote.get_global_state.return_value = (_FakeFT.RET_OK, {"trd_logined": True, "server_ver": 1})
    trade.get_acc_list.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{"acc_id": 99, "trd_env": "SIMULATE", "acc_type": "STOCK"}]),
    )
    client._quote_ctx = quote
    client._trade_ctx = trade
    client._connected = True
    client._acc_id = 99
    return client, quote, trade


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_start_push_registers_order_and_deal_handlers(_ensure):
    client, _quote, trade = _client_with_mocks()

    assert client.start_push()

    handlers = [call.args[0] for call in trade.set_handler.call_args_list]
    assert len(handlers) == 2
    assert any(isinstance(handler, _FakeFT.TradeOrderHandlerBase) for handler in handlers)
    assert any(isinstance(handler, _FakeFT.TradeDealHandlerBase) for handler in handlers)


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_place_market_order_success(_ensure):
    client, quote, trade = _client_with_mocks()
    quote.get_market_snapshot.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{"lot_size": 100, "last_price": 350.0}]),
    )
    trade.place_order.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{
            "order_id": "OID-1",
            "order_status": "SUBMITTED",
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "qty": 100,
            "price": 350.0,
            "code": "HK.00700",
            "trd_side": "BUY",
            "remark": "r1",
        }]),
    )
    result = client.place_market_order("00700.HK", "buy", 100, "HKStock", remark="r1")
    assert result.success
    assert result.order_id == "OID-1"
    assert result.status == "submitted"
    kwargs = trade.place_order.call_args.kwargs
    assert kwargs["code"] == "HK.00700"
    assert kwargs["qty"] == 100
    assert kwargs["remark"] == "r1"


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_place_order_rejects_bad_lot(_ensure):
    client, quote, trade = _client_with_mocks()
    quote.get_market_snapshot.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{"lot_size": 100, "last_price": 350.0}]),
    )
    result = client.place_market_order("00700.HK", "buy", 50, "HKStock")
    assert not result.success
    assert "FUTU_INVALID_LOT_SIZE" in result.message
    trade.place_order.assert_not_called()


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_get_order_status_and_find_by_remark(_ensure):
    client, _quote, trade = _client_with_mocks()
    trade.order_list_query.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{
            "order_id": "OID-9",
            "order_status": "FILLED_ALL",
            "dealt_qty": 100,
            "dealt_avg_price": 351.2,
            "qty": 100,
            "code": "HK.00700",
            "trd_side": "BUY",
            "remark": "futu-remark",
        }]),
    )
    status = client.get_order_status("OID-9")
    assert status.success
    assert status.status == "filled"
    assert status.filled == 100
    found = client.find_order_by_remark("futu-remark")
    assert found is not None
    assert found.order_id == "OID-9"


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_get_order_status_treats_empty_query_as_failure(_ensure):
    client, _quote, trade = _client_with_mocks()
    trade.order_list_query.return_value = (_FakeFT.RET_OK, pd.DataFrame())

    status = client.get_order_status("OID-MISSING")

    assert not status.success
    assert status.filled == 0
    assert "not found" in status.message.lower()


@patch("app.services.futu_trading.client._ensure_futu")
def test_trading_client_rejects_remote_host_before_loading_sdk(ensure_futu, monkeypatch):
    monkeypatch.delenv("FUTU_ALLOW_REMOTE_OPEND", raising=False)
    client = FutuClient(FutuConfig(host="169.254.169.254"))

    assert client.connect() is False
    ensure_futu.assert_not_called()


@patch("app.services.futu_trading.client._ensure_futu", return_value=_FakeFT)
def test_cancel_order(_ensure):
    client, _quote, trade = _client_with_mocks()
    trade.modify_order.return_value = (_FakeFT.RET_OK, pd.DataFrame([{"order_id": "OID-1"}]))
    assert client.cancel_order("OID-1") is True


def test_parse_futu_deal_normalizer():
    try:
        from app.services.execution_streams.normalizers import parse_futu_deal
    except ModuleNotFoundError:
        pytest.skip("optional deps missing for execution_streams import")

    events = parse_futu_deal({
        "code": "HK.00700",
        "order_id": "OID-1",
        "deal_id": "D-1",
        "qty": 100,
        "price": 350.0,
        "trd_side": "BUY",
        "remark": "r1",
        "dealt_qty": 100,
        "order_status": "FILLED_ALL",
        "create_time": "2026-08-10 10:00:00",
    })
    assert len(events) == 1
    assert events[0].exchange_id == "futu"
    assert events[0].symbol == "00700.HK"
    assert events[0].quantity == 100
    assert events[0].client_order_id == "r1"
    assert events[0].occurred_at == datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)


def test_parse_futu_order_snapshot_uses_cumulative_fill_and_average_price():
    from app.services.execution_streams.normalizers import parse_futu_deal

    base = {
        "code": "HK.00700",
        "order_id": "OID-2",
        "qty": 100,
        "price": 350.0,
        "dealt_avg_price": 349.2,
        "trd_side": "BUY",
        "order_status": "FILLED_PART",
        "updated_time": "2026-08-10 10:01:00",
    }
    first = parse_futu_deal({**base, "dealt_qty": 20})[0]
    second = parse_futu_deal({**base, "dealt_qty": 40})[0]

    assert first.exchange_fill_id == ""
    assert first.price == 349.2
    assert first.quantity == 0
    assert first.cumulative_quantity == 20
    assert first.is_cumulative
    assert first.event_key() != second.event_key()


@patch("app.services.futu_trading.client._ensure_futu")
def test_connect_trade_only_skips_quote_context(ensure):
    ft = _FakeFT()
    quote_ctx = MagicMock()
    trade_ctx = MagicMock()
    trade_ctx.get_acc_list.return_value = (
        _FakeFT.RET_OK,
        pd.DataFrame([{"acc_id": 7, "trd_env": "SIMULATE", "acc_type": "STOCK"}]),
    )
    ft.OpenQuoteContext = MagicMock(return_value=quote_ctx)
    ft.OpenSecTradeContext = MagicMock(return_value=trade_ctx)
    ensure.return_value = ft

    client = FutuClient(FutuConfig(host="127.0.0.1", port=11111, trade_env="demo"))
    assert client.connect(need_quote=False)
    ft.OpenQuoteContext.assert_not_called()
    ft.OpenSecTradeContext.assert_called_once()
    assert client.connected
    assert client._quote_ctx is None
    status = client.get_connection_status()
    assert status["quote_ctx"] is False
    assert status["trade_ctx"] is True

