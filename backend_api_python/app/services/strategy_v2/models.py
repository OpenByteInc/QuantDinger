"""Strategy API V2 immutable contract models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .frequencies import driving_frequency, unique_frequencies


@dataclass(frozen=True)
class InstrumentSpec:
    market: str
    symbol: str
    exchange_id: str = ""
    market_type: str = ""
    instrument_id: str = ""

    @property
    def key(self) -> str:
        suffix = ""
        if self.exchange_id:
            suffix = f"@{self.exchange_id}"
            if self.market_type:
                suffix += f":{self.market_type}"
        elif self.market_type:
            suffix = f"@{self.market_type}"
        return f"{self.market}:{self.symbol}{suffix}"

    def metadata(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UniverseSpec:
    kind: str
    reference: str = ""
    instruments: tuple[InstrumentSpec, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reference": self.reference,
            "instruments": [item.metadata() for item in self.instruments],
        }


@dataclass(frozen=True)
class SubscriptionSpec:
    instruments: tuple[InstrumentSpec, ...] = ()
    universe_reference: str = ""
    frequency: str = "1d"
    fields: tuple[str, ...] = ("open", "high", "low", "close", "volume")

    def metadata(self) -> dict[str, Any]:
        return {
            "instruments": [item.metadata() for item in self.instruments],
            "universeReference": self.universe_reference,
            "frequency": self.frequency,
            "fields": list(self.fields),
        }


@dataclass(frozen=True)
class ScheduleSpec:
    frequency: str
    callback: str
    time: str = ""
    weekday: int | None = None
    monthday: int | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "callback": self.callback,
            "time": self.time,
            "weekday": self.weekday,
            "monthday": self.monthday,
        }


@dataclass(frozen=True)
class StrategyManifest:
    api_version: int
    code_hash: str
    strategy_type: str
    universe: UniverseSpec
    subscriptions: tuple[SubscriptionSpec, ...]
    schedules: tuple[ScheduleSpec, ...]
    benchmark: InstrumentSpec | None = None
    handlers: tuple[str, ...] = ()
    factor_dependencies: tuple[str, ...] = ()
    fundamental_dependencies: tuple[str, ...] = ()
    warmup_bars: int = 0
    leverage_allowed: bool = False
    max_leverage: float = 1.0
    direction_mode: str = ""
    metadata_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def markets(self) -> tuple[str, ...]:
        values: set[str] = set()
        if self.benchmark:
            values.add(self.benchmark.market)
        for instrument in self.universe.instruments:
            values.add(instrument.market)
        for subscription in self.subscriptions:
            values.update(item.market for item in subscription.instruments)
        return tuple(sorted(values))

    @property
    def primary_frequency(self) -> str:
        if self.subscriptions:
            return self.subscriptions[0].frequency
        return "1d"

    @property
    def frequencies(self) -> tuple[str, ...]:
        return unique_frequencies(item.frequency for item in self.subscriptions)

    @property
    def driving_frequency(self) -> str:
        return driving_frequency(self.frequencies)

    def metadata(self) -> dict[str, Any]:
        return {
            "apiVersion": self.api_version,
            "codeHash": self.code_hash,
            "strategyType": self.strategy_type,
            "primaryFrequency": self.primary_frequency,
            "drivingFrequency": self.driving_frequency,
            "frequencies": list(self.frequencies),
            "markets": list(self.markets),
            "universe": self.universe.metadata(),
            "subscriptions": [item.metadata() for item in self.subscriptions],
            "schedules": [item.metadata() for item in self.schedules],
            "benchmark": self.benchmark.metadata() if self.benchmark else None,
            "handlers": list(self.handlers),
            "factorDependencies": list(self.factor_dependencies),
            "fundamentalDependencies": list(self.fundamental_dependencies),
            "warmupBars": self.warmup_bars,
            "leverageAllowed": self.leverage_allowed,
            "maxLeverage": self.max_leverage,
            "directionMode": self.direction_mode,
            "metadata": dict(self.metadata_fields),
        }
