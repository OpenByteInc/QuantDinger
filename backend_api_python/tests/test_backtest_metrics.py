import math

import pandas as pd
import pytest

from app.services.backtest.metrics import (
    benchmark_level_curve,
    calculate_benchmark_relative_metrics,
    calculate_beta_and_alpha,
    calculate_capture_ratios,
    calculate_information_ratio,
)


def _curve(returns, *, frequency="D"):
    value = 100.0
    index = pd.date_range("2026-01-01", periods=len(returns) + 1, freq=frequency, tz="UTC")
    points = [{"time": index[0].isoformat(), "value": value}]
    for timestamp, periodic_return in zip(index[1:], returns):
        value *= 1.0 + periodic_return
        points.append({"time": timestamp.isoformat(), "value": value})
    return points


def test_information_ratio_uses_aligned_periodic_returns_and_sample_tracking_error():
    result = calculate_information_ratio(
        _curve([0.01, 0.02, -0.01, 0.005]),
        _curve([0.005, 0.01, -0.005, 0.002]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["portfolioReturnAnnualized"] == pytest.approx(1.575)
    assert result["benchmarkReturnAnnualized"] == pytest.approx(0.756)
    assert result["informationRatio"] == pytest.approx(8.270196225621152)
    assert result["classification"] == "exceptional"
    assert result["observations"] == 4


def test_metric_samples_portfolio_at_native_benchmark_frequency():
    index = pd.date_range("2026-01-01", periods=61, freq="min", tz="UTC")
    values = [100.0 * (1.0001**position) for position in range(len(index))]
    portfolio = [
        {"time": timestamp.isoformat(), "value": value}
        for timestamp, value in zip(index, values)
    ]
    benchmark_frame = pd.DataFrame({"close": values[::15]}, index=index[::15])

    result = calculate_information_ratio(
        portfolio,
        benchmark_level_curve(benchmark_frame),
        benchmark="Crypto:BTC/USDT",
        frequency="15m",
        annualization_factor=365.25 * 24 * 4,
        market="Crypto",
    )

    assert result["status"] == "zero_tracking_error"
    assert result["observations"] == 4
    assert result["trackingErrorAnnualized"] == pytest.approx(0.0, abs=1e-12)


def test_metric_rejects_missing_continuous_market_periods():
    times = ["2026-01-01T00:00:00Z", "2026-01-01T00:30:00Z", "2026-01-01T00:45:00Z"]
    portfolio = [{"time": time, "value": value} for time, value in zip(times, [100.0, 121.0, 133.1])]
    benchmark = [{"time": time, "value": value} for time, value in zip(times, [100.0, 102.01, 103.0301])]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="Crypto:BTC/USDT",
        frequency="15m",
        annualization_factor=365.25 * 24 * 4,
        market="Crypto",
    )

    assert result["status"] == "insufficient_history"
    assert result["observations"] == 1


def test_metric_accepts_consecutive_equity_sessions_across_weekend():
    portfolio = [
        {"time": "2026-01-09T00:00:00Z", "value": 100.0},
        {"time": "2026-01-12T00:00:00Z", "value": 101.0},
    ]
    benchmark = [
        {"time": "2026-01-09T00:00:00Z", "value": 100.0},
        {"time": "2026-01-12T00:00:00Z", "value": 100.5},
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252,
        market="USStock",
    )

    assert result["observations"] == 1
    assert result["portfolioReturnAnnualized"] == pytest.approx(2.52)


def test_information_ratio_preserves_negative_values_and_custom_bands():
    result = calculate_information_ratio(
        _curve([-0.01, -0.02, 0.005, -0.01]),
        _curve([0.005, -0.005, 0.01, 0.002]),
        benchmark="custom",
        frequency="1d",
        annualization_factor=252,
        market="USStock",
        classification_bands=((0.0, "negative"), (math.inf, "positive")),
    )

    assert result["informationRatio"] < 0
    assert result["classification"] == "negative"


def test_information_ratio_supports_monthly_frequency_and_annualization_factor():
    times = [
        "2026-01-31T00:00:00Z",
        "2026-02-28T00:00:00Z",
        "2026-03-31T00:00:00Z",
        "2026-04-30T00:00:00Z",
    ]
    portfolio = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 102.0, 105.0, 103.0])
    ]
    benchmark = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 101.0, 102.0, 101.5])
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="Crypto:BTC/USDT",
        frequency="1mo",
        annualization_factor=12.0,
        market="Crypto",
    )

    assert result["status"] == "available"
    assert result["observations"] == 3
    assert result["frequency"] == "1mo"
    assert result["annualizationFactor"] == 12.0
    assert result["portfolioReturnAnnualized"] == pytest.approx(0.1214565826)
    assert result["benchmarkReturnAnnualized"] == pytest.approx(0.0599961172)
    assert result["activeReturnAnnualized"] == pytest.approx(0.0614604653)
    assert result["trackingErrorAnnualized"] == pytest.approx(0.0601036291)
    assert result["informationRatio"] == pytest.approx(1.022574946)
    assert result["classification"] == "exceptional"


