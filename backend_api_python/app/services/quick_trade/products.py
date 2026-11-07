"""Instrument-product resolution for Quick Trade operations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.market.product_catalog import (
    validate_product_account_environment,
    validate_runtime_products,
)
from app.services.market.assets import resolve_runtime_instrument
from app.services.quick_trade.credentials import build_exchange_config


def _product_values(source: Any) -> Dict[str, str]:
    getter = source.get if hasattr(source, "get") else lambda key, default="": default
    return {
        "instrument_id": str(getter("instrument_id", "") or "").strip(),
        "product_type": str(getter("product_type", "") or "").strip().lower(),
        "api_family": str(getter("api_family", "") or "").strip().lower(),
        "asset_id": str(getter("asset_id", "") or getter("assetId", "") or "").strip(),
    }


def build_product_aware_config(
    credential_id: int,
    user_id: int,
    *,
    symbol: str,
    market_type: str,
    source: Any,
    overrides: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Build account-derived exchange config and resolve one exact catalog product."""
    account_overrides = dict(overrides or {"market_type": market_type})
    account_overrides.pop("exchange_id", None)
    config = build_exchange_config(credential_id, user_id, account_overrides)
    requested = _product_values(source)
    exchange_id = str(config.get("exchange_id") or "").strip().lower()
    product = resolve_runtime_instrument(
        asset_id=int(requested["asset_id"] or 0),
        market="Crypto",
        symbol=symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        product_type=requested["product_type"],
    )
    expected_product_type = requested["product_type"]
    actual_product_type = str(product.get("product_type") or "").strip().lower()
    if expected_product_type and actual_product_type != expected_product_type:
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
