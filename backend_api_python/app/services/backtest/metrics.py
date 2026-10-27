"""Benchmark-relative performance metrics for backtest result curves."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from app.services.market_schedule import CALENDAR_BY_MARKET, equity_bar_session_date

__all__ = [
    "DEFAULT_INFORMATION_RATIO_BANDS",
    "benchmark_level_curve",
    "calculate_batting_average",
    "calculate_benchmark_relative_metrics",
    "calculate_beta_and_alpha",
    "calculate_calmar_ratio",
    "calculate_capture_ratios",
    "calculate_downside_risk",
    "calculate_drawdown_recovery",
    "calculate_dual_beta",
    "calculate_information_ratio",
    "calculate_m_squared",
    "calculate_omega_ratio",
    "calculate_tail_risk",
]


_FREQUENCY_SECONDS = {
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


DEFAULT_INFORMATION_RATIO_BANDS: tuple[tuple[float, str], ...] = (
    (0.30, "weak"),
    (0.50, "acceptable"),
    (1.00, "good"),
    (math.inf, "exceptional"),
)


def _align_curves_and_returns(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    frequency: str,
    market: str = "",
) -> tuple[pd.DataFrame, str]:
    """Align portfolio and benchmark curves to native benchmark timestamps.

    Returns the aligned periodic returns DataFrame with columns 'portfolio' and
    'benchmark', along with the normalized frequency. Non-continuous gaps and
    non-consecutive equity sessions are excluded.
    """
    normalized_frequency = _normalize_frequency(frequency)
    levels = pd.concat(
        (
            _curve_levels(portfolio_curve).rename("portfolio"),
            _curve_levels(benchmark_curve).rename("benchmark"),
        ),
        axis=1,
        join="inner",
    ).dropna()
    returns = levels.pct_change(fill_method=None).dropna()
    if not returns.empty:
        valid_intervals = _valid_interval_mask(levels.index, normalized_frequency, market)
        returns = returns.loc[valid_intervals]
        returns = returns.loc[
            returns.apply(lambda row: all(math.isfinite(float(value)) for value in row), axis=1)
        ]
    return returns, normalized_frequency


def calculate_information_ratio(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None,
    frequency: str,
    annualization_factor: float,
    market: str = "",
    classification_bands: Sequence[tuple[float, str]] = DEFAULT_INFORMATION_RATIO_BANDS,
) -> dict[str, Any]:
    """Calculate an Information Ratio from levels observed at common timestamps.

    The portfolio is sampled only at native benchmark observations before
    returns are calculated. This keeps every active return on one common
    interval when the benchmark is intentionally fetched at a coarser
    frequency than the strategy. Gaps that are not valid consecutive market
    periods are excluded instead of being annualized as a single period.
    """
    factor = float(annualization_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("annualization_factor must be a positive finite number")

    bands = _validate_classification_bands(classification_bands)
    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )

    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "portfolioReturnAnnualized": None,
        "benchmarkReturnAnnualized": None,
        "activeReturnAnnualized": None,
        "trackingErrorAnnualized": None,
        "informationRatio": None,
        "classification": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
        "annualizationFactor": factor,
    }
    if observations == 0:
        return result

    active_returns = returns["portfolio"] - returns["benchmark"]
    portfolio_annualized = float(returns["portfolio"].mean()) * factor
    benchmark_annualized = float(returns["benchmark"].mean()) * factor
    active_annualized = float(active_returns.mean()) * factor
    result.update({
        "portfolioReturnAnnualized": portfolio_annualized,
        "benchmarkReturnAnnualized": benchmark_annualized,
        "activeReturnAnnualized": active_annualized,
    })
    if observations < 2:
        return result

    periodic_tracking_error = float(active_returns.std(ddof=1))
    tracking_error_annualized = periodic_tracking_error * math.sqrt(factor)
    result["trackingErrorAnnualized"] = tracking_error_annualized
    if math.isclose(periodic_tracking_error, 0.0, rel_tol=0.0, abs_tol=1e-15):
        result["status"] = "zero_tracking_error"
        return result

    information_ratio = active_annualized / tracking_error_annualized
    result.update({
        "status": "available",
        "informationRatio": information_ratio,
        "classification": _classify_information_ratio(information_ratio, bands),
    })
    return result


def calculate_beta_and_alpha(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None = None,
    frequency: str = "1d",
    annualization_factor: float = 252.0,
    risk_free_rate: float = 0.0,
    market: str = "",
) -> dict[str, Any]:
    """Calculate Beta, Jensen's Alpha, Treynor Ratio, Correlation, and R-squared.

    Beta measures systematic risk relative to the benchmark:
    beta = Cov(R_p, R_b) / Var(R_b).
    Jensen's Alpha measures the annualized risk-adjusted excess return over CAPM:
    alpha_ann = (R_p_ann - R_f) - beta * (R_b_ann - R_f).
    Treynor Ratio measures excess return per unit of systematic risk:
    (R_p_ann - R_f) / beta.
    """
    factor = float(annualization_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("annualization_factor must be a positive finite number")

    rf = float(risk_free_rate)
    if not math.isfinite(rf):
        raise ValueError("risk_free_rate must be a finite number")

    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )
    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "correlationStatus": "insufficient_history",
        "treynorStatus": "insufficient_history",
        "portfolioReturnAnnualized": None,
        "benchmarkReturnAnnualized": None,
        "riskFreeRate": rf,
        "beta": None,
        "alphaAnnualized": None,
        "treynorRatio": None,
        "correlation": None,
        "rSquared": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
        "annualizationFactor": factor,
    }
    if observations == 0:
        return result

    portfolio_annualized = float(returns["portfolio"].mean()) * factor
    benchmark_annualized = float(returns["benchmark"].mean()) * factor
    result.update({
        "portfolioReturnAnnualized": portfolio_annualized,
        "benchmarkReturnAnnualized": benchmark_annualized,
    })
    if observations < 2:
        return result

    systematic = _calculate_systematic_risk_metrics(
        returns,
        annualization_factor=factor,
        risk_free_rate=rf,
    )
    result.update(systematic)
    result["status"] = systematic["betaStatus"]
    result.pop("betaStatus", None)
    return result


def calculate_capture_ratios(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None = None,
    frequency: str = "1d",
    market: str = "",
) -> dict[str, Any]:
    """Calculate Up-Market Capture, Down-Market Capture, and Capture Ratio.

    Up-market periods are intervals where the benchmark return is strictly positive (> 0).
    Down-market periods are intervals where the benchmark return is strictly negative (< 0).
    Capture ratios compare the geometric mean portfolio and benchmark returns
    within each regime. Results use ratio units, where 1.0 means 100% capture:
    Up Capture = GeoMean(R_p_up) / GeoMean(R_b_up)
    Down Capture = GeoMean(R_p_down) / GeoMean(R_b_down)
    Capture Ratio = Up Capture / Down Capture.
    """
    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )
    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "upCapture": None,
        "downCapture": None,
        "captureRatio": None,
        "upMarketPeriods": 0,
        "downMarketPeriods": 0,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
    }
    capture = _calculate_capture_metrics(returns)
    result.update(capture)
    result["status"] = capture["captureStatus"]
    result.pop("captureStatus", None)
    return result


def calculate_benchmark_relative_metrics(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None,
    frequency: str,
    annualization_factor: float,
    risk_free_rate: float = 0.0,
    market: str = "",
    classification_bands: Sequence[tuple[float, str]] = DEFAULT_INFORMATION_RATIO_BANDS,
) -> dict[str, Any]:
    """Calculate the complete suite of benchmark-relative performance metrics.

    Includes:
    - Information Ratio, Active Return, Tracking Error, and Classification
    - Beta, Jensen's Alpha, and Treynor Ratio
    - Correlation and R-Squared
    - Up-Market Capture, Down-Market Capture, and Capture Ratio
    """
    factor = float(annualization_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("annualization_factor must be a positive finite number")

    rf = float(risk_free_rate)
    if not math.isfinite(rf):
        raise ValueError("risk_free_rate must be a finite number")

    bands = _validate_classification_bands(classification_bands)
    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )

    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "portfolioReturnAnnualized": None,
        "benchmarkReturnAnnualized": None,
        "activeReturnAnnualized": None,
        "trackingErrorAnnualized": None,
        "informationRatio": None,
        "classification": None,
        "riskFreeRate": rf,
        "riskFreeRateSource": "configured" if rf else "default_zero",
        "betaStatus": "insufficient_history",
        "correlationStatus": "insufficient_history",
        "treynorStatus": "insufficient_history",
        "captureStatus": "insufficient_history",
        "mSquaredStatus": "insufficient_history",
        "dualBetaStatus": "insufficient_history",
        "battingStatus": "insufficient_history",
        "beta": None,
        "alphaAnnualized": None,
        "treynorRatio": None,
        "correlation": None,
        "rSquared": None,
        "mSquared": None,
        "mSquaredAlpha": None,
        "upCapture": None,
        "downCapture": None,
        "captureRatio": None,
        "upMarketPeriods": 0,
        "downMarketPeriods": 0,
        "upBeta": None,
        "downBeta": None,
        "betaSpread": None,
        "battingAverage": None,
        "outperformancePeriods": 0,
        "underperformancePeriods": 0,
        "outperformancePayoffRatio": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
        "annualizationFactor": factor,
    }
    if observations == 0:
        return result

    active_returns = returns["portfolio"] - returns["benchmark"]
    portfolio_annualized = float(returns["portfolio"].mean()) * factor
    benchmark_annualized = float(returns["benchmark"].mean()) * factor
    active_annualized = float(active_returns.mean()) * factor
    result.update({
        "portfolioReturnAnnualized": portfolio_annualized,
        "benchmarkReturnAnnualized": benchmark_annualized,
        "activeReturnAnnualized": active_annualized,
    })

    result.update(_calculate_capture_metrics(returns))
    result.update(_calculate_dual_beta_metrics(returns))
    result.update(_calculate_batting_metrics(returns))

    if observations < 2:
        return result

    # Tracking error
    periodic_tracking_error = float(active_returns.std(ddof=1))
    tracking_error_annualized = periodic_tracking_error * math.sqrt(factor)
    result["trackingErrorAnnualized"] = tracking_error_annualized

    result.update(_calculate_systematic_risk_metrics(
        returns,
        annualization_factor=factor,
        risk_free_rate=rf,
    ))
    result.update(_calculate_m_squared_metrics(
        returns,
        annualization_factor=factor,
        risk_free_rate=rf,
    ))

    if math.isclose(periodic_tracking_error, 0.0, rel_tol=0.0, abs_tol=1e-15):
        result["status"] = "zero_tracking_error"
        return result

    information_ratio = active_annualized / tracking_error_annualized
    result.update({
        "status": "available",
        "informationRatio": information_ratio,
        "classification": _classify_information_ratio(information_ratio, bands),
    })
    return result


def _calculate_systematic_risk_metrics(
    returns: pd.DataFrame,
    *,
    annualization_factor: float,
    risk_free_rate: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "betaStatus": "insufficient_history",
        "correlationStatus": "insufficient_history",
        "treynorStatus": "insufficient_history",
        "beta": None,
        "alphaAnnualized": None,
        "treynorRatio": None,
        "correlation": None,
        "rSquared": None,
    }
    if len(returns.index) < 2:
        return result

    bench_var = max(0.0, float(returns["benchmark"].var(ddof=1)))
    port_var = max(0.0, float(returns["portfolio"].var(ddof=1)))
    covar = float(returns["portfolio"].cov(returns["benchmark"]))
    if bench_var <= 1e-15:
        result.update({
            "betaStatus": "zero_benchmark_variance",
            "correlationStatus": "undefined_constant_benchmark",
            "treynorStatus": "zero_benchmark_variance",
        })
        return result

    portfolio_annualized = float(returns["portfolio"].mean()) * annualization_factor
    benchmark_annualized = float(returns["benchmark"].mean()) * annualization_factor
    beta = covar / bench_var
    alpha_annualized = (
        (portfolio_annualized - risk_free_rate)
        - beta * (benchmark_annualized - risk_free_rate)
    )
    treynor_ratio = None
    treynor_status = "zero_beta"
    if abs(beta) > 1e-15:
        treynor_ratio = (portfolio_annualized - risk_free_rate) / beta
        treynor_status = "available"

    result.update({
        "betaStatus": "available",
        "treynorStatus": treynor_status,
        "beta": beta,
        "alphaAnnualized": alpha_annualized,
        "treynorRatio": treynor_ratio,
    })
    if port_var <= 1e-15:
        result["correlationStatus"] = "undefined_constant_portfolio"
        return result

    raw_corr = covar / math.sqrt(port_var * bench_var)
    correlation = max(-1.0, min(1.0, raw_corr))
    result.update({
        "correlationStatus": "available",
        "correlation": correlation,
        "rSquared": max(0.0, min(1.0, correlation**2)),
    })
    return result


def _calculate_capture_metrics(returns: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "captureStatus": "insufficient_history",
        "upCapture": None,
        "downCapture": None,
        "captureRatio": None,
        "upMarketPeriods": 0,
        "downMarketPeriods": 0,
    }
    if returns.empty:
        return result

    up_returns = returns.loc[returns["benchmark"] > 0]
    down_returns = returns.loc[returns["benchmark"] < 0]
    result["upMarketPeriods"] = int(len(up_returns.index))
    result["downMarketPeriods"] = int(len(down_returns.index))
    up_capture = _capture_ratio(up_returns)
    down_capture = _capture_ratio(down_returns)
    capture_ratio = None
    if up_capture is not None and down_capture is not None and abs(down_capture) > 1e-15:
        capture_ratio = up_capture / down_capture

    if up_capture is not None and down_capture is not None:
        status = "available"
    elif up_capture is not None or down_capture is not None:
        status = "partial"
    else:
        status = "insufficient_regimes"
    result.update({
        "captureStatus": status,
        "upCapture": up_capture,
        "downCapture": down_capture,
        "captureRatio": capture_ratio,
    })
    return result


def _capture_ratio(regime_returns: pd.DataFrame) -> float | None:
    if regime_returns.empty:
        return None
    observations = len(regime_returns.index)
    benchmark_return = float((1.0 + regime_returns["benchmark"]).prod()) ** (1.0 / observations) - 1.0
    if abs(benchmark_return) <= 1e-15:
        return None
    portfolio_return = float((1.0 + regime_returns["portfolio"]).prod()) ** (1.0 / observations) - 1.0
    return portfolio_return / benchmark_return


def benchmark_level_curve(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Return valid native-frequency benchmark levels without forward filling."""
    if frame is None or frame.empty or "close" not in frame.columns:
        return []
    values = pd.to_numeric(frame["close"], errors="coerce")
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce", utc=True))
    points: list[dict[str, Any]] = []
    for timestamp, value in zip(index, values):
        if pd.isna(timestamp) or pd.isna(value):
            continue
        numeric_value = float(value)
        if not math.isfinite(numeric_value) or numeric_value <= 0:
            continue
        points.append({"time": timestamp.isoformat().replace("+00:00", "Z"), "value": numeric_value})
    return points


