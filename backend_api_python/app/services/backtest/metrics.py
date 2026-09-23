"""Benchmark-relative performance metrics for backtest result curves."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd


DEFAULT_INFORMATION_RATIO_BANDS: tuple[tuple[float, str], ...] = (
    (0.30, "weak"),
    (0.50, "acceptable"),
    (1.00, "good"),
    (math.inf, "exceptional"),
)


def calculate_information_ratio(
    portfolio_curve: Iterable[dict[str, Any]],
    benchmark_curve: Iterable[dict[str, Any]],
    *,
    benchmark: str | None,
    frequency: str,
    annualization_factor: float,
    classification_bands: Sequence[tuple[float, str]] = DEFAULT_INFORMATION_RATIO_BANDS,
) -> dict[str, Any]:
    """Calculate an Information Ratio from timestamped portfolio and benchmark curves.

    Curves contain level observations with ``time`` and ``value`` keys. Returns
    are paired only when both curves cover the exact same start and end
    timestamps, preventing a missing observation from comparing intervals with
    different lengths. Annualized returns use arithmetic periodic means so the
    active-return numerator is consistent with the tracking-error denominator.
    Tracking error uses sample standard deviation (``ddof=1``).
    """
    factor = float(annualization_factor)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("annualization_factor must be a positive finite number")

    bands = _validate_classification_bands(classification_bands)
    portfolio_returns = _periodic_returns(portfolio_curve, "portfolioReturn")
    benchmark_returns = _periodic_returns(benchmark_curve, "benchmarkReturn")
    aligned = portfolio_returns.join(benchmark_returns, how="inner")
    if not aligned.empty:
        aligned = aligned.loc[aligned["portfolioStart"] == aligned["benchmarkStart"]]

    observations = int(len(aligned.index))
    result = {
        "status": "insufficient_history",
        "portfolioReturnAnnualized": None,
        "benchmarkReturnAnnualized": None,
        "activeReturnAnnualized": None,
        "trackingErrorAnnualized": None,
        "informationRatio": None,
        "classification": None,
        "benchmark": benchmark,
        "observations": observations,
        "frequency": str(frequency),
        "annualizationFactor": factor,
    }
    if observations == 0:
        return result

    active_returns = aligned["portfolioReturn"] - aligned["benchmarkReturn"]
    portfolio_annualized = float(aligned["portfolioReturn"].mean()) * factor
    benchmark_annualized = float(aligned["benchmarkReturn"].mean()) * factor
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


def _periodic_returns(curve: Iterable[dict[str, Any]], prefix: str) -> pd.DataFrame:
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

    if len(rows) < 2:
        return pd.DataFrame(columns=[prefix, f"{prefix.removesuffix('Return')}Start"])

    levels = pd.Series(
        (value for _, value in rows),
        index=pd.DatetimeIndex(timestamp for timestamp, _ in rows),
        dtype="float64",
    )
    levels = levels.loc[~levels.index.duplicated(keep="last")].sort_index()
    starts = pd.Series(levels.index, index=levels.index).shift(1)
    returns = levels.pct_change(fill_method=None)
    start_column = f"{prefix.removesuffix('Return')}Start"
    return pd.DataFrame({prefix: returns, start_column: starts}).dropna()


def _validate_classification_bands(
    classification_bands: Sequence[tuple[float, str]],
) -> tuple[tuple[float, str], ...]:
    bands = tuple((float(limit), str(label)) for limit, label in classification_bands)
    if not bands or any(not label for _, label in bands):
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

