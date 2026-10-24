import math

import pandas as pd
import pytest

from app.services.backtest.metrics import (
    benchmark_level_curve,
    calculate_benchmark_relative_metrics,
    calculate_beta_and_alpha,
    calculate_calmar_ratio,
    calculate_capture_ratios,
    calculate_downside_risk,
    calculate_drawdown_recovery,
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



def test_calculate_downside_risk_known_values():
    returns = [0.03, -0.02, 0.04, -0.01]
    # Excess under MAR = 0.0:
    # negative returns: [-0.02, -0.01]
    # semi_variance = ((-0.02)^2 + (-0.01)^2) / 4 = (0.0004 + 0.0001) / 4 = 0.000125
    # downside_dev = sqrt(0.000125) = 0.01118033988749895
    # mean return = (0.03 - 0.02 + 0.04 - 0.01) / 4 = 0.04 / 4 = 0.01
    # Sortino = (0.01 / 0.01118033988749895) * sqrt(252) = 0.8944271909999159 * 15.874507866387544 = 14.198591478023193
    result = calculate_downside_risk(returns, periods_per_year=252.0, mar=0.0)

    expected_downside_dev = math.sqrt((0.0004 + 0.0001) / 4.0)
    expected_sortino = (0.01 / expected_downside_dev) * math.sqrt(252.0)

    assert result["observations"] == 4
    assert result["downsideObservations"] == 2
    assert result["downsideDeviation"] == pytest.approx(expected_downside_dev)
    assert result["annualizedDownsideDeviation"] == pytest.approx(expected_downside_dev * math.sqrt(252.0) * 100.0)
    assert result["sortinoRatio"] == pytest.approx(expected_sortino)


def test_calculate_downside_risk_strictly_positive_returns_sortino_none():
    returns = [0.01, 0.02, 0.015, 0.005]
    result = calculate_downside_risk(returns, periods_per_year=252.0, mar=0.0)

    assert result["observations"] == 4
    assert result["downsideObservations"] == 0
    assert result["downsideDeviation"] == 0.0
    assert result["annualizedDownsideDeviation"] == 0.0
    assert result["sortinoRatio"] is None


def test_calculate_downside_risk_strictly_negative_returns():
    returns = [-0.01, -0.02, -0.03]
    result = calculate_downside_risk(returns, periods_per_year=252.0, mar=0.0)

    expected_dev = math.sqrt((0.0001 + 0.0004 + 0.0009) / 3.0)
    expected_mean = -0.02
    expected_sortino = (expected_mean / expected_dev) * math.sqrt(252.0)

    assert result["observations"] == 3
    assert result["downsideObservations"] == 3
    assert result["downsideDeviation"] == pytest.approx(expected_dev)
    assert result["sortinoRatio"] == pytest.approx(expected_sortino)


def test_calculate_downside_risk_custom_mar_and_validation():
    returns = [0.02, 0.01, 0.03]
    # Under MAR = 0.015: excess is [0.005, -0.005, 0.015]
    # downside excess is [-0.005]
    result = calculate_downside_risk(returns, periods_per_year=252.0, mar=0.015)

    assert result["downsideObservations"] == 1
    expected_dev = math.sqrt((-0.005) ** 2 / 3.0)
    assert result["downsideDeviation"] == pytest.approx(expected_dev)

    with pytest.raises(ValueError, match="periods_per_year"):
        calculate_downside_risk(returns, periods_per_year=-1.0)

    with pytest.raises(ValueError, match="mar"):
        calculate_downside_risk(returns, mar=float("nan"))

    empty_res = calculate_downside_risk([])
    assert empty_res["observations"] == 0
    assert empty_res["downsideDeviation"] == 0.0
    assert empty_res["sortinoRatio"] == 0.0


def test_calculate_calmar_ratio_known_values_and_edge_cases():
    # 25% annualized return with 10% max drawdown
    assert calculate_calmar_ratio(25.0, -10.0) == pytest.approx(2.5)
    assert calculate_calmar_ratio(25.0, 10.0) == pytest.approx(2.5)

    # Zero drawdown with positive return -> None
    assert calculate_calmar_ratio(15.0, 0.0) is None

    # Zero drawdown with zero or negative return -> 0.0
    assert calculate_calmar_ratio(0.0, 0.0) == pytest.approx(0.0)
    assert calculate_calmar_ratio(-5.0, 0.0) == pytest.approx(0.0)

    # Negative return with drawdown -> negative Calmar
    assert calculate_calmar_ratio(-15.0, -10.0) == pytest.approx(-1.5)

    # Unavailable return -> None
    assert calculate_calmar_ratio(25.0, -10.0, available=False) is None
    assert calculate_calmar_ratio(float("nan"), -10.0) is None


def test_calculate_drawdown_recovery_multi_episode_lifecycle():
    # Curve structure:
    # 2026-01-01: 100.0 (initial peak)
    # 2026-01-02: 120.0 (new peak)
    # 2026-01-03: 90.0  (deep drawdown: (90/120 - 1)*100 = -25.0%)
    # 2026-01-04: 125.0 (recovery of Episode 1! Duration: 2 days from 01-02 to 01-04)
    # 2026-01-05: 110.0 (Episode 2 drawdown: (110/125 - 1)*100 = -12.0%)
    # 2026-01-06: 105.0 (deeper in Episode 2)
    # 2026-01-08: 130.0 (recovery of Episode 2! Duration: 4 days from 01-04 to 01-08)
    curve = [
        {"time": "2026-01-01T00:00:00Z", "value": 100.0},
        {"time": "2026-01-02T00:00:00Z", "value": 120.0},
        {"time": "2026-01-03T00:00:00Z", "value": 90.0},
        {"time": "2026-01-04T00:00:00Z", "value": 125.0},
        {"time": "2026-01-05T00:00:00Z", "value": 110.0},
        {"time": "2026-01-06T00:00:00Z", "value": 105.0},
        {"time": "2026-01-08T00:00:00Z", "value": 130.0},
    ]

    result = calculate_drawdown_recovery(curve, initial_capital=100.0)

    # Maximum drawdown duration across all episodes: Episode 2 took 4.0 days
    assert result["maxDrawdownDurationDays"] == pytest.approx(4.0)

    # Deepest drawdown was Episode 1 (-25.0%), which started at 01-02 and recovered at 01-04 (2.0 days)
    assert result["maxDrawdownRecovered"] is True
    assert result["maxDrawdownRecoveryTime"] == "2026-01-04T00:00:00Z"
    assert result["maxDrawdownRecoveryDays"] == pytest.approx(2.0)

    # Recovery factor = (final - initial) / (peak - trough) = (130 - 100) / (120 - 90) = 30 / 30 = 1.0
    assert result["recoveryFactor"] == pytest.approx(1.0)


def test_calculate_drawdown_recovery_unrecovered_at_end():
    # Deepest drawdown occurs and does not recover before end
    curve = [
        {"time": "2026-01-01", "value": 100.0},
        {"time": "2026-01-02", "value": 110.0},
        {"time": "2026-01-03", "value": 88.0},
        {"time": "2026-01-05", "value": 95.0},
    ]

    result = calculate_drawdown_recovery(curve, initial_capital=100.0)

    assert result["maxDrawdownRecovered"] is False
    assert result["maxDrawdownRecoveryTime"] == ""
    # Days from peak (01-02) to end of backtest (01-05): 3 days
    assert result["maxDrawdownRecoveryDays"] == pytest.approx(3.0)
    assert result["maxDrawdownDurationDays"] == pytest.approx(3.0)
    # Recovery factor: (95 - 100) / (110 - 88) = -5 / 22 = -0.22727
    assert result["recoveryFactor"] == pytest.approx(-5.0 / 22.0)


def test_calculate_drawdown_recovery_strictly_increasing_and_empty():
    curve = [
        {"time": "2026-01-01", "value": 100.0},
        {"time": "2026-01-02", "value": 105.0},
        {"time": "2026-01-03", "value": 110.0},
    ]
    result = calculate_drawdown_recovery(curve, initial_capital=100.0)

    assert result["maxDrawdownDurationDays"] == 0.0
    assert result["maxDrawdownRecoveryDays"] == 0.0
    assert result["maxDrawdownRecoveryTime"] == ""
    assert result["maxDrawdownRecovered"] is True
    assert result["recoveryFactor"] is None

    empty_res = calculate_drawdown_recovery([])
    assert empty_res["maxDrawdownDurationDays"] == 0.0
    assert empty_res["maxDrawdownRecovered"] is True
