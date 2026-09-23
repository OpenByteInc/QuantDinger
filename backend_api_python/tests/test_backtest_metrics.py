import math

import pytest

from app.services.backtest.metrics import calculate_information_ratio


def _curve(returns, *, missing_index=None):
    value = 100.0
    points = [{"time": "2026-01-01T00:00:00Z", "value": value}]
    for index, periodic_return in enumerate(returns, start=1):
        value *= 1.0 + periodic_return
        if index != missing_index:
            points.append({
                "time": f"2026-01-{index + 1:02d}T00:00:00Z",
                "value": value,
            })
    return points


def test_information_ratio_uses_aligned_periodic_returns_and_sample_tracking_error():
    result = calculate_information_ratio(
        _curve([0.01, 0.02, -0.01, 0.005]),
        _curve([0.005, 0.01, -0.005, 0.002]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
    )

    active_returns = [0.005, 0.01, -0.005, 0.003]
    active_mean = sum(active_returns) / len(active_returns)
    tracking_error = pytest.approx(0.006238322424070967 * math.sqrt(252))

    assert result["status"] == "available"
    assert result["portfolioReturnAnnualized"] == pytest.approx(1.575)
    assert result["benchmarkReturnAnnualized"] == pytest.approx(0.756)
    assert result["activeReturnAnnualized"] == pytest.approx(active_mean * 252)
    assert result["trackingErrorAnnualized"] == tracking_error
    assert result["informationRatio"] == pytest.approx(8.270196225621152)
    assert result["classification"] == "exceptional"
    assert result["benchmark"] == "USStock:SPY"
    assert result["observations"] == 4
    assert result["frequency"] == "1d"
    assert result["annualizationFactor"] == 252


def test_information_ratio_drops_dates_missing_from_either_curve():
    result = calculate_information_ratio(
        _curve([0.01, 0.02, -0.01, 0.005]),
        _curve([0.005, 0.01, -0.005, 0.002], missing_index=2),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
    )

    assert result["status"] == "available"
    assert result["observations"] == 2


def test_information_ratio_handles_zero_tracking_error_explicitly():
    result = calculate_information_ratio(
        _curve([0.01, 0.02, 0.03]),
        _curve([0.005, 0.015, 0.025]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
    )

    assert result["status"] == "zero_tracking_error"
    assert result["trackingErrorAnnualized"] == pytest.approx(0.0, abs=1e-12)
    assert result["informationRatio"] is None
    assert result["classification"] is None


def test_information_ratio_reports_insufficient_aligned_history():
    result = calculate_information_ratio(
        _curve([0.01]),
        _curve([0.005]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
    )

    assert result["status"] == "insufficient_history"
    assert result["observations"] == 1
    assert result["informationRatio"] is None


def test_information_ratio_preserves_negative_values():
    result = calculate_information_ratio(
        _curve([-0.01, -0.02, 0.005, -0.01]),
        _curve([0.005, -0.005, 0.01, 0.002]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
    )

    assert result["status"] == "available"
    assert result["informationRatio"] < 0
    assert result["classification"] == "weak"


def test_information_ratio_accepts_configurable_interpretation_bands():
    result = calculate_information_ratio(
        _curve([0.01, 0.02, -0.01, 0.005]),
        _curve([0.005, 0.01, -0.005, 0.002]),
        benchmark="custom",
        frequency="1d",
        annualization_factor=252,
        classification_bands=((10.0, "ordinary"), (math.inf, "excellent")),
    )

    assert result["classification"] == "ordinary"
