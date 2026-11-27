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
        self.cancel_orders = []

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

    def stop_strategy(self, strategy_id, persist_status=False, preserve_run=False, cancel_orders=True):
        del persist_status
        self.cancel_orders.append(cancel_orders)
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
        self.deferred = []
        self.ready = True

    def runtime_request(self, _strategy_id):
        return {"status": "running", "desired_runtime_epoch": 1, "ready": self.ready}

    def defer_start(self, command_id, **kwargs):
        self.deferred.append((command_id, kwargs))

    def begin_restart(self, _command):
        return 2

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


def test_starting_command_is_deferred_until_real_readiness(monkeypatch):
    repository = FakeRepository()
    worker = TradingWorker(FakeExecutor(), repository)
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting", "runtime_epoch": 6})
    worker._execute(_command("start"))
    assert not repository.completed and not repository.failed
    assert repository.deferred[0][1]["result"]["status"] == "starting"
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "running", "runtime_epoch": 6})
    worker._execute(_command("start"))
    assert repository.completed[0][1]["status"] == "running"


def test_cooldown_survives_start_timeout_and_exhausted_attempts_then_recovers(monkeypatch):
    from dataclasses import replace
    from app.utils.binance_request_guard import BinanceRateLimitError

    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(worker, "_start", lambda sid: (_ for _ in ()).throw(BinanceRateLimitError(1084)))
    command = replace(_command("start"), attempts=worker.max_attempts, elapsed_seconds=10000)
    worker._execute(command)
    assert not worker.repository.failed and not worker.repository.completed
    result = worker.repository.deferred[-1][1]["result"]
    assert result["retry_after_seconds"] == 1084
    assert not worker.executor.stopped
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})
    worker._execute(replace(command, result=result))
    assert not worker.repository.failed
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "running"})
    worker._execute(replace(command, result=result))
    assert worker.repository.completed[-1][1]["status"] == "running"


def test_start_wait_reads_durable_cooldown_but_respects_manual_stop(monkeypatch):
    from app.services.strategy import StrategyService

    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(StrategyService, "get_strategy", lambda *a: {"status": "running", "desired_runtime_epoch": 1})
    monkeypatch.setattr(worker.repository, "runtime_request", lambda sid: {
        "status": "running", "desired_runtime_epoch": 1, "retry_after_seconds": 1084, "startup_retry_at": 2000,
    })
    assert worker._start(55)["retry_after_seconds"] == 1084
    assert not worker.executor.running_strategies
    monkeypatch.setattr(StrategyService, "get_strategy", lambda *a: {"status": "stopped"})
    assert worker._start(55)["status"] == "skipped"
    assert not worker.executor.running_strategies


def test_expired_cooldown_does_not_disable_real_startup_timeout(monkeypatch):
    from dataclasses import replace

    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting", "startup_retry_at": 1})
    worker._execute(replace(_command("start"), elapsed_seconds=10000))
    assert worker.repository.failed[0][1] == "strategyRuntime.startFailed"


def test_restore_rate_limits_do_not_exhaust_attempts(monkeypatch):
    from unittest.mock import Mock
    from app.services.strategy import StrategyService
    from app.services import strategy_lifecycle
    from app.utils.binance_request_guard import BinanceRateLimitError

    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(StrategyService, "get_running_strategies_with_type", lambda *a: [{"id": 55}])
    monkeypatch.setattr(worker, "_start", Mock(side_effect=BinanceRateLimitError(1084)))
    stopped = Mock()
    monkeypatch.setattr(strategy_lifecycle, "maybe_auto_stop_on_exchange_error", stopped)
    for _ in range(10):
        worker.restore_desired_strategies()
    stopped.assert_not_called()
    assert worker._restore_failure_counts == {}


