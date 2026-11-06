"""Exact product-catalog lookups for data and live-trading preflight."""

from __future__ import annotations

import json
from typing import Any

from app.services.live_trading.capabilities import supports_equity_product
from app.services.market.instrument_products import PRODUCT_CRYPTO
from app.services.market.assets import resolve_runtime_instrument
from app.utils.db import get_db_connection


def get_catalog_product(
    *,
    market: str,
    symbol: str,
    exchange_id: str = "",
    market_type: str = "",
    instrument_id: str = "",
) -> dict[str, Any] | None:
    clauses = ["market = ?", "UPPER(symbol) = UPPER(?)", "is_active = 1"]
    params: list[Any] = [str(market or "").strip(), str(symbol or "").strip()]
    if exchange_id:
        clauses.append("LOWER(exchange) = ?")
        params.append(str(exchange_id).strip().lower())
    if market_type:
        clauses.append("market_type = ?")
        params.append(str(market_type).strip().lower())
    if instrument_id:
        clauses.append("UPPER(instrument_id) = UPPER(?)")
        params.append(str(instrument_id).strip())

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT asset_id, market, symbol, name, exchange AS exchange_id, market_type,
                   instrument_id, settle_currency, asset_class, product_type,
                   api_family, underlying_market, underlying_symbol, product_meta,
                   metadata_updated_at
            FROM qd_market_symbols
            WHERE {' AND '.join(clauses)}
            ORDER BY metadata_updated_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        row = cur.fetchone()
        cur.close()
    if not row:
        return None
    result = dict(row)
    result["product_meta"] = _json_object(result.get("product_meta"))
    return result


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def validate_product_account_environment(
    products: list[dict[str, Any]] | None,
    exchange_config: dict[str, Any],
) -> None:
    from app.services.live_trading.factory import exchange_trading_environment

    has_gate_stock = any(
        isinstance(product, dict)
        and str(product.get("exchange_id") or exchange_config.get("exchange_id") or "").lower() == "gate"
        and str(product.get("api_family") or "").lower() == "stock"
        for product in products or []
    )
    if has_gate_stock and exchange_trading_environment(exchange_config) != "live":
        raise ValueError("strategyV2.gateStockTestnetUnsupported")


def validate_runtime_products(
    products: list[dict[str, Any]] | None,
    *,
    credential_exchange_id: str,
) -> list[dict[str, Any]]:
    """Revalidate a deployment's immutable product contract before live start."""
    validated: list[dict[str, Any]] = []
    credential_exchange = str(credential_exchange_id or "").strip().lower()
    for stored in products or []:
        if not isinstance(stored, dict):
            continue
        product_type = str(stored.get("product_type") or PRODUCT_CRYPTO).strip().lower()
        market_type = str(stored.get("market_type") or "spot").strip().lower()
        current = resolve_runtime_instrument(
            asset_id=int(stored.get("asset_id") or 0),
            market=str(stored.get("market") or "Crypto"),
            symbol=str(stored.get("symbol") or ""),
            exchange_id=credential_exchange,
            market_type=market_type,
            product_type=product_type,
        )
        api_family = str(current.get("api_family") or market_type).strip().lower()
        if product_type != PRODUCT_CRYPTO and not supports_equity_product(
            credential_exchange, product_type, market_type, api_family
        ):
            raise ValueError("strategyV2.equityProductUnsupported")
        if str(current.get("product_type") or PRODUCT_CRYPTO).strip().lower() != product_type:
            raise ValueError("strategyV2.equityProductContractChanged")
        if not catalog_product_is_tradable(current):
            raise ValueError("strategyV2.equityProductNotTradable")
        validated.append(current)
    return validated


def catalog_product_is_tradable(product: dict[str, Any]) -> bool:
    meta = _json_object(product.get("product_meta"))
    status = str(meta.get("status") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not status:
        return True
    blocked_fragments = (
        "close",
        "deliver",
        "delist",
        "maintain",
        "maintenance",
        "offline",
        "prelaunch",
        "restricted",
        "settle",
        "suspend",
        "limit_open",
    )
    return not any(fragment in status for fragment in blocked_fragments)