def _curve_levels(curve: Iterable[dict[str, Any]]) -> pd.Series:
    rows = []
    for point in curve:
        timestamp = pd.to_datetime(point.get("time"), errors="coerce", utc=True)
        value = pd.to_numeric(point.get("value"), errors="coerce")
        if pd.isna(timestamp) or pd.isna(value):
            continue
        numeric_value = float(value)
        if not math.isfinite(numeric_value) or numeric_value <= 0:
            continue
        rows.append((timestamp, numeric_value))
    if not rows:
        return pd.Series(dtype="float64")
    levels = pd.Series(
        (value for _, value in rows),
        index=pd.DatetimeIndex(timestamp for timestamp, _ in rows),
        dtype="float64",
    )
    return levels.loc[~levels.index.duplicated(keep="last")].sort_index()


def _valid_interval_mask(index: pd.DatetimeIndex, frequency: str, market: str) -> pd.Series:
    if len(index) < 2:
        return pd.Series(dtype="bool")
    expected = pd.Timedelta(seconds=_FREQUENCY_SECONDS.get(frequency, 86_400))
    valid: list[bool] = []
    for start, end in zip(index[:-1], index[1:]):
        delta = end - start
        if frequency == "1mo":
            start_date = (
                equity_bar_session_date(start, market)
                if market and market in CALENDAR_BY_MARKET
                else start.date()
            )
            end_date = (
                equity_bar_session_date(end, market)
                if market and market in CALENDAR_BY_MARKET
                else end.date()
            )
            months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
            valid.append(months == 1 and pd.Timedelta(days=20) <= delta <= pd.Timedelta(days=40))
            continue
        if delta == expected:
            valid.append(True)
            continue
        if str(market or "").strip() in {"Crypto", "Cryptocurrency"}:
            valid.append(False)
            continue
        if frequency == "1w":
            valid.append(pd.Timedelta(days=4) <= delta <= pd.Timedelta(days=10))
            continue
        valid.append(_are_consecutive_equity_sessions(start, end, market))
    return pd.Series(valid, index=index[1:], dtype="bool")


