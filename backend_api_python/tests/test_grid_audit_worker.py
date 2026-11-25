"""Audit reads must be bounded, durable, tenant-scoped, and execution-free."""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.grid import audit_state, audit_worker, order_audit
from app.services.grid.engine import GridEngine
from app.services.grid.resting_orders_repo import GridRestingOrder
from app.services.live_trading.grid_cells import GridCell, GridCellState


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


@pytest.mark.parametrize('purpose,initial_state,quantity', [
    ('long_entry', GridCellState.IDLE, 0), ('short_entry', GridCellState.IDLE, 0),
    ('long_exit', GridCellState.LONG_HELD, 1), ('short_exit', GridCellState.SHORT_HELD, 1),
])
def test_accounting_only_fill_updates_cell_without_new_orders(monkeypatch, row, purpose, initial_state, quantity):
    monkeypatch.setattr('app.services.grid.fill_handler.apply_grid_fill_to_local_state', MagicMock())
    monkeypatch.setattr('app.services.grid.engine.append_strategy_log', lambda *a: None)
    engine = GridEngine.__new__(GridEngine)
    engine.strategy_id, engine.symbol = 3, 'ETH/USDT'
    engine.trading_config = {'market_type': 'swap'}
    engine._observe_market_price = MagicMock()
    engine._cells = MagicMock()
    engine._cell_record = lambda i: GridCell(3, 'ETH/USDT', i, 90, 110,
        state=initial_state, leg_size=quantity, leg_entry_price=95)
    engine._levels_and_cells = MagicMock(side_effect=AssertionError('must use persisted cells'))
    engine._ensure_cell_exit_coverage = MagicMock(side_effect=AssertionError('must not place exits'))
    engine._place_limit = MagicMock(side_effect=AssertionError('must not place entries'))
    engine.on_order_filled(replace(row, purpose=purpose), 1, 100, place_followup_orders=False)
    assert engine._cells.update_state.call_count == 1
    assert engine._cells.update_state.call_args.kwargs['leg_size'] == (1 if purpose.endswith('entry') else 0)
    engine._ensure_cell_exit_coverage.assert_not_called()
    engine._place_limit.assert_not_called()


@pytest.mark.parametrize('status,epoch,lease,expected', [
    ('stopped', 5, False, True), ('running', 5, False, False),
    ('stopped', 6, False, False), ('stopped', 5, True, False),
])
def test_stopped_recovery_fences_restart_and_active_owner(monkeypatch, strategy, status, epoch, lease, expected):
    db = MagicMock()
    db.cursor.return_value.fetchone.return_value = {**strategy, 'status': status, 'desired_runtime_epoch': epoch}
    monkeypatch.setattr(audit_worker, 'get_db_connection', lambda: nullcontext(db))
    commands = MagicMock()
    commands.has_active_strategy_lease.return_value = lease
    monkeypatch.setattr(audit_worker, 'StrategyCommandRepository', lambda: commands)
    assert audit_worker._stopped_context_unchanged(strategy, lambda: True) is expected
    assert 'FOR UPDATE' in db.cursor.return_value.execute.call_args.args[0]


@pytest.mark.parametrize('stopped', [True, False])
def test_worker_posts_stopped_fills_only_with_accounting_flag(monkeypatch, strategy, row, stopped):
    strategy['status'] = 'stopped' if stopped else 'running'
    service = MagicMock()
    service.get_strategy.return_value = strategy
    repo, poller = MagicMock(), MagicMock()
    repo.list_for_audit.return_value = [row]
    binding = dict(owner_id=row.id, strategy_id=3, user_id=7, credential_id=9, exchange_id='binance',
                   market_type='swap', exchange_order_id=row.exchange_order_id, symbol=row.symbol)
    repo.execution_bindings.return_value = {row.id: binding}
    repo.execution_binding.return_value = binding
    monkeypatch.setattr(audit_worker, 'StrategyService', lambda: service)
    monkeypatch.setattr(audit_worker, 'GridRestingOrderRepository', lambda: repo)
    monkeypatch.setattr(audit_worker, 'get_grid_fill_poller', lambda: poller)
    monkeypatch.setattr(audit_worker, 'resolve_exchange_config', lambda cfg, **kw: cfg)
    monkeypatch.setattr(audit_worker, '_reconciled', lambda *a: True)
    engine = MagicMock()
    monkeypatch.setattr(audit_worker, 'GridEngine', engine)
    def audit(st, rows, **kwargs):
        done = kwargs['reconcile_order'](object(), row, strategy['exchange_config'], 'swap', {'status': 'filled', 'filled': 1})
        assert done == stopped
        return {'orders': {row.id: {'status': 'filled', 'reconciliation_pending': not done}}}
    monkeypatch.setattr(audit_worker, 'audit_grid_orders', audit)
    audit_worker.run_grid_order_audit(3, user_id=7, command_valid=lambda: True)
    service.get_strategy.assert_called_once_with(3, user_id=7)
    if stopped:
        assert poller._poll_order.call_args.kwargs['accounting_only'] is True
        with pytest.raises(RuntimeError, match='must not submit'):
            engine.call_args.kwargs['enqueue_market']()
    else:
        engine.assert_not_called()
        poller._poll_order.assert_not_called()


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
