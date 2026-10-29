"""Database persistence helpers for Alpaca order reconciliation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger


logger = get_logger(__name__)


class AlpacaOrderSyncPersistenceMixin:
    def _fetch_alpaca_sent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            try:
                stale_sec = int(self._stale_processing_sec or 0)
            except Exception:
                stale_sec = 0
            if stale_sec > 0:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        UPDATE pending_orders
                        SET status = 'sent',
                            dispatch_note = 'alpaca_fill_sync:requeued_stale_sync',
                            updated_at = NOW()
                        WHERE status = 'syncing'
                          AND LOWER(COALESCE(exchange_id, '')) IN ('alpaca', 'tiger', 'webull')
                          AND updated_at < NOW() - (%s * INTERVAL '1 second')
                        """,
                        (stale_sec,),
                    )
                    db.commit()
                    cur.close()
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM pending_orders
                    WHERE (
                            status = 'sent'
                            OR (status IN ('filled', 'cancelled') AND COALESCE(filled, 0) >
                                COALESCE((SELECT SUM(t.amount) FROM qd_strategy_trades t
                                          WHERE t.pending_order_id = pending_orders.id), 0))
                            OR (
                                status = 'filled'
                                AND COALESCE(filled, 0) > 0
                                AND updated_at < NOW() - (%s * INTERVAL '1 second')
                                AND EXISTS (
                                    SELECT 1
                                    FROM qd_strategy_trades t
                                    WHERE t.pending_order_id = pending_orders.id
                                      AND COALESCE(t.fee_status, 'pending') = 'pending'
                                )
                            )
                          )
                      AND LOWER(COALESCE(exchange_id, '')) IN ('alpaca', 'tiger', 'webull')
                      AND (
                            COALESCE(exchange_order_id, '') <> ''
                            OR COALESCE(client_order_id, '') <> ''
                          )
                    ORDER BY sent_at ASC NULLS FIRST, id ASC
                    LIMIT %s
                    """,
                    (int(self._fee_sync_retry_sec), int(limit)),
                )
                rows = cur.fetchall() or []
                cur.close()
            return rows
        except Exception as exc:
            logger.warning("fetch_alpaca_sent_orders failed: %s", exc)
            return []

    def _claim_alpaca_sent_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Atomically claim one Alpaca sent order for fill sync."""
        if int(order_id or 0) <= 0:
            return None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE pending_orders
                    SET status = 'syncing',
                        dispatch_note = 'alpaca_fill_sync:syncing',
                        updated_at = NOW()
                    WHERE id = %s
                      AND (
                            status = 'sent'
                            OR (status IN ('filled', 'cancelled') AND COALESCE(filled, 0) >
                                COALESCE((SELECT SUM(t.amount) FROM qd_strategy_trades t
                                          WHERE t.pending_order_id = pending_orders.id), 0))
                            OR (
                                status = 'filled'
                                AND COALESCE(filled, 0) > 0
                                AND updated_at < NOW() - (%s * INTERVAL '1 second')
                                AND EXISTS (
                                    SELECT 1
                                    FROM qd_strategy_trades t
                                    WHERE t.pending_order_id = pending_orders.id
                                      AND COALESCE(t.fee_status, 'pending') = 'pending'
                                )
                            )
                          )
                      AND LOWER(COALESCE(exchange_id, '')) IN ('alpaca', 'tiger', 'webull')
                      AND (COALESCE(exchange_order_id, '') <> '' OR COALESCE(client_order_id, '') <> '')
                    RETURNING *
                    """,
                    (int(order_id), int(self._fee_sync_retry_sec)),
                )
                row = cur.fetchone()
                db.commit()
                cur.close()
            return row if isinstance(row, dict) else None
        except Exception as exc:
            logger.warning("claim_alpaca_sent_order failed: pending_id=%s err=%s", order_id, exc)
            return None

    def _update_alpaca_sent_order_snapshot(
        self,
        *,
        order_id: int,
        status: str,
        exchange_status: str,
        filled: float,
        avg_price: float,
        exchange_response_json: str,
        final: bool,
        fee_status: str = "pending",
        fee_source: str = "",
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = %s,
                    last_error = CASE WHEN %s = 'failed' THEN %s ELSE '' END,
                    dispatch_note = %s,
                    filled = %s,
                    avg_price = %s,
                    fee_status = CASE
                        WHEN %s IN ('actual', 'actual_zero') THEN %s
                        ELSE fee_status
                    END,
                    fee_source = CASE
                        WHEN %s IN ('actual', 'actual_zero') THEN %s
                        ELSE fee_source
                    END,
                    exchange_response_json = %s,
                    executed_at = CASE WHEN %s THEN NOW() ELSE executed_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    str(status or "sent"),
                    str(status or "sent"),
                    str(exchange_status or ""),
                    f"alpaca_fill_sync:{exchange_status or 'unknown'}",
                    float(filled or 0.0),
                    float(avg_price or 0.0),
                    str(fee_status or "pending"),
                    str(fee_status or "pending"),
                    str(fee_status or "pending"),
                    str(fee_source or ""),
                    str(exchange_response_json or ""),
                    bool(final and float(filled or 0.0) > 0),
                    int(order_id),
                ),
            )
            cur.execute(
                """
                UPDATE strategy_order_intents soi
                SET status = CASE
                        WHEN %s = 'filled' THEN 'filled'
                        WHEN %s = 'failed' THEN 'rejected'
                        WHEN %s = 'cancelled' THEN 'cancelled'
                        WHEN %s > 0 THEN 'partially_filled'
                        ELSE 'submitted'
                    END,
                    exchange_order_id = COALESCE(NULLIF(po.exchange_order_id, ''), soi.exchange_order_id),
                    updated_at = NOW()
                FROM pending_orders po
                WHERE po.id = %s
                  AND po.order_intent_id = soi.id
                """,
                (
                    str(status or "sent"),
                    str(status or "sent"),
                    str(status or "sent"),
                    float(filled or 0.0),
                    int(order_id),
                ),
            )
            db.commit()
            cur.close()
