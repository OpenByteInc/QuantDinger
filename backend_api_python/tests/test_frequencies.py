from __future__ import annotations

import pytest

from app.services.strategy_v2.frequencies import (
    FREQUENCY_SECONDS,
    SUPPORTED_SUBSCRIPTION_FREQUENCIES,
    driving_frequency,
    frequency_seconds,
    normalize_frequency,
    periods_per_year,
    unique_frequencies,
)


def test_normalize_frequency_handles_canonical_and_aliases():
    assert normalize_frequency("1d") == "1d"
    assert normalize_frequency("daily") == "1d"
    assert normalize_frequency("Day") == "1d"
    assert normalize_frequency("1day") == "1d"

    assert normalize_frequency("1w") == "1w"
    assert normalize_frequency("weekly") == "1w"
    assert normalize_frequency("week") == "1w"
    assert normalize_frequency("1week") == "1w"
    assert normalize_frequency("1wk") == "1w"
    assert normalize_frequency("w1") == "1w"

    assert normalize_frequency("1mo") == "1mo"
    assert normalize_frequency("monthly") == "1mo"
    assert normalize_frequency("month") == "1mo"
    assert normalize_frequency("1month") == "1mo"
    assert normalize_frequency("1MO") == "1mo"

    assert normalize_frequency("5分钟") == "5m"
    assert normalize_frequency("1小时") == "1h"

    assert normalize_frequency("", default="1h") == "1h"
    assert normalize_frequency(None, default="1d") == "1d"


def test_frequency_seconds_duration_and_strict_monotonic_hierarchy():
    expected = {
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
    for key, seconds in expected.items():
        assert FREQUENCY_SECONDS[key] == seconds
        assert frequency_seconds(key) == seconds

    assert (
        frequency_seconds("1m")
        < frequency_seconds("5m")
        < frequency_seconds("1h")
        < frequency_seconds("1d")
        < frequency_seconds("1w")
        < frequency_seconds("1mo")
    )
    assert frequency_seconds("monthly") == 2_592_000
    assert frequency_seconds("1month") == 2_592_000
    assert frequency_seconds("weekly") == 604_800
    assert frequency_seconds("1week") == 604_800


def test_driving_frequency_selects_faster_interval_consistently():
    assert driving_frequency(["1d", "1mo"]) == "1d"
    assert driving_frequency(["1mo", "1d"]) == "1d"
    assert driving_frequency(["1w", "1mo"]) == "1w"
    assert driving_frequency(["1mo", "1w"]) == "1w"
    assert driving_frequency(["monthly", "weekly"]) == "1w"
    assert driving_frequency(["5m", "1h", "1d", "1mo"]) == "5m"


def test_periods_per_year_annualization_factors():
    assert periods_per_year("1mo", ["USStock"]) == 12.0
    assert periods_per_year("1mo", ["Crypto"]) == 12.0
    assert periods_per_year("monthly", ["USStock"]) == 12.0
    assert periods_per_year("1month", ["Forex"]) == 12.0

    assert periods_per_year("1w", ["USStock"]) == 52.0
    assert periods_per_year("1w", ["Crypto"]) == 52.0
    assert periods_per_year("weekly", ["USStock"]) == 52.0

    assert periods_per_year("1d", ["USStock"]) == 252.0
    assert periods_per_year("1d", ["Crypto"]) == 365.25
    assert periods_per_year("1d", ["Crypto", "USStock"]) == 252.0

    assert periods_per_year("1h", ["USStock"]) == pytest.approx(252.0 * 6.5 / 1.0)
    assert periods_per_year("1h", ["Crypto"]) == pytest.approx(365.25 * 24.0 / 1.0)
    assert periods_per_year("1h", ["Crypto", "USStock"]) == pytest.approx(252.0 * 6.5)
    assert periods_per_year("15m", ["USStock"]) == pytest.approx(252.0 * 390.0 / 15.0)
    assert periods_per_year("15m", ["Crypto"]) == pytest.approx(365.25 * 1440.0 / 15.0)


def test_unique_frequencies_preserves_order_and_deduplicates():
    assert unique_frequencies(["1d", "daily", "1mo", "monthly"]) == ("1d", "1mo")
    assert unique_frequencies([]) == ("1d",)


def test_supported_subscription_frequencies_excludes_monthly():
    assert "1mo" not in SUPPORTED_SUBSCRIPTION_FREQUENCIES
    assert "1w" in SUPPORTED_SUBSCRIPTION_FREQUENCIES
    assert "1d" in SUPPORTED_SUBSCRIPTION_FREQUENCIES
    assert SUPPORTED_SUBSCRIPTION_FREQUENCIES == (
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