def _are_consecutive_equity_sessions(start: pd.Timestamp, end: pd.Timestamp, market: str) -> bool:
    calendar_code = CALENDAR_BY_MARKET.get(str(market or "").strip())
    if not calendar_code:
        return False
    start_date = equity_bar_session_date(start, market)
    end_date = equity_bar_session_date(end, market)
    if start_date == end_date:
        return False
    try:
        import exchange_calendars

        calendar = exchange_calendars.get_calendar(calendar_code)
        sessions = calendar.sessions_in_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
    except (ImportError, ValueError):
        return False
    if len(sessions) != 2:
        return False
    return sessions[0].date() == start_date and sessions[1].date() == end_date


def _normalize_frequency(value: object) -> str:
    normalized = str(value or "1d").strip().lower()
    aliases = {
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
    }
    return aliases.get(normalized, normalized)


def _validate_classification_bands(
    classification_bands: Sequence[tuple[float, str]],
) -> tuple[tuple[float, str], ...]:
    bands = tuple((float(limit), str(label).strip()) for limit, label in classification_bands)
    if not bands or any(not label or math.isnan(limit) for limit, label in bands):
        raise ValueError("classification_bands must contain labelled upper bounds")
    if any(current <= previous for (previous, _), (current, _) in zip(bands, bands[1:])):
        raise ValueError("classification band upper bounds must be strictly increasing")
    return bands


