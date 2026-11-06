"""Logical-asset watchlist business logic."""

import re
from typing import Optional

from app.services.market.assets import AssetResolutionError, get_asset_capabilities, get_market_asset
from app.utils.db import get_db_connection

VALID_MARKETS = frozenset({
    "Crypto", "USStock", "CNStock", "HKStock", "Forex", "Futures", "MOEX",
})
CN_A_SHARE_PATTERN = re.compile(r"^\d{6}$")


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def validate_watchlist_pair(market: str, symbol: str) -> Optional[str]:
    """Validate a market/symbol pair before resolving it to a logical asset."""
    if market not in VALID_MARKETS:
        return f"Unsupported market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}"
    if not symbol:
        return "Empty symbol"
    if CN_A_SHARE_PATTERN.match(symbol) and market != "CNStock":
        return f"Symbol '{symbol}' looks like a Chinese A-share code; market must be CNStock, not {market}"
    if symbol.endswith(".HK") and market != "HKStock":
        return f"Symbol '{symbol}' looks like a Hong Kong stock; market must be HKStock, not {market}"
    if market == "Crypto" and "/" not in symbol:
        return f"Crypto symbol '{symbol}' must be a BASE/QUOTE pair (e.g. BTC/USDT)."
    return None


def list_watchlist(user_id: int) -> list[dict]:
    """Return user-selected logical assets without venue or product bindings."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT w.id, w.asset_id, a.asset_key, a.market, a.symbol, a.name,
                   a.asset_class, a.product_type, a.underlying_market,
                   a.underlying_symbol, w.created_at, w.updated_at
            FROM qd_watchlist w
            JOIN qd_market_assets a ON a.id = w.asset_id AND a.is_active = 1
            WHERE w.user_id = ?
            ORDER BY w.id DESC
            """,
            (user_id,),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()

    for row in rows:
        try:
            capabilities = get_asset_capabilities(int(row.get("asset_id") or 0))
        except AssetResolutionError:
            capabilities = {"market_types": [], "exchange_ids": [], "contexts": []}
        row["market_types"] = capabilities.get("market_types") or []
        row["exchange_ids"] = capabilities.get("exchange_ids") or []
        row["product_count"] = len(capabilities.get("contexts") or [])
    return rows


def add_watchlist_item(
    user_id: int,
    *,
    asset_id: int = 0,
) -> tuple[bool, str]:
    """Persist one catalog-backed logical asset."""
    if int(asset_id or 0) <= 0:
        return False, "market.assetIdRequired"

    try:
        asset = get_market_asset(asset_id=int(asset_id))
    except AssetResolutionError as exc:
        return False, str(exc)
    if not asset:
        return False, "market.assetNotFound"

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_watchlist (user_id, asset_id, created_at, updated_at)
            VALUES (?, ?, NOW(), NOW())
            ON CONFLICT (user_id, asset_id) DO UPDATE SET updated_at = NOW()
            """,
            (user_id, asset["asset_id"]),
        )
        db.commit()
        cur.close()
    return True, "success"


def remove_watchlist_item(
    user_id: int,
    *,
    asset_id: int = 0,
) -> bool:
    """Remove one logical asset from the user's watchlist."""
    resolved_asset_id = int(asset_id or 0)
    if resolved_asset_id <= 0:
        return False

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "DELETE FROM qd_watchlist WHERE user_id = ? AND asset_id = ?",
            (user_id, resolved_asset_id),
        )
        deleted = int(cur.rowcount or 0)
        db.commit()
        cur.close()
    return deleted > 0


def get_user_watchlist_pairs(user_id: int) -> list[dict]:
    """Return logical assets for quote-source resolution."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT a.id AS asset_id, a.market, a.symbol, a.product_type,
                   a.underlying_market, a.underlying_symbol
            FROM qd_watchlist w
            JOIN qd_market_assets a ON a.id = w.asset_id AND a.is_active = 1
            WHERE w.user_id = ?
            ORDER BY w.id DESC
            """,
            (user_id,),
        )
        rows = [dict(row) for row in (cur.fetchall() or [])]
        cur.close()
    return rows
