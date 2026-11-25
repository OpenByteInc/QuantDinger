"""Tests for grid poller scheduling and runtime state helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.grid.poller import GridFillPoller
from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.grid.runtime_state import load_grid_resting_state


@pytest.fixture(autouse=True)
def isolated_order_schema(monkeypatch):
    monkeypatch.setattr("app.services.grid.resting_orders_repo._ensure_table", lambda: None)


def test_load_grid_resting_state_initial_flag():
    tc = {"script_runtime_state": {"grid_resting": {"initial_market_done": True}}}
    assert load_grid_resting_state(tc).get("initial_market_done") is True


def test_poller_select_orders_respects_interval():
    poller = GridFillPoller()
    poller._min_order_interval = 100.0
    order = GridRestingOrder(id=1, strategy_id=99, symbol="BTC/USDT", status="open")
    poller._last_poll_by_order[1] = time.time()
    with patch("app.services.grid.poller.get_runner", return_value=MagicMock()):
        selected = poller._select_orders([order])
    assert selected == []


def test_poller_select_orders_prioritizes_partial():
    poller = GridFillPoller()
    poller._min_order_interval = 0.0
    partial = GridRestingOrder(id=1, strategy_id=1, symbol="BTC/USDT", status="partial")
    open_o = GridRestingOrder(id=2, strategy_id=1, symbol="BTC/USDT", status="open")
    runner = MagicMock()
    with patch("app.services.grid.poller.get_runner", return_value=runner):
        selected = poller._select_orders([open_o, partial])
    assert selected[0].status == "partial"


@pytest.mark.parametrize('status', ['partial', 'filled', 'cancelled'])
@pytest.mark.parametrize('fee', [0.0, 0.05, -0.01])
def test_poller_passes_one_consistent_cumulative_snapshot(monkeypatch, status, fee):
    from contextlib import nullcontext
    from app.services.execution_streams.processor import ExecutionEventProcessor
    worker = GridFillPoller()
    runner = MagicMock()
    runner.exchange_config = {'exchange_id': 'gate'}
    order = GridRestingOrder(id=13, strategy_id=1, symbol='BTC/USDT', quantity=1,
                             price=100, exchange_order_id='gate-13')
    monkeypatch.setattr('app.services.grid.poller.query_grid_order_fill', lambda *a, **k: (.25, 100, status))
    def snapshot(*args, **kwargs):
        kwargs['details'].update(fee=fee, fee_ccy='USDT', fee_status='actual_zero' if fee == 0 else 'actual')
        return .5, 105
    monkeypatch.setattr('app.services.grid.poller.wait_grid_market_fill', snapshot)
    monkeypatch.setattr('app.utils.db.get_db_transaction', nullcontext)
    monkeypatch.setattr('app.services.live_trading.fill_accounting.lock_strategy_fills', lambda *a: None)
    project = MagicMock()
    monkeypatch.setattr(ExecutionEventProcessor, '_project_grid', project)
    worker._poll_order(runner, object(), order, 'swap')
    event, binding = project.call_args.args
    assert event['cumulative_quantity'] == .5
    assert event['cumulative_average_price'] == 105
    assert event['order_status'] == status
    assert event['fees_cumulative'] is True
    assert event['_snapshot_fees'].get('USDT', 0) == fee
    assert binding['owner_id'] == 13
    if status == 'cancelled':
        runner.engine.sync_held_cell_exits.assert_called_once_with(105)


def test_terminal_status_without_quantity_does_not_invent_fill(monkeypatch):
    worker = GridFillPoller()
    runner = MagicMock()
    order = GridRestingOrder(id=1, quantity=10)
    monkeypatch.setattr('app.services.grid.poller.query_grid_order_fill', lambda *a, **k: (0, 0, 'filled'))
    worker._poll_order(runner, object(), order, 'swap')
    runner.engine.on_order_filled.assert_not_called()


def test_pending_order_recovers_exchange_id_without_resubmit(monkeypatch):
    from contextlib import nullcontext
    from app.services.execution_streams.processor import ExecutionEventProcessor

    worker = GridFillPoller()
    runner = MagicMock()
    runner.exchange_config = {"exchange_id": "gate"}
    order = GridRestingOrder(id=1, strategy_id=1, symbol="BTC/USDT", client_order_id="original-id")
    def snapshot(*args, **kwargs):
        assert kwargs["client_order_id"] == "original-id"
        kwargs["details"]["exchange_order_id"] = "venue-123"
        return 0, 0, "open"
    monkeypatch.setattr("app.services.grid.poller.query_grid_order_fill", snapshot)
    monkeypatch.setattr("app.utils.db.get_db_transaction", nullcontext)
    monkeypatch.setattr("app.services.live_trading.fill_accounting.lock_strategy_fills", lambda *a: None)
    project = MagicMock()
    monkeypatch.setattr(ExecutionEventProcessor, "_project_grid", project)
    assert worker._poll_order(runner, object(), order, "swap") == "open"
    assert project.call_args.args[0]["exchange_order_id"] == "venue-123"
    assert project.call_args.args[0]["cumulative_quantity"] == 0


def test_same_credential_never_reuses_spot_client_for_swap(monkeypatch):
    from types import SimpleNamespace
    from app.services.grid import poller as module
    worker = GridFillPoller()
    orders = [GridRestingOrder(id=1, strategy_id=1, symbol="ETH/USDT"),
              GridRestingOrder(id=2, strategy_id=2, symbol="ETH/USDT")]
    runners = {sid: SimpleNamespace(strategy_id=sid, user_id=1,
        exchange_config={"credential_id": 7, "exchange_id": "gate"},
        engine=SimpleNamespace(cfg=SimpleNamespace(market_type=market), trading_config={}))
        for sid, market in [(1, "spot"), (2, "swap")]}
    worker._repo = SimpleNamespace(list_open=lambda: orders, list_reconciliation=lambda: [])
    monkeypatch.setattr(worker, "_select_orders", lambda rows: rows)
    monkeypatch.setattr(module, "get_runner", lambda sid: runners[sid])
    monkeypatch.setattr(module, "resolve_exchange_config", lambda cfg, **kw: cfg)
    monkeypatch.setattr(module, "create_client", lambda cfg, market_type: market_type)
    observed = []
    monkeypatch.setattr(worker, "_poll_order", lambda runner, client, order, market: observed.append((client, market)))
    worker._poll_once()
    assert observed == [("spot", "spot"), ("swap", "swap")]


@pytest.mark.parametrize('allowed', [True, False])
def test_accounting_only_poller_never_rehangs_exits(monkeypatch, allowed):
    from contextlib import nullcontext
    from app.services.execution_streams.processor import ExecutionEventProcessor
    worker = GridFillPoller()
    runner = MagicMock()
    runner.exchange_config = {'exchange_id': 'binance'}
    order = GridRestingOrder(id=13, strategy_id=1, symbol='BTC/USDT', quantity=1)
    monkeypatch.setattr('app.services.grid.poller.query_grid_order_fill', lambda *a, **k: (.5, 100, 'cancelled'))
    monkeypatch.setattr('app.services.grid.poller.wait_grid_market_fill', lambda *a, **k: (.5, 100))
    monkeypatch.setattr('app.utils.db.get_db_transaction', nullcontext)
    monkeypatch.setattr('app.services.live_trading.fill_accounting.lock_strategy_fills', lambda *a: None)
    project = MagicMock()
    monkeypatch.setattr(ExecutionEventProcessor, '_project_grid', project)
    status = worker._poll_order(runner, object(), order, 'swap', accounting_only=True, before_project=lambda: allowed)
    assert status == ('cancelled' if allowed else 'unknown')
    assert project.call_count == int(allowed)
    if allowed:
        assert project.call_args.args[0]['_accounting_only'] is True
    runner.engine.sync_held_cell_exits.assert_not_called()