def _classify_information_ratio(
    value: float,
    bands: Sequence[tuple[float, str]],
) -> str:
    for upper_bound, label in bands:
        if value < upper_bound:
            return label
    return bands[-1][1]



def calculate_downside_risk(
    returns: pd.Series | Iterable[float],
    *,
    periods_per_year: float = 252.0,
    mar: float = 0.0,
) -> dict[str, Any]:
    """Calculate downside risk metrics including Downside Deviation and Sortino Ratio.

    Downside deviation (Target Semi-Deviation) evaluates the volatility of returns
    falling below the Minimum Acceptable Return (MAR, default 0.0) across all T observations:
        DownsideDeviation = sqrt(sum(min(R_t - MAR, 0)^2) / T)

    Sortino Ratio evaluates annualized excess return per unit of downside risk:
        Sortino = (Mean(R_t - MAR) * sqrt(periods_per_year)) / DownsideDeviation

    Returns:
        dict containing:
            - downsideDeviation: periodic downside deviation (ddof=0)
            - annualizedDownsideDeviation: annualized downside deviation in %
            - sortinoRatio: Sortino ratio, or None if downside deviation is zero with positive excess return
            - observations: total number of return observations
            - downsideObservations: count of returns strictly below MAR
    """
    factor = float(periods_per_year)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("periods_per_year must be a positive finite number")

    target_mar = float(mar)
    if not math.isfinite(target_mar):
        raise ValueError("mar must be a finite number")

    if isinstance(returns, pd.Series):
        clean_returns = returns.dropna()
    else:
        clean_returns = pd.Series(
            [float(x) for x in returns if x is not None and math.isfinite(float(x))],
            dtype="float64",
        )

    observations = int(len(clean_returns))
    if observations == 0:
        return {
            "downsideDeviation": 0.0,
            "annualizedDownsideDeviation": 0.0,
            "sortinoRatio": 0.0,
            "observations": 0,
            "downsideObservations": 0,
        }

    excess = clean_returns - target_mar
    negative_excess = excess.loc[excess < 0.0]
    downside_count = int(len(negative_excess))

    if downside_count == 0:
        return {
            "downsideDeviation": 0.0,
            "annualizedDownsideDeviation": 0.0,
            "sortinoRatio": None if float(excess.mean()) > 0 else 0.0,
            "observations": observations,
            "downsideObservations": 0,
        }

    semi_variance = float((negative_excess**2).sum() / observations)
    downside_dev = math.sqrt(semi_variance)
    annualized_downside_dev = downside_dev * math.sqrt(factor) * 100.0

    if downside_dev > 1e-15:
        sortino = float(excess.mean() / downside_dev * math.sqrt(factor))
    elif float(excess.mean()) > 0:
        sortino = None
    else:
        sortino = 0.0

    return {
        "downsideDeviation": downside_dev,
        "annualizedDownsideDeviation": annualized_downside_dev,
        "sortinoRatio": sortino,
        "observations": observations,
        "downsideObservations": downside_count,
    }


