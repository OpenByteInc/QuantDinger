"""Broker identity and history regressions using only offline fixtures."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask

from app.services.live_trading.records import normalize_strategy_symbol, _position_symbol_candidates
from app.services.live_trading.broker_snapshot import broker_position_maps
from app.services.live_trading.position_query import query_exchange_position_size, symbols_equivalent
from app.services.openapi_brokers.tiger import TigerClient
from app.services.ibkr_trading.client import IBKRClient


@pytest.mark.parametrize("symbol", ["700", "00700", "700.HK", "00700.HK", "HKStock:00700", "HK.00700", "HK00700"])
def test_hk_strategy_identity_matches_both_brokers(symbol):
    assert normalize_strategy_symbol(symbol) == "00700.HK"
    assert TigerClient._query_symbol(symbol) == "00700"
    client = object.__new__(TigerClient)
    client.get_positions = lambda **kw: [{"symbol": "00700.HK", "quantity": 200}]
    assert query_exchange_position_size(client=client, symbol=symbol, pos_side="long", market_type="spot", strict=True) == 200


@pytest.mark.parametrize("symbol, expected", [
    ("AAPL", "AAPL"), ("BRK-B", "BRK-B"), ("BRK.B", "BRK.B"),
    ("BTC-USDT", "BTC/USDT"), ("BTC/USDT:USDT", "BTC/USDT"),
    ("600519", "600519"), ("HSIZ26", "HSIZ26"), ("HSIF27", "HSIF27"),
    ("USStock:700", "USSTOCK:700"), ("Futures:700", "FUTURES:700"),
])
def test_equity_identity_preserves_other_markets(symbol, expected):
    assert normalize_strategy_symbol(symbol) == expected


def test_existing_hk_position_keys_remain_queryable():
    candidates = _position_symbol_candidates("00700.HK")
    assert {"700", "00700", "00700.HK", "HKStock:00700"}.issubset(candidates)


def test_share_classes_cannot_resolve_to_a_different_stock_position():
    for symbol in ("BRK-B", "BRK/B", "BRK.B"):
        assert "BRKB" not in _position_symbol_candidates(symbol)
        assert not symbols_equivalent(symbol, "BRKB")


@pytest.mark.parametrize("broker", ["alpaca", "ibkr", "tiger", "webull", "forthright"])
def test_account_legs_keep_a_distinct_instrument_id_for_each_stock(broker):
    from app.services.live_trading.leg_context import inst_id_for_symbol
    assert inst_id_for_symbol("AAPL", "spot", broker) == "AAPL"
    assert inst_id_for_symbol("MSFT", "spot", broker) == "MSFT"
    assert inst_id_for_symbol("00700", "spot", broker) == "00700.HK"


@pytest.mark.parametrize("broker", ["tiger", "webull", "forthright", "ibkr"])
def test_account_snapshot_reads_broker_positions_and_orders(monkeypatch, broker):
    from app.services.live_trading import account_snapshot as snapshots
    calls = []
    def positions(**kw):
        assert kw["raise_on_error"]
        return [{"symbol": "AAPL", "quantity": 3, "avg_entry_price": 200}]
    client = SimpleNamespace(get_positions=positions, get_orders=lambda **kw: [], get_open_orders=lambda **kw: [])
    monkeypatch.setattr(snapshots, "resolve_exchange_config", lambda cfg, **kw: {"exchange_id": broker})
    monkeypatch.setattr(snapshots, "create_client", lambda cfg, **kw: calls.append(kw["market_type"]) or client)
    snapshot = snapshots.fetch_account_snapshot(user_id=7, credential_id=11)
    assert calls == ["spot"]
    assert snapshot["spot_positions"][0]["size"] == 3
    assert snapshot["spot_positions"][0]["entry_price"] == 200
    assert snapshot["swap_positions"] == []


def test_account_snapshot_failure_is_not_silently_reported_as_empty(monkeypatch):
    from app.services.live_trading import account_snapshot as snapshots
    client = SimpleNamespace(get_positions=lambda **kw: None, get_orders=lambda **kw: [])
    monkeypatch.setattr(snapshots, "create_client", lambda *args, **kw: client)
    errors = []
    _, positions, _ = snapshots._fetch_equity_broker_snapshot({"exchange_id": "tiger"}, errors)
    assert positions == []
    assert errors == ["brokerAccounts.snapshotPositionsFailed"]


@pytest.mark.parametrize("broker", ["webull", "tiger"])
def test_fallback_history_includes_partial_and_cancelled_fills_without_unfilled_orders(monkeypatch, broker):
    from app.routes import openapi_brokers as routes
    def orders(**kw):
        if kw["status"] == "open":
            return [{"id": "partial", "symbol": "00700", "filled_qty": 20},
                    {"id": "unfilled", "symbol": "00700", "filled_qty": 0}]
        return [{"id": "partial", "symbol": "00700", "filled_qty": 10},
                {"id": "cancelled", "symbol": "00700", "filled_qty": 5, "status": "cancelled"},
                {"id": "other", "symbol": "00388", "filled_qty": 10}]
    monkeypatch.setattr(routes, "_connected_client", lambda name: (SimpleNamespace(get_orders=orders), None))
    with Flask(__name__).test_request_context("/fills?symbol=00700.HK"):
        data = inspect.unwrap(routes.fills)(broker).get_json()["data"]
    assert {row["id"]: row["filled_qty"] for row in data} == {"partial": 20, "cancelled": 5}


@pytest.mark.parametrize("rows", [None, {}, [None], [{"symbol": "AAPL"}], [{"symbol": "AAPL", "quantity": float("nan")} ]])
def test_invalid_broker_snapshot_never_means_flat(rows):
    with pytest.raises((ValueError, TypeError)):
        broker_position_maps(rows)


def test_broker_snapshot_preserves_hk_shares_us_short_and_cost():
    sizes, prices, instruments = broker_position_maps([
        {"symbol": "00700", "quantity": 100, "avg_entry_price": 353},
        {"symbol": "00700.HK", "quantity": 100, "avg_entry_price": 353.2},
        {"symbol": "AAPL", "quantity": -2, "avg_entry_price": 200},
    ])
    assert sizes["00700.HK"]["long"] == 200
    assert prices["00700.HK"]["long"] == pytest.approx(353.1)
    assert instruments["00700.HK"]["long"] == "00700.HK"
    assert sizes["AAPL"]["short"] == 2


def test_tiger_history_uses_sdk_symbol_for_query(monkeypatch):
    client = object.__new__(TigerClient)
    client.account_id = "test-account"
    calls = []
    sdk = SimpleNamespace(get_transactions=lambda **kw: calls.append(kw) or [], get_orders=lambda **kw: calls.append(kw) or [])
    monkeypatch.setattr(client, "_ensure_client", lambda: sdk)
    monkeypatch.setattr(client, "_sdk", lambda: {"SecurityType": SimpleNamespace(STK="STK"), "Market": SimpleNamespace(ALL="ALL")})
    client.get_transactions("00700.HK")
    client.get_orders(symbol="HKStock:700")
    client.get_transactions("BRK.B")
    assert [row["symbol"] for row in calls] == ["00700", "00700", "BRK.B"]
    assert all(row["account"] == "test-account" for row in calls)


def test_ibkr_fills_endpoint_uses_executions_and_preserves_partial_fill(monkeypatch):
    from app.routes import ibkr
    def executions(symbol, **kw):
        assert symbol == "AAPL" and kw["raise_on_error"]
        return [{"execution_id": "fill-1", "order_id": 12, "symbol": "AAPL", "side": "SLD", "quantity": 2,
                 "price": 205.5, "executed_at": datetime(2026, 11, 27, tzinfo=timezone.utc)}]
    monkeypatch.setattr(ibkr, "_require_connected_client", lambda: (SimpleNamespace(get_executions=executions), None))
    with Flask(__name__).test_request_context("/fills?symbol=AAPL"):
        data = inspect.unwrap(ibkr.get_fills)().get_json()["data"]
    assert data[0]["side"] == "sell"
    assert data[0]["filled_qty"] == 2
    assert data[0]["filled_avg_price"] == 205.5
    assert data[0]["id"] == "fill-1"


def test_ibkr_execution_query_is_account_scoped_and_preserves_share_classes():
    def fill(account, symbol):
        return SimpleNamespace(contract=SimpleNamespace(symbol=symbol),
            execution=SimpleNamespace(acctNumber=account, execId=account + symbol, side="BOT", shares=1, price=10))
    client = object.__new__(IBKRClient)
    client._account = "DU1"
    client._ensure_connected = lambda: None
    client._ib = SimpleNamespace(reqExecutions=lambda: [fill("DU1", "BRK-B"), fill("DU2", "BRK-B"), fill("DU1", "BRKB")])
    rows = client.get_executions("BRK-B", raise_on_error=True)
    assert len(rows) == 1 and rows[0]["symbol"] == "BRK-B"


def test_ibkr_failed_execution_query_propagates_for_history():
    client = object.__new__(IBKRClient)
    client._ensure_connected = lambda: None
    def failed():
        raise TimeoutError("offline")
    client._ib = SimpleNamespace(reqExecutions=failed)
    with pytest.raises(TimeoutError):
        client.get_executions(raise_on_error=True)


def test_ibkr_open_orders_stay_with_the_selected_account():
    def trade(account):
        return SimpleNamespace(
            contract=SimpleNamespace(symbol="AAPL"),
            order=SimpleNamespace(account=account, orderId=1, action="SELL", totalQuantity=5, orderType="LMT"),
            orderStatus=SimpleNamespace(status="Submitted", filled=2, remaining=3, avgFillPrice=200),
        )
    client = object.__new__(IBKRClient)
    client._account = "DU1"
    client._ensure_connected = lambda: None
    client._ib = SimpleNamespace(openTrades=lambda: [trade("DU1"), trade("DU2")])
    rows = client.get_open_orders(raise_on_error=True)
    assert len(rows) == 1 and rows[0]["filled"] == 2


def test_alpaca_fill_history_matches_its_share_class_format_without_merging_symbols():
    from app.services.alpaca_trading.client import AlpacaClient
    client = object.__new__(AlpacaClient)
    client.get_account_activities = lambda **kw: [
        {"id": symbol, "symbol": symbol, "side": "buy", "qty": "1", "price": "100"}
        for symbol in ("BRK.B", "BRKB", "BRK-B", "BTC/USD")
    ]
    assert [row["symbol"] for row in client.get_fill_history(symbol="BRK/B")] == ["BRK.B"]
    assert [row["symbol"] for row in client.get_fill_history(symbol="BRK-B")] == ["BRK-B"]
    assert [row["symbol"] for row in client.get_fill_history(symbol="BTCUSD")] == ["BTCUSD"]


@pytest.mark.parametrize("broker, broker_symbol", [("tiger", "00700"), ("forthright", "00700.HK")])
def test_hk_strategy_exit_preserves_manual_and_other_strategy_holdings(monkeypatch, broker, broker_symbol):
    from app.services.live_trading import alpaca_ownership as guard
    monkeypatch.setattr(guard, "ensure_alpaca_settled", lambda **kw: None)
    monkeypatch.setattr(guard, "list_strategy_allocations_for_account", lambda **kw: [
        {"strategy_id": 1, "side": "long", "size": 100},
        {"strategy_id": 2, "side": "long", "size": 100},
    ])
    observed = []
    monkeypatch.setattr(guard, "evaluate_and_record_ownership", lambda **kw: observed.append(kw) or SimpleNamespace(allowed=True))
    normalized = []
    client = SimpleNamespace(
        get_positions=lambda **kw: [{"symbol": broker_symbol, "quantity": 500, "salable_qty": 500}],
        get_orders=lambda **kw: [],
        normalize_strategy_quantity=lambda symbol, amount: normalized.append(amount) or amount,
    )
    qty = guard.guarded_alpaca_quantity(client=client, strategy_id=1, user_id=7, credential_id=11,
        symbol="700", signal_type="close_long", amount=500, order_id=3, exchange_id=broker)
    assert qty == 100
    assert observed[0]["account_qty"] == 500
    assert observed[0]["strategy_qty"] == 200
    assert normalized == [500, 100]
