"""Durable strategy command processor and runtime lease owner."""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid

from app.services.strategy_command_repository import StrategyCommand, StrategyCommandRepository
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.workers.lease_heartbeat import LeaseHeartbeat


logger = get_logger(__name__)


def build_worker_id() -> str:
    configured = os.getenv("QD_WORKER_ID", "").strip()
    if configured:
        return configured
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class TradingWorker:
    def __init__(self, executor, repository: StrategyCommandRepository | None = None) -> None:
        self.executor = executor
        self.repository = repository or StrategyCommandRepository()
        self.worker_id = build_worker_id()
        self.command_lease_seconds = max(10, int(os.getenv("STRATEGY_COMMAND_LEASE_SEC", "30")))
        self.strategy_lease_seconds = max(10, int(os.getenv("STRATEGY_RUNTIME_LEASE_SEC", "30")))
        self.poll_seconds = max(0.1, float(os.getenv("STRATEGY_COMMAND_WORKER_POLL_SEC", "0.5")))
        self.max_attempts = max(1, int(os.getenv("STRATEGY_COMMAND_MAX_ATTEMPTS", "3")))
        self.restore_max_attempts = max(
            1,
            int(os.getenv("STRATEGY_RESTORE_MAX_ATTEMPTS", "5")),
        )
        self._restore_failure_counts: dict[int, int] = {}
        self._stop = threading.Event()
        self._last_heartbeat = 0.0
        self._global_lease_key = "trading-global-services"
        self._global_services_leader = False
        self._last_global_lease_check = 0.0
        self._last_restore_check = 0.0
        self._lease_losses = []
        self._distributed_strategy_ids: set[int] = set()
        self._remote_strategy_ids: set[int] = set()
        from app.services.grid.actor import GridActorMailbox

        self._grid_actor_mailbox = GridActorMailbox()
        self.distributed_bar_evaluation = str(
            os.getenv("STRATEGY_DISTRIBUTED_BAR_ENABLED", "true")
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._lease_heartbeat = LeaseHeartbeat(self.worker_id, self.strategy_lease_seconds, self._lease_lost)
        self.executor.runtime_guard = self._lease_heartbeat.strategy_valid

    def run_forever(self) -> None:
        logger.info("Trading worker started: %s", self.worker_id)
        self._lease_heartbeat.start()
        try:
            self._ensure_global_services()
            self._heartbeat()
            self._restore_if_due(force=True)
            self._start_local_grid_poller()
            while not self._stop.is_set():
                self._heartbeat()
                self._ensure_global_services()
                self._renew_runtime_leases()
                self._restore_if_due()
                self._drain_grid_actor_events()
                if self._stop.is_set():
                    break
                command = self._claim_next_command()
                if command is None:
                    self._stop.wait(self.poll_seconds)
                    continue
                self._execute(command)
        finally:
            from app.startup import stop_trading_support_services
            from app.services.grid.poller import get_grid_fill_poller

            try:
                self._renew_runtime_leases()
                get_grid_fill_poller().stop()
                if self._global_services_leader:
                    stop_trading_support_services()
                self._shutdown_local_runtimes()
                close_executor = getattr(self.executor, "close", None)
                if callable(close_executor):
                    close_executor()
                if self._global_services_leader:
                    self._lease_heartbeat.forget_global()
                    self.repository.release_process_lease(lease_key=self._global_lease_key, owner_id=self.worker_id)
            finally:
                self._lease_heartbeat.close()
            self.repository.mark_worker_stopped(self.worker_id)
            logger.info("Trading worker stopped: %s", self.worker_id)

    def stop(self) -> None:
        self._stop.set()

    def _claim_next_command(self) -> StrategyCommand | None:
        try:
            return self.repository.claim_next(
                owner_id=self.worker_id,
                lease_seconds=self.command_lease_seconds,
                max_attempts=self.max_attempts,
            )
        except Exception:
            logger.warning(
                "Strategy command polling failed; retrying after backoff",
                exc_info=True,
            )
            return None

    def _lease_lost(self, kind, strategy_id):
        logger.error("Trading %s lease lost: strategy=%s owner=%s", kind, strategy_id, self.worker_id)
        self._lease_losses.append((kind, strategy_id))
        if kind in {"global", "command"}:
            self._stop.set()

    def restore_desired_strategies(self) -> None:
        from app.services.strategy import StrategyService
        from app.services.strategy_lifecycle import maybe_auto_stop_on_exchange_error

        rows = StrategyService().get_running_strategies_with_type()
        desired_ids = {
            int(row["id"])
            for row in (rows or [])
            if int(row.get("id") or 0) > 0
        }
        for strategy_id in set(self._restore_failure_counts) - desired_ids:
            self._restore_failure_counts.pop(strategy_id, None)
        restored = 0
        for row in rows or []:
            if self._stop.is_set():
                break
            strategy_id = int(row["id"])
            if (
                strategy_id in self._distributed_strategy_ids
                or strategy_id in self._local_strategy_ids()
            ):
                continue
            if strategy_id in self._remote_strategy_ids:
                if self._has_active_strategy_lease(strategy_id):
                    continue
                self._remote_strategy_ids.discard(strategy_id)
            if (
                self._has_active_strategy_lease(strategy_id)
                and not self._global_services_leader
            ):
                continue
            try:
                if self.repository.has_pending_stop(strategy_id):
                    continue
                result = self._start(strategy_id, wait_ready=False)
                if result.get("runtime_owner") == "trading-worker-pool":
                    self._remote_strategy_ids.add(strategy_id)
                self._restore_failure_counts.pop(strategy_id, None)
                restored += int(result.get("status") == "running")
            except Exception as exc:
                # Retry transient recovery failures, but stop deterministic or
                # repeatedly failing strategies so recovery cannot loop forever.
                failures = self._restore_failure_counts.get(strategy_id, 0) + 1
                self._restore_failure_counts[strategy_id] = failures
                stopped = maybe_auto_stop_on_exchange_error(
                    strategy_id,
                    str(exc),
                    source="strategy_restore",
                    consecutive_failures=failures,
                    consecutive_threshold=self.restore_max_attempts,
                )
                if stopped:
                    self._restore_failure_counts.pop(strategy_id, None)
                    logger.error(
                        "Strategy restore stopped after failure: strategy=%s attempts=%s",
                        strategy_id,
                        failures,
                        exc_info=True,
                    )
                    continue
                logger.warning(
                    "Strategy restore will retry: strategy=%s attempt=%s/%s",
                    strategy_id,
                    failures,
                    self.restore_max_attempts,
                    exc_info=True,
                )
        if restored:
            logger.info("Trading runtime restore completed: %s/%s", restored, len(rows or []))

    def _restore_if_due(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_restore_check < max(5.0, self.strategy_lease_seconds / 2):
            return
        self._last_restore_check = now
        try:
            self.restore_desired_strategies()
        except Exception:
            logger.warning("Desired strategy recovery check failed; retrying later", exc_info=True)

    def _execute(self, command: StrategyCommand) -> None:
        self._lease_heartbeat.watch_command(command.id, self.command_lease_seconds)
        try:
            if command.command_type == "start":
                result = self._start(command.strategy_id)
            elif command.command_type == "stop":
                result = self._stop_strategy(
                    command.strategy_id,
                    close_positions=bool(command.payload.get("close_positions")),
                )
            elif command.command_type == "restart":
                from app.services.strategy import StrategyService

                if not StrategyService().request_strategy_runtime_restart(
                    command.strategy_id,
                    user_id=command.user_id or None,
                ):
                    raise RuntimeError("Strategy is not running or no longer exists.")
                self._stop_strategy(command.strategy_id, persist_status=False)
                result = self._start(command.strategy_id)
            else:
                result = self._reconcile(command.strategy_id)
            if self._lease_heartbeat.command_valid(command.id):
                self._lease_heartbeat.forget_command()
                self.repository.complete(command.id, result=result)
        except Exception as exc:
            if not self._lease_heartbeat.command_valid(command.id):
                logger.warning("Command completion could not be recorded: %s", command.id, exc_info=True)
                return
            self._lease_heartbeat.forget_command()
            append_strategy_log(command.strategy_id, "error", str(exc))
            logger.error(
                "Strategy command failed: command=%s strategy=%s type=%s",
                command.id,
                command.strategy_id,
                command.command_type,
                exc_info=True,
            )
            if command.attempts < self.max_attempts:
                delay = min(60, 2 ** max(0, command.attempts - 1))
                self.repository.fail(command.id, str(exc), retry_delay_seconds=delay)
            else:
                self.repository.fail(command.id, str(exc))
                if command.command_type in {"start", "restart"}:
                    from app.services.strategy import StrategyService

                    StrategyService().update_strategy_status(command.strategy_id, "stopped")
        finally:
            self._lease_heartbeat.forget_command()

    def _start(self, strategy_id: int, *, wait_ready: bool = True) -> dict:
        from app.services.strategy import StrategyService

        strategy = StrategyService().get_strategy(strategy_id)
        if not strategy or str(strategy.get("status") or "").lower() != "running":
            return {"strategy_id": strategy_id, "status": "skipped", "reason": "desired_state_changed"}
        if self.distributed_bar_evaluation:
            from app.services.strategy_event_subscriptions import (
                StrategyEventSubscriptionPlanner,
            )

            streams = StrategyEventSubscriptionPlanner().register(strategy_id)
            if streams:
                if self._global_services_leader:
                    register_streams = getattr(self.executor, "register_market_streams", None)
                    if callable(register_streams):
                        register_streams(strategy_id, streams)
                self._distributed_strategy_ids.add(strategy_id)
                return {
                    "strategy_id": strategy_id,
                    "runtime_owner": "strategy-evaluator-group",
                    "status": "running",
                    "trigger": "distributed_bar",
                }
        if strategy_id in self._local_strategy_ids():
            return {"strategy_id": strategy_id, "runtime_owner": self.worker_id, "status": "running"}
        if not self._acquire_runtime(strategy_id):
            if self._has_active_strategy_lease(strategy_id):
                return {
                    "strategy_id": strategy_id,
                    "runtime_owner": "trading-worker-pool",
                    "status": "running",
                }
            raise RuntimeError("Strategy runtime lease is owned by another trading worker.")
        if not self.executor.start_strategy(strategy_id):
            self._lease_heartbeat.forget_strategy(strategy_id)
            self.repository.release_strategy_lease(strategy_id=strategy_id, owner_id=self.worker_id)
            clear_fencing_token = getattr(self.executor, "clear_runtime_fencing_token", None)
            if callable(clear_fencing_token):
                clear_fencing_token(strategy_id)
            detail = getattr(self.executor, "_last_start_failure", "") or "Executor rejected the strategy."
            raise RuntimeError(detail)
        if not wait_ready:
            return {
                "strategy_id": strategy_id,
                "runtime_owner": self.worker_id,
                "status": "running",
            }
        alive, hint = self.executor.wait_strategy_running(strategy_id, timeout=3.0)
        if not alive:
            append_strategy_log(strategy_id, "error", "strategyRuntime.startFailed")
            stopped = self.executor.stop_strategy(strategy_id, persist_status=False)
            if stopped:
                self._lease_heartbeat.forget_strategy(strategy_id)
                self.repository.release_strategy_lease(strategy_id=strategy_id, owner_id=self.worker_id)
            raise RuntimeError(hint or "Strategy exited during startup.")
        if not self._lease_heartbeat.strategy_valid(strategy_id):
            raise RuntimeError("strategyRuntime.leaseLost")
        return {"strategy_id": strategy_id, "runtime_owner": self.worker_id, "status": "running"}

    def _has_active_strategy_lease(self, strategy_id: int) -> bool:
        check = getattr(self.repository, "has_active_strategy_lease", None)
        return bool(callable(check) and check(int(strategy_id)))

    def _stop_strategy(
        self,
        strategy_id: int,
        *,
        close_positions: bool = False,
        persist_status: bool = True,
    ) -> dict:
        append_strategy_log(strategy_id, "info", "strategyRuntime.stopCommand")
        if self.distributed_bar_evaluation:
            from app.services.strategy_event_subscriptions import (
                StrategyEventSubscriptionRepository,
            )

            StrategyEventSubscriptionRepository().remove_strategy(strategy_id)
            unregister_streams = getattr(self.executor, "unregister_market_streams", None)
            if callable(unregister_streams):
                unregister_streams(strategy_id)
            self._distributed_strategy_ids.discard(strategy_id)
            self._remote_strategy_ids.discard(strategy_id)
        if close_positions:
            result = self.executor.stop_strategy_with_policy(
                strategy_id,
                close_positions=True,
            )
            if not bool(result.get("success")):
                raise RuntimeError(
                    str(result.get("message") or "Executor failed to stop the local strategy runtime.")
                )
            self._lease_heartbeat.forget_strategy(strategy_id)
            self.repository.release_strategy_lease(
                strategy_id=strategy_id,
                owner_id=self.worker_id,
            )
            self.repository.revoke_strategy_lease(strategy_id=strategy_id)
            return result
        if not self.executor.stop_strategy(strategy_id, persist_status=persist_status):
            raise RuntimeError("Executor failed to stop the local strategy runtime.")
        self._lease_heartbeat.forget_strategy(strategy_id)
        self.repository.release_strategy_lease(strategy_id=strategy_id, owner_id=self.worker_id)
        self.repository.revoke_strategy_lease(strategy_id=strategy_id)
        return {"strategy_id": strategy_id, "status": "stopped"}

    def _reconcile(self, strategy_id: int) -> dict:
        running = self._local_strategy_ids()
        return {
            "strategy_id": strategy_id,
            "runtime_owner": self.worker_id if strategy_id in running else "",
            "running": strategy_id in running,
        }

    def _acquire_runtime(self, strategy_id: int) -> bool:
        token = self.repository.acquire_strategy_lease(
            strategy_id=strategy_id,
            owner_id=self.worker_id,
            lease_seconds=self.strategy_lease_seconds,
        )
        if token is not None:
            set_fencing_token = getattr(self.executor, "set_runtime_fencing_token", None)
            if callable(set_fencing_token):
                set_fencing_token(strategy_id, token)
            self._lease_heartbeat.watch_strategy(strategy_id)
        return token is not None

    def _renew_runtime_leases(self) -> None:
        while self._lease_losses:
            kind, sid = self._lease_losses.pop(0)
            for strategy_id in ([sid] if kind == "strategy" else self._local_strategy_ids()):
                append_strategy_log(strategy_id, "error", "strategyRuntime.leaseLost")

    def _start_local_grid_poller(self) -> None:
        try:
            from app.services.grid.poller import get_grid_fill_poller

            get_grid_fill_poller().start()
        except Exception:
            logger.warning("Local grid fill poller failed to start", exc_info=True)

    def _drain_grid_actor_events(self) -> None:
        strategy_ids = self._local_strategy_ids()
        if not strategy_ids:
            return
        try:
            completed = self._grid_actor_mailbox.drain_owned(
                owner_id=self.worker_id,
                strategy_ids=strategy_ids,
                limit=max(1, int(os.getenv("GRID_ACTOR_BATCH_SIZE", "100"))),
            )
            if completed:
                logger.debug(
                    "Grid actor events completed: owner=%s count=%s",
                    self.worker_id,
                    completed,
                )
        except Exception:
            logger.warning("Grid actor mailbox polling failed", exc_info=True)

    def _local_strategy_ids(self) -> list[int]:
        lock = getattr(self.executor, "lock", None)
        running = getattr(self.executor, "running_strategies", {})
        if lock is None:
            return [int(value) for value in running]
        with lock:
            discard_dead = getattr(self.executor, "_discard_dead_runtimes", None)
            if callable(discard_dead):
                discard_dead()
            return [int(value) for value in running]

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < 10:
            return
        self._last_heartbeat = now
        try:
            self.repository.fail_exhausted_commands(self.max_attempts)
            capacity_snapshot = getattr(self.executor, "runtime_capacity_snapshot", None)
            metadata = (
                capacity_snapshot()
                if callable(capacity_snapshot)
                else {"running_strategies": len(self._local_strategy_ids())}
            )
            metadata.update(self._grid_actor_mailbox.snapshot())
            self.repository.record_worker_heartbeat(
                worker_id=self.worker_id,
                role="trading",
                metadata=metadata,
            )
        except Exception:
            logger.warning("Trading worker heartbeat failed", exc_info=True)

    def _shutdown_local_runtimes(self) -> None:
        local_strategy_ids = set(self._local_strategy_ids())
        watched_strategy_ids = set(self._lease_heartbeat.strategy_ids())
        for strategy_id in sorted(local_strategy_ids | watched_strategy_ids):
            try:
                if strategy_id in local_strategy_ids:
                    append_strategy_log(strategy_id, "info", "strategyRuntime.workerShutdown")
                    stopped = self.executor.stop_strategy(
                        strategy_id,
                        persist_status=False,
                        preserve_run=True,
                    )
                    if not stopped:
                        logger.error(
                            "Strategy runtime did not stop during worker shutdown; retaining lease: %s",
                            strategy_id,
                        )
                        continue
                self._lease_heartbeat.forget_strategy(strategy_id)
                self.repository.release_strategy_lease(
                    strategy_id=strategy_id,
                    owner_id=self.worker_id,
                )
            except Exception:
                logger.exception(
                    "Strategy runtime shutdown failed; retaining lease: %s",
                    strategy_id,
                )

    def _ensure_global_services(self) -> None:
        now = time.monotonic()
        interval = max(2.0, self.strategy_lease_seconds / 3)
        if now - self._last_global_lease_check < interval:
            return
        self._last_global_lease_check = now
        try:
            if self._global_services_leader:
                if not self._lease_heartbeat.global_valid():
                    self._stop.set()
                    return
            else:
                acquired = self.repository.acquire_process_lease(
                    lease_key=self._global_lease_key,
                    owner_id=self.worker_id,
                    lease_seconds=self.strategy_lease_seconds,
                )
                if not acquired:
                    return
                self._lease_heartbeat.watch_global(self._global_lease_key)
                self._global_services_leader = True
                self._distributed_strategy_ids.clear()
                self._remote_strategy_ids.clear()
                self._last_restore_check = 0.0
            from app.startup import _start_trading_support_services
            _start_trading_support_services(lease_guard=self._lease_heartbeat.global_valid)
        except Exception:
            logger.warning("Trading global service lease check failed", exc_info=True)
