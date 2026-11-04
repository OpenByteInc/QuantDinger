"""Trading worker ownership and command boundary tests."""

from __future__ import annotations

import threading

import pytest

from app.events.bar_clock import BarStreamKey
from app.services.strategy_command_repository import StrategyCommand
from app.services.trading_executor import TradingExecutor
from app.workers.trading import TradingWorker


@pytest.fixture(autouse=True)
def local_runtime_by_default(monkeypatch):
    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "false")


class FakeExecutor:
    def __init__(self, *, stop_result: bool = True) -> None:
        self.running_strategies = {}
        self.lock = __import__("threading").Lock()
        self.stopped = []
        self.registered_streams = []
        self.unregistered_streams = []
        self.policy_stops = []
        self.stop_result = stop_result
        self.handoffs = []

    def start_strategy(self, strategy_id):
        self.running_strategies[int(strategy_id)] = object()
        return True

    def wait_strategy_running(self, strategy_id, timeout=0):
        del timeout
        return int(strategy_id) in self.running_strategies, ""

    def set_runtime_fencing_token(self, strategy_id, fencing_token):
        del strategy_id, fencing_token

    def clear_runtime_fencing_token(self, strategy_id):
        del strategy_id

    def register_market_streams(self, strategy_id, streams):
        self.registered_streams.append((int(strategy_id), set(streams)))

    def unregister_market_streams(self, strategy_id):
        self.unregistered_streams.append(int(strategy_id))

    def stop_strategy(self, strategy_id, persist_status=False, preserve_run=False):
        del persist_status
        self.stopped.append(int(strategy_id))
        if preserve_run:
            self.handoffs.append(int(strategy_id))
        if self.stop_result:
            self.running_strategies.pop(int(strategy_id), None)
        return self.stop_result

    def stop_strategy_with_policy(self, strategy_id, *, close_positions):
        self.policy_stops.append((int(strategy_id), bool(close_positions)))
        stopped = self.stop_strategy(strategy_id)
        return {
            "strategy_id": strategy_id,
            "success": stopped,
            "status": "stopped" if stopped else "running",
            "close_requested": bool(close_positions),
            "close_positions_found": 0,
            "message": "runtime stop timeout" if not stopped else "",
        }


class FakeRepository:
    def __init__(self) -> None:
        self.completed = []
        self.failed = []
        self.released = []
        self.revoked = []
        self.active_leases = set()

    def complete(self, command_id, result=None):
        self.completed.append((command_id, result))

    def fail(self, command_id, error, retry_delay_seconds=None):
        self.failed.append((command_id, error, retry_delay_seconds))

    def release_strategy_lease(self, *, strategy_id, owner_id):
        self.released.append((strategy_id, owner_id))

    def revoke_strategy_lease(self, *, strategy_id):
        self.revoked.append(int(strategy_id))
        return True

    @staticmethod
    def has_pending_stop(_strategy_id):
        return False

    def has_active_strategy_lease(self, strategy_id):
        return int(strategy_id) in self.active_leases


def _command(command_type: str) -> StrategyCommand:
    return StrategyCommand(
        id=1,
        strategy_id=55,
        user_id=1,
        command_type=command_type,
        status="processing",
        idempotency_key="test-command",
        payload={},
        attempts=1,
    )


def test_stop_command_is_executed_by_trading_worker():
    repository = FakeRepository()
    executor = FakeExecutor()
    worker = TradingWorker(executor, repository)

    worker._execute(_command("stop"))

    assert executor.stopped == [55]
    assert repository.released == [(55, worker.worker_id)]
    assert repository.revoked == [55]
    assert repository.completed[0][1]["status"] == "stopped"


def test_failed_command_is_retried_with_backoff(monkeypatch):
    repository = FakeRepository()
    worker = TradingWorker(FakeExecutor(), repository)
    monkeypatch.setattr(worker, "_start", lambda _strategy_id: (_ for _ in ()).throw(RuntimeError("boom")))

    worker._execute(_command("start"))

    assert repository.failed == [(1, "boom", 1)]


@pytest.mark.parametrize("close_positions", [False, True])
def test_stop_timeout_retains_runtime_lease(monkeypatch, close_positions):
    repository = FakeRepository()
    executor = FakeExecutor(stop_result=False)
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, repository)
    monkeypatch.setattr("app.workers.trading.append_strategy_log", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="stop"):
        worker._stop_strategy(55, close_positions=close_positions)

    assert repository.released == []
    assert repository.revoked == []
    assert 55 in executor.running_strategies


