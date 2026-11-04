"""Chronological walk-forward split construction from market observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    train_bars: int = 0
    validation_bars: int = 0
    embargo_bars: int = 0

    def metadata(self) -> dict[str, str | int]:
        return {
            "index": self.index,
            "trainStart": self.train_start.isoformat(),
            "trainEnd": self.train_end.isoformat(),
            "validationStart": self.validation_start.isoformat(),
            "validationEnd": self.validation_end.isoformat(),
            "trainBars": self.train_bars,
            "validationBars": self.validation_bars,
            "embargoBars": self.embargo_bars,
        }


@dataclass(frozen=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]
    blind_start: datetime | None
    blind_end: datetime | None
    sample_count: int = 0
    development_bars: int = 0
    blind_bars: int = 0
    requested_folds: int = 0
    effective_folds: int = 0
    minimum_train_bars: int = 0
    minimum_validation_bars: int = 0
    embargo_bars: int = 0
    blind_omitted_reason: str | None = None

    def metadata(self) -> dict[str, object]:
        return {
            "folds": [fold.metadata() for fold in self.folds],
            "blindStart": self.blind_start.isoformat() if self.blind_start else None,
            "blindEnd": self.blind_end.isoformat() if self.blind_end else None,
            "sampleCount": self.sample_count,
            "developmentBars": self.development_bars,
            "blindBars": self.blind_bars,
            "requestedFolds": self.requested_folds,
            "effectiveFolds": self.effective_folds,
            "minimumTrainBars": self.minimum_train_bars,
            "minimumValidationBars": self.minimum_validation_bars,
            "embargoBars": self.embargo_bars,
            "blindOmittedReason": self.blind_omitted_reason,
        }


@dataclass(frozen=True)
class WalkForwardRequirements:
    warmup_bars: int
    minimum_train_bars: int
    minimum_validation_bars: int
    embargo_bars: int
    minimum_required_bars: int

    def metadata(self) -> dict[str, int]:
        return {
            "warmupBars": self.warmup_bars,
            "minimumTrainBars": self.minimum_train_bars,
            "minimumValidationBars": self.minimum_validation_bars,
            "embargoBars": self.embargo_bars,
            "minimumRequiredBars": self.minimum_required_bars,
        }


def walk_forward_requirements(
    *,
    warmup_bars: int = 0,
    embargo_bars: int | None = None,
    embargo_days: int | None = None,
    blind_ratio: float = 0.15,
) -> WalkForwardRequirements:
    lookback = max(1, int(warmup_bars or 0))
    gap_bars = max(0, int(embargo_bars if embargo_bars is not None else (embargo_days or 0)))
    has_blind_holdout = float(blind_ratio or 0.0) > 0
    required_gaps = 2 if has_blind_holdout else 1
    return WalkForwardRequirements(
        warmup_bars=lookback,
        minimum_train_bars=1,
        minimum_validation_bars=1,
        embargo_bars=gap_bars,
        minimum_required_bars=2 + int(has_blind_holdout) + required_gaps * gap_bars,
    )


def build_walk_forward_plan(
    start: datetime,
    end: datetime,
    *,
    folds: int,
    train_ratio: float,
    blind_ratio: float,
    observations: Sequence[datetime] | None = None,
    warmup_bars: int = 0,
    embargo_bars: int | None = None,
    embargo_days: int | None = None,
) -> WalkForwardPlan:
    start = _as_datetime(start)
    end = _as_datetime(end)
    if end <= start:
        raise ValueError("strategyEvolution.dateRangeInvalid")
    timestamps = _observation_times(start, end, observations)
    sample_count = len(timestamps)
    requirements = walk_forward_requirements(
        warmup_bars=warmup_bars,
        embargo_bars=embargo_bars,
        embargo_days=embargo_days,
        blind_ratio=blind_ratio,
    )
    minimum_train_bars = requirements.minimum_train_bars
    minimum_validation_bars = requirements.minimum_validation_bars
    gap_bars = requirements.embargo_bars
    if sample_count < requirements.minimum_required_bars:
        raise ValueError("strategyEvolution.observationRangeTooShort")

    requested_folds = 1
    blind_fraction = max(0.0, min(0.3, float(blind_ratio or 0.0)))
    blind_count = max(1, _fraction_count(sample_count, blind_fraction)) if blind_fraction > 0 else 0
    blind_start = None
    blind_end = None
    blind_omitted_reason = None
    blind_gap = gap_bars if blind_count else 0
    development_limit = sample_count - blind_count - blind_gap
    maximum_train_bars = development_limit - gap_bars - minimum_validation_bars
    train_bars = min(
        maximum_train_bars,
        max(minimum_train_bars, _fraction_count(sample_count, train_ratio)),
    )
    validation_start_index = train_bars + gap_bars
    validation_end_index = development_limit - 1
    validation_bars = validation_end_index - validation_start_index + 1
    if train_bars < minimum_train_bars or validation_bars < minimum_validation_bars:
        raise ValueError("strategyEvolution.observationRangeTooShort")
    if blind_count:
        blind_index = development_limit + blind_gap
        blind_start = timestamps[blind_index]
        blind_end = timestamps[-1]

    output = [
        WalkForwardFold(
            index=1,
            train_start=timestamps[0],
            train_end=timestamps[train_bars - 1],
            validation_start=timestamps[validation_start_index],
            validation_end=timestamps[validation_end_index],
            train_bars=train_bars,
            validation_bars=validation_bars,
            embargo_bars=gap_bars,
        )
    ]
    return WalkForwardPlan(
        folds=tuple(output),
        blind_start=blind_start,
        blind_end=blind_end,
        sample_count=sample_count,
        development_bars=development_limit,
        blind_bars=blind_count,
        requested_folds=requested_folds,
        effective_folds=1,
        minimum_train_bars=minimum_train_bars,
        minimum_validation_bars=minimum_validation_bars,
        embargo_bars=gap_bars,
        blind_omitted_reason=blind_omitted_reason,
    )


def _observation_times(
    start: datetime,
    end: datetime,
    observations: Sequence[datetime] | None,
) -> tuple[datetime, ...]:
    if observations is None:
        total_days = (end.date() - start.date()).days + 1
        return tuple(start + timedelta(days=index) for index in range(total_days))
    normalized = (_as_datetime(value) for value in observations)
    return tuple(sorted({value for value in normalized if start <= value <= end}))


def _as_datetime(value: datetime) -> datetime:
    converted = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(converted, datetime):
        raise TypeError("strategyEvolution.observationTimestampInvalid")
    if converted.tzinfo is not None:
        converted = converted.astimezone(timezone.utc).replace(tzinfo=None)
    return converted


def _fraction_count(total: int, ratio: float) -> int:
    return int(Decimal(total) * Decimal(str(ratio)))
