"""Backtest range policy shared by human and agent endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.data_sources.factory import DataSourceFactory


_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
}


@dataclass(frozen=True)
class BacktestRangePolicy:
    max_days: int
    label: str
    reason: str


_DEFAULT_LIMITS: Dict[str, BacktestRangePolicy] = {
    "1m": BacktestRangePolicy(30, "1 month", "engine workload limit"),
    "3m": BacktestRangePolicy(30, "1 month", "engine workload limit"),
    "5m": BacktestRangePolicy(180, "6 months", "engine workload limit"),
    "15m": BacktestRangePolicy(365, "1 year", "engine workload limit"),
    "30m": BacktestRangePolicy(365, "1 year", "engine workload limit"),
    "1H": BacktestRangePolicy(1095, "3 years", "engine workload limit"),
    "4H": BacktestRangePolicy(1095, "3 years", "engine workload limit"),
    "1D": BacktestRangePolicy(1095, "3 years", "engine workload limit"),
    "1W": BacktestRangePolicy(1095, "3 years", "engine workload limit"),
}


_MARKET_LIMITS: Dict[str, Dict[str, BacktestRangePolicy]] = {
    # yfinance intraday endpoints are much narrower than daily/weekly history.
    # Keep the cap below the upstream hard edge so indicator warmup does not
    # push an apparently valid user window into an upstream 400.
    "USStock": {
        "1m": BacktestRangePolicy(7, "7 days", "US stock intraday data provider limit"),
        "3m": BacktestRangePolicy(7, "7 days", "US stock intraday data provider limit"),
        "5m": BacktestRangePolicy(60, "60 days", "US stock intraday data provider limit"),
        "15m": BacktestRangePolicy(60, "60 days", "US stock intraday data provider limit"),
        "30m": BacktestRangePolicy(60, "60 days", "US stock intraday data provider limit"),
        "1H": BacktestRangePolicy(700, "about 23 months", "US stock hourly data provider limit"),
        "4H": BacktestRangePolicy(700, "about 23 months", "US stock hourly data provider limit"),
        "1D": BacktestRangePolicy(3650, "10 years", "US stock daily data provider limit"),
        "1W": BacktestRangePolicy(3650, "10 years", "US stock weekly data provider limit"),
    },
    # Public forex fallbacks often cap output size or paid subscription depth.
    # These limits avoid silently requesting more bars than the configured
    # provider can return in one backtest run.
    "Forex": {
        "1m": BacktestRangePolicy(7, "7 days", "forex intraday data provider limit"),
        "3m": BacktestRangePolicy(30, "30 days", "forex intraday data provider limit"),
        "5m": BacktestRangePolicy(60, "60 days", "forex intraday data provider limit"),
        "15m": BacktestRangePolicy(60, "60 days", "forex intraday data provider limit"),
        "30m": BacktestRangePolicy(120, "120 days", "forex intraday data provider limit"),
        "1H": BacktestRangePolicy(365, "1 year", "forex hourly data provider limit"),
        "4H": BacktestRangePolicy(730, "2 years", "forex 4H data provider limit"),
        "1D": BacktestRangePolicy(1095, "3 years", "forex daily data provider limit"),
        "1W": BacktestRangePolicy(1095, "3 years", "forex weekly data provider limit"),
    },
}


def backtest_range_policy(market: str, timeframe: str) -> BacktestRangePolicy:
    normalized_market = DataSourceFactory.normalize_market(market or "")
    tf = str(timeframe or "1D").strip()
    return (
        _MARKET_LIMITS.get(normalized_market, {}).get(tf)
        or _DEFAULT_LIMITS.get(tf)
        or _DEFAULT_LIMITS["1D"]
    )


def _date_limit_start(end_date: datetime, max_days: int, warmup_seconds: int) -> datetime:
    """Return a date-only friendly start that keeps the fetch window under max_days."""
    return end_date - timedelta(days=max(0, int(max_days) - 1)) + timedelta(seconds=warmup_seconds)


def _date_limit_end(fetch_start: datetime, max_days: int) -> datetime:
    """Return a date-only friendly end that keeps the fetch window under max_days."""
    return fetch_start + timedelta(days=max(0, int(max_days) - 1))


def validate_backtest_range(
    *,
    market: str,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    warmup_bars: int = 0,
) -> Optional[Dict[str, Any]]:
    """Return a structured range error, or None when the request is allowed."""
    policy = backtest_range_policy(market, timeframe)
    tf_seconds = _TIMEFRAME_SECONDS.get(str(timeframe or "1D").strip(), 86400)
    warmup_seconds = max(0, int(warmup_bars or 0)) * tf_seconds
    fetch_start = start_date - timedelta(seconds=warmup_seconds)
    selected_days = max(0, (end_date - start_date).days)
    fetch_days = max(0, (end_date - fetch_start).days)
    if fetch_days <= policy.max_days:
        return None

    warmup_note = ""
    if warmup_bars:
        warmup_note = f" including {int(warmup_bars)} warmup bars"
    warmup_days = int((warmup_seconds + 86399) // 86400)
    recommendation_available = warmup_seconds < policy.max_days * 86400
    recommended_start_str = None
    recommended_end_str = None
    recommendation_msg = (
        "Please shorten the date range or use a higher timeframe."
    )
    if recommendation_available:
        recommended_start = _date_limit_start(end_date, policy.max_days, warmup_seconds)
        if recommended_start > end_date:
            recommended_start = end_date
        recommended_end = _date_limit_end(fetch_start, policy.max_days)
        if recommended_end > end_date:
            recommended_end = end_date
        recommended_start_str = recommended_start.strftime("%Y-%m-%d")
        recommended_end_str = recommended_end.strftime("%Y-%m-%d")
        recommendation_msg = (
            f"Please shorten the date range or use a higher timeframe. "
            f"Suggested fix: use {recommended_start_str} to {end_date.strftime('%Y-%m-%d')} "
            f"to keep the current end date, or keep start date {start_date.strftime('%Y-%m-%d')} "
            f"and set end date to {recommended_end_str}."
        )
    elif warmup_bars:
        recommendation_msg = (
            "The indicator warmup alone exceeds this data provider limit. "
            "Reduce long lookback parameters, reduce warmup requirements, or use a higher timeframe."
        )
    msg = (
        f"Backtest range exceeds limit: {market}:{symbol} timeframe {timeframe} "
        f"supports up to {policy.label} ({policy.max_days} days) because of the "
        f"{policy.reason}, but this request needs {fetch_days} days{warmup_note}. "
        f"{recommendation_msg}"
    )
    return {
        "error_type": "BACKTEST_RANGE_LIMIT",
        "msg": msg,
        "market": DataSourceFactory.normalize_market(market or ""),
        "symbol": symbol,
        "timeframe": timeframe,
        "max_days": policy.max_days,
        "max_range": policy.label,
        "reason": policy.reason,
        "selected_days": selected_days,
        "fetch_days": fetch_days,
        "warmup_bars": int(warmup_bars or 0),
        "warmup_days": warmup_days,
        "fetch_start": fetch_start.strftime("%Y-%m-%d"),
        "requested_start": start_date.strftime("%Y-%m-%d"),
        "requested_end": end_date.strftime("%Y-%m-%d"),
        "recommendation_available": recommendation_available,
        "recommended_start": recommended_start_str,
        "recommended_end": recommended_end_str,
    }
