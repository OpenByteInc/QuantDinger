"""Canonical Strategy API V2 frequency helpers."""

from __future__ import annotations

from collections.abc import Iterable


FREQUENCY_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14_400,
    "1d": 86_400,
    "1w": 604_800,
}

_ALIASES = {
    "daily": "1d",
    "day": "1d",
    "d": "1d",
    "1day": "1d",
    "weekly": "1w",
    "week": "1w",
    "w": "1w",
    "monthly": "1mo",
    "month": "1mo",
    "m1": "1m",
    "h1": "1h",
    "d1": "1d",
}


def normalize_frequency(value: object, default: str = "1d") -> str:
    raw = str(value or default).strip().lower().replace("分钟", "m").replace("小时", "h")
    return _ALIASES.get(raw, raw or default)


def frequency_seconds(value: object) -> int:
    return FREQUENCY_SECONDS.get(normalize_frequency(value), FREQUENCY_SECONDS["1d"])


def unique_frequencies(values: Iterable[object], *, default: str = "1d") -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(normalize_frequency(value, default) for value in values))
    return normalized or (normalize_frequency(default),)


def driving_frequency(values: Iterable[object], *, default: str = "1d") -> str:
    frequencies = unique_frequencies(values, default=default)
    return min(frequencies, key=lambda item: (frequency_seconds(item), frequencies.index(item)))


__all__ = [
    "FREQUENCY_SECONDS",
    "driving_frequency",
    "frequency_seconds",
    "normalize_frequency",
    "unique_frequencies",
]
