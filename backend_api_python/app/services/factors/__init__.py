"""Versioned factor registry and deterministic computation helpers."""

from app.services.factors.registry import (
    FactorDefinition,
    FactorError,
    compute_factor,
    compute_panel_factor,
    get_factor,
    list_factors,
)
from app.services.factors.talib_adapter import (
    TalibFactorError,
    assert_talib_catalog_ready,
    compute_talib_factor,
    compute_talib_indicator,
    is_talib_available,
    list_talib_factors,
)
from app.services.factors.catalog import (
    compute_catalog_factor,
    get_factor_metadata,
    list_factor_catalog,
)

__all__ = [
    "FactorDefinition",
    "FactorError",
    "compute_factor",
    "compute_panel_factor",
    "get_factor",
    "list_factors",
    "TalibFactorError",
    "assert_talib_catalog_ready",
    "compute_talib_factor",
    "compute_talib_indicator",
    "is_talib_available",
    "list_talib_factors",
    "compute_catalog_factor",
    "get_factor_metadata",
    "list_factor_catalog",
]
