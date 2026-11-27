from __future__ import annotations

import threading

import pytest

from app.services import distributed_runtime_host as host_module
from app.services.distributed_runtime_host import DistributedRuntimeHost


class FakeHeartbeat:
    def __init__(self, *_args, **_kwargs):
        self.watched = set()

    def start(self):
        return None

    def watch_strategy(self, strategy_id):
        self.watched.add(int(strategy_id))

    def forget_strategy(self, strategy_id):
        self.watched.discard(int(strategy_id))

    def strategy_valid(self, strategy_id):
        return int(strategy_id) in self.watched

    def close(self):
        return None


class FakeRepository:
    def __init__(self):
        self.released = []
        self.request = {"status": "running", "desired_runtime_epoch": 1, "ready": True}

    def runtime_request(self, _strategy_id):
        return dict(self.request)

    def runtime_requests(self, strategy_ids):
        return {sid: dict(self.request) for sid in strategy_ids}

    def acquire_strategy_lease(self, **_kwargs):
        return 9

    def release_strategy_lease(self, *, strategy_id, owner_id):
        self.released.append((int(strategy_id), owner_id))


class RejectingExecutor:
    def __init__(self):
        self.lock = threading.Lock()
        self.runtime_guard = None
        self.running_strategies = {}
        self._last_start_failure = "Bitget position mode does not match the strategy."
        self.cleared = []

    def _discard_dead_runtimes(self):
        return None

    def is_running(self, _strategy_id):
        return False

    def set_runtime_fencing_token(self, _strategy_id, _token):
        return None

    def clear_runtime_fencing_token(self, strategy_id):
        self.cleared.append(int(strategy_id))

    def start_strategy(self, _strategy_id):
        return False


def test_distributed_start_failure_is_logged_once_with_exact_detail(monkeypatch):
    logs = []
    repository = FakeRepository()
    monkeypatch.setattr(host_module, "LeaseHeartbeat", FakeHeartbeat)
    monkeypatch.setattr(
        host_module,
        "append_strategy_log",
        lambda strategy_id, level, message: logs.append((strategy_id, level, message)),
    )
    host = DistributedRuntimeHost(
        owner_id="evaluator-a",
        executor=RejectingExecutor(),
        repository=repository,
    )

    assert host.evaluate(55, object()) is False
    assert host.evaluate(55, object()) is False

    assert logs == [
        (55, "error", "Bitget position mode does not match the strategy."),
    ]
    assert repository.released == [(55, "evaluator-a"), (55, "evaluator-a")]


