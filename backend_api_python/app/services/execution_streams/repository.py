"""Durable execution event and live-order binding repository."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from app.services.execution_streams.events import ExecutionEvent
from app.utils.db import get_db_connection


class ExecutionEventRepository:
    def register_binding(
        self,
        *,
        credential_id: int,
        exchange_id: str,
        market_type: str,
        owner_type: str,
        owner_id: int,
        user_id: int = 1,
        strategy_id: int = 0,
        pending_order_id: int = 0,
        strategy_run_id: int = 0,
        order_intent_id: int = 0,
        symbol: str = "",
        signal_type: str = "",
        client_order_id: str = "",
        exchange_order_id: str = "",
        observed_filled: float = 0.0,
    ) -> None:
        if not owner_type or int(owner_id or 0) <= 0:
            return
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_live_order_bindings
                (credential_id, exchange_id, market_type, owner_type, owner_id,
                 user_id, strategy_id, pending_order_id, strategy_run_id,
                 order_intent_id, symbol, signal_type, client_order_id,
                 exchange_order_id, observed_filled, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open')
                ON CONFLICT (owner_type, owner_id) DO UPDATE SET
                    credential_id = EXCLUDED.credential_id,
                    exchange_id = EXCLUDED.exchange_id,
                    market_type = EXCLUDED.market_type,
                    user_id = EXCLUDED.user_id,
                    strategy_id = EXCLUDED.strategy_id,
                    pending_order_id = EXCLUDED.pending_order_id,
                    strategy_run_id = EXCLUDED.strategy_run_id,
                    order_intent_id = EXCLUDED.order_intent_id,
                    symbol = EXCLUDED.symbol,
                    signal_type = EXCLUDED.signal_type,
                    client_order_id = COALESCE(NULLIF(EXCLUDED.client_order_id, ''), qd_live_order_bindings.client_order_id),
                    exchange_order_id = COALESCE(NULLIF(EXCLUDED.exchange_order_id, ''), qd_live_order_bindings.exchange_order_id),
                    observed_filled = GREATEST(qd_live_order_bindings.observed_filled, EXCLUDED.observed_filled),
                    updated_at = NOW()
                """,
                (
                    int(credential_id or 0),
                    str(exchange_id or "").lower(),
                    str(market_type or "swap").lower(),
                    str(owner_type),
                    int(owner_id),
                    int(user_id or 1),
                    int(strategy_id or 0),
                    int(pending_order_id or 0),
                    int(strategy_run_id or 0),
                    int(order_intent_id or 0),
                    str(symbol or ""),
                    str(signal_type or ""),
                    str(client_order_id or ""),
                    str(exchange_order_id or ""),
                    float(observed_filled or 0.0),
                ),
            )
            db.commit()
            cur.close()

    def ingest(self, event: ExecutionEvent) -> Optional[int]:
        from app.services.live_trading.partner_attribution import redact_partner_attribution

        raw = redact_partner_attribution(event.raw or {})
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_execution_events
                (event_key, credential_id, user_id, exchange_id, market_type,
                 account_id, symbol, exchange_order_id, client_order_id,
                 exchange_fill_id, side, position_side, order_status, price,
                 quantity, cumulative_quantity, is_cumulative, realized_pnl,
                 maker, fee_status, occurred_at, raw_json)
                VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id
                """,
                (
                    event.event_key(),
                    int(event.credential_id or 0),
                    int(event.user_id or 1),
                    str(event.exchange_id or "").lower(),
                    str(event.market_type or "swap").lower(),
                    str(event.account_id or ""),
                    str(event.symbol or ""),
                    str(event.exchange_order_id or ""),
                    str(event.client_order_id or ""),
                    str(event.exchange_fill_id or ""),
                    str(event.side or "").lower(),
                    str(event.position_side or "").lower(),
                    str(event.order_status or ""),
                    float(event.price or 0.0),
                    float(event.quantity or 0.0),
                    float(event.cumulative_quantity or 0.0),
                    bool(event.is_cumulative),
                    float(event.realized_pnl) if event.realized_pnl is not None else None,
                    event.maker,
                    str(event.fee_status or "pending"),
                    event.occurred_at,
                    json.dumps(raw, ensure_ascii=False, default=str),
                ),
            )
            row = cur.fetchone()
            event_id = int((row or {}).get("id") or 0)
            if event_id:
                aggregated: Dict[tuple[str, str], Dict[str, Any]] = {}
                for component in event.fees:
                    key = (
                        str(component.fee_type or "trade"),
                        str(component.currency or "").upper(),
                    )
                    current = aggregated.setdefault(
                        key,
                        {
                            "amount": 0.0,
                            "quote_amount": 0.0,
                            "quote_known": True,
                            "source": str(component.source or "websocket"),
                        },
                    )
                    current["amount"] += float(component.amount or 0.0)
                    if component.quote_amount is None:
                        current["quote_known"] = False
                    else:
                        current["quote_amount"] += float(component.quote_amount)
                for (fee_type, currency), component in aggregated.items():
                    cur.execute(
                        """
                        INSERT INTO qd_execution_fee_components
                        (execution_event_id, fee_type, currency, amount, quote_amount, source)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (execution_event_id, fee_type, currency)
                        DO UPDATE SET amount = EXCLUDED.amount,
                                      quote_amount = COALESCE(EXCLUDED.quote_amount, qd_execution_fee_components.quote_amount),
                                      source = EXCLUDED.source
                        """,
                        (
                            event_id,
                            fee_type,
                            currency,
                            float(component["amount"]),
                            float(component["quote_amount"]) if component["quote_known"] else None,
                            str(component["source"]),
                        ),
                    )
            db.commit()
            cur.close()
        return event_id or None

    def pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_execution_events
                WHERE processed_at IS NULL
                  AND process_attempts < 20
                ORDER BY received_at ASC, id ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        return rows

    def fee_components(self, event_id: int) -> List[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT * FROM qd_execution_fee_components WHERE execution_event_id = %s ORDER BY id",
                (int(event_id),),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        return rows

    def resolve_binding(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        credential_id = int(event.get("credential_id") or 0)
        exchange_id = str(event.get("exchange_id") or "").lower()
        market_type = str(event.get("market_type") or "").lower()
        exchange_order_id = str(event.get("exchange_order_id") or "")
        client_order_id = str(event.get("client_order_id") or "")
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM qd_live_order_bindings
                WHERE credential_id = %s
                  AND exchange_id = %s
                  AND (market_type = %s OR market_type = '' OR %s = '')
                  AND (
                    (%s <> '' AND exchange_order_id = %s)
                    OR (%s <> '' AND client_order_id = %s)
                  )
                ORDER BY
                  CASE WHEN exchange_order_id = %s AND %s <> '' THEN 0 ELSE 1 END,
                  id DESC
                LIMIT 1
                """,
                (
                    credential_id,
                    exchange_id,
                    market_type,
                    market_type,
                    exchange_order_id,
                    exchange_order_id,
                    client_order_id,
                    client_order_id,
                    exchange_order_id,
                    exchange_order_id,
                ),
            )
            row = cur.fetchone()
            cur.close()
        return dict(row) if row else self._discover_legacy_binding(event)

    def _discover_legacy_binding(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        credential_id = int(event.get("credential_id") or 0)
        exchange_id = str(event.get("exchange_id") or "").lower()
        market_type = str(event.get("market_type") or "").lower()
        exchange_order_id = str(event.get("exchange_order_id") or "")
        client_order_id = str(event.get("client_order_id") or "")
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT po.*, 'pending_order' AS owner_type, po.id AS owner_id
                FROM pending_orders po
                WHERE po.credential_id = %s
                  AND LOWER(COALESCE(po.exchange_id, '')) = %s
                  AND (
                    (%s <> '' AND po.exchange_order_id = %s)
                    OR (%s <> '' AND po.client_order_id = %s)
                  )
                ORDER BY po.id DESC LIMIT 1
                """,
                (
                    credential_id,
                    exchange_id,
                    exchange_order_id,
                    exchange_order_id,
                    client_order_id,
                    client_order_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                cur.execute(
                    """
                    SELECT gro.*, 'grid' AS owner_type, gro.id AS owner_id,
                           st.user_id, st.credential_id, st.market_type,
                           COALESCE(st.exchange_config->>'exchange_id', '') AS exchange_id
                    FROM qd_grid_resting_orders gro
                    JOIN qd_strategies_trading st ON st.id = gro.strategy_id
                    WHERE (
                        (%s <> '' AND gro.exchange_order_id = %s)
                        OR (%s <> '' AND gro.client_order_id = %s)
                    )
                    ORDER BY gro.id DESC LIMIT 1
                    """,
                    (exchange_order_id, exchange_order_id, client_order_id, client_order_id),
                )
                row = cur.fetchone()
            cur.close()
        if not row:
            return None
        data = dict(row)
        self.register_binding(
            credential_id=int(data.get("credential_id") or credential_id),
            exchange_id=exchange_id,
            # A broker push uses its canonical stock market (for example
            # ``hkstock``), while legacy pending rows may store execution
            # product type ``spot``. Register with the event market first so
            # the recursive lookup below can actually match the new binding.
            market_type=str(market_type or data.get("market_type")),
            owner_type=str(data.get("owner_type")),
            owner_id=int(data.get("owner_id") or 0),
            user_id=int(data.get("user_id") or 1),
            strategy_id=int(data.get("strategy_id") or 0),
            pending_order_id=int(data.get("id") or 0) if data.get("owner_type") == "pending_order" else 0,
            strategy_run_id=int(data.get("strategy_run_id") or 0),
            order_intent_id=int(data.get("order_intent_id") or 0),
            symbol=str(data.get("symbol") or ""),
            signal_type=str(data.get("signal_type") or data.get("purpose") or ""),
            client_order_id=str(data.get("client_order_id") or client_order_id),
            exchange_order_id=str(data.get("exchange_order_id") or exchange_order_id),
            observed_filled=float(data.get("filled") or data.get("processed_fill_qty") or 0.0),
        )
        return self.resolve_binding(event)

    def mark_processed(self, event_id: int) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_execution_events
                SET processed_at = NOW(), process_error = ''
                WHERE id = %s
                """,
                (int(event_id),),
            )
            db.commit()
            cur.close()

    def mark_failed(self, event_id: int, error: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_execution_events
                SET process_attempts = process_attempts + 1,
                    process_error = %s
                WHERE id = %s
                """,
                (str(error or "")[:2000], int(event_id)),
            )
            db.commit()
            cur.close()

    def update_health(
        self,
        *,
        stream_key: str,
        credential_id: int,
        exchange_id: str,
        market_type: str,
        state: str,
        error: str = "",
        event: bool = False,
        reconnect: bool = False,
        rest_fallback: bool = False,
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_execution_stream_health
                (stream_key, credential_id, exchange_id, market_type, state,
                 last_event_at, last_connected_at, last_disconnected_at,
                 reconnect_count, rest_fallback, last_error, updated_at)
                VALUES
                (%s,%s,%s,%s,%s,
                 CASE WHEN %s THEN NOW() ELSE NULL END,
                 CASE WHEN %s = 'connected' THEN NOW() ELSE NULL END,
                 CASE WHEN %s IN ('disconnected','error') THEN NOW() ELSE NULL END,
                 CASE WHEN %s THEN 1 ELSE 0 END,
                 %s,%s,NOW())
                ON CONFLICT (stream_key) DO UPDATE SET
                    state = EXCLUDED.state,
                    last_event_at = CASE WHEN %s THEN NOW() ELSE qd_execution_stream_health.last_event_at END,
                    last_connected_at = CASE WHEN %s = 'connected' THEN NOW() ELSE qd_execution_stream_health.last_connected_at END,
                    last_disconnected_at = CASE WHEN %s IN ('disconnected','error') THEN NOW() ELSE qd_execution_stream_health.last_disconnected_at END,
                    reconnect_count = qd_execution_stream_health.reconnect_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    rest_fallback = EXCLUDED.rest_fallback,
                    last_error = EXCLUDED.last_error,
                    updated_at = NOW()
                """,
                (
                    str(stream_key),
                    int(credential_id or 0),
                    str(exchange_id or ""),
                    str(market_type or ""),
                    str(state or ""),
                    bool(event),
                    str(state or ""),
                    str(state or ""),
                    bool(reconnect),
                    bool(rest_fallback),
                    str(error or "")[:2000],
                    bool(event),
                    str(state or ""),
                    str(state or ""),
                    bool(reconnect),
                ),
            )
            db.commit()
            cur.close()
