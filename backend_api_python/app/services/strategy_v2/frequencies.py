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
    "1mo": 2_592_000,
}

SUPPORTED_SUBSCRIPTION_FREQUENCIES: tuple[str, ...] = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "4h",
    "1d",
    "1w",
)

_ALIASES = {
    "daily": "1d",
    "day": "1d",
    "d": "1d",
    "1day": "1d",
    "weekly": "1w",
    "week": "1w",
    "w": "1w",
    "1week": "1w",
    "1wk": "1w",
    "monthly": "1mo",
    "month": "1mo",
    "1month": "1mo",
    "m1": "1m",
    "h1": "1h",
    "d1": "1d",
    "w1": "1w",
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


def periods_per_year(frequency: object, markets: Iterable[str]) -> float:
    normalized = normalize_frequency(frequency)
    normalized_markets = {str(item) for item in markets if str(item)}
    is_crypto = normalized_markets == {"Crypto"}
    trading_days = 365.25 if is_crypto else 252.0
    if normalized in {"1mo", "1month"}:
        return 12.0
    if normalized.endswith("m"):
        minutes = max(1, int(normalized[:-1] or 1))
        session_minutes = 1440.0 if is_crypto else 390.0
        return trading_days * session_minutes / minutes
    if normalized.endswith("h"):
        hours = max(1, int(normalized[:-1] or 1))
        session_hours = 24.0 if is_crypto else 6.5
        return trading_days * session_hours / hours
    if normalized.endswith("w"):
        return 52.0
    return trading_days


__all__ = [
    "FREQUENCY_SECONDS",
    "SUPPORTED_SUBSCRIPTION_FREQUENCIES",
    "driving_frequency",
    "frequency_seconds",
    "normalize_frequency",
    "periods_per_year",
    "unique_frequencies",
]
