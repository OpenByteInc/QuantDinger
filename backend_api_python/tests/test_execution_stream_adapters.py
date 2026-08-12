from __future__ import annotations

import json

import pytest

from app.services.execution_streams.adapters import (
    ADAPTERS,
    AlpacaExecutionAdapter,
    BitgetExecutionAdapter,
    BybitExecutionAdapter,
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
