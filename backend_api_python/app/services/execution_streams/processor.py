"""Project durable execution events into strategy, grid, and fee ledgers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config
from app.services.execution_streams.repository import ExecutionEventRepository
from app.services.live_trading.factory import create_client
from app.services.live_trading.fee_quote import fee_to_quote
from app.services.pending_orders.fill_records import persist_strategy_fill, trade_close_reason_from_payload
from app.services.pending_orders.live_order_support import bind_instrument_product_contract
from app.utils.db import get_db_connection, get_db_transaction
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.services.live_trading.fill_accounting import (
    cumulative_delta,
    posted_totals,
    lock_strategy_fills,
    adjust_order_fee,
)
from app.services.execution_streams.fill_snapshot import prepare_event, complete_snapshot, combine_pending_snapshot

logger = get_logger(__name__)


class ExecutionEventProcessor:
    def __init__(
        self,
        repository: Optional[ExecutionEventRepository] = None,
        grid_actors: Any = None,
    ) -> None:
        self.repository = repository or ExecutionEventRepository()
        if grid_actors is None:
            from app.services.grid.actor import GridActorRepository

            grid_actors = GridActorRepository()
        self.grid_actors = grid_actors

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
                    if self._route_grid_event(event, binding):
                        processed += 1
                        continue
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

    def _route_grid_event(self, event: Dict[str, Any], binding: Dict[str, Any]) -> bool:
        enabled = str(os.getenv("GRID_ACTOR_ROUTING_ENABLED", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        event_id = int(event.get("id") or 0)
        if not enabled or event_id <= 0:
            self._process_grid(event, binding)
            return False
        strategy_id = int(binding.get("strategy_id") or 0)
        grid_order_id = int(binding.get("owner_id") or 0)
        self.grid_actors.enqueue(
            execution_event_id=event_id,
            strategy_id=strategy_id,
            grid_order_id=grid_order_id,
        )
        return True

    def process_grid_actor_event(
        self,
        actor_event,
        *,
        runner: Any,
        owner_id: str,
    ) -> None:
        event = self.repository.get(int(actor_event.execution_event_id))
        if not event:
            raise RuntimeError("gridActor.executionEventMissing")
        if int(getattr(runner, "strategy_id", 0) or 0) != int(actor_event.strategy_id):
            raise RuntimeError("gridActor.ownerRuntimeMismatch")
        binding = {
            "owner_id": int(actor_event.grid_order_id),
            "strategy_id": int(actor_event.strategy_id),
        }
        event["_runner"] = runner
        with get_db_transaction():
            self.grid_actors.lock_ownership(actor_event, owner_id=owner_id)
            self._process_grid(event, binding)
        checkpoint = getattr(runner, "checkpoint", None)
        if callable(checkpoint):
            checkpoint(
                force=True,
                last_execution_event_id=int(actor_event.execution_event_id),
            )

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
        if event.get("fees_cumulative"):
            price = float(event.get("cumulative_average_price") or price)
        components = (
            [dict(currency=ccy, amount=amount) for ccy, amount in event["_snapshot_fees"].items()]
            if "_snapshot_fees" in event
            else self.repository.fee_components(int(event.get("id") or 0))
        )
        if event.get("fees_cumulative"):
            components = list(components) + [
                dict(currency=ccy, amount=amount) for ccy, amount in event.get("_other_fees", {}).items()
            ]
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
            if converted is None:
                quote_known = False
            else:
                quote_total += float(converted)
        return fees, quote_total if quote_known else None

    @staticmethod
    def _fee_storage(fees: Dict[str, float]) -> Tuple[float, str]:
        if len(fees) == 1:
            ccy, amount = next(iter(fees.items()))
            return float(amount), ccy
        if fees:
            return 0.0, "MIXED"
        return 0.0, ""

    def _process_pending_order(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        sc = load_strategy_configs(int(binding.get("strategy_id") or 0))
        saved = dict(sc.get("exchange_config") or {})
        credential = int(event.get("credential_id") or binding.get("credential_id") or 0)
        if credential:
            saved.update(credential_id=credential, exchange_id=event.get("exchange_id"))
        cfg = resolve_exchange_config(saved, user_id=int(sc.get("user_id") or 1))
        cfg = bind_instrument_product_contract(
            cfg,
            sc.get("trading_config") or {},
            symbol=event.get("symbol") or "",
            exchange_id=event.get("exchange_id") or "",
            market_type=event.get("market_type") or "swap",
        )
        client = create_client(cfg, market_type=event.get("market_type") or "swap")
        event = complete_snapshot(prepare_event(event, client, cfg))
        with get_db_transaction():
            lock_strategy_fills(int(binding.get("strategy_id") or 0))
            self._project_pending_order(event, binding)

    def _project_pending_order(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        pending_id = int(binding.get("pending_order_id") or binding.get("owner_id") or 0)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM pending_orders WHERE id = %s FOR UPDATE", (pending_id,))
            pending = cur.fetchone()
            if not pending:
                cur.close()
                return
            # Recheck after locking: another projector may have committed while
            # this event was waiting for the same order.
            if self._already_projected(int(event.get("id") or 0)):
                cur.close()
                return
            pending = dict(pending)
            if event.get("fees_cumulative") and "_snapshot_fees" not in event:
                native, _ = self._fees(
                    event,
                    client=event.get("_client"),
                    symbol=event.get("symbol") or "",
                    price=float(event.get("price") or 0),
                )
                event["_snapshot_fees"] = native
            event, response = combine_pending_snapshot(event, pending)
            if response:
                cur.execute(
                    "UPDATE pending_orders SET exchange_response_json = %s WHERE id = %s",
                    (json.dumps(response), pending_id),
                )
            payload = self._json(pending.get("payload_json"))
            posted = posted_totals("pending_order_id", pending_id)
            previous = posted["quantity"]
            event_qty = max(0.0, float(event.get("quantity") or 0.0))
            cumulative = max(0.0, float(event.get("cumulative_quantity") or 0.0))
            if bool(event.get("is_cumulative")) or cumulative > 0:
                target = max(previous, cumulative)
                delta = max(0.0, target - previous)
            else:
                delta = event_qty
                target = previous + delta
            price = float(event.get("price") or 0.0)
            previous_avg = posted["average"]
            if event.get("cumulative_average_price"):
                delta, price = cumulative_delta(previous, previous_avg, cumulative, event["cumulative_average_price"])
            if delta > 0:
                from app.services.live_trading.fill_evidence import require_execution
                require_execution(delta, price)
            aggregate_avg = (
                ((previous * previous_avg) + (delta * price)) / target
                if target > 0 and delta > 0 and price > 0
                else previous_avg or price
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
                        WHEN %s = 'filled' THEN 'filled'
                        WHEN status = 'failed' AND %s > 0 THEN 'sent'
                        WHEN status IN ('failed','cancelled') THEN status
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
                    target,
                    str(event.get("fee_status") or "pending"),
                    target,
                    pending_id,
                ),
            )
            order_intent_id = int(pending.get("order_intent_id") or 0)
            if order_intent_id > 0:
                cur.execute(
                    """
                    UPDATE strategy_order_intents
                    SET status = CASE
                            WHEN status = 'filled' OR %s = 'filled' THEN 'filled'
                            WHEN %s > 0 THEN 'partially_filled'
                            WHEN %s = 'cancelled' THEN 'cancelled'
                            ELSE status
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (queue_status, target, queue_status, order_intent_id),
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
        exchange_config = bind_instrument_product_contract(
            exchange_config,
            sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {},
            symbol=symbol,
            exchange_id=str(event.get("exchange_id") or exchange_config.get("exchange_id") or ""),
            market_type=market_type,
        )
        exchange_config = event.get("_exchange_config") or exchange_config
        client = event.get("_client") or create_client(exchange_config, market_type=market_type)
        fees, commission_quote = self._fees(event, client=client, symbol=symbol, price=price)
        fees, commission_quote = self._incremental_fees(event, posted, delta, fees, commission_quote)
        commission, commission_ccy = self._fee_storage(fees)

        if delta > 1e-12 and price > 0:
            signal_type = str(
                binding.get("signal_type") or pending.get("signal_type") or payload.get("signal_type") or ""
            )
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
                fees_by_ccy=fees,
                profit=None,
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
        elif (
            event.get("fees_cumulative")
            and posted["quantity"] > 0
            and event.get("fee_status") in {"actual", "actual_zero"}
        ):
            adjust_order_fee(
                "pending_order_id", pending_id, fees, commission_quote, str(event.get("fee_status") or "pending")
            )
        elif fees:
            self._apply_late_fee(
                execution_event_id=int(event.get("id") or 0),
                pending_order_id=pending_id,
                exchange_fill_id=str(event.get("exchange_fill_id") or "").removesuffix(":commission"),
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
        exchange_fill_id: str = "",
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
                SELECT id, commission, commission_ccy, commission_quote, commission_breakdown,
                       fee_status, fee_source
                FROM qd_strategy_trades
                WHERE pending_order_id = %s
                ORDER BY CASE WHEN exchange_fill_id = %s THEN 0 ELSE 1 END, id DESC LIMIT 1
                FOR UPDATE
                """,
                (int(pending_order_id), exchange_fill_id),
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
                    existing_actual and existing_source not in {"", "websocket"} and abs(existing_commission) > 1e-18
                ):
                    adjust_order_fee("id", int(row["id"]), fees, commission_quote, fee_status)
            db.commit()
            cur.close()

    def _process_grid(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        from app.services.grid.runner import get_runner

        runner = event.get("_runner") or get_runner(int(binding.get("strategy_id") or 0))
        if not runner:
            raise RuntimeError("Grid runner is not ready for execution projection")
        cfg = bind_instrument_product_contract(
            runner.exchange_config,
            runner.engine.trading_config,
            symbol=event.get("symbol") or "",
            exchange_id=event.get("exchange_id") or "",
            market_type=event.get("market_type") or "swap",
        )
        client = create_client(cfg, market_type=event.get("market_type") or "swap")
        event = complete_snapshot(prepare_event(event, client, cfg))
        with get_db_transaction():
            lock_strategy_fills(int(binding.get("strategy_id") or 0))
            if self._already_projected(int(event.get("id") or 0)):
                return
            self._project_grid(event, binding)

    @staticmethod
    def _incremental_fees(event, posted, delta, fees, quote):
        if event.get("fee_status") == "pending":
            return {}, None
        if event.get("fees_cumulative"):
            if float(event.get("cumulative_quantity") or 0) + 1e-12 < posted["quantity"]:
                return {}, 0.0
            difference = {
                ccy: fees.get(ccy, 0.0) - posted["fees"].get(ccy, 0.0) for ccy in fees.keys() | posted["fees"].keys()
            }
            return difference, (quote - posted["quote"]) if quote is not None else None
        if delta <= 1e-12:
            if str(event.get("exchange_fill_id") or "").endswith(":commission"):
                return fees, quote
            return {}, 0.0
        if abs(delta - float(event.get("quantity") or 0)) > max(1e-12, delta * 1e-8):
            event["fee_status"] = "pending"
            return {}, None
        return fees, quote

    def _project_grid(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        order_id = int(binding.get("owner_id") or 0)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM qd_grid_resting_orders WHERE id = %s FOR UPDATE", (order_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return
            row = dict(row)
            posted = posted_totals("grid_order_id", order_id)
            observed_previous = float(row.get("filled_quantity") or 0.0)
            processed_previous = posted["quantity"]
            event_qty = max(0.0, float(event.get("quantity") or 0.0))
            cumulative = max(0.0, float(event.get("cumulative_quantity") or 0.0))
            total = (
                max(observed_previous, cumulative)
                if bool(event.get("is_cumulative")) or cumulative > 0
                else observed_previous + event_qty
            )
            previous_avg = float(row.get("avg_fill_price") or 0.0)
            price = float(event.get("price") or 0.0)
            observed_delta = max(0.0, total - observed_previous)
            delta = max(0.0, total - processed_previous)
            if event.get("cumulative_average_price"):
                delta, price = cumulative_delta(
                    processed_previous, posted["average"], cumulative, event["cumulative_average_price"]
                )
            if delta > 0:
                from app.services.live_trading.fill_evidence import require_execution
                require_execution(delta, price)
            avg = (
                ((observed_previous * previous_avg) + (observed_delta * price)) / total
                if total > 0 and observed_delta > 0
                else previous_avg or price
            )
            if cumulative >= observed_previous and event.get("cumulative_average_price"):
                avg = event["cumulative_average_price"]
            status = str(event.get("order_status") or "")
            if cumulative + 1e-12 < observed_previous:
                return
            if status not in {"filled", "cancelled"} and row.get("status") in {"filled", "cancelled"}:
                status = row["status"]
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
        strategy_id = int(binding.get("strategy_id") or row.get("strategy_id") or 0)
        from app.services.grid.runner import get_runner

        runner = event.get("_runner") or get_runner(strategy_id)
        if not runner:
            return
        exchange_config = runner.exchange_config
        client = event.get("_client") or create_client(
            exchange_config, market_type=str(event.get("market_type") or "swap")
        )
        fees, commission_quote = self._fees(
            event,
            client=client,
            symbol=str(event.get("symbol") or row.get("symbol") or ""),
            price=price or float(event.get("cumulative_average_price") or 0),
        )
        fees, commission_quote = self._incremental_fees(event, posted, delta, fees, commission_quote)
        commission, commission_ccy = self._fee_storage(fees)
        if delta <= 1e-12:
            if (
                event.get("fees_cumulative")
                and posted["quantity"] > 0
                and event.get("fee_status") in {"actual", "actual_zero"}
            ):
                adjust_order_fee(
                    "grid_order_id",
                    order_id,
                    fees,
                    commission_quote,
                    event.get("fee_status") or "pending",
                    str(event.get("fee_source") or "websocket"),
                )
            return
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
            fee_source=str(event.get("fee_source") or "websocket"),
            fees_by_ccy=fees,
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
        from app.services.live_trading.records import apply_fill_to_local_position
        from app.services.live_trading.leg_context import LegContext

        strategy_id = int(binding.get("strategy_id") or 0)
        trade_id = int(binding.get("owner_id") or 0)
        sc = load_strategy_configs(strategy_id)
        saved = dict(sc.get("exchange_config") or {})
        saved.update(
            credential_id=event.get("credential_id") or binding.get("credential_id") or saved.get("credential_id"),
            exchange_id=event.get("exchange_id") or saved.get("exchange_id"),
        )
        cfg = resolve_exchange_config(saved, user_id=int(sc.get("user_id") or 1))
        cfg = bind_instrument_product_contract(
            cfg,
            sc.get("trading_config") or {},
            symbol=event.get("symbol") or "",
            exchange_id=event.get("exchange_id") or "",
            market_type=event.get("market_type") or "swap",
        )
        client = create_client(cfg, market_type=event.get("market_type") or "swap")
        event = complete_snapshot(prepare_event(event, client, cfg))
        with get_db_transaction():
            lock_strategy_fills(strategy_id)
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT * FROM qd_strategy_trades WHERE id = %s FOR UPDATE", (trade_id,))
                trade = cur.fetchone()
                if not trade:
                    raise RuntimeError("Grid market trade is not ready")
                cur.execute(
                    """INSERT INTO qd_execution_owner_projections
                    (execution_event_id, owner_type, owner_id) VALUES (%s, 'grid_market', %s)
                    ON CONFLICT DO NOTHING RETURNING execution_event_id""",
                    (event["id"], trade_id),
                )
                if not cur.fetchone():
                    cur.close()
                    return
                total = float(event.get("cumulative_quantity") or 0)
                previous = float(trade.get("amount") or 0)
                if total + 1e-12 < previous:
                    cur.close()
                    return
                average = float(event["cumulative_average_price"])
                delta, price = cumulative_delta(previous, float(trade.get("price") or 0), total, average)
                native = self._json(trade.get("commission_breakdown"))
                if not native and trade.get("commission_ccy") not in (None, "", "MIXED"):
                    native = {trade["commission_ccy"]: float(trade.get("commission") or 0)}
                posted = dict(quantity=previous, fees=native, quote=float(trade.get("commission_quote") or 0))
                fees, quote = self._fees(event, client=client, symbol=event["symbol"], price=price or average)
                fees, quote = self._incremental_fees(event, posted, delta, fees, quote)
                if delta > 1e-12:
                    signal = trade["type"]
                    quantity = self._spot_position_quantity(
                        market_type=event["market_type"],
                        symbol=event["symbol"],
                        signal_type=signal,
                        gross_quantity=delta,
                        fees=fees,
                    )
                    profit, _, _ = apply_fill_to_local_position(
                        strategy_id=strategy_id,
                        symbol=event["symbol"],
                        signal_type=signal,
                        filled=quantity,
                        avg_price=price,
                        leg=LegContext(
                            market_type=event["market_type"], credential_id=int(trade.get("credential_id") or 0)
                        ),
                        strategy_run_id=int(trade.get("strategy_run_id") or 0),
                    )
                    cur.execute(
                        """UPDATE qd_strategy_trades SET amount = %s, price = %s, value = %s,
                        profit = CASE WHEN %s IS NULL THEN profit ELSE COALESCE(profit, 0) + %s END
                        WHERE id = %s""",
                        (total, average, total * average, profit, profit, trade_id),
                    )
                if event.get("fee_status") in {"actual", "actual_zero"}:
                    adjust_order_fee("id", trade_id, fees, quote, event["fee_status"], update_inventory=delta <= 1e-12)
                cur.close()

    def _process_quick_trade(self, event: Dict[str, Any], binding: Dict[str, Any]) -> None:
        trade_id = int(binding.get("owner_id") or 0)
        event_id = int(event.get("id") or 0)
        config = resolve_exchange_config(
            {"credential_id": event.get("credential_id"), "exchange_id": event.get("exchange_id")},
            user_id=int(event.get("user_id") or 1),
        )
        config = bind_instrument_product_contract(
            config,
            {},
            symbol=event.get("symbol") or "",
            exchange_id=event.get("exchange_id") or "",
            market_type=event.get("market_type") or "spot",
        )
        client = create_client(config, market_type=event.get("market_type") or "spot")
        event = complete_snapshot(prepare_event(event, client, config))
        with get_db_transaction():
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT * FROM qd_quick_trades WHERE id = %s FOR UPDATE", (trade_id,))
                current = cur.fetchone()
                if not current:
                    raise RuntimeError("Quick trade is not ready for execution projection")
                cur.execute(
                    """INSERT INTO qd_execution_owner_projections
                    (execution_event_id, owner_type, owner_id) VALUES (%s, 'quick_trade', %s)
                    ON CONFLICT DO NOTHING RETURNING execution_event_id""",
                    (event_id, trade_id),
                )
                if not cur.fetchone():
                    cur.close()
                    return
                previous = float(current.get("filled_amount") or 0)
                total = float(event.get("cumulative_quantity") or 0)
                if total < previous:
                    cur.close()
                    return
                average = float(event["cumulative_average_price"])
                delta, price = cumulative_delta(previous, float(current.get("avg_fill_price") or 0), total, average)
                raw = self._json(current.get("raw_result"))
                old_fees = raw.get("_qd_fees") or {}
                if not old_fees and current.get("commission_ccy") not in (None, "", "MIXED"):
                    old_fees = {current["commission_ccy"]: float(current.get("commission") or 0)}
                posted = dict(quantity=previous, fees=old_fees, quote=float(current.get("commission_quote") or 0))
                fees, quote = self._fees(event, client=client, symbol=event["symbol"], price=price or average)
                fees, quote = self._incremental_fees(event, posted, delta, fees, quote)
                merged = dict(old_fees)
                for currency, amount in fees.items():
                    merged[currency] = float(merged.get(currency) or 0) + amount
                commission, currency = self._fee_storage(merged)
                raw["_qd_fees"] = merged
                cur.execute(
                    """UPDATE qd_quick_trades SET filled_amount = %s, avg_fill_price = %s,
                    commission = %s, commission_ccy = %s, commission_quote = %s,
                    raw_result = %s::jsonb, status = CASE WHEN %s IN ('filled', 'cancelled') THEN %s ELSE status END
                    WHERE id = %s""",
                    (
                        total,
                        average,
                        commission,
                        currency,
                        posted["quote"] + quote if quote is not None else current.get("commission_quote"),
                        json.dumps(raw),
                        event.get("order_status"),
                        event.get("order_status"),
                        trade_id,
                    ),
                )
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
