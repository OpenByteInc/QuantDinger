"""Benchmark-relative performance metrics for backtest result curves."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from app.services.market_schedule import CALENDAR_BY_MARKET, equity_bar_session_date

__all__ = [
    "DEFAULT_INFORMATION_RATIO_BANDS",
    "benchmark_level_curve",
    "calculate_benchmark_relative_metrics",
    "calculate_beta_and_alpha",
    "calculate_calmar_ratio",
    "calculate_capture_ratios",
    "calculate_downside_risk",
    "calculate_drawdown_recovery",
    "calculate_information_ratio",
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
        "beta": None,
        "alphaAnnualized": None,
        "treynorRatio": None,
        "correlation": None,
        "rSquared": None,
        "upCapture": None,
        "downCapture": None,
        "captureRatio": None,
        "upMarketPeriods": 0,
        "downMarketPeriods": 0,
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
        if peak <= 0 or v > peak:
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
