"""Instrument-product resolution for Quick Trade operations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.market.product_catalog import (
    get_catalog_product,
    validate_product_account_environment,
    validate_runtime_products,
)
from app.services.quick_trade.credentials import build_exchange_config


def _product_values(source: Any) -> Dict[str, str]:
    getter = source.get if hasattr(source, "get") else lambda key, default="": default
    return {
        "instrument_id": str(getter("instrument_id", "") or "").strip(),
        "product_type": str(getter("product_type", "") or "").strip().lower(),
        "api_family": str(getter("api_family", "") or "").strip().lower(),
    }


def _should_resolve_product(source: Any) -> bool:
    getter = source.get if hasattr(source, "get") else lambda key, default="": default
    value = getter("resolve_product", False)
    return value is True or str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_product_aware_config(
    credential_id: int,
    user_id: int,
    *,
    symbol: str,
    market_type: str,
    source: Any,
    overrides: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Build an exchange config and validate an optional catalog contract."""
    config = build_exchange_config(credential_id, user_id, overrides or {"market_type": market_type})
    requested = _product_values(source)
    has_identity = any(requested.values())
    is_regular_crypto_identity = (
        requested["product_type"] == "crypto"
        and requested["api_family"] in {"", "spot", "swap"}
    )
    if not has_identity and not _should_resolve_product(source):
        return config, None

    exchange_id = str(config.get("exchange_id") or "").strip().lower()
    product = get_catalog_product(
        market="Crypto",
        symbol=symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        instrument_id=requested["instrument_id"],
    )
    if not product:
        if has_identity and not is_regular_crypto_identity:
            raise ValueError("strategyV2.equityProductCatalogStale")
        return config, None
    for key in ("product_type", "api_family"):
        expected = requested[key]
        actual = str(product.get(key) or "").strip().lower()
        if expected and actual != expected:
            raise ValueError("strategyV2.equityProductContractChanged")

    current = validate_runtime_products([product], credential_exchange_id=exchange_id)[0]
    validate_product_account_environment([current], config)
    config.update(
        {
            "market_type": str(current.get("market_type") or market_type).strip().lower(),
            "instrument_id": str(current.get("instrument_id") or ""),
            "settle_currency": str(current.get("settle_currency") or ""),
            "instrument_product_type": str(current.get("product_type") or ""),
            "api_family": str(current.get("api_family") or ""),
            "instrument_product_meta": dict(current.get("product_meta") or {}),
        }
    )
    return config, current


__all__ = ["build_product_aware_config"]
