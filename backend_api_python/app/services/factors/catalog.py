"""Unified built-in and TA-Lib factor catalog helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from .registry import FactorError, compute_factor, get_factor, list_factors
from .talib_adapter import (
    TalibFactorError,
    compute_talib_factor,
    is_talib_available,
    list_talib_factors,
)


def list_factor_catalog(*, category: str = "", factor_type: str = "") -> list[dict[str, Any]]:
    rows = list_factors(category=category, factor_type=factor_type)
    if factor_type == "fundamental" or not is_talib_available():
        return rows
    rows.extend(
        item for item in list_talib_factors()
        if not category or item.get("category") == category
    )
    return sorted(rows, key=lambda item: (
        str(item.get("factor_type") or ""),
        str(item.get("category") or ""),
        str(item.get("factor_id") or "").lower(),
    ))


def get_factor_metadata(factor_id: str) -> dict[str, Any]:
    canonical = str(factor_id or "").strip()
    if canonical.lower().startswith("talib:"):
        target = canonical.upper()
        if not is_talib_available():
            raise TalibFactorError("factor.talibUnavailable")
        item = next(
            (row for row in list_talib_factors() if row["factor_id"].upper() == target),
            None,
        )
        if item is None:
            raise TalibFactorError("factor.notFound")
        return item
    return get_factor(canonical.lower()).metadata()


def compute_catalog_factor(
    factor_id: str,
    frame: pd.DataFrame,
    params: Mapping[str, Any] | None = None,
) -> float:
    canonical = str(factor_id or "").strip()
    if canonical.lower().startswith("talib:"):
        normalized = dict(params or {})
        output = str(normalized.pop("output", "") or "")
        return compute_talib_factor(canonical, frame, normalized, output=output)
    try:
        return compute_factor(canonical.lower(), frame, params)
    except FactorError as exc:
        if exc.code != "factor.notFound":
            raise
    return compute_talib_factor(canonical, frame, params)
