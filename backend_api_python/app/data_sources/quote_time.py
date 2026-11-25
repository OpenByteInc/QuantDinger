"""Normalize provider observation times without substituting request time."""

from math import isfinite

import pandas as pd


def quote_timestamp(value, *, timezone: str | None = None) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        if isinstance(value, (int, float)):
            result = float(value)
            if result > 10_000_000_000:
                result /= 1000.0
        else:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp):
                return 0.0
            if stamp.tzinfo is None:
                if not timezone:
                    return 0.0
                stamp = stamp.tz_localize(timezone)
            result = stamp.timestamp()
        return result if isfinite(result) and result > 0 else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0
