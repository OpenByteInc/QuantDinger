"""Transform closed-bar events into stable strategy-shard evaluation batches."""

from __future__ import annotations

import os
from collections import defaultdict

from app.events.kafka import KafkaEventProducer
from app.events.protocol import (
    EventEnvelope, MarketBarClosedV1, StrategyEvaluationBatchV1, StrategyRuntimePrepareV1,
)
from app.services.strategy_event_subscriptions import (
    StrategyEventSubscriptionRepository,
    strategy_shard,
)


class StrategyDueDispatcher:
    def __init__(
        self,
        *,
        repository: StrategyEventSubscriptionRepository | None = None,
        producer: KafkaEventProducer | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.repository = repository or StrategyEventSubscriptionRepository()
        self.producer = producer or KafkaEventProducer()
        configured = batch_size
        if configured is None:
            try:
                configured = int(os.getenv("STRATEGY_EVALUATION_BATCH_SIZE", "250"))
            except (TypeError, ValueError):
                configured = 250
        self.batch_size = max(1, min(2_000, int(configured)))
        self.source_events = 0
        self.strategies_dispatched = 0
        self.batches_dispatched = 0

    def dispatch(self, event: EventEnvelope) -> bool:
        if event.event_type != MarketBarClosedV1.event_type:
            return True
        strategy_ids = self.repository.strategy_ids_for_event(
            event.event_type,
            event.partition_key,
        )
        grouped: dict[int, list[int]] = defaultdict(list)
        for strategy_id in strategy_ids:
            grouped[strategy_shard(strategy_id)].append(strategy_id)
        batches: list[EventEnvelope] = []
        payload = event.payload
        for shard, shard_ids in grouped.items():
            for start in range(0, len(shard_ids), self.batch_size):
                chunk = shard_ids[start:start + self.batch_size]
                batches.append(StrategyEvaluationBatchV1.create(
                    strategy_shard=shard,
                    strategy_ids=chunk,
                    source_event_id=event.event_id,
                    closed_bar_token=int(payload["closed_bar_token"]),
                    timeframe=str(payload["timeframe"]),
                    batch_index=start // self.batch_size,
                ))
        if batches and not self.producer.publish_batch(
            batches,
            timeout=float(os.getenv("KAFKA_DISPATCH_FLUSH_TIMEOUT_SEC", "10")),
        ):
            return False
        self.source_events += 1
        self.strategies_dispatched += len(strategy_ids)
        self.batches_dispatched += len(batches)
        return True

    def snapshot(self) -> dict[str, int]:
        return {
            "source_events": self.source_events,
            "strategies_dispatched": self.strategies_dispatched,
            "batches_dispatched": self.batches_dispatched,
            **self.producer.snapshot(),
        }

    def prepare_due_runtimes(self) -> bool:
        """Recover durable start requests even when no candle has closed yet."""
        grouped: dict[int, list[int]] = defaultdict(list)
        for strategy_id in self.repository.unready_strategy_ids():
            grouped[strategy_shard(strategy_id)].append(strategy_id)
        events = [
            StrategyRuntimePrepareV1.create(
                strategy_shard=shard, strategy_ids=ids[start:start + self.batch_size],
            )
            for shard, ids in grouped.items()
            for start in range(0, len(ids), self.batch_size)
        ]
        return not events or self.producer.publish_batch(events, timeout=10.0)

    def close(self) -> None:
        self.producer.close()


__all__ = ["StrategyDueDispatcher"]
