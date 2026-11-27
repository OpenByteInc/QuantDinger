"""Operator view of durable security and dated-futures specifications."""
from app.utils.db import get_db_connection


def list_instrument_catalog(market, keyword="", page=1, page_size=30):
    if market not in {"HKStock", "Futures"}:
        raise ValueError("brokerAccounts.forthright.unsupportedMarket")
    page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
    keyword = str(keyword).strip()[:80]
    clause = "market = ? AND (symbol ILIKE ? OR name ILIKE ? OR product_meta->>'root' ILIKE ?)"
    params = [market, *([f"%{keyword}%"] * 3)]
    clause += " AND exchange = 'HKEX'" if market == "HKStock" else " AND product_type = 'futures' AND product_meta ? 'expiry'"
    # Use jsonb_exists because the database wrapper rewrites question-mark placeholders.
    clause = clause.replace("product_meta ? 'expiry'", "jsonb_exists(product_meta, 'expiry')")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(f"SELECT COUNT(*) AS total FROM qd_market_symbols WHERE {clause}", params)
        total = cur.fetchone()["total"]
        cur.execute(f"""SELECT symbol, name, exchange, currency, board_lot, rules_as_of, rules_source,
                    product_meta, metadata_updated_at, is_active,
                    CASE WHEN market = 'Futures' THEN
                      COALESCE(NULLIF(product_meta->>'last_trade_date', ''), product_meta->>'expiry')
                        < TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYYMMDD')
                      ELSE false END AS expired
                FROM qd_market_symbols WHERE {clause}
                ORDER BY symbol, exchange LIMIT ? OFFSET ?""", [*params, page_size, (page - 1) * page_size])
        rows = cur.fetchall()
        cur.close()
    for row in rows:
        for field in ("rules_as_of", "metadata_updated_at"):
            row[field] = str(row[field]) if row.get(field) is not None else None
    return {"items": rows, "total": total, "page": page, "page_size": page_size}