def test_readiness_deadline_is_a_failure_not_infinite_polling(monkeypatch):
    from dataclasses import replace

    repository = FakeRepository()
    worker = TradingWorker(FakeExecutor(), repository)
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})
    worker._execute(replace(_command("start"), elapsed_seconds=10000))
    assert repository.deferred == []
    assert repository.failed[0][1] == "strategyRuntime.startFailed"


def test_large_pool_startup_has_its_own_budget(monkeypatch):
    from dataclasses import replace

    monkeypatch.delenv("STRATEGY_RUNTIME_STARTUP_TIMEOUT_SECONDS", raising=False)
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})
    worker._execute(replace(_command("start"), elapsed_seconds=300))
    assert worker.repository.deferred and not worker.repository.failed


def test_deferred_command_stops_heartbeat_before_database_transition(monkeypatch):
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})

    def defer(command_id, **kwargs):
        assert not worker._lease_heartbeat.command_valid(command_id)

    monkeypatch.setattr(worker.repository, "defer_start", defer)
    worker._execute(_command("start"))
    assert not worker._stop.is_set()


@pytest.mark.parametrize("stop_result", [True, False])
def test_terminal_start_failure_fences_and_cleans_local_runtime(monkeypatch, stop_result):
    from dataclasses import replace

    executor = FakeExecutor(stop_result=stop_result)
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, FakeRepository())
    worker._lease_heartbeat.watch_strategy(55)
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})
    monkeypatch.setattr(executor, "wait_strategy_running", lambda *a, **kw: (False, "starting"))
    marked = []
    monkeypatch.setattr(TradingExecutor, "_mark_stopped", lambda sid, **kw: marked.append(sid))
    worker._execute(replace(_command("start"), attempts=3, elapsed_seconds=10000))
    assert marked == [55]
    assert executor.stopped == [55] and executor.handoffs == []
    assert executor.cancel_orders == [False]
    assert not worker._lease_heartbeat.strategy_valid(55)
    assert worker.repository.released == ([(55, worker.worker_id)] if stop_result else [])
    assert not worker.repository.revoked


def test_failed_start_cleanup_does_not_touch_newer_generation():
    executor = FakeExecutor()
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, FakeRepository())
    worker._lease_heartbeat.watch_strategy(55)
    worker._cleanup_failed_start(55, expected_epoch=2)
    assert not executor.stopped
    assert worker._lease_heartbeat.strategy_valid(55)


def test_runtime_ready_at_deadline_is_not_marked_failed(monkeypatch):
    from dataclasses import replace

    executor = FakeExecutor()
    executor.running_strategies[55] = object()
    worker = TradingWorker(executor, FakeRepository())
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting"})
    worker._execute(replace(_command("start"), attempts=3, elapsed_seconds=10000))
    assert worker.repository.completed[0][1]["status"] == "running"
    assert not worker.repository.failed and not executor.stopped


def test_failed_start_releases_lease_after_runtime_already_exited():
    worker = TradingWorker(FakeExecutor(), FakeRepository())
    worker._lease_heartbeat.watch_strategy(55)
    worker._cleanup_failed_start(55, expected_epoch=1)
    assert not worker._lease_heartbeat.strategy_valid(55)
    assert worker.repository.released == [(55, worker.worker_id)]


def test_local_failure_cleanup_does_not_call_broker_shutdown(monkeypatch):
    from types import SimpleNamespace

    executor = object.__new__(TradingExecutor)
    executor.lock = threading.Lock()
    executor.stop_join_timeout = 0.1
    executor._runtime_handoff_ids = set()
    stop_event = threading.Event()
    executor._runtime_stop_events = {55: stop_event}
    joined = []
    executor.running_strategies = {55: SimpleNamespace(
        cancel=lambda: None, join=lambda **kw: joined.append(True), is_alive=lambda: False,
    )}
    monkeypatch.setattr("app.services.trading_executor.append_strategy_log", lambda *a: None)
    monkeypatch.setattr("app.services.grid.runner.shutdown_grid_for_strategy", lambda *a: pytest.fail("broker shutdown"))
    assert executor.stop_strategy(55, persist_status=False, cancel_orders=False)
    assert stop_event.is_set() and joined
    assert not executor.running_strategies and not executor._runtime_handoff_ids


