from __future__ import annotations

import json

import pytest

from app.services.execution_streams.adapters import (
    ADAPTERS,
    AlpacaExecutionAdapter,
    BitgetExecutionAdapter,
    BybitExecutionAdapter,
    FutuExecutionAdapter,
    GateExecutionAdapter,
    HtxExecutionAdapter,
    OkxExecutionAdapter,
)


class FakeSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


def _adapter(adapter_cls, *, market_type="swap", symbols=()):
    states: list[str] = []
    adapter = adapter_cls(
        credential_id=9,
        user_id=3,
        exchange_id="test",
        market_type=market_type,
        config={
            "api_key": "key",
            "secret_key": "secret",
            "passphrase": "pass",
            "paper": True,
        },
        symbols=symbols,
        on_event=lambda _event: None,
        on_state=lambda state, _error, _reconnect: states.append(state),
    )
    return adapter, states


def test_adapter_registry_covers_six_exchanges_and_three_brokers():
    assert set(ADAPTERS) == {
        "binance",
        "okx",
        "bitget",
        "bybit",
        "gate",
        "htx",
        "alpaca",
        "ibkr",
        "futu",
    }


def _futu_adapter(events):
    return FutuExecutionAdapter(
        credential_id=9,
        user_id=3,
        config={},
        on_event=events.append,
        on_state=lambda *_args: None,
    )


def test_futu_order_then_deal_push_is_recorded_once():
    events = []
    adapter = _futu_adapter(events)
    order = {
        "code": "HK.00700",
        "order_id": "OID-1",
        "dealt_avg_price": 350,
        "trd_side": "BUY",
        "order_status": "FILLED_PART",
        "updated_time": "2026-08-10 10:00:00",
    }

    adapter._emit_order({**order, "dealt_qty": 10})
    adapter._emit_deal({**order, "deal_id": "D-1", "qty": 10, "price": 350})
    adapter._emit_order({**order, "dealt_qty": 20})
    adapter._emit_deal({**order, "deal_id": "D-2", "qty": 10, "price": 351})
    adapter._emit_deal({**order, "deal_id": "D-2", "qty": 10, "price": 351})

    assert [event.cumulative_quantity for event in events] == [10, 20]


def test_futu_deal_then_order_push_is_recorded_once():
    events = []
    adapter = _futu_adapter(events)
    base = {
        "code": "US.AAPL",
        "order_id": "OID-2",
        "trd_side": "BUY",
        "order_status": "FILLED_PART",
        "updated_time": "2026-08-10 10:00:00",
    }

    adapter._emit_deal({**base, "deal_id": "D-1", "qty": 10, "price": 220})
    adapter._emit_order({**base, "dealt_qty": 10, "dealt_avg_price": 220})
    adapter._emit_deal({**base, "deal_id": "D-2", "qty": 10, "price": 221})
    adapter._emit_order({**base, "dealt_qty": 20, "dealt_avg_price": 220.5})

    assert [event.exchange_fill_id for event in events] == ["D-1", "D-2"]


def test_futu_failed_order_ingest_does_not_suppress_authoritative_deal():
    def fail_ingest(_event):
        raise RuntimeError("database unavailable")

    adapter = FutuExecutionAdapter(
        credential_id=9,
        user_id=3,
        config={},
        on_event=fail_ingest,
        on_state=lambda *_args: None,
    )
    order = {
        "code": "HK.00700",
        "order_id": "OID-3",
        "dealt_qty": 10,
        "dealt_avg_price": 350,
        "trd_side": "BUY",
        "order_status": "FILLED_PART",
        "updated_time": "2026-08-10 10:00:00",
    }
    with pytest.raises(RuntimeError, match="database unavailable"):
        adapter._emit_order(order)

    events = []
    adapter.on_event = events.append
    adapter._emit_deal({**order, "deal_id": "D-3", "qty": 10, "price": 350})

    assert [event.exchange_fill_id for event in events] == ["D-3"]


@pytest.mark.parametrize(
    "adapter_cls",
    (OkxExecutionAdapter, BybitExecutionAdapter, BitgetExecutionAdapter, HtxExecutionAdapter, AlpacaExecutionAdapter),
)
def test_authenticated_adapters_are_not_healthy_before_auth_ack(adapter_cls):
    adapter, _states = _adapter(adapter_cls)
    assert adapter.ready_on_open() is False
    assert adapter.connected is False


