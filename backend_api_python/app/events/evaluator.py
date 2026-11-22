"""Idempotent strategy-evaluation batch consumer with active runtime ownership."""

from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.events.protocol import EventEnvelope, StrategyEvaluationBatchV1, StrategyRuntimePrepareV1
from app.services.event_inbox import (
    EventInboxRepository,
    StrategyShardLeaseRepository,
)
from app.services.strategy_event_subscriptions import (
    StrategyEventSubscriptionRepository,
    strategy_shard,
)
from app.utils.logger import get_logger


logger = get_logger(__name__)


class StrategyEvaluationBatchHandler:
    """Validate, fence, deduplicate, and execute strategy-evaluation batches."""

    def __init__(
        self,
        *,
        owner_id: str,
        consumer_group: str,
        inbox: EventInboxRepository | None = None,
        shard_leases: StrategyShardLeaseRepository | None = None,
        subscriptions: StrategyEventSubscriptionRepository | None = None,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
        mode: str | None = None,
        runtime_host: Any | None = None,
    ) -> None:
        self.owner_id = str(owner_id)
        self.consumer_group = str(consumer_group)
        self.inbox = inbox or EventInboxRepository()
        self.shard_leases = shard_leases or StrategyShardLeaseRepository()
        self.subscriptions = subscriptions or StrategyEventSubscriptionRepository()
        self.lease_seconds = max(
            10,
            int(lease_seconds or os.getenv("STRATEGY_EVALUATOR_LEASE_SEC", "45")),
        )
        self.max_attempts = max(
            1,
            int(max_attempts or os.getenv("STRATEGY_EVALUATOR_MAX_ATTEMPTS", "5")),
        )
        self.mode = str(mode or os.getenv("STRATEGY_EVALUATOR_MODE", "active")).strip().lower()
        if self.mode not in {"shadow", "active"}:
            raise ValueError(f"strategyEvaluator.invalidMode:{self.mode}")
        self.runtime_host = runtime_host
        if self.mode == "active" and self.runtime_host is None:
            from app.services.distributed_runtime_host import DistributedRuntimeHost

            self.runtime_host = DistributedRuntimeHost(owner_id=self.owner_id)
        self.worker_count = max(
            1,
            min(128, int(os.getenv("STRATEGY_EVALUATOR_BATCH_WORKERS", "16"))),
        )
        self._pool = (
            ThreadPoolExecutor(
                max_workers=self.worker_count,
                thread_name_prefix="distributed-evaluator",
            )
            if self.mode == "active"
            else None
        )
        self.received_batches = 0
        self.completed_batches = 0
        self.duplicate_batches = 0
        self.busy_batches = 0
        self.dead_batches = 0
        self.active_strategies = 0
        self.inactive_strategies = 0
        self.owned_shards: dict[int, int] = {}
        self._strategies_by_shard: dict[int, set[int]] = defaultdict(set)
        self.rebalance_released_strategies = 0

    def handle(self, event: EventEnvelope) -> bool:
        if event.event_type not in {
            StrategyEvaluationBatchV1.event_type, StrategyRuntimePrepareV1.event_type,
        }:
            return True
        self.received_batches += 1
        claim = self.inbox.claim(
            consumer_group=self.consumer_group,
            event=event,
            owner_id=self.owner_id,
            lease_seconds=self.lease_seconds,
        )
        if claim.state in {"completed", "dead"}:
            self.duplicate_batches += 1
            return True
        if claim.state != "acquired":
            self.busy_batches += 1
            return False
        acquired_shard: int | None = None
        acquired_token: int | None = None
        try:
            shard, strategy_ids = self._validate(event)
            fencing_token = self.shard_leases.acquire(
                strategy_shard=shard,
                owner_id=self.owner_id,
                lease_seconds=self.lease_seconds,
            )
            if fencing_token is None:
                self.inbox.fail(
                    consumer_group=self.consumer_group,
                    event_id=event.event_id,
                    owner_id=self.owner_id,
                    error="strategyEvaluator.shardLeaseBusy",
                    terminal=False,
                )
                self.busy_batches += 1
                return False
            acquired_shard = shard
            acquired_token = fencing_token
            self.owned_shards[shard] = fencing_token
            running_ids = self.subscriptions.running_strategy_ids(strategy_ids)
            inactive_count = len(strategy_ids) - len(running_ids)
            if self.mode == "active" and running_ids:
                self._strategies_by_shard[shard].update(running_ids)
                futures = [
                    self._pool.submit(
                        self.runtime_host.prepare
                        if event.event_type == StrategyRuntimePrepareV1.event_type
                        else self.runtime_host.evaluate,
                        strategy_id,
                        event,
                        timeout=float(
                            os.getenv("STRATEGY_EVALUATION_TIMEOUT_SEC", "90")
                        ),
                    )
                    for strategy_id in running_ids
                ]
                results = [future.result() for future in futures]
                if not all(results):
                    raise RuntimeError("strategyEvaluator.runtimeEvaluationFailed")
            completed = self.inbox.complete(
                consumer_group=self.consumer_group,
                event_id=event.event_id,
                owner_id=self.owner_id,
                result={
                    "mode": self.mode,
                    "strategy_shard": shard,
                    "fencing_token": fencing_token,
                    "running_strategy_count": len(running_ids),
                    "inactive_strategy_count": inactive_count,
                },
            )
            if not completed:
                self.busy_batches += 1
                return False
            self.completed_batches += 1
            self.active_strategies += len(running_ids)
            self.inactive_strategies += inactive_count
            return True
        except Exception as exc:
            terminal = claim.attempts >= self.max_attempts
            self.inbox.fail(
                consumer_group=self.consumer_group,
                event_id=event.event_id,
                owner_id=self.owner_id,
                error=str(exc),
                terminal=terminal,
            )
            if terminal:
                self.dead_batches += 1
                return True
            return False
        finally:
            if acquired_shard is not None and acquired_token is not None:
                try:
                    self.shard_leases.release(
                        strategy_shard=acquired_shard,
                        owner_id=self.owner_id,
                        fencing_token=acquired_token,
                    )
                except Exception:
                    logger.exception(
                        "Failed to release strategy shard lease: shard=%s owner=%s",
                        acquired_shard,
                        self.owner_id,
                    )
                if self.owned_shards.get(acquired_shard) == acquired_token:
                    self.owned_shards.pop(acquired_shard, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "evaluation_mode": self.mode,
            "evaluation_batches_received": self.received_batches,
            "evaluation_batches_completed": self.completed_batches,
            "evaluation_batches_duplicate": self.duplicate_batches,
            "evaluation_batches_busy": self.busy_batches,
            "evaluation_batches_dead": self.dead_batches,
            "evaluation_active_strategies": self.active_strategies,
            "evaluation_inactive_strategies": self.inactive_strategies,
            "evaluation_owned_shards": len(self.owned_shards),
            "evaluation_rebalance_released_strategies": (
                self.rebalance_released_strategies
            ),
            **(
                self.runtime_host.snapshot()
                if self.runtime_host is not None
                else {}
            ),
        }

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
        if self.runtime_host is not None:
            self.runtime_host.close()
        self.shard_leases.release_all(owner_id=self.owner_id)

    def release_partition_keys(self, partition_keys: set[str]) -> int:
        shards: set[int] = set()
        for partition_key in partition_keys:
            prefix, separator, raw_shard = str(partition_key).partition(":")
            if prefix != "strategy-shard" or not separator:
                continue
            try:
                shards.add(int(raw_shard))
            except ValueError:
                continue
        strategy_ids: set[int] = set()
        for shard in shards:
            strategy_ids.update(self._strategies_by_shard.pop(shard, set()))
        if self.mode != "active" or not strategy_ids or self.runtime_host is None:
            return 0
        released = int(self.runtime_host.release_strategies(strategy_ids))
        self.rebalance_released_strategies += released
        return released

    @staticmethod
    def _validate(event: EventEnvelope) -> tuple[int, list[int]]:
        payload = event.payload
        shard = int(payload.get("strategy_shard", -1))
        raw_strategy_ids = payload.get("strategy_ids")
        if shard < 0 or not isinstance(raw_strategy_ids, list) or not raw_strategy_ids:
            raise ValueError(f"strategyEvaluator.invalidBatch:{event.event_id}")
        strategy_ids = sorted({int(strategy_id) for strategy_id in raw_strategy_ids})
        if event.partition_key != f"strategy-shard:{shard}":
            raise ValueError(f"strategyEvaluator.invalidPartition:{event.event_id}")
        if any(strategy_shard(strategy_id) != shard for strategy_id in strategy_ids):
            raise ValueError(f"strategyEvaluator.invalidShard:{event.event_id}")
        return shard, strategy_ids


__all__ = ["StrategyEvaluationBatchHandler"]