def calculate_calmar_ratio(
    annualized_return: float,
    max_drawdown: float,
    *,
    available: bool = True,
) -> float | None:
    """Calculate the Calmar Ratio: Annualized Return / |Max Drawdown|.

    Parameters:
        annualized_return: Annualized return in percent (e.g. 25.0 for 25%).
        max_drawdown: Maximum drawdown in percent (e.g. -15.0 or 15.0).
        available: Flag indicating whether annualized return is valid.

    Returns:
        float | None: Calmar ratio, or None if drawdown is zero or return is unavailable.
    """
    if not available:
        return None
    try:
        ann_ret = float(annualized_return)
        mdd = abs(float(max_drawdown))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ann_ret) or not math.isfinite(mdd):
        return None
    if mdd > 1e-15:
        return float(ann_ret / mdd)
    if ann_ret <= 0:
        return 0.0
    return None


def calculate_omega_ratio(
    returns: pd.Series | Iterable[float],
    *,
    threshold: float = 0.0,
) -> float | None:
    """Calculate the Omega Ratio for a series of periodic returns.

    The Omega Ratio is a performance metric that evaluates the probability-weighted
    ratio of gains versus losses relative to a target threshold L (default 0.0):
        Omega(L) = sum(max(R_t - L, 0)) / sum(max(L - R_t, 0))

    Parameters:
        returns: Periodic return series or sequence of floats.
        threshold: Return benchmark threshold L (default 0.0).

    Returns:
        float | None: Omega ratio, or None if no observations, or if downside losses
            are zero with positive upside gains (infinite payoff). Returns 0.0 if
            both gains and losses are zero.
    """
    target = float(threshold)
    if not math.isfinite(target):
        raise ValueError("threshold must be a finite number")

    if isinstance(returns, pd.Series):
        clean_returns = returns.loc[
            returns.apply(lambda x: x is not None and math.isfinite(float(x)))
        ].astype("float64")
    else:
        clean_returns = pd.Series(
            [float(x) for x in returns if x is not None and math.isfinite(float(x))],
            dtype="float64",
        )

    if clean_returns.empty:
        return None

    excess = clean_returns - target
    gains = float(excess.loc[excess > 0.0].sum())
    losses = float((-excess.loc[excess < 0.0]).sum())

    if losses > 1e-15:
        return float(gains / losses)
    if gains > 1e-15:
        return None
    return 0.0


