"""Durable execution event and live-order binding repository."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.services.execution_streams.events import ExecutionEvent, normalize_symbol
from app.services.exchange_execution import coalesce_exchange_config_from_payload
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
    ) -> int:
        if not owner_type or int(owner_id or 0) <= 0:
            return 0
        binding_id = 0
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
                RETURNING id
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
            row = cur.fetchone() or {}
            binding_id = int(row.get("id") or 0)
            db.commit()
            cur.close()
        identifiers = []
        if client_order_id:
            identifiers.append(("client_order_id", client_order_id))
        if exchange_order_id:
            identifiers.append(("exchange_order_id", exchange_order_id))
        self.register_identifiers(binding_id, identifiers, role="entry")
        return binding_id

    def register_identifiers(
        self,
        binding_id: int,
        identifiers: Iterable[Mapping[str, Any] | Tuple[str, Any]],
        *,
        role: str = "",
    ) -> int:
        bid = int(binding_id or 0)
        if bid <= 0:
            return 0
        rows: List[Tuple[str, str, str]] = []
        for item in identifiers or []:
            if isinstance(item, Mapping):
                identifier_type = str(item.get("type") or item.get("identifier_type") or "").strip()
                identifier_value = str(item.get("value") or item.get("identifier_value") or "").strip()
                item_role = str(item.get("role") or role or "").strip()
            else:
                try:
                    identifier_type, identifier_value = item
                except (TypeError, ValueError):
                    continue
                identifier_type = str(identifier_type or "").strip()
                identifier_value = str(identifier_value or "").strip()
                item_role = str(role or "").strip()
            if identifier_type and identifier_value:
                rows.append((identifier_type[:40], identifier_value[:160], item_role[:40]))
        if not rows:
            return 0
        inserted = 0
        with get_db_connection() as db:
            cur = db.cursor()
            for identifier_type, identifier_value, item_role in rows:
                cur.execute(
                    """
                    INSERT INTO qd_live_order_binding_identifiers
                    (binding_id, identifier_type, identifier_value, role)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (binding_id, identifier_type, identifier_value)
                    DO UPDATE SET role = CASE
                        WHEN EXCLUDED.role <> '' THEN EXCLUDED.role
                        ELSE qd_live_order_binding_identifiers.role
                    END
                    """,
                    (bid, identifier_type, identifier_value, item_role),
                )
                inserted += 1
            db.commit()
            cur.close()
        return inserted

    def ingest(self, event: ExecutionEvent) -> Optional[int]:
        from app.services.live_trading.partner_attribution import redact_partner_attribution

        raw = redact_partner_attribution(event.raw or {})
        raw = dict(raw, _qd_execution={
            "cumulative_average_price": event.cumulative_average_price,
            "fees_cumulative": event.fees_cumulative,
            "order_identifiers": list(event.order_identifiers or []),
        })
        with get_db_connection() as db:
            cur = db.cursor()
            if event.exchange_fill_id and not event.fees_cumulative:
                cur.execute("""SELECT id FROM qd_execution_events WHERE credential_id = %s
                    AND exchange_id = %s AND market_type = %s AND symbol = %s
                    AND exchange_order_id = %s AND exchange_fill_id = %s LIMIT 1""",
                    (int(event.credential_id or 0), event.exchange_id.lower(), event.market_type.lower(),
                     event.symbol, event.exchange_order_id, event.exchange_fill_id))
                if cur.fetchone():
                    cur.close()
                    return None
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
                  AND next_attempt_at <= NOW()
                  AND NOT EXISTS (
                    SELECT 1
                    FROM qd_grid_actor_events AS actor_event
                    WHERE actor_event.execution_event_id = qd_execution_events.id
                  )
                ORDER BY received_at ASC, id ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        return rows

    def get(self, event_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    "SELECT * FROM qd_execution_events WHERE id = %s",
                    (int(event_id),),
                )
                row = cur.fetchone()
            finally:
                cur.close()
        return dict(row) if row else None

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

    @staticmethod
    def _event_identifiers(event: Mapping[str, Any]) -> List[Tuple[str, str]]:
        identifiers: List[Tuple[str, str]] = []
        seen = set()

        def add(identifier_type: Any, value: Any) -> None:
            key = (str(identifier_type or "").strip(), str(value or "").strip())
            if not key[0] or not key[1] or key in seen:
                return
            seen.add(key)
            identifiers.append(key)

        add("exchange_order_id", event.get("exchange_order_id"))
        add("client_order_id", event.get("client_order_id"))
        raw = event.get("raw_json") or event.get("raw") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = {}
        if not isinstance(raw, Mapping):
            return identifiers
        execution = raw.get("_qd_execution")
        if isinstance(execution, Mapping):
            for item in execution.get("order_identifiers") or []:
                if isinstance(item, Mapping):
                    add(item.get("type"), item.get("value"))
        venue = raw.get("data") if isinstance(raw.get("data"), Mapping) else raw
        if isinstance(venue, Mapping):
            for identifier_type, keys in {
                "algo_id": ("algoId", "algo_id"),
                "algo_client_id": ("algoClOrdId", "clientAlgoId"),
                "parent_order_id": (
                    "parentOrderId", "parent_order_id", "source_order_id",
                    "relation_tpsl_order_id", "planOrderId", "triggerOrderId",
                    "trigger_order_id", "strategyOrderId",
                ),
                "parent_client_order_id": ("parentOrderLinkId",),
                "child_order_id": ("suborder_id", "trade_id", "relation_order_id"),
                "child_client_order_id": ("suborder_text",),
            }.items():
                for key in keys:
                    add(identifier_type, venue.get(key))
        return identifiers

    @staticmethod
    def _protection_side_matches(binding: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
        signal_type = str(binding.get("signal_type") or "").strip().lower()
        side = str(event.get("side") or "").strip().lower()
        role = str(binding.get("matched_identifier_role") or "").strip().lower()
        if not role or role == "entry" or not side:
            return True
        if "long" in signal_type:
            return side in {"sell", "short"}
        if "short" in signal_type:
            return side in {"buy", "long"}
        return True

    def _resolve_identifier_binding(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        identifiers = self._event_identifiers(event)
        if not identifiers:
            return None
        credential_id = int(event.get("credential_id") or 0)
        exchange_id = str(event.get("exchange_id") or "").lower()
        market_type = str(event.get("market_type") or "").lower()
        symbol = normalize_symbol(event.get("symbol") or "").replace("/", "").replace("-", "")
        user_id = int(event.get("user_id") or 0)
        clauses = []
        params: List[Any] = [
            credential_id,
            exchange_id,
            market_type,
            market_type,
            market_type,
            symbol,
            user_id,
            user_id,
        ]
        for identifier_type, identifier_value in identifiers:
            clauses.append("(identifier.identifier_type = %s AND identifier.identifier_value = %s)")
            params.extend((identifier_type, identifier_value))
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT binding.*, identifier.role AS matched_identifier_role,
                       identifier.identifier_type AS matched_identifier_type
                FROM qd_live_order_binding_identifiers AS identifier
                JOIN qd_live_order_bindings AS binding ON binding.id = identifier.binding_id
                WHERE binding.credential_id = %s
                  AND binding.exchange_id = %s
                  AND (binding.market_type = %s OR binding.market_type = '' OR %s = ''
                    OR (binding.market_type IN ('crypto', 'spot') AND %s IN ('crypto', 'spot')))
                  AND (binding.symbol = '' OR regexp_replace(upper(binding.symbol), '[-/_]', '', 'g') = %s)
                  AND (%s <= 0 OR binding.user_id = %s)
                  AND ({' OR '.join(clauses)})
                  AND (binding.strategy_id <= 0 OR EXISTS (
                    SELECT 1 FROM qd_strategies_trading AS strategy
                    WHERE strategy.id = binding.strategy_id
                  ))
                ORDER BY binding.id DESC
                """,
                tuple(params),
            )
            candidates = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for candidate in candidates:
            if self._protection_side_matches(candidate, event):
                grouped.setdefault(int(candidate.get("id") or 0), []).append(candidate)
        grouped.pop(0, None)
        matches: Dict[int, Dict[str, Any]] = {}
        for binding_id, binding_rows in grouped.items():
            protection_rows = [
                row
                for row in binding_rows
                if str(row.get("matched_identifier_role") or "").strip().lower()
                not in {"", "entry"}
            ]
            protection_roles = {
                str(row.get("matched_identifier_role") or "").strip().lower()
                for row in protection_rows
            }
            if len(protection_roles) > 1:
                continue
            matches[binding_id] = protection_rows[0] if protection_rows else binding_rows[0]
        if len(matches) != 1:
            return None
        binding = next(iter(matches.values()))
        child_order_id = str(event.get("exchange_order_id") or "").strip()
        if child_order_id and str(binding.get("matched_identifier_role") or "") != "entry":
            self.register_identifiers(
                int(binding.get("id") or 0),
                [("exchange_order_id", child_order_id)],
                role=str(binding.get("matched_identifier_role") or "protection"),
            )
        return binding

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
                  AND (
                    strategy_id <= 0
                    OR EXISTS (
                      SELECT 1
                      FROM qd_strategies_trading AS strategy
                      WHERE strategy.id = qd_live_order_bindings.strategy_id
                    )
                  )
                  AND (market_type = %s OR market_type = '' OR %s = ''
                    OR (market_type IN ('crypto', 'spot') AND %s IN ('crypto', 'spot')))
                  AND (symbol = '' OR regexp_replace(upper(symbol), '[-/_]', '', 'g') = %s)
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
                    market_type,
                    normalize_symbol(event.get("symbol") or "").replace("/", "").replace("-", ""),
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
        if row:
            return dict(row)
        identifier_binding = self._resolve_identifier_binding(event)
        return identifier_binding or self._discover_legacy_binding(event)

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
                  AND (LOWER(COALESCE(po.market_type, '')) = %s
                    OR (LOWER(po.market_type) IN ('crypto', 'spot') AND %s IN ('crypto', 'spot')))
                  AND regexp_replace(upper(po.symbol), '[-/_]', '', 'g') = %s
                  AND (
                    (%s <> '' AND po.exchange_order_id = %s)
                    OR (%s <> '' AND po.client_order_id = %s)
                  )
                ORDER BY po.id DESC LIMIT 1
                """,
                (
                    credential_id,
                    exchange_id,
                    market_type,
                    market_type,
                    normalize_symbol(event.get("symbol") or "").replace("/", "").replace("-", ""),
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
                           st.user_id, st.market_type,
                           to_jsonb(st)->>'credential_id' AS credential_id,
                           st.exchange_config::text AS exchange_config,
                           st.trading_config::text AS trading_config
                    FROM qd_grid_resting_orders gro
                    JOIN qd_strategies_trading st ON st.id = gro.strategy_id
                    WHERE (
                        (%s <> '' AND gro.exchange_order_id = %s)
                        OR (%s <> '' AND gro.client_order_id = %s)
                    )
                    ORDER BY gro.id DESC
                    """,
                    (exchange_order_id, exchange_order_id, client_order_id, client_order_id),
                )
                candidates = cur.fetchall() or []
                row = next((binding for candidate in candidates
                            if (binding := self._matching_grid_binding(candidate, event)) is not None), None)
            cur.close()
        if not row:
            return None
        data = dict(row)
        self.register_binding(
            credential_id=int(data.get("credential_id") or credential_id),
            exchange_id=exchange_id,
            market_type=str(data.get("market_type") or market_type),
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

    @staticmethod
    def _matching_grid_binding(candidate, event):
        data = dict(candidate)
        if normalize_symbol(data.get("symbol") or "") != normalize_symbol(event.get("symbol") or ""):
            return None
        for key in ("exchange_config", "trading_config"):
            raw = data.get(key)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    return None
            data[key] = raw if isinstance(raw, dict) else {}
        config = coalesce_exchange_config_from_payload(data)
        try:
            credential_id = int(config.get("credential_id") or config.get("credentials_id") or 0)
        except (ValueError, TypeError):
            return None
        exchange_id = str(config.get("exchange_id") or config.get("exchangeId") or config.get("exchange") or "").lower()
        market_type = str(data.get("trading_config", {}).get("market_type") or data.get("market_type") or "").lower()
        if credential_id <= 0 or credential_id != int(event.get("credential_id") or 0):
            return None
        if exchange_id != str(event.get("exchange_id") or "").lower():
            return None
        if event.get("market_type") and market_type != str(event["market_type"]).lower():
            return None
        if event.get("user_id") and int(data.get("user_id") or 0) != int(event["user_id"]):
            return None
        data.update(credential_id=credential_id, exchange_id=exchange_id, market_type=market_type)
        return data

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
                    next_attempt_at = NOW() + LEAST(300, POWER(2, LEAST(process_attempts, 8))) * INTERVAL '1 second',
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
                RETURNING stream_key
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
