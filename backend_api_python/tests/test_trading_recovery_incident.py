"""Regressions for slow restore, lease starvation and unconsumed close orders."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.workers.lease_heartbeat import LeaseHeartbeat
from app.workers.trading import TradingWorker
from app.services.pending_order_worker import PendingOrderWorker
from app.services.grid.exchange_orders import cancel_grid_order
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient


class LeaseDatabase:
    def __init__(self):
        self.now = 100.0
        self.strategies = {}
        self.global_expiry = 130.0
        self.command_expiry = 130.0
        self.renewed = threading.Event()
        self.closed = False
        self.fail = False

    def cursor(self):
        database = self
        class Cursor:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def execute(self, sql, params):
                if database.fail:
                    raise RuntimeError("database unavailable")
                ttl, target, owner = params
                assert owner == 'test-worker'
                if 'qd_strategy_runtime_leases' in sql:
                    self.rows = [(sid,) for sid in target if database.strategies.get(sid, 0) >= database.now]
                    for sid, in self.rows:
                        database.strategies[sid] = database.now + ttl
                    database.renewed.set()
                elif 'qd_process_leases' in sql:
                    self.rows = [(target,)] if database.global_expiry >= database.now else []
                    if self.rows:
                        database.global_expiry = database.now + ttl
                else:
                    self.rows = [(target,)] if database.command_expiry >= database.now else []
                    if self.rows:
                        database.command_expiry = database.now + ttl
            def fetchone(self):
                return self.rows[0] if self.rows else None
            def fetchall(self):
                return self.rows
        return Cursor()

    def close(self):
        self.closed = True


def test_restore_24_strategies_schedules_without_serial_ready_waits(monkeypatch):
    database = LeaseDatabase()
    monkeypatch.setenv('QD_WORKER_ID', 'test-worker')
    monkeypatch.setenv('STRATEGY_DISTRIBUTED_BAR_ENABLED', 'false')
    monkeypatch.setattr('app.workers.lease_heartbeat.time.monotonic', lambda: database.now)
    service = SimpleNamespace(get_running_strategies_with_type=lambda: [{'id': sid} for sid in range(1, 25)],
                              get_strategy=lambda sid: {'id': sid, 'status': 'running'})
    monkeypatch.setattr('app.services.strategy.StrategyService', lambda: service)
    executor = SimpleNamespace(running_strategies={})
    def start(sid):
        executor.running_strategies[sid] = object()
        return True
    wait = Mock(return_value=(True, ''))
    def acquire(**kwargs):
        database.strategies[kwargs['strategy_id']] = database.now + kwargs['lease_seconds']
        return 1
    executor.start_strategy, executor.wait_strategy_running = start, wait
    repository = SimpleNamespace(
        has_pending_stop=lambda _: False,
        acquire_strategy_lease=acquire,
        runtime_request=lambda _: {"retry_after_seconds": 0, "startup_retry_at": 0},
    )
    worker = TradingWorker(executor, repository)
    heartbeat = worker._lease_heartbeat
    heartbeat._connect = lambda: database
    heartbeat.interval = 0.005
    heartbeat.watch_global('trading-global-services')
    heartbeat.start()
    try:
        worker.restore_desired_strategies()
        assert database.now == 100
        assert len(executor.running_strategies) == 24
        wait.assert_not_called()
        assert heartbeat.global_valid()
        assert all(heartbeat.strategy_valid(sid) for sid in range(1, 25))
        assert not worker._stop.is_set()
        assert not worker._lease_losses
    finally:
        heartbeat.close()


def test_expired_or_transferred_lease_is_never_reacquired(monkeypatch):
    database = LeaseDatabase()
    monkeypatch.setattr('app.workers.lease_heartbeat.time.monotonic', lambda: database.now)
    lost = Mock()
    heartbeat = LeaseHeartbeat('test-worker', 30, lost, lambda: database)
    heartbeat.watch_strategy(20)
    heartbeat.watch_global('global')
    database.now = 131
    assert not heartbeat.strategy_valid(20)
    assert not heartbeat.global_valid()
    heartbeat.renew()
    assert not heartbeat.strategy_valid(20)
    assert not heartbeat.global_valid()
    assert lost.call_count == 2


def test_pool_outage_cannot_extend_local_lease_deadline(monkeypatch):
    database = LeaseDatabase()
    monkeypatch.setattr('app.workers.lease_heartbeat.time.monotonic', lambda: database.now)
    heartbeat = LeaseHeartbeat('test-worker', 30, Mock(), lambda: database)
    heartbeat.watch_strategy(20)
    database.fail = True
    heartbeat.renew()
    assert heartbeat.strategy_valid(20)
    database.now = 131
    assert not heartbeat.strategy_valid(20)


def test_long_command_is_renewed_and_loss_is_detected(monkeypatch):
    database = LeaseDatabase()
    monkeypatch.setattr('app.workers.lease_heartbeat.time.monotonic', lambda: database.now)
    lost = Mock()
    heartbeat = LeaseHeartbeat('test-worker', 30, lost, lambda: database)
    heartbeat.watch_command(9, 30)
    for _ in range(8):
        database.now += 10
        heartbeat.renew()
        assert heartbeat.command_valid(9)
    database.command_expiry = 0
    heartbeat.renew()
    assert not heartbeat.command_valid(9)
    lost.assert_called_once_with('command', 9)


def test_close_dispatch_continues_while_exchange_sync_blocks(monkeypatch):
    worker = PendingOrderWorker()
    blocked, release, dispatched = threading.Event(), threading.Event(), threading.Event()
    def sync():
        blocked.set()
        release.wait(2)
    worker._sync_quick_trade_orders = sync
    worker._sync_alpaca_sent_orders = Mock()
    worker._sync_live_sent_orders = Mock()
    worker._maybe_sync_positions = Mock()
    worker._fetch_pending_orders = lambda **_: [{'id': 56716, 'signal_type': 'close_long'}]
    worker._mark_processing = lambda **_: True
    worker._dispatch_one = lambda _: dispatched.set()
    thread = threading.Thread(target=worker._run_sync_loop)
    thread.start()
    try:
        assert blocked.wait(1)
        worker._tick()
        assert dispatched.is_set()
    finally:
        worker._stop_event.set()
        release.set()
        thread.join(2)


def test_consumer_does_not_claim_after_global_lease_loss():
    worker = PendingOrderWorker()
    worker.lease_guard = lambda: False
    worker._fetch_pending_orders = Mock()
    worker._tick()
    worker._fetch_pending_orders.assert_not_called()


@pytest.mark.parametrize('client_class,path', [
    (GateSpotClient, '/api/v4/spot/orders/28201'),
    (GateUsdtFuturesClient, '/api/v4/futures/usdt/orders/28201'),
])
def test_gate_grid_cancel_uses_native_signature(client_class, path):
    client = client_class.__new__(client_class)
    client._signed_request = Mock(return_value={'id': '28201'})
    cancel_grid_order(client, symbol='BTC/USDT', market_type='spot', exchange_order_id='28201', client_order_id='grid-168-1')
    expected = {'params': {'currency_pair': 'BTC_USDT'}} if client_class is GateSpotClient else {}
    client._signed_request.assert_called_once_with('DELETE', path, **expected)


@pytest.mark.parametrize('client_class,path', [
    (GateSpotClient, '/api/v4/spot/orders/t-grid-168-1'),
    (GateUsdtFuturesClient, '/api/v4/futures/usdt/orders/t-grid-168-1'),
])
def test_gate_cancel_recovers_with_exchange_custom_order_id(client_class, path):
    client = client_class.__new__(client_class)
    client._signed_request = Mock(return_value={'id': '28201'})

    cancel_grid_order(
        client,
        symbol='BTC/USDT',
        market_type='spot',
        client_order_id='grid-168-1',
    )

    expected = {'params': {'currency_pair': 'BTC_USDT'}} if client_class is GateSpotClient else {}
    client._signed_request.assert_called_once_with('DELETE', path, **expected)


def test_heartbeat_uses_a_bounded_dedicated_connection(monkeypatch):
    connection = SimpleNamespace(autocommit=False)
    connect = Mock(return_value=connection)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://tester@localhost/testdb')
    monkeypatch.setattr('app.workers.lease_heartbeat.psycopg2.connect', connect)
    monkeypatch.setattr('app.utils.db_postgres._get_connection_pool', Mock(side_effect=AssertionError('shared pool must not be used')))
    assert LeaseHeartbeat._open_connection() is connection
    assert connection.autocommit
    params = connect.call_args.kwargs
    assert params['connect_timeout'] == 3
    assert 'statement_timeout=3000' in params['options']


def test_pending_service_start_failure_is_retried_while_lease_is_maintained(monkeypatch):
    monkeypatch.setattr('app.workers.trading.time.monotonic', lambda: 100)
    executor = SimpleNamespace(running_strategies={})
    repository = SimpleNamespace(acquire_process_lease=Mock(return_value=True))
    worker = TradingWorker(executor, repository)
    start_services = Mock(side_effect=[RuntimeError('thread capacity temporarily exhausted'), None])
    monkeypatch.setattr('app.startup._start_trading_support_services', start_services)
    worker._ensure_global_services()
    assert worker._lease_heartbeat.global_valid()
    worker._last_global_lease_check = 0
    worker._ensure_global_services()
    assert start_services.call_count == 2
    repository.acquire_process_lease.assert_called_once()


def test_expired_runtime_guard_prevents_another_signal_cycle():
    from app.services.trading_executor import TradingExecutor
    executor = TradingExecutor()
    executor.runtime_guard = lambda _: False
    assert not executor._is_strategy_running(20, threading.Event())
    assert executor._last_exit_reason[20] == 'strategyRuntime.leaseLost'


def test_grid_lease_loss_blocks_placement_but_allows_cancel(monkeypatch):
    from app.services.grid.engine import GridEngine
    from app.services.grid.levels import GridCellSpec
    engine = GridEngine(42, 'BTC/USDT', {'market_type': 'spot'}, {},
                        create_client_fn=lambda: object(), enqueue_market=Mock())
    engine.order_guard = lambda: False
    engine._normalize_grid_base_qty = lambda qty, _: qty
    engine._grid_entry_ownership_allowed = lambda *a, **k: (True, {})
    place = Mock()
    monkeypatch.setattr('app.services.grid.engine.place_grid_limit_order', place)
    assert not engine._place_limit(GridCellSpec(index=1, lower_price=99, upper_price=101),
                                   'long_entry', 'buy', 99, reduce_only=False, pos_side='long', quantity=0.1)
    place.assert_not_called()
    engine._orders = SimpleNamespace(list_open=lambda _: [{'id': 1}])
    engine._cancel_confirmed_order = Mock()
    engine.cancel_all_orders_on_exchange()
    engine._cancel_confirmed_order.assert_called_once()


def test_grid_initial_market_stops_if_lease_expires_during_probe(monkeypatch):
    from app.services.grid.engine import GridEngine
    engine = GridEngine(42, 'BTC/USDT', {'market_type': 'spot'}, {},
                        create_client_fn=lambda: object(), enqueue_market=Mock())
    engine.order_guard = lambda: False
    engine._try_recover_initial_from_exchange = lambda *a: False
    engine._initial_exchange_delta = lambda _: 0
    engine._probe_initial_client_order_fill = lambda *a: False
    place = Mock()
    monkeypatch.setattr('app.services.grid.engine.execute_grid_market_order', place)
    assert not engine._sync_initial_market_leg('open_long', 100, 100, 'initial')
    place.assert_not_called()


def test_empty_grid_cleanup_does_not_require_exchange_binding():
    from app.services.grid.engine import GridEngine
    client = Mock(side_effect=AssertionError('No orders need cancellation'))
    engine = GridEngine(42, 'BTC/USDT', {}, {}, create_client_fn=client, enqueue_market=Mock())
    engine._orders = SimpleNamespace(list_open=lambda _: [])
    engine.cancel_all_orders_on_exchange()
    client.assert_not_called()


def test_grid_stop_recovers_legacy_exchange_binding(monkeypatch):
    from app.services.grid.runner import shutdown_grid_for_strategy
    monkeypatch.setattr('app.services.grid.runner.get_runner', lambda _: None)
    monkeypatch.setattr('app.services.exchange_execution.load_strategy_configs', lambda _: {
        'symbol': 'BTC/USDT', 'user_id': 8, 'market_type': 'spot', 'exchange_config': {},
        'trading_config': {'bot_type': 'grid', 'exchange_id': 'gate', 'credential_id': 7}})
    monkeypatch.setattr('app.services.strategy_runtime.bot_type.resolve_bot_type', lambda *a: 'grid')
    resolve = Mock(side_effect=lambda config, **k: config)
    monkeypatch.setattr('app.services.exchange_execution.resolve_exchange_config', resolve)
    engine = Mock()
    monkeypatch.setattr('app.services.grid.runner.GridEngine', engine)
    monkeypatch.setattr('app.services.grid.runner.append_strategy_log', Mock())
    shutdown_grid_for_strategy(527)
    config = resolve.call_args.args[0]
    assert config['exchange_id'] == 'gate'
    assert str(config['credential_id']) == '7'
    engine.return_value.shutdown.assert_called_once()