def calculate_tail_risk(
    returns: pd.Series | Iterable[float],
    *,
    confidence_levels: Sequence[float] = (0.95, 0.99),
) -> dict[str, Any]:
    """Calculate tail risk and distribution analytics for return series.

    Metrics computed:
      - Historical Value at Risk (VaR): VaR_alpha = -Quantile(R, 1 - alpha)
      - Historical Conditional Value at Risk (CVaR / Expected Shortfall):
        CVaR_alpha = -E[R | R <= -VaR_alpha]
      - Parametric Gaussian VaR: ParametricVaR_alpha = z_alpha * sigma - mu
      - Tail Ratio: |Quantile(R, 0.95)| / |Quantile(R, 0.05)|
      - Skewness: Fisher-Pearson sample skewness g1
      - Kurtosis: Fisher excess kurtosis g2 (0 for normal distribution)

    Parameters:
        returns: Periodic return series or sequence of floats.
        confidence_levels: Sequence of confidence thresholds (e.g. 0.95, 0.99).
            Each must satisfy 0 < confidence < 1.

    Returns:
        dict containing:
            - status: "available" or "insufficient_history"
            - valueAtRisk95: Historical VaR at 95% confidence (or None)
            - valueAtRisk99: Historical VaR at 99% confidence (or None)
            - cvar95: Historical CVaR at 95% confidence (or None)
            - cvar99: Historical CVaR at 99% confidence (or None)
            - parametricVaR95: Parametric Gaussian VaR at 95% (or None)
            - parametricVaR99: Parametric Gaussian VaR at 99% (or None)
            - tailRatio: Ratio of 95th percentile to absolute 5th percentile (or None)
            - skewness: Return skewness (or None)
            - kurtosis: Return excess kurtosis (or None)
            - observations: Count of valid return observations
    """
    validated_confidences: list[float] = []
    for level in confidence_levels:
        c = float(level)
        if not math.isfinite(c) or c <= 0.0 or c >= 1.0:
            raise ValueError(f"confidence level must be a finite float strictly between 0 and 1, got {level}")
        validated_confidences.append(c)

    if isinstance(returns, pd.Series):
        clean_returns = returns.loc[
            returns.apply(lambda x: x is not None and math.isfinite(float(x)))
        ].astype("float64")
    else:
        clean_returns = pd.Series(
            [float(x) for x in returns if x is not None and math.isfinite(float(x))],
            dtype="float64",
        )

    observations = int(len(clean_returns))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "valueAtRisk95": None,
        "valueAtRisk99": None,
        "cvar95": None,
        "cvar99": None,
        "parametricVaR95": None,
        "parametricVaR99": None,
        "tailRatio": None,
        "skewness": None,
        "kurtosis": None,
        "observations": observations,
    }
    if observations == 0:
        return result

    result["status"] = "available"
    q95 = float(clean_returns.quantile(0.95))
    q05 = float(clean_returns.quantile(0.05))
    if abs(q05) > 1e-15:
        result["tailRatio"] = float(abs(q95) / abs(q05))
    else:
        result["tailRatio"] = None

    if observations >= 3:
        raw_skew = float(clean_returns.skew())
        result["skewness"] = raw_skew if math.isfinite(raw_skew) else None
    if observations >= 4:
        raw_kurt = float(clean_returns.kurt())
        result["kurtosis"] = raw_kurt if math.isfinite(raw_kurt) else None

    mean_ret = float(clean_returns.mean())
    sample_std = float(clean_returns.std(ddof=1)) if observations >= 2 else 0.0
    normal_dist = statistics.NormalDist()

    for c in validated_confidences:
        suffix = f"{int(round(c * 100))}" if math.isclose(c * 100, round(c * 100), abs_tol=1e-5) else f"{c * 100:.1f}"
        q_loss = float(clean_returns.quantile(1.0 - c))
        var_hist = -q_loss
        tail = clean_returns.loc[clean_returns <= q_loss + 1e-15]
        cvar_hist = -float(tail.mean()) if not tail.empty else var_hist
        if observations >= 2 and math.isfinite(sample_std):
            z_score = normal_dist.inv_cdf(c)
            param_var = float(z_score * sample_std - mean_ret)
        else:
            param_var = None

        result[f"valueAtRisk{suffix}"] = var_hist
        result[f"cvar{suffix}"] = cvar_hist
        result[f"parametricVaR{suffix}"] = param_var

    return result


def calculate_drawdown_recovery(
    equity_curve: Iterable[dict[str, Any]],
    initial_capital: float = 0.0,
) -> dict[str, Any]:
    """Calculate maximum drawdown duration, recovery timestamps, and recovery factor.

    Tracks high-water marks (peaks), drawdown episodes, recovery points, and duration
    across an equity curve.

    Returns:
        dict containing:
            - maxDrawdownDurationDays: Longest continuous underwater period across all drawdowns in calendar days.
            - maxDrawdownRecoveryTime: Timestamp when the deepest drawdown recovered (or "" if unrecovered).
            - maxDrawdownRecoveryDays: Days from deepest drawdown peak to recovery (or to backtest end).
            - maxDrawdownRecovered: True if the deepest drawdown fully recovered before backtest end.
            - recoveryFactor: Total net profit divided by max drawdown dollar magnitude.
    """
    curve = list(equity_curve)
    if not curve:
        return {
            "maxDrawdownDurationDays": 0.0,
            "maxDrawdownRecoveryTime": "",
            "maxDrawdownRecoveryDays": 0.0,
            "maxDrawdownRecovered": True,
            "recoveryFactor": 0.0,
        }

    initial = float(initial_capital)
    final = float(curve[-1].get("value") or 0.0)

    points: list[tuple[pd.Timestamp, float, str]] = []
    for item in curve:
        time_str = str(item.get("time") or "")
        t = pd.to_datetime(time_str, errors="coerce", utc=True)
        v = pd.to_numeric(item.get("value"), errors="coerce")
        if pd.isna(t) or pd.isna(v) or not math.isfinite(float(v)):
            continue
        points.append((t, float(v), time_str))

    if not points:
        return {
            "maxDrawdownDurationDays": 0.0,
            "maxDrawdownRecoveryTime": "",
            "maxDrawdownRecoveryDays": 0.0,
            "maxDrawdownRecovered": True,
            "recoveryFactor": 0.0,
        }

    peak = initial if initial > 0 else points[0][1]
    peak_time = points[0][0]

    max_dd = 0.0
    max_dd_peak = peak
    max_dd_trough = peak
    max_dd_peak_time = peak_time

    episodes: list[dict[str, Any]] = []
    current_episode: dict[str, Any] | None = None

    for t, v, _ in points:
        if peak <= 0 or v >= peak:
            if current_episode is not None:
                current_episode["recovery_time"] = t
                current_episode["duration_days"] = max(
                    0.0,
                    (t - current_episode["peak_time"]).total_seconds() / 86400.0,
                )
                current_episode["recovered"] = True
                episodes.append(current_episode)
                current_episode = None
            peak = v
            peak_time = t
        elif v < peak:
            dd = (v / peak - 1.0) * 100.0
            if current_episode is None:
                current_episode = {
                    "peak_time": peak_time,
                    "peak_value": peak,
                    "trough_value": v,
                    "trough_time": t,
                    "max_drawdown": dd,
                    "duration_days": 0.0,
                    "recovered": False,
                }
            else:
                if v < current_episode["trough_value"]:
                    current_episode["trough_value"] = v
                    current_episode["trough_time"] = t
                    current_episode["max_drawdown"] = min(current_episode["max_drawdown"], dd)

            if dd < max_dd:
                max_dd = dd
                max_dd_peak = peak
                max_dd_trough = v
                max_dd_peak_time = peak_time

    last_time = points[-1][0]
    if current_episode is not None:
        current_episode["duration_days"] = max(
            0.0,
            (last_time - current_episode["peak_time"]).total_seconds() / 86400.0,
        )
        current_episode["recovered"] = False
        episodes.append(current_episode)

    max_duration_days = max((ep["duration_days"] for ep in episodes), default=0.0)

    max_dd_recovery_time_str = ""
    max_dd_recovered = False
    max_dd_recovery_days = 0.0

    if abs(max_dd) > 1e-15:
        for t, v, original_str in points:
            if t > max_dd_peak_time and v >= max_dd_peak:
                max_dd_recovery_time_str = original_str
                max_dd_recovered = True
                max_dd_recovery_days = max(
                    0.0,
                    (t - max_dd_peak_time).total_seconds() / 86400.0,
                )
                break
        if not max_dd_recovered:
            max_dd_recovery_days = max(
                0.0,
                (last_time - max_dd_peak_time).total_seconds() / 86400.0,
            )
    else:
        max_dd_recovered = True

    dollar_drawdown = max_dd_peak - max_dd_trough
    if dollar_drawdown > 1e-15 and initial > 0:
        recovery_factor = float((final - initial) / dollar_drawdown)
    elif final > initial:
        recovery_factor = None
    else:
        recovery_factor = 0.0

    return {
        "maxDrawdownDurationDays": max_duration_days,
        "maxDrawdownRecoveryTime": max_dd_recovery_time_str,
        "maxDrawdownRecoveryDays": max_dd_recovery_days,
        "maxDrawdownRecovered": max_dd_recovered,
        "recoveryFactor": recovery_factor,
    }
