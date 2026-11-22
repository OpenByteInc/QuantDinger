"""Kafka topic names and immutable partitioning defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from app.events.protocol import (
    MarketBarClosedV1,
    OrderFillEventV1,
    OrderIntentEventV1,
    StrategyCommandV1,
    StrategyEvaluationBatchV1,
    StrategyRuntimePrepareV1,
)


@dataclass(frozen=True, slots=True)
class TopicSpec:
    name: str
    partitions: int
    replication_factor: int
    config: Mapping[str, str] = field(default_factory=dict)


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def topic_specs() -> tuple[TopicSpec, ...]:
    replication = _positive_int("KAFKA_REPLICATION_FACTOR", 1)
    market_partitions = _positive_int("KAFKA_MARKET_PARTITIONS", 6)
    strategy_partitions = _positive_int("KAFKA_STRATEGY_PARTITIONS", 12)
    order_partitions = _positive_int("KAFKA_ORDER_PARTITIONS", 12)
    dlq_partitions = _positive_int("KAFKA_DLQ_PARTITIONS", 3)
    day_ms = 86_400_000
    return (
        TopicSpec(
            os.getenv("KAFKA_TOPIC_MARKET_BAR", "qd.market.bar.closed.v1"),
            market_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(3 * day_ms)},
        ),
        TopicSpec(
            os.getenv("KAFKA_TOPIC_STRATEGY_LIFECYCLE", "qd.strategy.lifecycle.v1"),
            strategy_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(7 * day_ms)},
        ),
        TopicSpec(
            os.getenv("KAFKA_TOPIC_STRATEGY_EVALUATE", "qd.strategy.evaluate.v1"),
            strategy_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(3 * day_ms)},
        ),
        TopicSpec(
            os.getenv("KAFKA_TOPIC_ORDER_INTENT", "qd.order.intent.v1"),
            order_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(30 * day_ms)},
        ),
        TopicSpec(
            os.getenv("KAFKA_TOPIC_ORDER_EVENT", "qd.order.event.v1"),
            order_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(30 * day_ms)},
        ),
        TopicSpec(
            os.getenv("KAFKA_TOPIC_RUNTIME_DLQ", "qd.runtime.dlq.v1"),
            dlq_partitions,
            replication,
            {"cleanup.policy": "delete", "retention.ms": str(30 * day_ms)},
        ),
    )


def event_topic_map() -> dict[str, str]:
    specs = topic_specs()
    return {
        MarketBarClosedV1.event_type: specs[0].name,
        StrategyCommandV1.event_type: specs[1].name,
        StrategyEvaluationBatchV1.event_type: specs[2].name,
        StrategyRuntimePrepareV1.event_type: specs[2].name,
        OrderIntentEventV1.event_type: specs[3].name,
        OrderFillEventV1.event_type: specs[4].name,
    }


def topic_for_event(event_type: str) -> str:
    try:
        return event_topic_map()[str(event_type)]
    except KeyError as exc:
        raise ValueError(f"kafka.unknownEventType:{event_type}") from exc


__all__ = ["TopicSpec", "event_topic_map", "topic_for_event", "topic_specs"]
