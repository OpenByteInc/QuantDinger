"""Normalize, convert, and backfill exchange-reported execution fees."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.fee_quote import fee_to_quote
from app.utils.db import get_db_connection


def fee_breakdown_snapshot(raw: Any) -> Dict[str, float]:
    data = raw if isinstance(raw, dict) else {}
    phases = data.get("phases") if isinstance(data.get("phases"), dict) else {}
    breakdown = data.get("fees_by_ccy") or data.get("fee_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = phases.get("fee_breakdown")
    fees: Dict[str, float] = {}
    if isinstance(breakdown, dict):
        for currency, amount in breakdown.items():
            try:
                fee = abs(float(amount or 0.0))
            except Exception:
                fee = 0.0
            if fee > 0:
                key = str(currency or "").strip().upper() or "UNKNOWN"
                fees[key] = fees.get(key, 0.0) + fee
    if fees:
        return fees
    try:
        commission = abs(float(data.get("commission") or data.get("fee") or 0.0))
    except Exception:
        commission = 0.0
    if commission > 0:
        currency = str(data.get("commission_ccy") or data.get("fee_ccy") or "").strip().upper() or "UNKNOWN"
        fees[currency] = commission
    return fees


def commission_snapshot(raw: Any) -> Tuple[float, str]:
    fees = fee_breakdown_snapshot(raw)
    if len(fees) == 1:
        currency, commission = next(iter(fees.items()))
        return commission, "" if currency == "UNKNOWN" else currency
    if len(fees) > 1:
        return 0.0, "MIXED"
    return 0.0, ""


def previous_fee_breakdown(row: Dict[str, Any]) -> Dict[str, float]:
    raw = row.get("exchange_response_json") or ""
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = json.loads(raw) or {}
    except Exception:
        return {}
    if isinstance(data.get("live_fill_sync"), dict):
        data = data["live_fill_sync"]
    return fee_breakdown_snapshot(data)


def previous_commission(row: Dict[str, Any]) -> float:
    fees = previous_fee_breakdown(row)
    return sum(fees.values()) if len(fees) == 1 else 0.0


def incremental_fees(cumulative: Dict[str, float], previous: Dict[str, float]) -> Dict[str, float]:
    return {
        currency: amount - previous.get(currency, 0.0)
        for currency, amount in cumulative.items()
        if amount - previous.get(currency, 0.0) > 1e-12
    }


def fee_storage_values(fees: Dict[str, float]) -> Tuple[float, str]:
    if len(fees) == 1:
        currency, amount = next(iter(fees.items()))
        return amount, "" if currency == "UNKNOWN" else currency
    if fees:
        return 0.0, "MIXED"
    return 0.0, ""


def fee_breakdown_to_quote(
    client: Any,
    *,
    symbol: str,
    fees: Dict[str, float],
    fill_price: float,
) -> Optional[float]:
    total = 0.0
    for currency, amount in fees.items():
        converted = fee_to_quote(
            client,
            symbol=symbol,
            fee=amount,
            fee_ccy="" if currency == "UNKNOWN" else currency,
            fill_price=fill_price,
        )
        if converted is None:
            return None
        total += converted
    return total


def backfill_zero_commission_trades(
    *,
    order_id: int,
    fees_by_ccy: Dict[str, float],
    commission_quote: Optional[float],
) -> int:
    """Repair persisted fills whose authoritative exchange fee arrived later."""
    if int(order_id or 0) <= 0 or not fees_by_ccy:
        return 0
    native_total, commission_ccy = fee_storage_values(fees_by_ccy)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, COALESCE(value, 0) AS value, COALESCE(amount, 0) AS amount
            FROM qd_strategy_trades
            WHERE pending_order_id = %s
              AND (COALESCE(commission, 0) = 0 OR COALESCE(commission_quote, 0) = 0)
            ORDER BY id ASC
            """,
            (int(order_id),),
        )
        rows = cur.fetchall() or []
        if not rows:
            cur.close()
            return 0
        weights = [max(0.0, float(row.get("value") or row.get("amount") or 0.0)) for row in rows]
        total_weight = sum(weights)
        if total_weight <= 0:
            weights = [1.0 for _ in rows]
            total_weight = float(len(rows))
        for row, weight in zip(rows, weights):
            ratio = weight / total_weight
            cur.execute(
                """
                UPDATE qd_strategy_trades
                SET commission = CASE WHEN COALESCE(commission, 0) = 0 THEN %s ELSE commission END,
                    commission_ccy = CASE WHEN COALESCE(commission, 0) = 0 THEN %s ELSE commission_ccy END,
                    commission_quote = CASE WHEN COALESCE(commission_quote, 0) = 0 THEN %s ELSE commission_quote END
                WHERE id = %s
                  AND (COALESCE(commission, 0) = 0 OR COALESCE(commission_quote, 0) = 0)
                """,
                (
                    native_total * ratio,
                    commission_ccy,
                    (float(commission_quote) * ratio) if commission_quote is not None else None,
                    int(row.get("id") or 0),
                ),
            )
        db.commit()
        cur.close()
    return len(rows)
