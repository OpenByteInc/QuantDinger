"""Shared market-bar close clock for event-driven strategy scheduling."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.events.bus import EventBus, EventSubscription
from app.events.protocol import EventEnvelope, MarketBarClosedV1
from app.services.market_schedule import equity_data_market, is_equity_bar_close
from app.services.strategy_runtime.timeframes import completed_bar_token, completed_bar_close, seconds_until_next_completed_bar
from app.services.strategy_v2.frequencies import frequency_seconds, normalize_frequency
from app.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BarStreamKey:
    venue: str
    market: str
    market_type: str
    instrument_id: str
    symbol: str
    timeframe: str

    @classmethod
    def from_member(
        cls,
        member: dict[str, object],
        timeframe: str,
        *,
        default_venue: str = "",
    ) -> "BarStreamKey":
        source_market = str(member.get("market") or "unknown").strip()
        market = equity_data_market(
            source_market,
            member,
            str(member.get("api_family") or ""),
        )
        venue = str(
            member.get("exchange_id")
            or member.get("data_source")
            or member.get("provider")
            or default_venue
            or market
        ).strip().lower()
        market_type = str(member.get("market_type") or member.get("api_family") or "spot").strip().lower()
        instrument_id = str(
            member.get("instrument_id")
            or member.get("symbol")
            or member.get("key")
            or ""
        ).strip()
        symbol = str(member.get("symbol") or instrument_id).strip()
        return cls(
            venue=venue,
            market=market,
            market_type=market_type,
            instrument_id=instrument_id,
            symbol=symbol,
            timeframe=normalize_frequency(timeframe),
        )

    @property
    def partition_key(self) -> str:
        return MarketBarClosedV1.partition_key(
            venue=self.venue,
            market=self.market,
            market_type=self.market_type,
            instrument_id=self.instrument_id,
            timeframe=self.timeframe,
        )


class BarCloseSubscription:
    def __init__(self, bus_subscription: EventSubscription, release_stream: Callable[[], None]) -> None:
        self._bus_subscription = bus_subscription
        self._release_stream = release_stream
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._bus_subscription.close()
        self._release_stream()


class MarketBarCloseClock:
    """Publish one coalescible event per venue/instrument/timeframe boundary."""

    def __init__(
        self,
        bus: EventBus,
        *,
        grace_seconds: float | None = None,
        now: Callable[[], datetime] | None = None,
        autostart: bool = True,
    ) -> None:
        self.bus = bus
        try:
            configured_grace = float(
                grace_seconds
                if grace_seconds is not None
                else os.getenv("BAR_CLOSE_EVENT_GRACE_SEC", "0.5")
            )
        except (TypeError, ValueError):
            configured_grace = 0.5
        self.grace_seconds = max(0.0, configured_grace)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._autostart = bool(autostart)
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ref_counts: dict[BarStreamKey, int] = {}
        self._last_tokens: dict[BarStreamKey, int] = {}
        self._published_events = 0

    def subscribe(
        self,
        key: BarStreamKey,
        handler: Callable[[EventEnvelope], None],
    ) -> BarCloseSubscription:
        bus_subscription = self.bus.subscribe(
            MarketBarClosedV1.event_type,
            handler,
            partition_key=key.partition_key,
        )
        with self._lock:
            self._ref_counts[key] = self._ref_counts.get(key, 0) + 1
            self._last_tokens.setdefault(key, completed_bar_token(key.timeframe, self._now(), venue=key.venue, market_type=key.market_type))
            if self._autostart:
                self._start_locked()
            self._wake.set()

        def release() -> None:
            with self._lock:
                remaining = self._ref_counts.get(key, 0) - 1
                if remaining > 0:
                    self._ref_counts[key] = remaining
                else:
                    self._ref_counts.pop(key, None)
                    self._last_tokens.pop(key, None)
                self._wake.set()

        return BarCloseSubscription(bus_subscription, release)

    def publish_due(self, now: datetime | None = None) -> int:
        current = now or self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        with self._lock:
            keys = tuple(self._ref_counts)
        published = 0
        session_boundaries: dict[tuple[str, datetime], bool] = {}
        for key in keys:
            token = completed_bar_token(key.timeframe, current, venue=key.venue, market_type=key.market_type)
            with self._lock:
                previous = self._last_tokens.get(key)
                if previous is None:
                    self._last_tokens[key] = token
                    continue
                if token <= previous:
                    continue
                self._last_tokens[key] = token
            closed_at = completed_bar_close(key.timeframe, token, venue=key.venue, market_type=key.market_type)
            session_key = (key.market, closed_at)
            actionable = session_boundaries.get(session_key)
            if actionable is None:
                actionable = is_equity_bar_close(key.market, closed_at)
                session_boundaries[session_key] = actionable
            if not actionable:
                continue
            event = MarketBarClosedV1.create(
                venue=key.venue,
                market=key.market,
                market_type=key.market_type,
                instrument_id=key.instrument_id,
                symbol=key.symbol,
                timeframe=key.timeframe,
                closed_bar_token=token,
                closed_at=closed_at,
            )
            self.bus.publish(event)
            published += 1
        if published:
            with self._lock:
                self._published_events += published
        return published

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "bar_streams": len(self._ref_counts),
                "bar_stream_subscribers": sum(self._ref_counts.values()),
                "bar_close_events": self._published_events,
            }

    def close(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(max(0.0, float(timeout or 0.0)))

    def _start_locked(self) -> None:
        if self._thread is not None or self._stop.is_set():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="market-bar-close-clock",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.publish_due()
            except Exception:
                logger.exception("Market bar close clock failed")
            with self._lock:
                keys = tuple(self._ref_counts)
            if not keys:
                wait_seconds = 60.0
            else:
                wait_seconds = min(
                    seconds_until_next_completed_bar(
                        key.timeframe,
                        self._now(),
                        grace_seconds=self.grace_seconds,
                        venue=key.venue,
                        market_type=key.market_type,
                    )
                    for key in keys
                )
                wait_seconds = min(60.0, max(0.05, wait_seconds))
            self._wake.wait(wait_seconds)
            self._wake.clear()


def supports_bar_close_events(frequency: str, candidates: list[dict[str, object]]) -> bool:
    """Daily equity bars keep their calendar-aware completed-session policy."""
    stock_markets = {"USStock", "HKStock", "AStock", "CNStock"}
    if any(
        str(item.get("api_family") or "").strip().lower() == "stock"
        and str(item.get("market") or "") == "Crypto"
        and str(item.get("underlying_market") or "") not in stock_markets
        for item in candidates
    ):
        return False
    has_equity = any(
        equity_data_market(
            str(item.get("market") or ""),
            item,
            str(item.get("api_family") or ""),
        ) in stock_markets
        for item in candidates
    )
    return not (has_equity and frequency_seconds(frequency) >= 86_400)


__all__ = [
    "BarCloseSubscription",
    "BarStreamKey",
    "MarketBarCloseClock",
    "supports_bar_close_events",
]
