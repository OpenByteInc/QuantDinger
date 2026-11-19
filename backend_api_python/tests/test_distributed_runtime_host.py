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