def calculate_m_squared(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None = None,
    frequency: str = "1d",
    annualization_factor: float = 252.0,
    risk_free_rate: float = 0.0,
    market: str = "",
) -> dict[str, Any]:
    """Calculate the Modigliani-Modigliani (M^2) measure and M^2 Alpha.

    M^2 leverages or deleverages the portfolio to match benchmark total volatility,
    converting the Sharpe ratio into an annualized percentage return directly
    comparable to the benchmark:
        M^2 = R_f + Sharpe_p * Vol_b_ann
        M^2_alpha = M^2 - R_b_ann
    """
    factor = float(annualization_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("annualization_factor must be a positive finite number")

    rf = float(risk_free_rate)
    if not math.isfinite(rf):
        raise ValueError("risk_free_rate must be a finite number")

    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )
    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "portfolioReturnAnnualized": None,
        "benchmarkReturnAnnualized": None,
        "riskFreeRate": rf,
        "mSquared": None,
        "mSquaredAlpha": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
        "annualizationFactor": factor,
    }
    if observations < 2:
        return result

    result["portfolioReturnAnnualized"] = float(returns["portfolio"].mean()) * factor
    result["benchmarkReturnAnnualized"] = float(returns["benchmark"].mean()) * factor
    m2 = _calculate_m_squared_metrics(
        returns,
        annualization_factor=factor,
        risk_free_rate=rf,
    )
    result.update({
        "status": m2["mSquaredStatus"],
        "mSquared": m2["mSquared"],
        "mSquaredAlpha": m2["mSquaredAlpha"],
    })
    return result


def calculate_dual_beta(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None = None,
    frequency: str = "1d",
    market: str = "",
) -> dict[str, Any]:
    """Calculate Up-Market Beta (beta+), Down-Market Beta (beta-), and Beta Spread.

    Up-market beta measures sensitivity during benchmark positive periods (R_b > 0).
    Down-market beta measures sensitivity during benchmark negative periods (R_b < 0).
    Beta spread = beta+ - beta-. A positive spread indicates convex upside participation.
    """
    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )
    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "upBeta": None,
        "downBeta": None,
        "betaSpread": None,
        "upMarketPeriods": 0,
        "downMarketPeriods": 0,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
    }
    if observations < 2:
        return result

    dual = _calculate_dual_beta_metrics(returns)
    result.update({
        "status": dual["dualBetaStatus"],
        "upBeta": dual["upBeta"],
        "downBeta": dual["downBeta"],
        "betaSpread": dual["betaSpread"],
        "upMarketPeriods": int(len(returns.loc[returns["benchmark"] > 0].index)),
        "downMarketPeriods": int(len(returns.loc[returns["benchmark"] < 0].index)),
    })
    return result


