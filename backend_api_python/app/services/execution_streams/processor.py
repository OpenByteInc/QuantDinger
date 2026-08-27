"""Project durable execution events into strategy, grid, and fee ledgers."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config
from app.services.execution_streams.repository import ExecutionEventRepository
from app.services.live_trading.factory import create_client
from app.services.live_trading.fee_quote import fee_to_quote
from app.services.pending_orders.fill_records import persist_strategy_fill, trade_close_reason_from_payload
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

_FUTU_QUOTE_FEE_CCY = {"HKD", "USD", "CNH", "CNY", "USDT", "USDC"}


class ExecutionEventProcessor:
    def __init__(self, repository: Optional[ExecutionEventRepository] = None) -> None:
        self.repository = repository or ExecutionEventRepository()

    def process_pending(self, limit: int = 100) -> int:
        processed = 0
        for event in self.repository.pending(limit=limit):
            event_id = int(event.get("id") or 0)
            try:
                if self._already_projected(event_id):
                    self.repository.mark_processed(event_id)
                    processed += 1
                    continue
                binding = self.repository.resolve_binding(event)
                if not binding:
                    # Keep the account-level event, but never attribute an
                    # unowned/manual trade to a strategy.  Give the order
                    # submission transaction time to persist its binding.
                    received = event.get("received_at")
                    if isinstance(received, datetime):
                        if received.tzinfo is None:
                            received = received.replace(tzinfo=timezone.utc)
                        age = (datetime.now(timezone.utc) - received).total_seconds()
                    else:
                        age = 999.0
                    if age >= 120:
                        self.repository.mark_processed(event_id)
                        processed += 1
                    continue
                owner_type = str(binding.get("owner_type") or "")
                if owner_type == "pending_order":
                    self._process_pending_order(event, binding)
                elif owner_type == "grid":
                    self._process_grid(event, binding)
                elif owner_type == "grid_market":
                    self._process_grid_market(event, binding)
                elif owner_type == "quick_trade":
                    self._process_quick_trade(event, binding)
                else:
                    self.repository.mark_processed(event_id)
                    processed += 1
                    continue
                self.repository.mark_processed(event_id)
                processed += 1
            except Exception as exc:
                self.repository.mark_failed(event_id, str(exc))
                logger.warning("Execution event projection failed id=%s: %s", event_id, exc)
        return processed

    @staticmethod
    def _already_projected(event_id: int) -> bool:
        if event_id <= 0:
            return False
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT 1 FROM qd_strategy_trades WHERE execution_event_id = %s LIMIT 1",
                (event_id,),
            )
            exists = cur.fetchone() is not None
            cur.close()
        return exists

    def _fees(
        self,
        event: Dict[str, Any],
        *,
        client: Any,
        symbol: str,
        price: float,
    ) -> Tuple[Dict[str, float], Optional[float]]:
        components = self.repository.fee_components(int(event.get("id") or 0))
        fees: Dict[str, float] = {}
        quote_total = 0.0
        quote_known = True
        for row in components:
            currency = str(row.get("currency") or "").upper()
            amount = float(row.get("amount") or 0.0)
            fees[currency] = fees.get(currency, 0.0) + amount
            converted = row.get("quote_amount")
            if converted is None:
                converted = fee_to_quote(
                    client,
                    symbol=symbol,
                    fee=amount,
                    fee_ccy=currency,
                    fill_price=price,
                )
            if converted is None and client is None:
                converted = amount
            if converted is None:
                quote_known = False
            else:
                quote_total += float(converted)
        return fees, quote_total if quote_known else None

    def _fee_client_required(self, event: Dict[str, Any], exchange_config: Dict[str, Any]) -> bool:
        components = self.repository.fee_components(int(event.get("id") or 0))
        if not components:
            return False
        if all(row.get("quote_amount") is not None for row in components):
            return False
        exchange_id = str(
            (exchange_config or {}).get("exchange_id") or event.get("exchange_id") or ""
        ).strip().lower()
        if exchange_id == "futu":
            pending = [row for row in components if row.get("quote_amount") is None]
            return not all(
                str(row.get("currency") or "").upper() in _FUTU_QUOTE_FEE_CCY
                for row in pending
            )
        return True

    @contextmanager
    def _fee_client(
        self,
        event: Dict[str, Any],
        exchange_config: Dict[str, Any],
        market_type: str,
    ) -> Iterator[Any]:
        if not self._fee_client_required(event, exchange_config):
            yield None
            return
        client = create_client(
            exchange_config,
            market_type=market_type,
            need_quote=False,
        )
        try:
            yield client
        finally:
            disconnect = getattr(client, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass

    @staticmethod
    def _fee_storage(fees: Dict[str, float]) -> Tuple[float, str]:
        if len(fees) == 1:
            ccy, amount = next(iter(fees.items()))
            return float(amount), ccy
        if fees:
            return 0.0, "MIXED"
        return 0.0, ""

    @staticmethod
    def _fill_progress(
        *,
        previous: float,
        durable_recorded: float,
        event_qty: float,
        cumulative: float,
        is_cumulative: bool,
    ) -> Tuple[float, float]:
        """Return the unrecorded delta and monotonic cumulative target."""
        durable = max(0.0, float(previous), float(durable_recorded))
        if is_cumulative or cumulative > 0:
            target = max(durable, max(0.0, float(cumulative)))
            return max(0.0, target - durable), target
        ledger_ahead = max(0.0, durable - float(previous))
        delta = max(0.0, max(0.0, float(event_qty)) - ledger_ahead)
        return delta, durable + delta

    def _process_pending_order(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        pending_id = int(binding.get("pending_order_id") or binding.get("owner_id") or 0)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM pending_orders WHERE id = %s FOR UPDATE", (pending_id,))
            pending = cur.fetchone()
            if not pending:
                cur.close()
                return
            pending = dict(pending)
            payload = self._json(pending.get("payload_json"))
            previous = float(pending.get("filled") or 0.0)
            durable_recorded = previous
            if str(event.get("exchange_id") or "").lower() == "futu":
                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) AS recorded
                    FROM qd_strategy_trades
                    WHERE pending_order_id = %s
                    """,
                    (pending_id,),
                )
                trade_row = cur.fetchone() or {}
                durable_recorded = max(previous, float(trade_row.get("recorded") or 0.0))
            event_qty = max(0.0, float(event.get("quantity") or 0.0))
            cumulative = max(0.0, float(event.get("cumulative_quantity") or 0.0))
            delta, target = self._fill_progress(
                previous=previous,
                durable_recorded=durable_recorded,
                event_qty=event_qty,
                cumulative=cumulative,
                is_cumulative=bool(event.get("is_cumulative")),
            )
            price = float(event.get("price") or pending.get("avg_price") or 0.0)
            previous_avg = float(pending.get("avg_price") or 0.0)
            durable_avg = previous_avg or price
            aggregate_avg = (
                ((durable_recorded * durable_avg) + (delta * price)) / target
                if target > 0 and delta > 0 and price > 0
                else durable_avg
            )
            status = str(event.get("order_status") or "")
            queue_status = "filled" if status == "filled" else "sent"
            if status == "cancelled":
                queue_status = "cancelled"
            cur.execute(
                """
                UPDATE pending_orders
                SET filled = GREATEST(COALESCE(filled, 0), %s),
                    avg_price = CASE WHEN %s > 0 THEN %s ELSE avg_price END,
                    status = CASE
                        WHEN status IN ('failed','cancelled') THEN status
                        WHEN %s = 'filled' THEN 'filled'
                        ELSE status
                    END,
                    fee_status = %s,
                    fee_source = 'websocket',
                    executed_at = CASE WHEN %s > 0 THEN COALESCE(executed_at, NOW()) ELSE executed_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    target,
                    aggregate_avg,
                    aggregate_avg,
                    queue_status,
                    str(event.get("fee_status") or "pending"),
                    target,
                    pending_id,
                ),
            )
            cur.execute(
                """
                UPDATE qd_live_order_bindings
                SET observed_filled = GREATEST(observed_filled, %s),
                    exchange_order_id = COALESCE(NULLIF(%s, ''), exchange_order_id),
                    status = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    target,
                    str(event.get("exchange_order_id") or ""),
                    status or "open",
                    int(binding.get("id") or 0),
                ),
            )
            db.commit()
            cur.close()

        strategy_id = int(binding.get("strategy_id") or pending.get("strategy_id") or 0)
        if strategy_id <= 0:
            return
        sc = load_strategy_configs(strategy_id)
        exchange_config = resolve_exchange_config(
            sc.get("exchange_config") or {},
            user_id=int(sc.get("user_id") or pending.get("user_id") or 1),
        )
        market_type = str(event.get("market_type") or pending.get("market_type") or "swap")
        symbol = str(event.get("symbol") or pending.get("symbol") or "")
        price = float(event.get("price") or pending.get("avg_price") or 0.0)
        with self._fee_client(event, exchange_config, market_type) as client:
            fees, commission_quote = self._fees(event, client=client, symbol=symbol, price=price)
        commission, commission_ccy = self._fee_storage(fees)

        if delta > 1e-12 and price > 0:
            signal_type = str(binding.get("signal_type") or pending.get("signal_type") or payload.get("signal_type") or "")
            position_filled = self._spot_position_quantity(
                market_type=market_type,
                symbol=symbol,
                signal_type=signal_type,
                gross_quantity=delta,
                fees=fees,
            )
            persist_strategy_fill(
                strategy_id=strategy_id,
                symbol=symbol,
                signal_type=signal_type,
                filled=delta,
                position_filled=position_filled,
                avg_price=price,
                exchange_config=exchange_config,
                market_type=market_type,
                order_id=pending_id,
                fill_source="private_websocket",
                commission=commission,
                commission_ccy=commission_ccy,
                commission_quote=commission_quote,
                profit=float(event.get("realized_pnl")) if event.get("realized_pnl") is not None else None,
                close_reason=trade_close_reason_from_payload(payload, signal_type),
                strategy_run_id=int(binding.get("strategy_run_id") or pending.get("strategy_run_id") or 0),
                order_intent_id=int(binding.get("order_intent_id") or pending.get("order_intent_id") or 0),
                exchange_id=str(event.get("exchange_id") or ""),
                exchange_order_id=str(event.get("exchange_order_id") or ""),
                exchange_fill_id=str(event.get("exchange_fill_id") or ""),
                execution_event_id=int(event.get("id") or 0),
                fee_status=str(event.get("fee_status") or "pending"),
                fee_source="websocket",
                raw_fill=self._json(event.get("raw_json")),
            )
            append_strategy_log(
                strategy_id,
                "trade",
                f"Private stream fill: {signal_type} {symbol} qty={delta:.8f} @ {price:.8f}",
            )
        elif fees:
            self._apply_late_fee(
                execution_event_id=int(event.get("id") or 0),
                pending_order_id=pending_id,
                fees=fees,
                commission_quote=commission_quote,
                fee_status=str(event.get("fee_status") or "actual"),
            )

    @staticmethod
    def _spot_position_quantity(
        *,
        market_type: str,
        symbol: str,
        signal_type: str,
        gross_quantity: float,
        fees: Dict[str, float],
    ) -> float:
        from app.services.pending_orders.fill_records import (
            spot_position_fill_quantity,
        )

        return spot_position_fill_quantity(
            market_type=market_type,
            symbol=symbol,
            signal_type=signal_type,
            gross_quantity=gross_quantity,
            fees_by_ccy=fees,
        )

    @staticmethod
    def _apply_late_fee(
        *,
        execution_event_id: int,
        pending_order_id: int,
        fees: Dict[str, float],
        commission_quote: Optional[float],
        fee_status: str,
    ) -> None:
        commission, ccy = ExecutionEventProcessor._fee_storage(fees)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_execution_fee_projections
                    (execution_event_id, pending_order_id)
                VALUES (%s, %s)
                ON CONFLICT (execution_event_id) DO NOTHING
                RETURNING execution_event_id
                """,
                (int(execution_event_id), int(pending_order_id)),
            )
            if not cur.fetchone():
                db.commit()
                cur.close()
                return
            cur.execute(
                """
                SELECT id, commission, commission_ccy, commission_quote,
                       fee_status, fee_source
                FROM qd_strategy_trades
                WHERE pending_order_id = %s
                ORDER BY id DESC LIMIT 1
                FOR UPDATE
                """,
                (int(pending_order_id),),
            )
            row = cur.fetchone()
            if not row:
                db.rollback()
                cur.close()
                raise RuntimeError("strategy trade row is not ready for late fee projection")
            if row:
                existing_actual = str(row.get("fee_status") or "") in {"actual", "actual_zero"}
                existing_source = str(row.get("fee_source") or "")
                existing_commission = float(row.get("commission") or 0.0)
                # A REST response may already contain the authoritative fee.
                # Do not add the matching private-stream fee a second time.
                if not (
                    existing_actual
                    and existing_source not in {"", "websocket"}
                    and abs(existing_commission) > 1e-18
                ):
                    append = existing_source == "websocket"
                    cur.execute(
                        """
                        UPDATE qd_strategy_trades
                        SET commission = CASE
                                WHEN %s THEN COALESCE(commission, 0) + %s
                                ELSE %s
                            END,
                            commission_ccy = CASE
                                WHEN %s AND COALESCE(commission_ccy, '') NOT IN ('', %s)
                                    THEN 'MIXED'
                                ELSE %s
                            END,
                            commission_quote = CASE
                                WHEN %s THEN COALESCE(commission_quote, 0) + COALESCE(%s, 0)
                                ELSE %s
                            END,
                            fee_status = %s,
                            fee_source = 'websocket'
                        WHERE id = %s
                        """,
                        (
                            append,
                            commission,
                            commission,
                            append,
                            ccy,
                            ccy,
                            append,
                            commission_quote,
                            commission_quote,
                            fee_status,
                            int(row.get("id") or 0),
                        ),
                    )
            db.commit()
            cur.close()

    def _process_grid(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        order_id = int(binding.get("owner_id") or 0)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM qd_grid_resting_orders WHERE id = %s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return
            row = dict(row)
            observed_previous = float(row.get("filled_quantity") or 0.0)
            processed_previous = float(row.get("processed_fill_qty") or 0.0)
            event_qty = max(0.0, float(event.get("quantity") or 0.0))
            cumulative = max(0.0, float(event.get("cumulative_quantity") or 0.0))
            total = (
                max(observed_previous, cumulative)
                if bool(event.get("is_cumulative")) or cumulative > 0
                else observed_previous + event_qty
            )
            previous_avg = float(row.get("avg_fill_price") or 0.0)
            price = float(event.get("price") or row.get("price") or 0.0)
            observed_delta = max(0.0, total - observed_previous)
            delta = max(0.0, total - processed_previous)
            avg = (
                ((observed_previous * previous_avg) + (observed_delta * price)) / total
                if total > 0 and observed_delta > 0
                else previous_avg or price
            )
            status = str(event.get("order_status") or "")
            cur.execute(
                """
                UPDATE qd_grid_resting_orders
                SET filled_quantity = %s,
                    avg_fill_price = %s,
                    status = CASE
                        WHEN %s = 'filled' THEN 'filled'
                        WHEN %s = 'cancelled' THEN 'cancelled'
                        WHEN %s > 0 THEN 'partial'
                        ELSE status
                    END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (total, avg, status, status, total, order_id),
            )
            db.commit()
            cur.close()
        if delta <= 1e-12 or price <= 0:
            if self.repository.fee_components(int(event.get("id") or 0)):
                self._apply_grid_trade_fee(
                    event=event,
                    binding=binding,
                    grid_order_id=order_id,
                )
            return
        strategy_id = int(binding.get("strategy_id") or row.get("strategy_id") or 0)
        from app.services.grid.runner import get_runner

        runner = get_runner(strategy_id)
        if not runner:
            return
        sc = load_strategy_configs(strategy_id)
        exchange_config = resolve_exchange_config(
            sc.get("exchange_config") or {},
            user_id=int(sc.get("user_id") or 1),
        )
        with self._fee_client(
            event,
            exchange_config,
            str(event.get("market_type") or "swap"),
        ) as client:
            fees, commission_quote = self._fees(
                event,
                client=client,
                symbol=str(event.get("symbol") or row.get("symbol") or ""),
                price=price,
            )
        commission, commission_ccy = self._fee_storage(fees)
        from app.services.grid.resting_orders_repo import GridRestingOrder

        order = GridRestingOrder.from_row(row)
        runner.engine.on_order_filled(
            order,
            delta,
            price,
            commission=commission,
            commission_ccy=commission_ccy,
            commission_quote=commission_quote,
            fee_status=str(event.get("fee_status") or "pending"),
            fee_source="websocket",
            exchange_fill_id=str(event.get("exchange_fill_id") or ""),
            execution_event_id=int(event.get("id") or 0),
        )
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_grid_resting_orders
                SET processed_fill_qty = GREATEST(processed_fill_qty, %s),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (total, order_id),
            )
            db.commit()
            cur.close()

    def _process_grid_market(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        """Enrich an already-recorded synchronous grid market fill.

        The market quantity is written synchronously before the private event
        is projected, so this path applies authoritative fees and identifiers
        without posting the position or trade quantity a second time.
        """
        self._apply_grid_trade_fee(
            event=event,
            binding=binding,
            trade_id=int(binding.get("owner_id") or 0),
        )

    def _apply_grid_trade_fee(
        self,
        *,
        event: Dict[str, Any],
        binding: Dict[str, Any],
        grid_order_id: int = 0,
        trade_id: int = 0,
    ) -> None:
        event_id = int(event.get("id") or 0)
        if event_id <= 0:
            return
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_execution_owner_projections
                    (execution_event_id, owner_type, owner_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (execution_event_id, owner_type, owner_id) DO NOTHING
                RETURNING execution_event_id
                """,
                (
                    event_id,
                    str(binding.get("owner_type") or "grid"),
                    int(binding.get("owner_id") or trade_id or grid_order_id),
                ),
            )
            if not cur.fetchone():
                db.commit()
                cur.close()
                return
            if trade_id > 0:
                cur.execute(
                    "SELECT * FROM qd_strategy_trades WHERE id = %s FOR UPDATE",
                    (int(trade_id),),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM qd_strategy_trades
                    WHERE grid_order_id = %s
                    ORDER BY id DESC LIMIT 1
                    FOR UPDATE
                    """,
                    (int(grid_order_id),),
                )
            trade = cur.fetchone()
            if not trade:
                db.rollback()
                cur.close()
                raise RuntimeError("grid trade row is not ready for fee projection")
            trade = dict(trade)

            strategy_id = int(binding.get("strategy_id") or trade.get("strategy_id") or 0)
            sc = load_strategy_configs(strategy_id)
            exchange_config = resolve_exchange_config(
                sc.get("exchange_config") or {},
                user_id=int(sc.get("user_id") or trade.get("user_id") or 1),
            )
            market_type = str(event.get("market_type") or trade.get("market_type") or "swap")
            with self._fee_client(event, exchange_config, market_type) as client:
                fees, commission_quote = self._fees(
                    event,
                    client=client,
                    symbol=str(event.get("symbol") or trade.get("symbol") or ""),
                    price=float(event.get("price") or trade.get("price") or 0.0),
                )
            commission, commission_ccy = self._fee_storage(fees)
            existing_status = str(trade.get("fee_status") or "")
            existing_source = str(trade.get("fee_source") or "")
            existing_commission = float(trade.get("commission") or 0.0)
            rest_is_authoritative = (
                existing_status in {"actual", "actual_zero"}
                and existing_source not in {"", "websocket"}
                and (abs(existing_commission) > 1e-18 or existing_status == "actual_zero")
            )
            append = existing_source == "websocket" and not rest_is_authoritative
            if fees and not rest_is_authoritative:
                cur.execute(
                    """
                    UPDATE qd_strategy_trades
                    SET commission = CASE
                            WHEN %s THEN COALESCE(commission, 0) + %s
                            ELSE %s
                        END,
                        commission_ccy = CASE
                            WHEN %s AND COALESCE(commission_ccy, '') NOT IN ('', %s)
                                THEN 'MIXED'
                            ELSE %s
                        END,
                        commission_quote = CASE
                            WHEN %s THEN COALESCE(commission_quote, 0) + COALESCE(%s, 0)
                            ELSE %s
                        END,
                        fee_status = %s,
                        fee_source = 'websocket',
                        execution_event_id = CASE
                            WHEN COALESCE(execution_event_id, 0) = 0 THEN %s
                            ELSE execution_event_id
                        END,
                        exchange_fill_id = COALESCE(NULLIF(%s, ''), exchange_fill_id)
                    WHERE id = %s
                    """,
                    (
                        append,
                        commission,
                        commission,
                        append,
                        commission_ccy,
                        commission_ccy,
                        append,
                        commission_quote,
                        commission_quote,
                        str(event.get("fee_status") or "actual"),
                        event_id,
                        str(event.get("exchange_fill_id") or ""),
                        int(trade.get("id") or 0),
                    ),
                )
            elif str(event.get("fee_status") or "") == "actual_zero" and not rest_is_authoritative:
                cur.execute(
                    """
                    UPDATE qd_strategy_trades
                    SET fee_status = 'actual_zero',
                        fee_source = 'websocket',
                        execution_event_id = CASE
                            WHEN COALESCE(execution_event_id, 0) = 0 THEN %s
                            ELSE execution_event_id
                        END,
                        exchange_fill_id = COALESCE(NULLIF(%s, ''), exchange_fill_id)
                    WHERE id = %s
                    """,
                    (
                        event_id,
                        str(event.get("exchange_fill_id") or ""),
                        int(trade.get("id") or 0),
                    ),
                )
            cur.execute(
                """
                UPDATE qd_live_order_bindings
                SET observed_filled = GREATEST(observed_filled, %s),
                    exchange_order_id = COALESCE(NULLIF(%s, ''), exchange_order_id),
                    status = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    float(event.get("cumulative_quantity") or event.get("quantity") or 0.0),
                    str(event.get("exchange_order_id") or ""),
                    str(event.get("order_status") or "filled"),
                    int(binding.get("id") or 0),
                ),
            )
            db.commit()
            cur.close()

    def _process_quick_trade(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        trade_id = int(binding.get("owner_id") or 0)
        event_id = int(event.get("id") or 0)
        fees = self.repository.fee_components(event_id)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_execution_owner_projections
                    (execution_event_id, owner_type, owner_id)
                VALUES (%s, 'quick_trade', %s)
                ON CONFLICT (execution_event_id, owner_type, owner_id) DO NOTHING
                RETURNING execution_event_id
                """,
                (event_id, trade_id),
            )
            if not cur.fetchone():
                db.commit()
                cur.close()
                return
            cur.execute("SELECT * FROM qd_quick_trades WHERE id = %s FOR UPDATE", (trade_id,))
            current = cur.fetchone()
            if not current:
                db.rollback()
                cur.close()
                return
            current = dict(current)
            previous = float(current.get("filled_amount") or 0.0)
            event_qty = max(0.0, float(event.get("quantity") or 0.0))
            cumulative = max(0.0, float(event.get("cumulative_quantity") or 0.0))
            if bool(event.get("is_cumulative")) or cumulative > 0:
                total = max(previous, cumulative)
            elif (
                str(event.get("order_status") or "") == "filled"
                and str(current.get("status") or "") == "filled"
                and previous > 0
            ):
                # The synchronous REST result already captured the final fill.
                # Non-cumulative private events for the same completed order
                # enrich its fees but must not add the quantity again.
                total = previous
            else:
                total = previous + event_qty
            delta = max(0.0, total - previous)
            price = float(event.get("price") or 0.0)
            previous_avg = float(current.get("avg_fill_price") or 0.0)
            avg = (
                ((previous * previous_avg) + (delta * price)) / total
                if total > 0 and delta > 0 and price > 0
                else previous_avg or price
            )

            # REST enrichment may already have persisted the exchange's actual
            # commission.  In that case the same private-stream fill must not
            # add it a second time.  An empty currency means the REST result
            # did not provide a usable fee and WS remains authoritative.
            existing_fee_actual = bool(str(current.get("commission_ccy") or "").strip())
            commission = 0.0
            commission_ccy = str(current.get("commission_ccy") or "")
            commission_quote = current.get("commission_quote")
            if fees and not existing_fee_actual:
                by_ccy: Dict[str, float] = {}
                quote_total = 0.0
                quote_known = True
                for fee in fees:
                    ccy = str(fee.get("currency") or "").upper()
                    by_ccy[ccy] = by_ccy.get(ccy, 0.0) + float(fee.get("amount") or 0.0)
                    if fee.get("quote_amount") is None:
                        quote_known = False
                    else:
                        quote_total += float(fee.get("quote_amount") or 0.0)
                if len(by_ccy) == 1:
                    commission_ccy, commission = next(iter(by_ccy.items()))
                elif by_ccy:
                    commission_ccy = "MIXED"
                commission_quote = quote_total if quote_known else None
            cur.execute(
                """
                UPDATE qd_quick_trades
                SET filled_amount = GREATEST(COALESCE(filled_amount, 0), %s),
                    avg_fill_price = CASE WHEN %s > 0 THEN %s ELSE avg_fill_price END,
                    commission = CASE
                        WHEN %s THEN COALESCE(commission, 0)
                        ELSE COALESCE(commission, 0) + %s
                    END,
                    commission_ccy = CASE
                        WHEN %s THEN commission_ccy
                        ELSE %s
                    END,
                    commission_quote = CASE
                        WHEN %s THEN commission_quote
                        WHEN %s IS NULL THEN commission_quote
                        ELSE COALESCE(commission_quote, 0) + %s
                    END,
                    status = CASE WHEN %s = 'filled' THEN 'filled' ELSE status END
                WHERE id = %s
                """,
                (
                    total,
                    avg,
                    avg,
                    existing_fee_actual,
                    commission,
                    existing_fee_actual,
                    commission_ccy,
                    existing_fee_actual,
                    commission_quote,
                    commission_quote,
                    str(event.get("order_status") or ""),
                    trade_id,
                ),
            )
            db.commit()
            cur.close()

    @staticmethod
    def _json(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(str(value or "{}"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