def test_worker_shutdown_retains_lease_when_runtime_does_not_stop(monkeypatch):
    repository = FakeRepository()
    executor = FakeExecutor(stop_result=False)
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, repository)
    monkeypatch.setattr("app.workers.trading.append_strategy_log", lambda *_args, **_kwargs: None)

    worker._shutdown_local_runtimes()

    assert repository.released == []
    assert 55 in executor.running_strategies


def test_worker_shutdown_releases_watched_lease_after_runtime_already_exited():
    repository = FakeRepository()
    executor = FakeExecutor()
    worker = TradingWorker(executor, repository)
    worker._lease_heartbeat.watch_strategy(55)

    worker._shutdown_local_runtimes()

    assert repository.released == [(55, worker.worker_id)]
    assert worker._lease_heartbeat.strategy_ids() == []


def test_distributed_bar_start_registers_routes_without_local_runtime(monkeypatch):
    from app.services.strategy import StrategyService
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionPlanner,
    )

    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {"id": strategy_id, "status": "running"},
    )
    stream = object()
    monkeypatch.setattr(
        StrategyEventSubscriptionPlanner,
        "register",
        lambda _self, _strategy_id: {stream},
    )
    executor = FakeExecutor()
    worker = TradingWorker(executor, FakeRepository())
    worker._global_services_leader = True

    result = worker._start(55)

    assert result["trigger"] == "distributed_bar"
    assert result["runtime_owner"] == "strategy-evaluator-group"
    assert executor.registered_streams == [(55, {stream})]
    assert executor.running_strategies == {}


def test_distributed_stop_and_close_runs_full_stop_policy(monkeypatch):
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionRepository,
    )

    removed = []
    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
    monkeypatch.setattr(
        StrategyEventSubscriptionRepository,
        "remove_strategy",
        lambda _self, strategy_id: removed.append(int(strategy_id)),
    )
    repository = FakeRepository()
    executor = FakeExecutor()
    worker = TradingWorker(executor, repository)
    worker._distributed_strategy_ids.add(55)

    result = worker._stop_strategy(55, close_positions=True)

    assert result["status"] == "stopped"
    assert result["close_positions_found"] == 0
    assert removed == [55]
    assert executor.unregistered_streams == [55]
    assert executor.policy_stops == [(55, True)]
    assert repository.released == [(55, worker.worker_id)]
    assert repository.revoked == [55]
    assert 55 not in worker._distributed_strategy_ids


def test_distributed_restore_does_not_reregister_an_active_strategy(monkeypatch):
    from app.services.strategy import StrategyService
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionPlanner,
    )

    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
    monkeypatch.setattr(
        StrategyService,
        "get_running_strategies_with_type",
        lambda _self: [{"id": 55}],
    )
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {"id": strategy_id, "status": "running"},
    )
    stream = object()
    planner_calls = []

    def register(_self, strategy_id):
        planner_calls.append(int(strategy_id))
        return {stream}

    monkeypatch.setattr(StrategyEventSubscriptionPlanner, "register", register)
    executor = FakeExecutor()
    worker = TradingWorker(executor, FakeRepository())
    worker._global_services_leader = True

    worker.restore_desired_strategies()
    worker.restore_desired_strategies()

    assert planner_calls == [55]
    assert executor.registered_streams == [(55, {stream})]


def test_running_strategy_restore_cannot_be_disabled_by_legacy_env(monkeypatch):
    monkeypatch.setenv("DISABLE_RESTORE_RUNNING_STRATEGIES", "true")
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    calls = []
    monkeypatch.setattr(
        worker,
        "restore_desired_strategies",
        lambda: calls.append(True),
    )

    worker._restore_if_due(force=True)

    assert calls == [True]


def test_nonleader_persists_distributed_route_without_duplicate_market_stream(monkeypatch):
    from app.services.strategy import StrategyService
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionPlanner,
    )

    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {"id": strategy_id, "status": "running"},
    )
    stream = object()
    monkeypatch.setattr(
        StrategyEventSubscriptionPlanner,
        "register",
        lambda _self, _strategy_id: {stream},
    )
    executor = FakeExecutor()
    worker = TradingWorker(executor, FakeRepository())

    result = worker._start(55)

    assert result["trigger"] == "distributed_bar"
    assert executor.registered_streams == []


def test_start_succeeds_when_another_pool_worker_already_owns_runtime(monkeypatch):
    from app.services.strategy import StrategyService

    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {"id": strategy_id, "status": "running"},
    )
    repository = FakeRepository()
    repository.active_leases.add(55)
    worker = TradingWorker(FakeExecutor(), repository)
    monkeypatch.setattr(worker, "_acquire_runtime", lambda _strategy_id: False)

    result = worker._start(55)

    assert result == {
        "strategy_id": 55,
        "runtime_owner": "trading-worker-pool",
        "status": "running",
    }


