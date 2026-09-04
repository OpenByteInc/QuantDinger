"""Regression tests for IBKR session ownership and account payload shape.

Two failures motivated these:

* every process defaulted to client id 1 and kept its session in local memory,
  so a second API worker's connect was answered with
  "Error 326: client id already in use";
* ``ib_insync`` binds a connection to the event loop of the creating thread, so
  a call issued from another request thread ran against an idle loop.
"""

import threading
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.services.ibkr_trading import config as ibkr_config
from app.services.ibkr_trading import session as ibkr_session
from app.services.ibkr_trading.account import (
    flatten_account_summary,
    position_entry_price,
)
from app.services.ibkr_trading.client import IBKRClient, IBKRConfig
from app.services.ibkr_trading.config import build_ibkr_config


class _FakeIBKRClient:
    """Stand-in that records which thread each call ran on."""

    connect_ok = True

    def __init__(self, config: IBKRConfig):
        self.config = config
        self._connected = False
        self.call_threads: set = set()

    def connect(self) -> bool:
        self.call_threads.add(threading.current_thread().name)
        self._connected = _FakeIBKRClient.connect_ok
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def get_positions(self):
        self.call_threads.add(threading.current_thread().name)
        return [{"symbol": "AAPL"}]


@pytest.fixture
def fake_ib(monkeypatch):
    _FakeIBKRClient.connect_ok = True
    monkeypatch.setattr(ibkr_session, "IBKRClient", _FakeIBKRClient)
    monkeypatch.setattr(ibkr_session, "_registry", {})
    return _FakeIBKRClient


def _config(client_id: int = 1) -> IBKRConfig:
    return IBKRConfig(host="gateway.test", port=4002, client_id=client_id)


def test_second_acquire_reuses_the_live_session(fake_ib):
    """A repeated connect must not open a second socket on the same client id."""
    first = ibkr_session.get_or_create_session(_config())
    second = ibkr_session.get_or_create_session(_config())

    assert second is first
    assert first.connected is True


def test_a_different_client_id_gets_its_own_session(fake_ib):
    ui_session = ibkr_session.get_or_create_session(_config(client_id=1))
    order_session = ibkr_session.get_or_create_session(_config(client_id=7))

    assert order_session is not ui_session