def test_metric_accepts_consecutive_equity_monthly_sessions():
    times = [
        "2026-01-30T21:00:00Z",
        "2026-02-27T21:00:00Z",
        "2026-03-31T20:00:00Z",
    ]
    portfolio = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 102.0, 105.0])
    ]
    benchmark = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 101.0, 103.0])
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="USStock:SPY",
        frequency="monthly",
        annualization_factor=12.0,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["observations"] == 2
    assert result["frequency"] == "1mo"
    assert result["annualizationFactor"] == 12.0
    assert result["informationRatio"] is not None


def test_metric_rejects_missing_monthly_periods_across_gap():
    times = [
        "2026-01-31T00:00:00Z",
        "2026-02-28T00:00:00Z",
        # March is missing
        "2026-04-30T00:00:00Z",
    ]
    portfolio = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 102.0, 103.0])
    ]
    benchmark = [
        {"time": time, "value": value}
        for time, value in zip(times, [100.0, 101.0, 101.5])
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="Crypto:BTC/USDT",
        frequency="1mo",
        annualization_factor=12.0,
        market="Crypto",
    )

    assert result["status"] == "insufficient_history"
    assert result["observations"] == 1


def test_metric_rejects_missing_month_when_gap_is_exactly_thirty_days():
    portfolio = [
        {"time": "2026-01-31T00:00:00Z", "value": 100.0},
        {"time": "2026-03-02T00:00:00Z", "value": 101.0},
    ]
    benchmark = [
        {"time": "2026-01-31T00:00:00Z", "value": 100.0},
        {"time": "2026-03-02T00:00:00Z", "value": 100.5},
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="Crypto:BTC/USDT",
        frequency="1mo",
        annualization_factor=12.0,
        market="Crypto",
    )

    assert result["status"] == "insufficient_history"
    assert result["observations"] == 0


def test_metric_rejects_same_month_when_gap_is_exactly_thirty_days():
    portfolio = [
        {"time": "2026-01-01T00:00:00Z", "value": 100.0},
        {"time": "2026-01-31T00:00:00Z", "value": 101.0},
    ]
    benchmark = [
        {"time": "2026-01-01T00:00:00Z", "value": 100.0},
        {"time": "2026-01-31T00:00:00Z", "value": 100.5},
    ]

    result = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="Crypto:BTC/USDT",
        frequency="1mo",
        annualization_factor=12.0,
        market="Crypto",
    )

    assert result["status"] == "insufficient_history"
    assert result["observations"] == 0


