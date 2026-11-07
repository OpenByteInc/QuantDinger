"""Logical market assets and exact runtime instrument resolution."""

from __future__ import annotations

import json
from typing import Any

from app.services.market.instrument_products import PRODUCT_CRYPTO
from app.utils.db import get_db_connection


class AssetResolutionError(ValueError):
    """Raised when a logical asset cannot be resolved to one exact product."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


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


def normalize_asset_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["id"] = int(result.get("id") or result.get("asset_id") or 0)
    result["asset_id"] = result["id"]
    result["market"] = _clean(result.get("market"))
    result["symbol"] = _clean(result.get("symbol")).upper()
    result["name"] = _clean(result.get("name")) or result["symbol"]
    result["asset_class"] = _clean(result.get("asset_class")).lower()
    result["product_type"] = _clean(result.get("product_type")).lower()
    result["underlying_market"] = _clean(result.get("underlying_market"))
    result["underlying_symbol"] = _clean(result.get("underlying_symbol")).upper()
    return result


def normalize_product_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    result["asset_id"] = int(result.get("asset_id") or 0)
    result["exchange_id"] = _clean(result.get("exchange_id") or result.get("exchange")).lower()
    result["market_type"] = _clean(result.get("market_type") or "spot").lower()
    result["instrument_id"] = _clean(result.get("instrument_id"))
    result["settle_currency"] = _clean(result.get("settle_currency")).upper()
    result["product_type"] = _clean(result.get("product_type") or PRODUCT_CRYPTO).lower()
    result["api_family"] = _clean(result.get("api_family") or result["market_type"]).lower()
    result["product_meta"] = _json_object(result.get("product_meta"))
    return result


def get_market_asset(
    *,
    asset_id: int = 0,
    market: str = "",
    symbol: str = "",
    product_type: str = "",
    underlying_market: str = "",
    underlying_symbol: str = "",
) -> dict[str, Any] | None:
    clauses = ["is_active = 1"]
    params: list[Any] = []
    if int(asset_id or 0) > 0:
        clauses.append("id = ?")
        params.append(int(asset_id))
    else:
        clauses.extend(["market = ?", "UPPER(symbol) = UPPER(?)"])
        params.extend([_clean(market), _clean(symbol)])
        if product_type:
            clauses.append("product_type = ?")
            params.append(_clean(product_type).lower())
        if underlying_market:
            clauses.append("underlying_market = ?")
            params.append(_clean(underlying_market))
        if underlying_symbol:
            clauses.append("UPPER(underlying_symbol) = UPPER(?)")
            params.append(_clean(underlying_symbol))

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT id, asset_key, market, symbol, name, asset_class, product_type,
                   underlying_market, underlying_symbol, is_active
            FROM qd_market_assets
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE WHEN product_type = 'crypto' THEN 0 ELSE 1 END, id
            LIMIT 2
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
        cur.close()
    if not rows:
        return None
    if len(rows) > 1 and not product_type and not asset_id:
        raise AssetResolutionError("market.assetAmbiguous")
    return normalize_asset_row(rows[0])


def search_market_assets(*, market: str, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
    normalized_market = _clean(market)
    normalized_keyword = _clean(keyword).upper()
    safe_limit = max(1, min(int(limit or 20), 100))
    if not normalized_market or not normalized_keyword:
        return []

    like_value = f"%{normalized_keyword}%"
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, asset_key, market, symbol, name, asset_class, product_type,
                   underlying_market, underlying_symbol, is_active
            FROM qd_market_assets
            WHERE market = ? AND is_active = 1
              AND (UPPER(symbol) LIKE ? OR UPPER(name) LIKE ? OR UPPER(underlying_symbol) LIKE ?)
              AND EXISTS (
                SELECT 1 FROM qd_market_symbols s
                WHERE s.asset_id = qd_market_assets.id AND s.is_active = 1
              )
            ORDER BY
              CASE WHEN UPPER(symbol) = ? THEN 0 WHEN UPPER(symbol) LIKE ? THEN 1 ELSE 2 END,
              symbol, id
            LIMIT ?
            """,
            (
                normalized_market,
                like_value,
                like_value,
                like_value,
                normalized_keyword,
                f"{normalized_keyword}%",
                safe_limit,
            ),
        )
        assets = [normalize_asset_row(row) for row in (cur.fetchall() or [])]
        asset_ids = [int(asset["asset_id"]) for asset in assets if asset]
        products_by_asset: dict[int, list[dict[str, Any]]] = {asset_id: [] for asset_id in asset_ids}
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            cur.execute(
                f"""
                SELECT asset_id, exchange AS exchange_id, market_type, instrument_id,
                       settle_currency, product_type, api_family
                FROM qd_market_symbols
                WHERE asset_id IN ({placeholders}) AND is_active = 1
                ORDER BY asset_id, exchange, market_type, instrument_id
                """,
                tuple(asset_ids),
            )
            for row in cur.fetchall() or []:
                product = normalize_product_row(row)
                products_by_asset.setdefault(int(product["asset_id"]), []).append(product)
        cur.close()

    output: list[dict[str, Any]] = []
    for asset in assets:
        if not asset:
            continue
        products = products_by_asset.get(int(asset["asset_id"]), [])
        asset["market_types"] = sorted({p["market_type"] for p in products if p.get("market_type")})
        asset["exchange_ids"] = sorted({p["exchange_id"] for p in products if p.get("exchange_id")})
        asset["product_count"] = len(products)
        output.append(asset)
    return output