def test_grid_runtime_can_be_owned_by_nonleader_pool_worker(monkeypatch):
    from app.services.strategy import StrategyService

    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "false")
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {
            "id": strategy_id,
            "status": "running",
            "trading_config": {"executor_type": "grid"},
        },
    )
    monkeypatch.setattr(
        "app.services.trading_executor.TradingExecutor._load_source",
        lambda _strategy: (0, ""),
    )
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(worker, "_acquire_runtime", lambda _strategy_id: True)

    result = worker._start(55, wait_ready=False)

    assert result == {
        "strategy_id": 55,
        "runtime_owner": worker.worker_id,
        "status": "running",
    }


def test_new_global_leader_rebuilds_distributed_stream_with_active_runtime_lease(
    monkeypatch,
):
    from app.events.bar_clock import BarStreamKey
    from app.services.strategy import StrategyService
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionPlanner,
    )

    stream = BarStreamKey("binance", "Crypto", "swap", "BTCUSDT", "BTC/USDT", "1m")
    monkeypatch.setenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
    monkeypatch.setattr(
        StrategyService,
        "get_running_strategies_with_type",
        lambda _self: [{"id": 55}],
    )
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {"id": strategy_id, "status": "running"},
    )
    monkeypatch.setattr(
        StrategyEventSubscriptionPlanner,
        "register",
        lambda _self, _strategy_id: {stream},
    )
    repository = FakeRepository()
    repository.active_leases.add(55)
    executor = FakeExecutor()
    worker = TradingWorker(executor, repository)
    worker._global_services_leader = True

    worker.restore_desired_strategies()

    assert executor.registered_streams == [(55, {stream})]
    assert worker._distributed_strategy_ids == {55}


def test_worker_shutdown_handoffs_runtime_without_business_stop():
    repository = FakeRepository()
    executor = FakeExecutor()
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, repository)

    worker._shutdown_local_runtimes()

    assert executor.handoffs == [55]
    assert repository.released == [(55, worker.worker_id)]


def test_worker_stop_does_not_end_runtime_before_handoff():
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    worker._lease_heartbeat.watch_strategy(55)

    worker.stop()

    assert worker.executor.runtime_guard(55) is True


def test_command_polling_survives_temporary_database_failure():
    repository = FakeRepository()
    repository.claim_next = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("database unavailable")
    )
    worker = TradingWorker(FakeExecutor(), repository)

    assert worker._claim_next_command() is None


def test_market_stream_registration_is_idempotent_and_diff_based():
    class Subscription:
        def __init__(self, stream):
            self.stream = stream
            self.close_count = 0

        def close(self):
            self.close_count += 1

    class Clock:
        def __init__(self):
            self.subscriptions = []

        def subscribe(self, stream, _handler):
            subscription = Subscription(stream)
            self.subscriptions.append(subscription)
            return subscription

    first = BarStreamKey("binance", "Crypto", "swap", "BTCUSDT", "BTC/USDT", "1m")
    second = BarStreamKey("bybit", "Crypto", "swap", "BTCUSDT", "BTC/USDT", "1m")
    executor = object.__new__(TradingExecutor)
    executor.lock = threading.Lock()
    executor._bar_close_clock = Clock()
    executor._routing_streams_by_strategy = {}
    executor._routing_stream_subscriptions = {}
    executor._routing_stream_ref_counts = {}

    executor.register_market_streams(55, {first})
    original = executor._bar_close_clock.subscriptions[0]
    executor.register_market_streams(55, {first})

    assert len(executor._bar_close_clock.subscriptions) == 1
    assert original.close_count == 0

    executor.register_market_streams(55, {second})

    assert len(executor._bar_close_clock.subscriptions) == 2
    assert original.close_count == 1
    assert executor._routing_streams_by_strategy[55] == {second}


def test_external_evaluator_does_not_own_durable_event_subscriptions(monkeypatch):
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionRepository,
    )

    replace_calls = []
    monkeypatch.setattr(
        StrategyEventSubscriptionRepository,
        "replace_bar_streams",
        lambda _self, strategy_id, streams: replace_calls.append(
            (int(strategy_id), set(streams))
        ),
    )
    stream = BarStreamKey("binance", "Crypto", "swap", "BTCUSDT", "BTC/USDT", "1m")
    executor = object.__new__(TradingExecutor)
    executor.external_bar_triggers = True

    repository = executor._register_event_subscriptions(55, {stream})

    assert repository is None
    assert replace_calls == []