def test_calls_run_on_the_session_thread_not_the_caller(fake_ib):
    """Every IB call must be marshalled onto the thread that owns the loop."""
    session = ibkr_session.get_or_create_session(_config())
    caller_threads = set()

    def issue_call():
        caller_threads.add(threading.current_thread().name)
        session.get_positions()

    threads = [threading.Thread(target=issue_call, name=f"request-{i}") for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    used = session._client.call_threads
    assert len(used) == 1, f"IB calls leaked across threads: {used}"
    assert not (used & caller_threads)


def test_failed_connect_raises_and_leaves_no_registry_entry(fake_ib):
    fake_ib.connect_ok = False

    with pytest.raises(ConnectionError):
        ibkr_session.get_or_create_session(_config())

    assert ibkr_session.find_session(_config()) is None


def test_dead_session_is_replaced(fake_ib):
    stale = ibkr_session.get_or_create_session(_config())
    stale._client._connected = False

    assert ibkr_session.find_session(_config()) is None
    assert ibkr_session.get_or_create_session(_config()) is not stale


SAVED_CREDENTIAL: Dict[str, Any] = {
    "ibkr_host": "host.docker.internal",
    "ibkr_port": 4002,
    "ibkr_client_id": 7,
    "ibkr_account": "DU1234567",
}


@pytest.fixture
def saved_credential(monkeypatch):
    monkeypatch.setattr(
        ibkr_config, "load_saved_ibkr_config", lambda user_id: dict(SAVED_CREDENTIAL)
    )


def test_ui_placeholder_host_falls_back_to_the_saved_host(saved_credential):
    """127.0.0.1 is the UI default and is unreachable from inside a container."""
    resolved = build_ibkr_config({"host": "127.0.0.1", "port": 4002, "clientId": 1}, user_id=1)

    assert resolved.host == "host.docker.internal"
    assert resolved.account == "DU1234567"


def test_explicit_host_and_default_looking_port_are_honoured(saved_credential):
    resolved = build_ibkr_config({"host": "192.168.1.9", "port": 7497, "clientId": 3}, user_id=1)

    assert (resolved.host, resolved.port, resolved.client_id) == ("192.168.1.9", 7497, 3)


def test_blank_client_id_falls_back_instead_of_raising(saved_credential):
    resolved = build_ibkr_config({"host": "", "port": "", "clientId": ""}, user_id=1)

    assert resolved.client_id == ibkr_config.DEFAULT_CLIENT_ID
    assert (resolved.host, resolved.port) == ("host.docker.internal", 4002)


def test_ui_session_never_borrows_the_credential_order_client_id(saved_credential):
    """ibkr_client_id belongs to the order session; sharing it evicts live orders."""
    resolved = build_ibkr_config({}, user_id=1)

    assert resolved.client_id == ibkr_config.DEFAULT_CLIENT_ID
    assert resolved.client_id != SAVED_CREDENTIAL["ibkr_client_id"]


def test_missing_credential_falls_back_to_ib_defaults(monkeypatch):
    monkeypatch.setattr(ibkr_config, "load_saved_ibkr_config", lambda user_id: {})

    resolved = build_ibkr_config({}, user_id=1)

    assert (resolved.host, resolved.port, resolved.client_id) == (
        ibkr_config.DEFAULT_HOST,
        ibkr_config.DEFAULT_PORT,
        ibkr_config.DEFAULT_CLIENT_ID,
    )


def test_flatten_exposes_the_fields_the_account_ui_reads():
    summary = {
        "AccountType": {"value": "INDIVIDUAL", "currency": ""},
        "NetLiquidation": {"value": "179817.69", "currency": "HKD"},
        "TotalCashValue": {"value": "181397.32", "currency": "HKD"},
        "BuyingPower": {"value": "627661.25", "currency": "HKD"},
        "InitMarginReq": {"value": "85668.50", "currency": "HKD"},
        "MaintMarginReq": {"value": "73394.64", "currency": "HKD"},
    }

    flat = flatten_account_summary(summary)

    assert flat["net_liquidation"] == pytest.approx(179817.69)
    assert flat["total_cash_value"] == pytest.approx(181397.32)
    assert flat["buying_power"] == pytest.approx(627661.25)
    assert flat["init_margin_req"] == pytest.approx(85668.50)
    assert flat["maint_margin_req"] == pytest.approx(73394.64)
    # Descriptive tags carry no currency, so it must come from a monetary tag.
    assert flat["currency"] == "HKD"
    assert flat["account_currency"] == "HKD"


def test_flatten_falls_back_to_the_full_margin_tags():
    flat = flatten_account_summary(
        {"FullInitMarginReq": {"value": "10", "currency": "USD"},
         "FullMaintMarginReq": {"value": "5", "currency": "USD"}}
    )

    assert flat["init_margin_req"] == pytest.approx(10)
    assert flat["maint_margin_req"] == pytest.approx(5)


def test_flatten_omits_missing_tags_rather_than_reporting_zero():
    """A margin requirement shown as 0 reads very differently from "unknown"."""
    flat = flatten_account_summary({"NetLiquidation": {"value": "100", "currency": "USD"}})

    assert flat["net_liquidation"] == pytest.approx(100)
    assert "maint_margin_req" not in flat
    assert "buying_power" not in flat


def test_flatten_ignores_unusable_input():
    assert flatten_account_summary(None) == {}
    assert flatten_account_summary({"NetLiquidation": {"value": "", "currency": "USD"}}) == {}


class _FakeContract(SimpleNamespace):
    pass


def _futures_position():
    """A real MGC micro gold future: multiplier 10, avgCost already multiplied."""
    contract = _FakeContract(
        symbol="MGC", localSymbol="MGCV6", secType="FUT", exchange="",
        primaryExchange="", currency="USD", multiplier="10",
        lastTradeDateOrContractMonth="20261028",
    )
    return SimpleNamespace(contract=contract, position=1.0, avgCost=44332.96)


def _stock_position():
    contract = _FakeContract(
        symbol="AAPL", localSymbol="AAPL", secType="STK", exchange="SMART",
        primaryExchange="NASDAQ", currency="USD", multiplier="",
        lastTradeDateOrContractMonth="",
    )
    return SimpleNamespace(contract=contract, position=10.0, avgCost=205.25)


def _client_with_positions(positions):
    client = object.__new__(IBKRClient)
    client._ib = SimpleNamespace(positions=lambda account: positions)
    client._account = "DU1"
    client._ensure_connected = lambda: None
    return client


def test_futures_average_price_is_reported_per_unit():
    """avgCost is per contract; showing it as the entry price inflates it 10x."""
    row = _client_with_positions([_futures_position()]).get_positions()[0]

    assert row["multiplier"] == 10
    assert row["avgCost"] == pytest.approx(44332.96)
    assert row["avgPrice"] == pytest.approx(4433.296)
    assert row["localSymbol"] == "MGCV6"
    assert row["lastTradeDate"] == "20261028"


def test_stock_average_price_is_unchanged_by_the_multiplier():
    row = _client_with_positions([_stock_position()]).get_positions()[0]

    assert row["multiplier"] == 1
    assert row["avgPrice"] == pytest.approx(205.25)
    assert row["avgCost"] == pytest.approx(205.25)


def _client_with_order(order_type, lmt_price, aux_price):
    order = SimpleNamespace(
        orderId=40, permId=515101143, action="SELL", totalQuantity=1.0,
        orderType=order_type, lmtPrice=lmt_price, auxPrice=aux_price,
    )
    trade = SimpleNamespace(
        order=order,
        contract=_FakeContract(symbol="MGC", localSymbol="MGCV6"),
        orderStatus=SimpleNamespace(
            status="PreSubmitted", filled=0, remaining=1, avgFillPrice=0
        ),
    )
    client = object.__new__(IBKRClient)
    client._ib = SimpleNamespace(openTrades=lambda: [trade])
    client._ensure_connected = lambda: None
    return client


def test_stop_order_reports_its_trigger_price():
    """A protective stop keeps its price in auxPrice; lmtPrice is 0."""
    row = _client_with_order("STP", 0.0, 4350.0).get_open_orders()[0]

    assert row["stopPrice"] == pytest.approx(4350.0)
    assert row["price"] == pytest.approx(4350.0)
    assert row["id"] == 40


def test_limit_order_still_reports_its_limit_price():
    row = _client_with_order("LMT", 4400.0, 0.0).get_open_orders()[0]

    assert row["price"] == pytest.approx(4400.0)
    assert row["stopPrice"] is None


def test_entry_price_prefers_the_per_unit_average():
    """Reconciliation and account mirrors must not use the per-contract cost."""
    row = _client_with_positions([_futures_position()]).get_positions()[0]

    assert position_entry_price(row) == pytest.approx(4433.296)


def test_entry_price_falls_back_to_avg_cost_for_rows_without_avg_price():
    assert position_entry_price({"avgCost": 205.25}) == pytest.approx(205.25)


def test_entry_price_ignores_unusable_rows():
    assert position_entry_price(None) == 0.0
    assert position_entry_price({}) == 0.0
    assert position_entry_price({"avgPrice": "", "avgCost": "n/a"}) == 0.0