def test_delayed_stop_command_cannot_stop_a_later_start(monkeypatch):
    from dataclasses import replace

    repository = FakeRepository()
    executor = FakeExecutor()
    worker = TradingWorker(executor, repository)
    monkeypatch.setattr(repository, "runtime_request", lambda sid: {"desired_runtime_epoch": 6})
    worker._execute(replace(_command("stop"), payload={"requested_runtime_epoch": 1}))
    assert executor.stopped == [] and repository.revoked == []
    assert repository.completed[0][1]["status"] == "skipped"


def test_resumed_restart_does_not_stop_the_new_runtime_again(monkeypatch):
    from dataclasses import replace

    repository = FakeRepository()
    worker = TradingWorker(FakeExecutor(), repository)
    monkeypatch.setattr(repository, "runtime_request", lambda sid: {"desired_runtime_epoch": 2})
    monkeypatch.setattr(worker, "_start", lambda sid: {"status": "starting", "runtime_epoch": 2})
    worker._execute(replace(_command("restart"), payload={"runtime_epoch": 2}))
    assert worker.executor.stopped == []
    assert repository.deferred and not repository.completed


def test_live_but_unready_executor_does_not_report_startup_success():
    from types import SimpleNamespace

    executor = object.__new__(TradingExecutor)
    executor.lock = threading.Lock()
    executor.running_strategies = {55: SimpleNamespace(is_alive=lambda: True, is_ready=lambda: False)}
    assert executor.wait_strategy_running(55, timeout=0) == (False, "strategyRuntime.startFailed")


def test_restart_advances_generation_and_stops_runtime_without_changing_desired_state(
    monkeypatch,
):
    repository = FakeRepository()
    worker = TradingWorker(FakeExecutor(), repository)
    calls = []
    monkeypatch.setattr(
        repository,
        "begin_restart",
        lambda command: calls.append(("request", command.strategy_id, command.user_id)) or 2,
    )
    monkeypatch.setattr(
        worker,
        "_stop_strategy",
        lambda strategy_id, **kwargs: calls.append(("stop", strategy_id, kwargs))
        or {"status": "stopped"},
    )
    monkeypatch.setattr(
        worker,
        "_start",
        lambda strategy_id: calls.append(("start", strategy_id))
        or {"status": "running"},
    )

    worker._execute(_command("restart"))

    assert calls == [
        ("request", 55, 1),
        ("stop", 55, {"persist_status": False}),
        ("start", 55),
    ]
    assert repository.completed[0][1]["status"] == "running"


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


def test_scheduled_strategy_is_not_registered_for_bar_only_distributed_execution(
    monkeypatch,
):
    from app.services.strategy import StrategyService
    from app.services.strategy_event_subscriptions import (
        StrategyEventSubscriptionPlanner,
    )
    from app.services.strategy_v2.service import StrategyV2BacktestService
    from app.services.trading_executor import TradingExecutor

    source = '''
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="1m")
    run_daily(rebalance, time="09:35")

def rebalance(context, data):
    pass
'''
    monkeypatch.setattr(
        StrategyService,
        "get_strategy",
        lambda _self, strategy_id: {
            "id": strategy_id,
            "status": "running",
            "user_id": 7,
            "execution_mode": "signal",
        },
    )
    monkeypatch.setattr(
        TradingExecutor,
        "_load_source",
        staticmethod(lambda _strategy: (1, source)),
    )
    monkeypatch.setattr(
        StrategyV2BacktestService,
        "resolve_candidates",
        lambda _self, **_kwargs: ([{
            "key": "Crypto:BTC/USDT@spot",
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "exchange_id": "binance",
        }], None),
    )

    assert StrategyEventSubscriptionPlanner.plan(55) == set()


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