def test_okx_subscribes_only_after_successful_login():
    adapter, states = _adapter(OkxExecutionAdapter)
    assert adapter.on_open_messages()[0]["op"] == "login"
    ws = FakeSocket()
    assert adapter.handle_control(ws, {"event": "login", "code": "0"})
    assert ws.messages == [{"op": "subscribe", "args": [{"channel": "orders", "instType": "ANY"}]}]
    assert adapter.connected
    assert states == ["connected"]


def test_bybit_subscribes_only_after_successful_authentication():
    adapter, _states = _adapter(BybitExecutionAdapter)
    ws = FakeSocket()
    adapter.handle_control(ws, {"op": "auth", "success": True})
    assert ws.messages == [{"op": "subscribe", "args": ["execution"]}]
    assert adapter.connected


@pytest.mark.parametrize(
    ("market_type", "inst_type"),
    (("spot", "SPOT"), ("swap", "USDT-FUTURES")),
)
def test_bitget_uses_market_specific_fill_subscription(market_type, inst_type):
    adapter, _states = _adapter(BitgetExecutionAdapter, market_type=market_type)
    assert adapter.on_open_messages()[0]["op"] == "login"
    ws = FakeSocket()
    adapter.handle_control(ws, {"event": "login", "code": "0"})
    assert ws.messages[0]["args"][0] == {
        "instType": inst_type,
        "channel": "fill",
        "instId": "default",
    }


def test_gate_becomes_healthy_only_after_authenticated_subscription_ack():
    adapter, _states = _adapter(GateExecutionAdapter, market_type="spot")
    assert adapter.ready_on_open() is False
    request = adapter.on_open_messages()[0]
    assert request["channel"] == "spot.usertrades"
    assert request["auth"]["KEY"] == "key"
    adapter.handle_control(FakeSocket(), {"event": "subscribe", "result": {"status": "success"}})
    assert adapter.connected


def test_gate_uses_current_official_testnet_websocket_paths():
    spot, _states = _adapter(GateExecutionAdapter, market_type="spot")
    swap, _states = _adapter(GateExecutionAdapter, market_type="swap")

    assert spot.url() == "wss://ws-testnet.gate.com/v4/ws/spot"
    assert swap.url() == "wss://ws-testnet.gate.com/v4/ws/futures/usdt"


def test_gate_futures_logs_in_for_uid_before_subscribing_to_all_contracts():
    adapter, _states = _adapter(GateExecutionAdapter, market_type="swap")
    login = adapter.on_open_messages()[0]
    assert login["channel"] == "futures.login"
    assert login["event"] == "api"
    assert login["payload"]["api_key"] == "key"
    assert login["payload"]["signature"]

    ws = FakeSocket()
    adapter.handle_control(
        ws,
        {
            "header": {"channel": "futures.login", "event": "api", "status": "200"},
            "data": {"result": {"api_key": "key", "uid": "110284739"}},
        },
    )
    assert ws.messages[0]["channel"] == "futures.usertrades"
    assert ws.messages[0]["payload"] == ["110284739", "!all"]
    assert adapter.connected is False

    adapter.handle_control(ws, {"event": "subscribe", "result": {"status": "success"}})
    assert adapter.connected


def test_htx_spot_subscribes_known_symbols_after_authentication():
    adapter, _states = _adapter(HtxExecutionAdapter, market_type="spot", symbols=("BTC/USDT", "ETH/USDT"))
    assert adapter.on_open_messages()[0]["ch"] == "auth"
    ws = FakeSocket()
    adapter.handle_control(ws, {"action": "req", "ch": "auth", "code": 200})
    assert {message["ch"] for message in ws.messages} == {
        "trade.clearing#btcusdt",
        "trade.clearing#ethusdt",
    }


def test_alpaca_listens_for_trade_updates_after_authorization():
    adapter, _states = _adapter(AlpacaExecutionAdapter, market_type="usstock")
    assert adapter.on_open_messages()[0]["action"] == "auth"
    ws = FakeSocket()
    adapter.handle_control(
        ws,
        {"stream": "authorization", "data": {"status": "authorized"}},
    )
    assert ws.messages == [{"action": "listen", "data": {"streams": ["trade_updates"]}}]
    assert adapter.connected


def test_futu_adapter_stop_timeout_marks_orphaned_and_refuses_restart():
    events = []
    adapter = _futu_adapter(events)
    adapter._thread = type(
        "AliveThread",
        (),
        {"is_alive": lambda self: True, "join": lambda self, timeout=None: None},
    )()
    adapter._client = type("Client", (), {"connected": True, "disconnect": lambda self: None})()

    assert adapter.stop(timeout=0.01) is False
    assert adapter.orphaned is True
    previous = adapter._thread
    adapter.start()
    assert adapter._thread is previous

