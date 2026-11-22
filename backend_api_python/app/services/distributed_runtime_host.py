"""Own externally triggered strategy runtimes inside one evaluator process."""

from __future__ import annotations

import os
import threading

from app.services.strategy_command_repository import StrategyCommandRepository
from app.services.strategy_v2.sandbox import live_sandbox_timeout_seconds
from app.services.trading_executor import TradingExecutor
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.workers.lease_heartbeat import LeaseHeartbeat


logger = get_logger(__name__)


class DistributedRuntimeHost:
    def __init__(
        self,
        *,
        owner_id: str,
        executor: TradingExecutor | None = None,
        repository: StrategyCommandRepository | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self.owner_id = str(owner_id)
        self.executor = executor or TradingExecutor(external_bar_triggers=True)
        self.repository = repository or StrategyCommandRepository()
        self.lease_seconds = max(
            10,
            int(lease_seconds or os.getenv("STRATEGY_RUNTIME_LEASE_SEC", "30")),
        )
        self._lock = threading.RLock()
        self._lost_strategies: set[int] = set()
        self._owned_strategies: set[int] = set()
        self._owned_epochs: dict[int, int] = {}
        self._start_failures: dict[int, str] = {}
        self._lease_heartbeat = LeaseHeartbeat(
            self.owner_id,
            self.lease_seconds,
            self._lease_lost,
        )
        self.executor.runtime_guard = self._runtime_valid
        self._lease_heartbeat.start()

    def evaluate(self, strategy_id: int, event, *, timeout: float = 60.0) -> bool:
        if not self.prepare(strategy_id):
            return self.repository.runtime_request(strategy_id).get("status") != "running"
        return self.executor.trigger_bar_evaluation(
            int(strategy_id), event,
            timeout=max(live_sandbox_timeout_seconds() + 30.0, float(timeout)),
        )

    def prepare(self, strategy_id: int, _event=None, *, timeout: float = 60.0) -> bool:
        """Create the requested generation without submitting a bar callback."""
        strategy_id = int(strategy_id)
        request = self.repository.runtime_request(strategy_id)
        if request.get("status") != "running":
            return False
        epoch = int(request.get("desired_runtime_epoch") or 1)
        startup_timeout = live_sandbox_timeout_seconds() + 30.0
        if self.executor.is_running(strategy_id) and (
            self._owned_epochs.get(strategy_id) != epoch or not self._runtime_valid(strategy_id)
        ):
            if not self.executor.stop_strategy(strategy_id, persist_status=False):
                return False
            self._release_runtime(strategy_id)
        started = False
        with self._lock:
            self._release_dead_runtime(strategy_id)
            if not self.executor.is_running(strategy_id):
                token = self.repository.acquire_strategy_lease(
                    strategy_id=strategy_id,
                    owner_id=self.owner_id,
                    lease_seconds=self.lease_seconds,
                )
                if token is None:
                    return False
                self.executor.set_runtime_fencing_token(strategy_id, token)
                self._owned_strategies.add(strategy_id)
                self._owned_epochs[strategy_id] = epoch
                self._lease_heartbeat.watch_strategy(strategy_id)
                if not self.executor.start_strategy(strategy_id):
                    detail = str(
                        getattr(self.executor, "_last_start_failure", "")
                        or "strategyRuntime.startFailed"
                    )
                    self._record_start_failure(strategy_id, detail)
                    self._release_runtime(strategy_id)
                    return False
                started = True
        if started:
            ready, hint = self.executor.wait_strategy_running(
                strategy_id,
                timeout=startup_timeout,
            )
            if not ready:
                self._record_start_failure(
                    strategy_id,
                    str(hint or "strategyRuntime.startFailed"),
                )
                if self.executor.stop_strategy(strategy_id, persist_status=False):
                    self._release_runtime(strategy_id)
                return False
        with self._lock:
            self._start_failures.pop(strategy_id, None)
        if not self._runtime_valid(strategy_id):
            return False
        request = self.repository.runtime_request(strategy_id)
        return bool(
            request.get("status") == "running"
            and int(request.get("desired_runtime_epoch") or 1) == epoch
            and request.get("ready")
        )

    def _record_start_failure(self, strategy_id: int, detail: str) -> None:
        strategy_id = int(strategy_id)
        with self._lock:
            if self._start_failures.get(strategy_id) == detail:
                return
            self._start_failures[strategy_id] = detail
        append_strategy_log(strategy_id, "error", detail)

    def reconcile(self, running_strategy_ids: set[int] | None = None) -> None:
        local_ids = set(self.local_strategy_ids())
        requests = self.repository.runtime_requests(local_ids)
        desired = local_ids if running_strategy_ids is None else {
            int(strategy_id) for strategy_id in running_strategy_ids
        }
        for strategy_id in sorted(local_ids):
            request = requests.get(strategy_id, {})
            if (
                strategy_id not in desired
                or request.get("status") != "running"
                or int(request.get("desired_runtime_epoch") or 1) != self._owned_epochs.get(strategy_id)
            ):
                if self.executor.stop_strategy(strategy_id, persist_status=False):
                    self._release_runtime(strategy_id)
            self._release_dead_runtime(strategy_id)

    def local_strategy_ids(self) -> list[int]:
        lock = self.executor.lock
        with lock:
            self.executor._discard_dead_runtimes()
            return [int(strategy_id) for strategy_id in self.executor.running_strategies]

    def release_strategies(self, strategy_ids: set[int]) -> int:
        released = 0
        for strategy_id in sorted({int(value) for value in strategy_ids}):
            if self.executor.is_running(strategy_id):
                stopped = self.executor.stop_strategy(
                    strategy_id,
                    persist_status=False,
                    preserve_run=True,
                )
                if not stopped:
                    continue
            with self._lock:
                owned = strategy_id in self._owned_strategies
            if owned:
                self._release_runtime(strategy_id)
                released += 1
        return released

    def snapshot(self) -> dict[str, int]:
        return self.executor.runtime_capacity_snapshot()

    def close(self) -> None:
        local_ids = set(self.local_strategy_ids())
        with self._lock:
            owned_ids = set(self._owned_strategies)
        for strategy_id in sorted(local_ids | owned_ids):
            if strategy_id in local_ids:
                stopped = self.executor.stop_strategy(
                    strategy_id,
                    persist_status=False,
                    preserve_run=True,
                )
                if not stopped:
                    continue
            self._release_runtime(strategy_id)
        self._lease_heartbeat.close()
        self.executor.close()

    def _runtime_valid(self, strategy_id: int) -> bool:
        with self._lock:
            if int(strategy_id) in self._lost_strategies:
                return False
        return self._lease_heartbeat.strategy_valid(int(strategy_id))

    def _lease_lost(self, kind, strategy_id) -> None:
        if kind != "strategy" or strategy_id is None:
            return
        strategy_id = int(strategy_id)
        with self._lock:
            if self._lease_heartbeat.strategy_valid(strategy_id):
                return
            self._lost_strategies.add(strategy_id)
            self.executor.request_runtime_handoff(strategy_id)
        logger.error(
            "Distributed runtime lease lost: strategy=%s owner=%s",
            strategy_id,
            self.owner_id,
        )

    def _release_dead_runtime(self, strategy_id: int) -> None:
        if self.executor.is_running(strategy_id):
            return
        with self._lock:
            if int(strategy_id) not in self._owned_strategies:
                return
        self._release_runtime(strategy_id)

    def _release_runtime(self, strategy_id: int) -> None:
        strategy_id = int(strategy_id)
        with self._lock:
            owned = strategy_id in self._owned_strategies
            self._owned_strategies.discard(strategy_id)
            self._owned_epochs.pop(strategy_id, None)
        if not owned:
            return
        self._lease_heartbeat.forget_strategy(strategy_id)
        self.repository.release_strategy_lease(
            strategy_id=strategy_id,
            owner_id=self.owner_id,
        )
        self.executor.clear_runtime_fencing_token(strategy_id)
        with self._lock:
            self._lost_strategies.discard(strategy_id)


__all__ = ["DistributedRuntimeHost"]