@pytest.mark.parametrize("outer_timeout,expected", [(60, 150), (90, 150), (240, 240)])
def test_distributed_waits_allow_the_full_sandbox_budget(monkeypatch, outer_timeout, expected):
    class ReadyExecutor(RejectingExecutor):
        def __init__(self):
            super().__init__()
            self.active = False
            self.start_waits = []
            self.bar_waits = []

        def is_running(self, _strategy_id):
            return self.active

        def start_strategy(self, _strategy_id):
            self.active = True
            return True

        def wait_strategy_running(self, _strategy_id, *, timeout):
            self.start_waits.append(timeout)
            return True, ""

        def trigger_bar_evaluation(self, _strategy_id, _event, *, timeout):
            self.bar_waits.append(timeout)
            return True

    monkeypatch.setenv("STRATEGY_RUNTIME_SANDBOX_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("STRATEGY_SCRIPT_CALLBACK_TIMEOUT_SEC", "5")
    monkeypatch.setattr(host_module, "LeaseHeartbeat", FakeHeartbeat)
    executor = ReadyExecutor()
    host = DistributedRuntimeHost(
        owner_id="evaluator-a", executor=executor, repository=FakeRepository(),
    )
    assert host.evaluate(55, object(), timeout=outer_timeout) is True
    assert host.evaluate(55, object(), timeout=outer_timeout) is True
    assert executor.start_waits == [150]
    assert executor.bar_waits == [expected, expected]


class RunningExecutor(RejectingExecutor):
    def __init__(self):
        super().__init__()
        self.starts = []
        self.stops = []
        self.bars = []
        self.stop_result = True
        self.ready = True
        self.handoffs = []

    def request_runtime_handoff(self, strategy_id):
        self.handoffs.append(strategy_id)

    def is_running(self, strategy_id):
        return strategy_id in self.running_strategies

    def start_strategy(self, strategy_id):
        self.starts.append(strategy_id)
        self.running_strategies[strategy_id] = object()
        return True

    def stop_strategy(self, strategy_id, **kwargs):
        self.stops.append((strategy_id, kwargs))
        if self.stop_result:
            self.running_strategies.pop(strategy_id, None)
        return self.stop_result

    def wait_strategy_running(self, _strategy_id, **_kwargs):
        return self.ready, "not ready"

    def trigger_bar_evaluation(self, strategy_id, event, **_kwargs):
        self.bars.append((strategy_id, event))
        return True


def _running_host(monkeypatch):
    monkeypatch.setattr(host_module, "LeaseHeartbeat", FakeHeartbeat)
    monkeypatch.setattr(host_module, "append_strategy_log", lambda *args: None)
    repository, executor = FakeRepository(), RunningExecutor()
    host = DistributedRuntimeHost(owner_id="evaluator-a", executor=executor, repository=repository)
    return host, repository, executor


def test_daily_runtime_prepares_without_a_bar_or_order_callback(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    repository.request["desired_runtime_epoch"] = 6
    assert host.prepare(1324)
    assert host.prepare(1324)
    assert executor.starts == [1324]
    assert executor.bars == []
    assert host._owned_epochs == {1324: 6}


def test_distributed_cooldown_waits_then_starts_without_submitting_orders(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    repository.request["retry_after_seconds"] = 1084
    assert not host.prepare(1324)
    assert not executor.starts and not executor.bars
    repository.request["retry_after_seconds"] = 0
    assert host.prepare(1324)
    assert executor.starts == [1324]
    assert not executor.bars


def test_manual_stop_during_distributed_cooldown_does_not_restart(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    repository.request.update(status="stopped", retry_after_seconds=0)
    assert not host.prepare(1324)
    assert not executor.starts


def test_new_epoch_replaces_old_runtime_before_evaluation(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    assert host.prepare(1324)
    repository.request["desired_runtime_epoch"] = 6
    assert host.prepare(1324)
    assert executor.starts == [1324, 1324]
    assert executor.stops == [(1324, {"persist_status": False})]
    assert host.evaluate(1324, "closed daily bar")
    assert executor.bars == [(1324, "closed daily bar")]


def test_stop_timeout_never_starts_a_second_generation_or_releases_lease(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    assert host.prepare(1324)
    executor.stop_result = False
    repository.request["desired_runtime_epoch"] = 6
    assert not host.prepare(1324)
    assert executor.starts == [1324]
    assert repository.released == []
    assert host.release_strategies({1324}) == 0
    assert repository.released == []


def test_prepare_requires_readiness_ack_and_ignores_stopped_request(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    repository.request["ready"] = False
    assert not host.prepare(1324)
    assert executor.bars == []
    repository.request["status"] = "stopped"
    assert not host.prepare(1324)
    assert host.evaluate(1324, "late event")
    assert executor.bars == []


def test_reconcile_retires_old_epoch_without_waiting_for_daily_bar(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    assert host.prepare(1324)
    repository.request["desired_runtime_epoch"] = 6
    host.reconcile({1324})
    assert executor.running_strategies == {}
    assert repository.released == [(1324, "evaluator-a")]


def test_lease_loss_cancels_without_blocking_other_strategy_heartbeats(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    assert host.prepare(1324)
    host._lease_heartbeat.forget_strategy(1324)
    host._lease_lost("strategy", 1324)
    assert executor.handoffs == [1324]
    assert executor.stops == []
    assert not host._runtime_valid(1324)
    assert host.prepare(1324)
    assert repository.released == [(1324, "evaluator-a")]
    assert len(executor.starts) == 2
    host._lease_lost("strategy", 1324)
    assert executor.handoffs == [1324]


def test_reconcile_stops_inactive_runtime_only_once(monkeypatch):
    host, repository, executor = _running_host(monkeypatch)
    assert host.prepare(1324)
    repository.request["status"] = "stopped"
    host.reconcile(set())
    assert executor.stops == [(1324, {"persist_status": False})]