def calculate_batting_average(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None = None,
    frequency: str = "1d",
    market: str = "",
) -> dict[str, Any]:
    """Calculate Batting Average and Outperformance Payoff Ratio relative to benchmark.

    Batting average is the percentage of observation periods where portfolio return
    exceeds benchmark return: Sum(I(R_p > R_b)) / T.
    Outperformance payoff ratio is average excess return in outperforming periods divided
    by average underperformance in underperforming periods.
    """
    returns, normalized_frequency = _align_curves_and_returns(
        portfolio_curve, benchmark_curve, frequency, market=market
    )
    observations = int(len(returns.index))
    result: dict[str, Any] = {
        "status": "insufficient_history",
        "battingAverage": None,
        "outperformancePeriods": 0,
        "underperformancePeriods": 0,
        "outperformancePayoffRatio": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": normalized_frequency,
    }
    if observations == 0:
        return result

    batting = _calculate_batting_metrics(returns)
    result.update({
        "status": batting["battingStatus"],
        "battingAverage": batting["battingAverage"],
        "outperformancePeriods": batting["outperformancePeriods"],
        "underperformancePeriods": batting["underperformancePeriods"],
        "outperformancePayoffRatio": batting["outperformancePayoffRatio"],
    })
    return result


def _calculate_m_squared_metrics(
    returns: pd.DataFrame,
    *,
    annualization_factor: float,
    risk_free_rate: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mSquaredStatus": "insufficient_history",
        "mSquared": None,
        "mSquaredAlpha": None,
    }
    if len(returns.index) < 2:
        return result

    bench_var = max(0.0, float(returns["benchmark"].var(ddof=1)))
    port_var = max(0.0, float(returns["portfolio"].var(ddof=1)))
    port_vol_ann = math.sqrt(port_var * annualization_factor)
    bench_vol_ann = math.sqrt(bench_var * annualization_factor)

    portfolio_annualized = float(returns["portfolio"].mean()) * annualization_factor
    benchmark_annualized = float(returns["benchmark"].mean()) * annualization_factor

    if port_vol_ann <= 1e-15:
        result.update({
            "mSquaredStatus": "zero_portfolio_variance",
            "mSquared": None,
            "mSquaredAlpha": None,
        })
        return result

    if bench_vol_ann <= 1e-15:
        result.update({
            "mSquaredStatus": "zero_benchmark_variance",
            "mSquared": risk_free_rate,
            "mSquaredAlpha": risk_free_rate - benchmark_annualized,
        })
        return result

    sharpe_p = (portfolio_annualized - risk_free_rate) / port_vol_ann
    m_squared = risk_free_rate + sharpe_p * bench_vol_ann
    m_squared_alpha = m_squared - benchmark_annualized

    result.update({
        "mSquaredStatus": "available",
        "mSquared": m_squared,
        "mSquaredAlpha": m_squared_alpha,
    })
    return result


def _calculate_dual_beta_metrics(returns: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dualBetaStatus": "insufficient_history",
        "upBeta": None,
        "downBeta": None,
        "betaSpread": None,
    }
    if len(returns.index) < 2:
        return result

    up_returns = returns.loc[returns["benchmark"] > 0]
    down_returns = returns.loc[returns["benchmark"] < 0]

    up_beta: float | None = None
    if len(up_returns.index) >= 2:
        up_bench_var = max(0.0, float(up_returns["benchmark"].var(ddof=1)))
        if up_bench_var > 1e-15:
            up_covar = float(up_returns["portfolio"].cov(up_returns["benchmark"]))
            up_beta = up_covar / up_bench_var

    down_beta: float | None = None
    if len(down_returns.index) >= 2:
        down_bench_var = max(0.0, float(down_returns["benchmark"].var(ddof=1)))
        if down_bench_var > 1e-15:
            down_covar = float(down_returns["portfolio"].cov(down_returns["benchmark"]))
            down_beta = down_covar / down_bench_var

    beta_spread = (up_beta - down_beta) if (up_beta is not None and down_beta is not None) else None
    if up_beta is not None and down_beta is not None:
        status = "available"
    elif up_beta is not None or down_beta is not None:
        status = "partial"
    else:
        status = "insufficient_regimes"

    result.update({
        "dualBetaStatus": status,
        "upBeta": up_beta,
        "downBeta": down_beta,
        "betaSpread": beta_spread,
    })
    return result


def _calculate_batting_metrics(returns: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "battingStatus": "insufficient_history",
        "battingAverage": None,
        "outperformancePeriods": 0,
        "underperformancePeriods": 0,
        "outperformancePayoffRatio": None,
    }
    if returns.empty:
        return result

    active = returns["portfolio"] - returns["benchmark"]
    outperformed = active > 0
    underperformed = active < 0

    out_count = int(outperformed.sum())
    under_count = int(underperformed.sum())
    batting_avg = float(out_count / len(returns.index))

    avg_out: float | None = float(active.loc[outperformed].mean()) if out_count > 0 else None
    avg_under: float | None = float(abs(active.loc[underperformed].mean())) if under_count > 0 else None

    payoff: float | None = None
    if avg_out is not None and avg_under is not None and avg_under > 1e-15:
        payoff = avg_out / avg_under

    result.update({
        "battingStatus": "available",
        "battingAverage": batting_avg,
        "outperformancePeriods": out_count,
        "underperformancePeriods": under_count,
        "outperformancePayoffRatio": payoff,
    })
    return result
