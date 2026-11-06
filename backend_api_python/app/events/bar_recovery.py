"""Independent recovery publisher for durable strategy bar subscriptions."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.events.kafka import KafkaEventProducer
from app.events.protocol import EventEnvelope, MarketBarClosedV1
from app.services.market_schedule import is_equity_bar_close
from app.services.strategy_event_subscriptions import StrategyEventSubscriptionRepository
from app.services.strategy_runtime.timeframes import completed_bar_token, completed_bar_close
from app.services.strategy_v2.frequencies import frequency_seconds, normalize_frequency
from app.utils.logger import get_logger


logger = get_logger(__name__)

_CANONICAL_MARKETS = {
    "astock": "AStock",
    "cnstock": "CNStock",
    "crypto": "Crypto",
    "cryptocurrency": "Cryptocurrency",
    "hkstock": "HKStock",
    "usstock": "USStock",
}


@dataclass(frozen=True, slots=True)
class BarRecoveryRoute:
    partition_key: str
    venue: str
    market: str
    market_type: str
    instrument_id: str
    timeframe: str

    @classmethod
    def parse(cls, partition_key: object, timeframe: object = "") -> "BarRecoveryRoute":
        normalized = str(partition_key or "").strip()
        parts = normalized.split(":")
        if len(parts) < 6 or parts[0].lower() != "bar":
            raise ValueError("strategyBarRecovery.invalidPartitionKey")
        route_timeframe = normalize_frequency(str(timeframe or parts[-1]).strip())
        if normalize_frequency(parts[-1]) != route_timeframe:
            raise ValueError("strategyBarRecovery.timeframeMismatch")
        instrument_id = ":".join(parts[4:-1]).strip()
        if not instrument_id:
            raise ValueError("strategyBarRecovery.instrumentRequired")
        market_part = parts[2].strip().lower()
        return cls(
            partition_key=normalized,
            venue=parts[1].strip().lower(),
            market=_CANONICAL_MARKETS.get(market_part, parts[2].strip()),
            market_type=parts[3].strip().lower(),
            instrument_id=instrument_id,
            timeframe=route_timeframe,
        )

    def event(self, token: int) -> EventEnvelope:
        closed_at = completed_bar_close(self.timeframe, int(token), venue=self.venue, market_type=self.market_type)
        return MarketBarClosedV1.create(
            venue=self.venue,
            market=self.market,
            market_type=self.market_type,
            instrument_id=self.instrument_id,
            symbol=self.instrument_id,
            timeframe=self.timeframe,
            closed_bar_token=int(token),
            closed_at=closed_at,
            producer="strategy-bar-recovery",
        )


class StrategyBarRecoveryPublisher:
    """Republish the latest due bar when the primary clock or mirror misses it."""

    def __init__(
        self,
        *,
        repository: StrategyEventSubscriptionRepository | None = None,
        producer: KafkaEventProducer | None = None,
        grace_seconds: float | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or StrategyEventSubscriptionRepository()
        self.producer = producer or KafkaEventProducer()
        try:
            configured_grace = float(
                grace_seconds
                if grace_seconds is not None
                else os.getenv("STRATEGY_BAR_RECOVERY_GRACE_SEC", "5")
            )
        except (TypeError, ValueError):
            configured_grace = 5.0
        self.grace_seconds = max(0.0, configured_grace)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._last_tokens: dict[str, int] = {}
        self._routes = 0
        self._published = 0
        self._publish_failures = 0
        self._invalid_routes = 0
        self._session_skips = 0

    def observe(self, event: EventEnvelope) -> None:
        if event.event_type != MarketBarClosedV1.event_type:
            return
        try:
            token = int(event.payload.get("closed_bar_token"))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._last_tokens[event.partition_key] = max(
                token,
                self._last_tokens.get(event.partition_key, token),
            )

    def publish_due(self, now: datetime | None = None) -> int:
        current = now or self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        eligible_at = current - timedelta(seconds=self.grace_seconds)
        rows = self.repository.active_bar_routes()
        active_keys: set[str] = set()
        events: list[EventEnvelope] = []
        event_tokens: dict[str, int] = {}
        session_only_tokens: dict[str, int] = {}
        invalid_routes = 0
        session_skips = 0

        for row in rows:
            partition_key = str(row.get("partition_key") or "").strip()
            if not partition_key:
                continue
            active_keys.add(partition_key)
            try:
                route = BarRecoveryRoute.parse(
                    partition_key,
                    row.get("timeframe"),
                )
                token = completed_bar_token(route.timeframe, eligible_at, venue=route.venue, market_type=route.market_type)
            except Exception as exc:
                invalid_routes += 1
                logger.warning(
                    "Strategy bar recovery ignored invalid route: partition=%s error=%s",
                    partition_key,
                    exc,
                )
                continue
            with self._lock:
                previous = self._last_tokens.get(partition_key)
                if previous is None:
                    previous = token - 1
                    self._last_tokens[partition_key] = previous
            if previous is not None and token <= previous:
                continue
            route_has_events = False
            for due_token in range(previous + 1, token + 1):
                event = route.event(due_token)
                if event.partition_key != partition_key:
                    invalid_routes += 1
                    logger.warning(
                        "Strategy bar recovery route changed during reconstruction: stored=%s generated=%s",
                        partition_key,
                        event.partition_key,
                    )
                    route_has_events = False
                    break
                closed_at = datetime.fromisoformat(
                    str(event.payload["closed_at"]).replace("Z", "+00:00")
                )
                if not is_equity_bar_close(route.market, closed_at):
                    session_skips += 1
                    continue
                events.append(event)
                route_has_events = True
            if route_has_events:
                event_tokens[partition_key] = token
            else:
                session_only_tokens[partition_key] = token

        with self._lock:
            self._routes = len(active_keys)
            self._invalid_routes += invalid_routes
            self._session_skips += session_skips
            self._last_tokens.update(session_only_tokens)
            for stale in set(self._last_tokens) - active_keys:
                self._last_tokens.pop(stale, None)

        if not events:
            return 0
        if not self.producer.publish_batch(events, timeout=10.0):
            with self._lock:
                self._publish_failures += 1
            logger.warning(
                "Strategy bar recovery batch was not delivered; routes=%s",
                len(events),
            )
            return 0
        with self._lock:
            self._last_tokens.update(event_tokens)
            self._published += len(events)
        return len(events)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "bar_recovery_routes": self._routes,
                "bar_recovery_published": self._published,
                "bar_recovery_publish_failures": self._publish_failures,
                "bar_recovery_invalid_routes": self._invalid_routes,
                "bar_recovery_session_skips": self._session_skips,
            }

    def close(self) -> None:
        self.producer.close()


__all__ = [
    "BarRecoveryRoute",
    "StrategyBarRecoveryPublisher",
]
