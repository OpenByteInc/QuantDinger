from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from app.services.execution_streams.adapters import (
    ADAPTERS,
    AlpacaExecutionAdapter,
    BinanceExecutionAdapter,
    BitgetExecutionAdapter,
    BybitExecutionAdapter,
    GateExecutionAdapter,
    HtxExecutionAdapter,
    OkxExecutionAdapter,
)


class FakeSocket:
    def __init__(self) -> None:
        self.messages: list[object] = []
        self.closed = False

    def send(self, raw: str) -> None:
        try:
            self.messages.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            self.messages.append(raw)

    def close(self) -> None:
        self.closed = True


class StopAfterOneHeartbeat:
    def __init__(self) -> None:
        self.waits = 0

    def wait(self, _timeout: float) -> bool:
        self.waits += 1
        return self.waits > 1


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


def test_adapter_registry_covers_six_exchanges_and_two_brokers():
    assert set(ADAPTERS) == {
        "binance",
        "okx",
        "bitget",
        "bybit",
        "gate",
        "htx",
        "alpaca",
        "ibkr",
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


def test_okx_and_bitget_use_literal_ping_and_bybit_uses_json_ping():
    okx, _states = _adapter(OkxExecutionAdapter)
    bitget, _states = _adapter(BitgetExecutionAdapter)
    bybit, _states = _adapter(BybitExecutionAdapter)

    assert 0 < okx.application_heartbeat_interval() < 30
    assert okx.application_heartbeat_payload() == "ping"
    assert 0 < bitget.application_heartbeat_interval() < 30
    assert bitget.application_heartbeat_payload() == "ping"
    assert bybit.application_heartbeat_interval() == 20
    assert bybit.application_heartbeat_payload() == {"op": "ping"}


@pytest.mark.parametrize(
    ("adapter_cls", "expected"),
    (
        (OkxExecutionAdapter, "ping"),
        (BitgetExecutionAdapter, "ping"),
        (BybitExecutionAdapter, {"op": "ping"}),
    ),
)
def test_application_heartbeat_sends_venue_payload(adapter_cls, expected):
    adapter, _states = _adapter(adapter_cls)
    adapter._last_message_at = -100.0
    ws = FakeSocket()

    adapter._application_heartbeat_loop(ws, StopAfterOneHeartbeat())

    assert ws.messages == [expected]


@pytest.mark.parametrize(("environment", "expected_url"), (
    ("live", "wss://ws-api.binance.com:443/ws-api/v3"),
    ("demo", "wss://demo-ws-api.binance.com/ws-api/v3"),
    ("testnet", "wss://ws-api.testnet.binance.vision/ws-api/v3"),
))
def test_binance_spot_uses_signed_websocket_api_subscription(monkeypatch, environment, expected_url):
    monkeypatch.setattr("app.services.execution_streams.adapters.time.time", lambda: 1_700_000_000.125)
    monkeypatch.setattr("app.services.execution_streams.adapters.requests.post", lambda *a, **kw: pytest.fail("Spot must not request a listenKey"))
    adapter, states = _adapter(BinanceExecutionAdapter, market_type="spot")
    adapter.config["environment"] = environment

    assert adapter.ready_on_open() is False
    assert adapter.url() == expected_url
    adapter.prepare()  # Must not call the retired REST listenKey endpoint.
    request = adapter.on_open_messages()[0]
    assert request["method"] == "userDataStream.subscribe.signature"
    params = request["params"]
    expected_payload = urlencode(sorted({
        "apiKey": "key",
        "timestamp": 1_700_000_000_125,
    }.items()))
    assert params["signature"] == hmac.new(
        b"secret", expected_payload.encode(), hashlib.sha256
    ).hexdigest()

    ws = FakeSocket()
    assert adapter.handle_control(
        ws,
        {"id": request["id"], "status": 200, "result": {"subscriptionId": 0}},
    )
    assert adapter.connected
    assert states == ["connected"]


@pytest.mark.parametrize(("environment", "rest_host", "ws_host"), (
    ("live", "https://fapi.binance.com", "wss://fstream.binance.com/ws"),
    ("demo", "https://demo-fapi.binance.com", "wss://fstream.binancefuture.com/ws"),
))
def test_binance_futures_retains_listen_key_subscription(monkeypatch, environment, rest_host, ws_host):
    adapter, _states = _adapter(BinanceExecutionAdapter)
    adapter.config["environment"] = environment
    calls = []
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"listenKey": "stream-key"})
    monkeypatch.setattr("app.services.execution_streams.adapters.requests.post", lambda url, **kw: calls.append((url, kw)) or response)
    adapter._keepalive_thread = SimpleNamespace(is_alive=lambda: True)

    adapter.prepare()

    assert calls[0][0] == rest_host + "/fapi/v1/listenKey"
    assert calls[0][1]["headers"]["X-MBX-APIKEY"] == "key"
    assert adapter.url() == ws_host + "/stream-key"
    assert adapter.ready_on_open()
    assert adapter.on_open_messages() == []


def test_binance_websocket_api_event_envelope_is_unwrapped():
    adapter, _states = _adapter(BinanceExecutionAdapter, market_type="spot")
    events = adapter.parse({
        "subscriptionId": 0,
        "event": {
            "e": "executionReport",
            "E": 1_700_000_000_000,
            "s": "BTCUSDT",
            "i": 12,
            "t": 34,
            "S": "BUY",
            "X": "FILLED",
            "L": "100",
            "l": "0.5",
            "z": "0.5",
        },
    })

    assert len(events) == 1
    assert events[0].symbol == "BTC/USDT"
    assert events[0].exchange_order_id == "12"


def test_bitget_demo_and_live_use_matching_private_websocket_hosts():
    demo, _states = _adapter(BitgetExecutionAdapter)
    live, _states = _adapter(BitgetExecutionAdapter)
    live.config["environment"] = "live"

    assert demo.url() == "wss://wspap.bitget.com/v3/ws/private"
    assert live.url() == "wss://ws.bitget.com/v3/ws/private"


def test_okx_demo_and_live_use_port_443_private_websocket_hosts():
    demo, _states = _adapter(OkxExecutionAdapter)
    live, _states = _adapter(OkxExecutionAdapter)
    live.config["environment"] = "live"

    assert demo.url() == "wss://wspap.okx.com/ws/v5/private"
    assert live.url() == "wss://ws.okx.com/ws/v5/private"


def test_bybit_subscribes_only_after_successful_authentication():
    adapter, _states = _adapter(BybitExecutionAdapter)
    ws = FakeSocket()
    adapter.handle_control(ws, {"op": "auth", "success": True})
    assert ws.messages == [{"op": "subscribe", "args": ["execution"]}]
    assert adapter.connected


def test_bybit_heartbeat_response_does_not_trigger_an_extra_pong():
    adapter, _states = _adapter(BybitExecutionAdapter)
    ws = FakeSocket()

    assert adapter.handle_control(ws, {"success": True, "ret_msg": "pong", "op": "ping"})
    assert ws.messages == []


@pytest.mark.parametrize("market_type", ("spot", "swap"))
def test_bitget_uses_uta_fill_subscription(market_type):
    adapter, _states = _adapter(BitgetExecutionAdapter, market_type=market_type)
    assert adapter.on_open_messages()[0]["op"] == "login"
    ws = FakeSocket()
    adapter.handle_control(ws, {"event": "login", "code": "0"})
    assert ws.messages[0]["args"][0] == {
        "instType": "UTA",
        "topic": "fill",
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
