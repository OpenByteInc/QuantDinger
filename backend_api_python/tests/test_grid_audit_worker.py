"""Audit reads must be bounded, durable, tenant-scoped, and execution-free."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.grid import audit_state, audit_worker, order_audit
from app.services.grid.resting_orders_repo import GridRestingOrder


@pytest.fixture
def strategy():
    return dict(id=3, user_id=7, status='stopped', execution_mode='live', desired_runtime_epoch=5,
                exchange_config={'exchange_id': 'binance', 'credential_id': 9},
                trading_config={'market_type': 'swap'})


@pytest.fixture
def row():
    return GridRestingOrder(id=12, strategy_id=3, symbol='ETH/USDT', status='open',
                           exchange_order_id='12345678901234567890', quantity=1)


def test_http_reads_cached_terminal_and_never_queues_when_fresh(monkeypatch, strategy, row):
    row.extra['exchange_audit'] = audit_state.stamp_audit({'status': 'cancelled'}, row, audit_state.audit_identity(strategy))
    commands = MagicMock()
    monkeypatch.setattr(audit_state, 'StrategyCommandRepository', lambda: commands)
    result = audit_state.read_grid_order_audit(strategy, [row], user_id=7, sync=True)
    assert result['terminal'] == 1
    assert result['unknown'] == 0
    assert not result['pending']
    commands.enqueue.assert_not_called()


def test_refresh_uses_stable_bucket_key_and_tenant(monkeypatch, strategy, row):
    commands = MagicMock()
    commands.enqueue.return_value.status = 'pending'
    monkeypatch.setattr(audit_state, 'StrategyCommandRepository', lambda: commands)
    monkeypatch.setattr(audit_state.time, 'time', lambda: 120)
    for _ in range(2):
        result = audit_state.read_grid_order_audit(strategy, [row], user_id=7, sync=True)
        assert result['pending']
        assert result['unknown'] == 1
    assert commands.enqueue.call_args_list[0] == commands.enqueue.call_args_list[1]
    args = commands.enqueue.call_args.kwargs
    assert args['user_id'] == 7 and args['strategy_id'] == 3
    assert args['idempotency_key'] == 'grid-audit:3:5:4'
    assert args['payload'] == {'grid_order_audit': True}


def test_account_or_order_change_invalidates_evidence(strategy, row):
    identity = audit_state.audit_identity(strategy)
    row.extra['exchange_audit'] = audit_state.stamp_audit({'status': 'open'}, row, identity)
    changed = {**strategy, 'exchange_config': {'credential_id': 10}}
    assert audit_state.cached_order_audit(row, audit_state.audit_identity(changed)) == {}
    assert audit_state.cached_order_audit(replace(row, exchange_order_id='other'), identity) == {}


def test_unknown_history_has_cooldown_but_is_never_cancelled(monkeypatch, strategy, row):
    entry = audit_state.stamp_audit({'status': 'not_open'}, row, audit_state.audit_identity(strategy))
    created = audit_state.datetime.fromisoformat(entry['checked_at']).timestamp()
    assert audit_state.audit_is_fresh(entry, now=created + 299)
    assert not audit_state.audit_is_fresh(entry, now=created + 301)
    assert row.status == 'open'


def test_missing_order_lookups_are_bounded_and_rotate(monkeypatch, strategy, row):
    monkeypatch.setattr(order_audit, 'resolve_exchange_config', lambda cfg, **kw: cfg)
    monkeypatch.setattr(order_audit, 'bind_instrument_product_contract', lambda cfg, *a, **kw: cfg)
    monkeypatch.setattr(order_audit, 'create_client', lambda *a, **kw: SimpleNamespace(timeout_sec=15))
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', lambda *a, **kw: [])
    query = MagicMock(return_value=(0, 0, 'unknown'))
    monkeypatch.setattr(order_audit, 'query_grid_order_fill', query)
    ledger = MagicMock()
    monkeypatch.setattr(order_audit, 'GridRestingOrderRepository', lambda: ledger)
    rows = [replace(row, id=i, exchange_order_id=str(i), extra={}) for i in range(500)]
    first = order_audit.audit_grid_orders(strategy, rows, user_id=7, max_lookups=10)
    assert query.call_count == 10 and not first['completed']
    for item in rows:
        if item.id in first['orders']:
            item.extra['exchange_audit'] = first['orders'][item.id]
    second = order_audit.audit_grid_orders(strategy, rows, user_id=7, max_lookups=10)
    assert query.call_count == 20
    assert len(second['orders']) == 20
    assert set(second['orders']) == set(range(20))
    ledger.mark_cancelled_if_unfilled.assert_not_called()


@pytest.mark.parametrize('stopped', [True, False])
def test_worker_verifies_without_fill_posting_or_runner(monkeypatch, strategy, row, stopped):
    strategy['status'] = 'stopped' if stopped else 'running'
    service = MagicMock()
    service.get_strategy.return_value = strategy
    repo, poller = MagicMock(), MagicMock()
    repo.list_for_audit.return_value = [row]
    repo.execution_bindings.return_value = {}
    monkeypatch.setattr(audit_worker, 'StrategyService', lambda: service)
    monkeypatch.setattr(audit_worker, 'GridRestingOrderRepository', lambda: repo)
    monkeypatch.setattr(audit_worker, 'get_grid_fill_poller', lambda: poller)
    monkeypatch.setattr(audit_worker, 'resolve_exchange_config', lambda cfg, **kw: cfg)
    engine = MagicMock(side_effect=AssertionError('verification must not create an engine'))
    monkeypatch.setattr('app.services.grid.engine.GridEngine', engine)
    audit = MagicMock(return_value={'orders': {row.id: {'status': 'cancelled', 'filled': .5,
                                                      'reconciliation_pending': True}}})
    monkeypatch.setattr(audit_worker, 'audit_grid_orders', audit)
    audit_worker.run_grid_order_audit(3, user_id=7, command_valid=lambda: True)
    service.get_strategy.assert_called_once_with(3, user_id=7)
    assert 'reconcile_order' not in audit.call_args.kwargs
    assert repo.save_audit.call_args.args[1]['reconciliation_pending']
    engine.assert_not_called()
    poller._poll_order.assert_not_called()


def test_terminal_evidence_survives_ttl_and_ledger_progress_clears_pending(monkeypatch, strategy, row):
    row.extra['exchange_audit'] = audit_state.stamp_audit(
        {'status': 'cancelled', 'filled': .5, 'reconciliation_pending': True},
        row, audit_state.audit_identity(strategy))
    row.extra['exchange_audit']['checked_at'] = '2020-01-01T00:00:00+00:00'
    commands = MagicMock()
    monkeypatch.setattr(audit_state, 'StrategyCommandRepository', lambda: commands)
    pending = audit_state.read_grid_order_audit(strategy, [row], user_id=7, sync=True)
    assert pending['terminal'] == 1 and pending['reconciliation_pending'] == 1
    posted = replace(row, status='cancelled', filled_quantity=.5, processed_fill_qty=.5)
    result = audit_state.read_grid_order_audit(strategy, [posted], user_id=7, sync=True)
    assert result['terminal'] == 1 and result['reconciliation_pending'] == 0
    commands.enqueue.assert_not_called()


def test_new_fill_invalidates_an_older_terminal_snapshot(strategy, row):
    identity = audit_state.audit_identity(strategy)
    row.extra['exchange_audit'] = audit_state.stamp_audit({'status': 'cancelled', 'filled': 0}, row, identity)
    assert audit_state.cached_order_audit(replace(row, filled_quantity=.1), identity) == {}


def test_failed_zero_fill_cancel_update_can_be_retried(strategy, row):
    entry = audit_state.stamp_audit({'status': 'cancelled', 'filled': 0, 'reconciliation_pending': True},
                                   row, audit_state.audit_identity(strategy))
    checked = audit_state.datetime.fromisoformat(entry['checked_at']).timestamp()
    assert not audit_state.audit_is_fresh(entry, now=checked + 31)


def test_refresh_preserves_last_known_result_while_queueing(monkeypatch, strategy, row):
    row.extra['exchange_audit'] = audit_state.stamp_audit({'status': 'open', 'filled': 0},
                                                        row, audit_state.audit_identity(strategy))
    row.extra['exchange_audit']['checked_at'] = '2020-01-01T00:00:00+00:00'
    commands = MagicMock()
    commands.enqueue.return_value.status = 'pending'
    monkeypatch.setattr(audit_state, 'StrategyCommandRepository', lambda: commands)
    result = audit_state.read_grid_order_audit(strategy, [row], user_id=7, sync=True)
    assert result['active'] == 1 and result['pending']
    assert not result['completed']
    assert result['checked_at'] == '2020-01-01T00:00:00+00:00'


def test_mismatched_credentials_cannot_be_accounted(strategy, row):
    binding = dict(strategy_id=3, user_id=7, credential_id=10, exchange_id='binance',
                   market_type='swap', exchange_order_id=row.exchange_order_id, symbol=row.symbol)
    assert not audit_worker.binding_matches(binding, row, strategy, strategy['exchange_config'], 'swap')


def test_trading_command_dispatches_audit_without_start_or_stop(monkeypatch):
    from app.workers.trading import TradingWorker
    from app.services.strategy_command_repository import StrategyCommand
    worker = TradingWorker.__new__(TradingWorker)
    worker.command_lease_seconds = 60
    worker._lease_heartbeat = MagicMock()
    worker._lease_heartbeat.command_valid.return_value = True
    worker.repository = MagicMock()
    worker.repository.runtime_request.return_value = {'desired_runtime_epoch': 5}
    worker._start = MagicMock(side_effect=AssertionError('must not start'))
    worker._stop_strategy = MagicMock(side_effect=AssertionError('must not stop'))
    audit = MagicMock(return_value={'completed': True, 'terminal': 1})
    monkeypatch.setattr(audit_worker, 'run_grid_order_audit', audit)
    command = StrategyCommand(1, 3, 7, 'reconcile', 'processing', 'grid-audit',
                              {'grid_order_audit': True, 'requested_runtime_epoch': 5})
    worker._execute(command)
    assert audit.call_args.kwargs['user_id'] == 7
    worker.repository.complete.assert_called_once_with(1, result={'completed': True, 'terminal': 1})
    worker._start.assert_not_called()
    worker._stop_strategy.assert_not_called()