def get_asset_capabilities(asset_id: int, *, exchange_id: str = "") -> dict[str, Any]:
    asset = get_market_asset(asset_id=int(asset_id or 0))
    if not asset:
        raise AssetResolutionError("market.assetNotFound")
    clauses = ["asset_id = ?", "is_active = 1"]
    params: list[Any] = [asset["asset_id"]]
    if exchange_id:
        clauses.append("LOWER(exchange) = ?")
        params.append(_clean(exchange_id).lower())
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT asset_id, market, symbol, name, exchange AS exchange_id, market_type,
                   instrument_id, settle_currency, asset_class, product_type, api_family,
                   underlying_market, underlying_symbol, product_meta, metadata_updated_at
            FROM qd_market_symbols
            WHERE {' AND '.join(clauses)}
            ORDER BY exchange, CASE market_type WHEN 'spot' THEN 0 WHEN 'swap' THEN 1 ELSE 2 END,
                     instrument_id
            """,
            tuple(params),
        )
        products = [normalize_product_row(row) for row in (cur.fetchall() or [])]
        cur.close()
    return {
        "asset": asset,
        "asset_id": asset["asset_id"],
        "market_types": sorted({p["market_type"] for p in products if p}),
        "exchange_ids": sorted({p["exchange_id"] for p in products if p and p.get("exchange_id")}),
        "contexts": [p for p in products if p],
    }


def resolve_research_instrument(
    asset_id: int,
    *,
    preferred_exchange_id: str = "",
    preferred_market_type: str = "spot",
) -> dict[str, Any]:
    """Choose a deterministic quote context without persisting it on the watchlist."""
    capabilities = get_asset_capabilities(int(asset_id or 0))
    contexts = capabilities.get("contexts") or []
    if not contexts:
        raise AssetResolutionError("market.assetHasNoProducts")
    exchange_id = _clean(preferred_exchange_id).lower()
    market_type = _clean(preferred_market_type or "spot").lower()
    ranked = sorted(
        contexts,
        key=lambda product: (
            0 if exchange_id and product.get("exchange_id") == exchange_id else 1,
            0 if product.get("market_type") == market_type else 1,
            0 if product.get("market_type") == "spot" else 1,
            product.get("exchange_id") or "",
            product.get("instrument_id") or "",
        ),
    )
    return ranked[0]


def resolve_runtime_instrument(
    *,
    asset_id: int = 0,
    market: str = "",
    symbol: str = "",
    exchange_id: str,
    market_type: str,
    product_type: str = "",
) -> dict[str, Any]:
    """Resolve one logical asset against the selected account and product type."""
    normalized_exchange = _clean(exchange_id).lower()
    normalized_market_type = _clean(market_type).lower()
    if not normalized_exchange:
        raise AssetResolutionError("strategyV2.instrumentVenueRequired")
    if normalized_market_type not in {"spot", "swap"}:
        raise AssetResolutionError("strategyV2.instrumentMarketTypeRequired")
    asset = get_market_asset(
        asset_id=int(asset_id or 0),
        market=market,
        symbol=symbol,
        product_type=product_type,
    )
    if not asset:
        raise AssetResolutionError("market.assetNotFound")

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT asset_id, market, symbol, name, exchange AS exchange_id, market_type,
                   instrument_id, settle_currency, asset_class, product_type, api_family,
                   underlying_market, underlying_symbol, product_meta, metadata_updated_at
            FROM qd_market_symbols
            WHERE asset_id = ? AND LOWER(exchange) = ? AND market_type = ? AND is_active = 1
            ORDER BY metadata_updated_at DESC NULLS LAST, id DESC
            LIMIT 2
            """,
            (asset["asset_id"], normalized_exchange, normalized_market_type),
        )
        rows = cur.fetchall() or []
        cur.close()
    if not rows:
        raise AssetResolutionError("strategyV2.instrumentUnavailableForAccount")
    if len(rows) > 1:
        instrument_ids = {_clean(row.get("instrument_id")) for row in rows}
        if len(instrument_ids) > 1:
            raise AssetResolutionError("strategyV2.instrumentResolutionAmbiguous")
    product = normalize_product_row(rows[0])
    product["asset"] = asset
    return product
