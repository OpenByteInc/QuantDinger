"""Stable, versioned event envelopes shared by runtime services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid4, uuid5


def _utc_iso(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _required(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"eventProtocol.missingField:{field}")
    return normalized


def _part(value: object) -> str:
    return _required(value, "partition_key_part").strip().lower()


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Transport-neutral event envelope with explicit schema versioning."""

    event_id: str
    event_type: str
    schema_version: int
    occurred_at: str
    partition_key: str
    producer: str
    payload: Mapping[str, Any]
    trace_id: str = ""
    correlation_id: str = ""
    causation_id: str = ""

    def __post_init__(self) -> None:
        _required(self.event_id, "event_id")
        _required(self.event_type, "event_type")
        _required(self.partition_key, "partition_key")
        _required(self.producer, "producer")
        if int(self.schema_version or 0) <= 0:
            raise ValueError("eventProtocol.invalidSchemaVersion")
        _utc_iso(self.occurred_at)
        if not isinstance(self.payload, Mapping):
            raise ValueError("eventProtocol.invalidPayload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": int(self.schema_version),
            "occurred_at": _utc_iso(self.occurred_at),
            "partition_key": self.partition_key,
            "producer": self.producer,
            "payload": dict(self.payload),
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "EventEnvelope":
        return cls(
            event_id=_required(values.get("event_id"), "event_id"),
            event_type=_required(values.get("event_type"), "event_type"),
            schema_version=int(values.get("schema_version") or 0),
            occurred_at=_utc_iso(_required(values.get("occurred_at"), "occurred_at")),
            partition_key=_required(values.get("partition_key"), "partition_key"),
            producer=_required(values.get("producer"), "producer"),
            payload=dict(values.get("payload") or {}),
            trace_id=str(values.get("trace_id") or ""),
            correlation_id=str(values.get("correlation_id") or ""),
            causation_id=str(values.get("causation_id") or ""),
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> "EventEnvelope":
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("eventProtocol.invalidEnvelope")
        return cls.from_dict(parsed)


class MarketBarClosedV1:
    event_type = "market.bar.closed.v1"
    schema_version = 1

    @classmethod
    def partition_key(
        cls,
        *,
        venue: str,
        market: str,
        market_type: str,
        instrument_id: str,
        timeframe: str,
    ) -> str:
        return ":".join((
            "bar",
            _part(venue),
            _part(market),
            _part(market_type),
            _part(instrument_id),
            _part(timeframe),
        ))

    @classmethod
    def create(
        cls,
        *,
        venue: str,
        market: str,
        market_type: str,
        instrument_id: str,
        symbol: str,
        timeframe: str,
        closed_bar_token: int,
        closed_at: datetime | str,
        producer: str = "market-bar-clock",
    ) -> EventEnvelope:
        partition_key = cls.partition_key(
            venue=venue,
            market=market,
            market_type=market_type,
            instrument_id=instrument_id,
            timeframe=timeframe,
        )
        event_identity = f"{partition_key}:{int(closed_bar_token)}"
        return EventEnvelope(
            event_id=str(uuid5(NAMESPACE_URL, event_identity)),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(closed_at),
            partition_key=partition_key,
            producer=producer,
            payload={
                "venue": _part(venue),
                "market": _required(market, "market"),
                "market_type": _part(market_type),
                "instrument_id": _required(instrument_id, "instrument_id"),
                "symbol": _required(symbol, "symbol"),
                "timeframe": _required(timeframe, "timeframe"),
                "closed_bar_token": int(closed_bar_token),
                "closed_at": _utc_iso(closed_at),
            },
        )


class StrategyCommandV1:
    event_type = "strategy.command.v1"
    schema_version = 1

    @classmethod
    def create(
        cls,
        *,
        strategy_id: int,
        command: str,
        producer: str,
        payload: Mapping[str, Any] | None = None,
        correlation_id: str = "",
    ) -> EventEnvelope:
        strategy_id = int(strategy_id)
        return EventEnvelope(
            event_id=str(uuid4()),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(),
            partition_key=f"strategy:{strategy_id}",
            producer=_required(producer, "producer"),
            payload={**dict(payload or {}), "strategy_id": strategy_id, "command": _required(command, "command")},
            correlation_id=str(correlation_id or ""),
        )


class StrategyEvaluationBatchV1:
    event_type = "strategy.evaluate.v1"
    schema_version = 1

    @classmethod
    def create(
        cls,
        *,
        strategy_shard: int,
        strategy_ids: list[int],
        source_event_id: str,
        closed_bar_token: int,
        timeframe: str,
        batch_index: int = 0,
        producer: str = "strategy-due-dispatcher",
    ) -> EventEnvelope:
        shard = int(strategy_shard)
        source_id = _required(source_event_id, "source_event_id")
        normalized_ids = sorted({int(item) for item in strategy_ids})
        if not normalized_ids:
            raise ValueError("eventProtocol.emptyStrategyBatch")
        partition_key = f"strategy-shard:{shard}"
        identity = f"{source_id}:{shard}:{int(batch_index)}"
        return EventEnvelope(
            event_id=str(uuid5(NAMESPACE_URL, identity)),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(),
            partition_key=partition_key,
            producer=_required(producer, "producer"),
            payload={
                "strategy_shard": shard,
                "strategy_ids": normalized_ids,
                "source_event_id": source_id,
                "closed_bar_token": int(closed_bar_token),
                "timeframe": _required(timeframe, "timeframe"),
                "batch_index": int(batch_index),
            },
            correlation_id=source_id,
            causation_id=source_id,
        )


class StrategyRuntimePrepareV1:
    """Wake a shard to prepare runtimes, without evaluating a market bar."""

    event_type = "strategy.runtime.prepare.v1"
    schema_version = 1

    @classmethod
    def create(cls, *, strategy_shard: int, strategy_ids: list[int]) -> EventEnvelope:
        return EventEnvelope(
            event_id=str(uuid4()),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(),
            partition_key=f"strategy-shard:{int(strategy_shard)}",
            producer="strategy-due-dispatcher",
            payload={
                "strategy_shard": int(strategy_shard),
                "strategy_ids": sorted({int(value) for value in strategy_ids}),
            },
        )


class OrderIntentEventV1:
    event_type = "order.intent.v1"
    schema_version = 1

    @classmethod
    def create(
        cls,
        *,
        exchange_id: str,
        credential_id: int,
        account_type: str,
        strategy_id: int,
        intent: Mapping[str, Any],
        producer: str,
        correlation_id: str = "",
    ) -> EventEnvelope:
        partition_key = f"account:{_part(exchange_id)}:{int(credential_id)}:{_part(account_type)}"
        return EventEnvelope(
            event_id=str(uuid4()),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(),
            partition_key=partition_key,
            producer=_required(producer, "producer"),
            payload={"strategy_id": int(strategy_id), "intent": dict(intent)},
            correlation_id=str(correlation_id or ""),
        )


class OrderFillEventV1:
    event_type = "order.fill.v1"
    schema_version = 1

    @classmethod
    def create(
        cls,
        *,
        exchange_id: str,
        credential_id: int,
        account_type: str,
        strategy_id: int,
        fill: Mapping[str, Any],
        producer: str,
        causation_id: str = "",
    ) -> EventEnvelope:
        partition_key = f"account:{_part(exchange_id)}:{int(credential_id)}:{_part(account_type)}"
        return EventEnvelope(
            event_id=str(uuid4()),
            event_type=cls.event_type,
            schema_version=cls.schema_version,
            occurred_at=_utc_iso(),
            partition_key=partition_key,
            producer=_required(producer, "producer"),
            payload={"strategy_id": int(strategy_id), "fill": dict(fill)},
            causation_id=str(causation_id or ""),
        )


__all__ = [
    "EventEnvelope",
    "MarketBarClosedV1",
    "OrderFillEventV1",
    "OrderIntentEventV1",
    "StrategyCommandV1",
    "StrategyEvaluationBatchV1",
    "StrategyRuntimePrepareV1",
]