def test_metric_normalizes_monthly_aliases():
    portfolio = [
        {"time": "2026-01-31T00:00:00Z", "value": 100.0},
        {"time": "2026-02-28T00:00:00Z", "value": 101.0},
    ]
    benchmark = [
        {"time": "2026-01-31T00:00:00Z", "value": 100.0},
        {"time": "2026-02-28T00:00:00Z", "value": 100.5},
    ]

    for alias in ("1mo", "1month", "monthly", "month"):
        result = calculate_information_ratio(
            portfolio,
            benchmark,
            benchmark="Crypto:BTC/USDT",
            frequency=alias,
            annualization_factor=12.0,
            market="Crypto",
        )
        assert result["frequency"] == "1mo"



def test_calculate_beta_and_alpha_known_values():
    result = calculate_beta_and_alpha(
        _curve([0.02, 0.04, -0.01, 0.03]),
        _curve([0.01, 0.02, -0.005, 0.015]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252.0,
        risk_free_rate=0.0,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["observations"] == 4
    assert result["frequency"] == "1d"
    assert result["annualizationFactor"] == 252.0
    assert result["portfolioReturnAnnualized"] == pytest.approx(5.04)
    assert result["benchmarkReturnAnnualized"] == pytest.approx(2.52)
    assert result["beta"] == pytest.approx(2.0)
    assert result["alphaAnnualized"] == pytest.approx(0.0)
    assert result["treynorRatio"] == pytest.approx(2.52)
    assert result["treynorStatus"] == "available"
    assert result["correlation"] == pytest.approx(1.0)
    assert result["rSquared"] == pytest.approx(1.0)
    assert result["correlationStatus"] == "available"


def test_calculate_beta_and_alpha_with_positive_risk_free_rate():
    result = calculate_beta_and_alpha(
        _curve([0.02, 0.04, -0.01, 0.03]),
        _curve([0.01, 0.02, -0.005, 0.015]),
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252.0,
        risk_free_rate=0.04,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["riskFreeRate"] == pytest.approx(0.04)
    assert result["beta"] == pytest.approx(2.0)
    # alpha = (5.04 - 0.04) - 2.0 * (2.52 - 0.04) = 5.00 - 4.96 = 0.04
    assert result["alphaAnnualized"] == pytest.approx(0.04)
    # treynor = (5.04 - 0.04) / 2.0 = 2.50
    assert result["treynorRatio"] == pytest.approx(2.50)


def test_calculate_capture_ratios_known_values():
    result = calculate_capture_ratios(
        _curve([0.06, -0.02, 0.04, -0.01]),
        _curve([0.05, -0.04, 0.03, -0.02]),
        benchmark="USStock:SPY",
        frequency="1d",
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["observations"] == 4
    assert result["upMarketPeriods"] == 2
    assert result["downMarketPeriods"] == 2
    # Capture uses geometric-mean returns within each market regime.
    expected_up_capture = ((1.06 * 1.04) ** 0.5 - 1.0) / ((1.05 * 1.03) ** 0.5 - 1.0)
    assert result["upCapture"] == pytest.approx(expected_up_capture)
    expected_down_capture = ((0.98 * 0.99) ** 0.5 - 1.0) / ((0.96 * 0.98) ** 0.5 - 1.0)
    assert result["downCapture"] == pytest.approx(expected_down_capture)
    assert result["captureRatio"] == pytest.approx(expected_up_capture / expected_down_capture)


def test_capture_ratios_only_up_market():
    result = calculate_capture_ratios(
        _curve([0.02, 0.03, 0.01]),
        _curve([0.01, 0.02, 0.01]),
        frequency="1d",
        market="USStock",
    )

    assert result["status"] == "partial"
    assert result["upMarketPeriods"] == 3
    assert result["downMarketPeriods"] == 0
    assert result["upCapture"] is not None
    assert result["downCapture"] is None
    assert result["captureRatio"] is None


def test_capture_ratios_only_down_market():
    result = calculate_capture_ratios(
        _curve([-0.01, -0.02, -0.005]),
        _curve([-0.02, -0.03, -0.01]),
        frequency="1d",
        market="USStock",
    )

    assert result["status"] == "partial"
    assert result["upMarketPeriods"] == 0
    assert result["downMarketPeriods"] == 3
    assert result["upCapture"] is None
    assert result["downCapture"] is not None
    assert result["captureRatio"] is None


def test_zero_benchmark_variance():
    result = calculate_beta_and_alpha(
        _curve([0.01, 0.02, 0.03]),
        _curve([0.0, 0.0, 0.0]),
        frequency="1d",
        annualization_factor=252.0,
        market="USStock",
    )

    assert result["status"] == "zero_benchmark_variance"
    assert result["correlationStatus"] == "undefined_constant_benchmark"
    assert result["treynorStatus"] == "zero_benchmark_variance"
    assert result["beta"] is None
    assert result["alphaAnnualized"] is None
    assert result["treynorRatio"] is None


def test_zero_beta_treynor_is_none():
    result = calculate_beta_and_alpha(
        _curve([0.02, 0.02, -0.02, -0.02]),
        _curve([0.01, -0.01, 0.01, -0.01]),
        frequency="1d",
        annualization_factor=252.0,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["beta"] == pytest.approx(0.0, abs=1e-12)
    assert result["treynorRatio"] is None
    assert result["treynorStatus"] == "zero_beta"


def test_constant_portfolio_leaves_correlation_undefined():
    result = calculate_beta_and_alpha(
        _curve([0.0, 0.0, 0.0]),
        _curve([0.01, -0.02, 0.03]),
        frequency="1d",
        annualization_factor=252.0,
        market="Crypto",
    )

    assert result["status"] == "available"
    assert result["beta"] == pytest.approx(0.0)
    assert result["correlationStatus"] == "undefined_constant_portfolio"
    assert result["correlation"] is None
    assert result["rSquared"] is None


def test_flat_benchmark_does_not_suppress_information_ratio():
    portfolio = _curve([0.01, -0.02, 0.03])
    benchmark = _curve([0.0, 0.0, 0.0])

    information = calculate_information_ratio(
        portfolio,
        benchmark,
        benchmark="flat",
        frequency="1d",
        annualization_factor=365.25,
        market="Crypto",
    )
    combined = calculate_benchmark_relative_metrics(
        portfolio,
        benchmark,
        benchmark="flat",
        frequency="1d",
        annualization_factor=365.25,
        market="Crypto",
    )

    assert combined["status"] == information["status"] == "available"
    assert combined["informationRatio"] == pytest.approx(information["informationRatio"])
    assert combined["classification"] == information["classification"]
    assert combined["betaStatus"] == "zero_benchmark_variance"
    assert combined["correlationStatus"] == "undefined_constant_benchmark"
    assert combined["beta"] is None


def test_benchmark_metrics_insufficient_history():
    result_zero = calculate_benchmark_relative_metrics(
        [],
        [],
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252.0,
        market="USStock",
    )
    assert result_zero["status"] == "insufficient_history"
    assert result_zero["observations"] == 0
    assert result_zero["portfolioReturnAnnualized"] is None
    assert result_zero["beta"] is None
    assert result_zero["riskFreeRateSource"] == "default_zero"

    result_one = calculate_benchmark_relative_metrics(
        [{"time": "2026-01-01T00:00:00Z", "value": 100.0}, {"time": "2026-01-02T00:00:00Z", "value": 102.0}],
        [{"time": "2026-01-01T00:00:00Z", "value": 100.0}, {"time": "2026-01-02T00:00:00Z", "value": 101.0}],
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252.0,
        market="USStock",
    )
    assert result_one["status"] == "insufficient_history"
    assert result_one["observations"] == 1
    assert result_one["portfolioReturnAnnualized"] == pytest.approx(252.0 * 0.02)
    assert result_one["benchmarkReturnAnnualized"] == pytest.approx(252.0 * 0.01)
    assert result_one["beta"] is None
    assert result_one["informationRatio"] is None


def test_benchmark_metrics_input_validation():
    with pytest.raises(ValueError, match="annualization_factor"):
        calculate_benchmark_relative_metrics(
            [], [], benchmark="USStock:SPY", frequency="1d", annualization_factor=-1.0
        )

    with pytest.raises(ValueError, match="annualization_factor"):
        calculate_benchmark_relative_metrics(
            [], [], benchmark="USStock:SPY", frequency="1d", annualization_factor=float("nan")
        )

    with pytest.raises(ValueError, match="risk_free_rate"):
        calculate_benchmark_relative_metrics(
            [], [], benchmark="USStock:SPY", frequency="1d", annualization_factor=252.0, risk_free_rate=float("nan")
        )


def test_calculate_benchmark_relative_metrics_full_integration():
    portfolio = _curve([0.01, 0.02, -0.01, 0.005])
    benchmark = _curve([0.005, 0.01, -0.005, 0.002])

    result = calculate_benchmark_relative_metrics(
        portfolio,
        benchmark,
        benchmark="USStock:SPY",
        frequency="1d",
        annualization_factor=252.0,
        risk_free_rate=0.02,
        market="USStock",
    )

    assert result["status"] == "available"
    assert result["benchmark"] == "USStock:SPY"
    assert result["frequency"] == "1d"
    assert result["annualizationFactor"] == 252.0
    assert result["observations"] == 4
    assert result["portfolioReturnAnnualized"] == pytest.approx(1.575)
    assert result["benchmarkReturnAnnualized"] == pytest.approx(0.756)
    assert result["activeReturnAnnualized"] == pytest.approx(0.819)
    assert result["informationRatio"] == pytest.approx(8.270196225621152)
    assert result["classification"] == "exceptional"
    assert result["riskFreeRate"] == pytest.approx(0.02)
    assert result["riskFreeRateSource"] == "configured"
    assert result["betaStatus"] == "available"
    assert result["correlationStatus"] == "available"
    assert result["treynorStatus"] == "available"
    assert result["captureStatus"] == "available"
    assert result["beta"] == pytest.approx(1.991525423728826)
    assert result["alphaAnnualized"] == pytest.approx(0.08923728813558407)
    assert result["treynorRatio"] == pytest.approx(0.7808085106382866)
    assert result["correlation"] == pytest.approx(0.99920872649114)
    assert result["rSquared"] == pytest.approx(0.9984180790960459)
    assert result["upMarketPeriods"] == 3
    assert result["downMarketPeriods"] == 1
    expected_up_capture = (
        (1.01 * 1.02 * 1.005) ** (1.0 / 3.0) - 1.0
    ) / (
        (1.005 * 1.01 * 1.002) ** (1.0 / 3.0) - 1.0
    )
    assert result["upCapture"] == pytest.approx(expected_up_capture)
    assert result["downCapture"] == pytest.approx(2.0)
    assert result["captureRatio"] == pytest.approx(expected_up_capture / 2.0)


def test_calculate_benchmark_relative_metrics_zero_tracking_error():
    curve = _curve([0.01, 0.02, -0.005, 0.015])
    result = calculate_benchmark_relative_metrics(
        curve,
        curve,
        benchmark="Crypto:BTC/USDT",
        frequency="1d",
        annualization_factor=365.25,
        market="Crypto",
    )

    assert result["status"] == "zero_tracking_error"
    assert result["trackingErrorAnnualized"] == pytest.approx(0.0, abs=1e-12)
    assert result["informationRatio"] is None
    assert result["beta"] == pytest.approx(1.0)
    assert result["alphaAnnualized"] == pytest.approx(0.0, abs=1e-12)
    assert result["correlation"] == pytest.approx(1.0)
    assert result["rSquared"] == pytest.approx(1.0)
    assert result["upCapture"] == pytest.approx(1.0)
    assert result["downCapture"] == pytest.approx(1.0)
    assert result["captureRatio"] == pytest.approx(1.0)
