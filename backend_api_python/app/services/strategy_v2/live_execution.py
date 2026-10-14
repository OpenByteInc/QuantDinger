"""Order queue boundary for Strategy API V2 live sessions."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from app.services.strategy_runtime.order_intents import OrderIntentService
from app.services.strategy_runtime.signals import StrategySignal
from app.utils.db import get_db_connection


@dataclass(frozen=True)
class LiveOrderRequest:
    strategy_id: int
    strategy_run_id: int
    user_id: int
    symbol: str
    action: str
    quantity: float
    reference_price: float
    signal_timestamp: int
    market_type: str
    execution_mode: str
    leverage: float = 1.0
    margin_mode: str = "cross"
    reason: str = ""
    notification_config: dict[str, Any] | None = None
    order_type: str = "market"
    execution_algo: str = "market"
    limit_price: float = 0.0
    maker_wait_sec: float = 0.0
    maker_offset_bps: float = 0.0
    protection: dict[str, Any] | None = None
    sizing: dict[str, Any] | None = None
    client_order_id: str = ""
    ai_decision_filter: bool = False
    strategy_type: str = ""
    decision_context: dict[str, Any] | None = None
    runtime_fencing_token: int = 0


class StrategyV2OrderGateway:
    """Persist idempotent orders for the existing asynchronous dispatcher."""

    _ACTIVE_PENDING_STATUSES = ("pending", "processing", "sent", "syncing", "reconciling")

    def __init__(self, *, decision_filter_factory=None) -> None:
        self._decision_filter_factory = decision_filter_factory
        self._ai_rejection_latches: dict[int, set[tuple[str, str, str]]] = {}
        self._ai_signal_cycle: dict[int, set[tuple[str, str, str]]] = {}
        self._ai_latch_lock = threading.Lock()

    @staticmethod
    def _ai_signal_fingerprint(request: LiveOrderRequest) -> tuple[str, str, str]:
        return (
            str(request.symbol or "").strip(),
            str(request.action or "").strip().lower(),
            str(request.reason or "").strip(),
        )

    def begin_signal_cycle(self, strategy_run_id: int) -> None:
        run_id = int(strategy_run_id or 0)
        if run_id <= 0:
            return
        with self._ai_latch_lock:
            self._ai_signal_cycle[run_id] = set()

    def finish_signal_cycle(self, strategy_run_id: int) -> None:
        run_id = int(strategy_run_id or 0)
        if run_id <= 0:
            return
        with self._ai_latch_lock:
            seen = self._ai_signal_cycle.pop(run_id, None)
            if seen is None:
                return
            remaining = self._ai_rejection_latches.get(run_id, set()).intersection(seen)
            if remaining:
                self._ai_rejection_latches[run_id] = remaining
            else:
                self._ai_rejection_latches.pop(run_id, None)

    def clear_signal_state(self, strategy_run_id: int) -> None:
        run_id = int(strategy_run_id or 0)
        with self._ai_latch_lock:
            self._ai_signal_cycle.pop(run_id, None)
            self._ai_rejection_latches.pop(run_id, None)

    def _is_ai_rejection_latched(self, request: LiveOrderRequest) -> bool:
        run_id = int(request.strategy_run_id or 0)
        fingerprint = self._ai_signal_fingerprint(request)
        with self._ai_latch_lock:
            seen = self._ai_signal_cycle.get(run_id)
            if seen is not None:
                seen.add(fingerprint)
            return fingerprint in self._ai_rejection_latches.get(run_id, set())

    def _latch_ai_rejection(self, request: LiveOrderRequest) -> None:
        run_id = int(request.strategy_run_id or 0)
        fingerprint = self._ai_signal_fingerprint(request)
        with self._ai_latch_lock:
            self._ai_rejection_latches.setdefault(run_id, set()).add(fingerprint)

    @staticmethod
    def _position_lane(action: str) -> str:
        signal = str(action or "").strip().lower()
        if signal.endswith("_long"):
            return "long"
        if signal.endswith("_short"):
            return "short"
        return ""

    def has_inflight(self, request: LiveOrderRequest) -> bool:
        """Return whether the same strategy position leg already has live work.

        Timestamp-based idempotency only deduplicates the same signal event. A
        target-position strategy can emit an equivalent order on the next poll
        while the first order is still waiting for the exchange. Serializing
        each symbol/position leg prevents those semantic duplicates without
        blocking the opposite leg of a true hedge strategy.
        """
        if (
            str(request.execution_mode or "").strip().lower() == "signal"
            and str(request.strategy_type or "").strip().lower() == "grid"
            and str(request.order_type or "").strip().lower() == "limit"
            and str(request.client_order_id or "").strip()
        ):
            # A generated grid emits several independently tracked price levels
            # in one cycle.  Their stable client IDs provide idempotency, while
            # the virtual ledger keeps every level isolated from live execution.
            return False
        lane = self._position_lane(request.action)
        if not lane:
            return False
        lane_actions = (
            ("open_long", "add_long", "reduce_long", "close_long")
            if lane == "long"
            else ("open_short", "add_short", "reduce_short", "close_short")
        )
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id
                FROM pending_orders
                WHERE strategy_id = %s
                  AND symbol = %s
                  AND signal_type IN (%s, %s, %s, %s)
                  AND status IN (%s, %s, %s, %s, %s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    int(request.strategy_id),
                    str(request.symbol or ""),
                    *lane_actions,
                    *self._ACTIVE_PENDING_STATUSES,
                ),
            )
            row = cur.fetchone() or {}
            cur.close()
        return int(row.get("id") or 0) > 0

    def submit(self, request: LiveOrderRequest) -> int | None:
        request = self._validate(request)
        self._assert_runtime_fence(request)
        if (
            request.execution_mode == "live"
            and request.ai_decision_filter
            and self._is_ai_rejection_latched(request)
        ):
            return None
        service = OrderIntentService(
            strategy_id=request.strategy_id,
            strategy_run_id=request.strategy_run_id,
        )
        client_order_id = str(request.client_order_id or "").strip()[:100]
        key = (
            f"run:{request.strategy_run_id}:strategy:{request.strategy_id}:client:{client_order_id}"
            if client_order_id
            else service.build_signal_idempotency_key(
                strategy_run_id=request.strategy_run_id,
                strategy_id=request.strategy_id,
                symbol=request.symbol,
                signal_type=request.action,
                signal_ts=request.signal_timestamp,
                signal_discriminator={
                    "quantity": request.quantity,
                    "reference_price": request.reference_price,
                    "reason": request.reason,
                    "order_type": request.order_type,
                    "execution_algo": request.execution_algo,
                    "limit_price": request.limit_price,
                    "protection": request.protection or {},
                    "sizing": request.sizing or {},
                },
            )
        )[:180]
        signal = StrategySignal(
            timestamp=request.signal_timestamp,
            strategy_id=request.strategy_id,
            strategy_run_id=request.strategy_run_id,
            symbol=request.symbol,
            action=request.action,
            market_type=request.market_type,
            amount=request.quantity,
            price_hint=request.reference_price,
            reason=request.reason,
            source="strategy_v2",
        )
        intent = service.create_intent(
            idempotency_key=key,
            client_order_id=client_order_id,
            portfolio_id=signal.portfolio_id,
            universe_id=signal.universe_id,
            rebalance_group_id=signal.rebalance_group_id,
            target_weight=signal.target_weight,
            target_notional=signal.target_notional,
            target_position_qty=signal.target_position_qty,
            runtime_fencing_token=request.runtime_fencing_token,
            **signal.to_order_intent_kwargs(leverage=request.leverage),
        )
        if intent.existing and intent.status == "ai_rejected":
            return None
        if intent.existing and intent.status not in {"failed", "cancelled", "rejected"}:
            pending_id = self._pending_id(key)
            if pending_id:
                return pending_id
        if intent.id <= 0:
            raise RuntimeError("strategyV2.orderIntentPersistenceFailed")

        if request.execution_mode == "live" and request.ai_decision_filter:
            from app.services.ai_decision_filter import AIDecisionFilter, AIDecisionRequest

            decision_filter = (
                self._decision_filter_factory()
                if self._decision_filter_factory is not None
                else AIDecisionFilter()
            )
            decision = decision_filter.evaluate(
                AIDecisionRequest(
                    user_id=request.user_id,
                    source_type="strategy",
                    strategy_id=request.strategy_id,
                    strategy_run_id=request.strategy_run_id,
                    order_intent_id=int(intent.id),
                    symbol=request.symbol,
                    action=request.action,
                    market_type=request.market_type,
                    order_type=request.order_type,
                    quantity=request.quantity,
                    reference_price=request.reference_price,
                    leverage=request.leverage,
                    reason=request.reason,
                    strategy_type=request.strategy_type,
                    context=dict(request.decision_context or {}),
                ),
                enabled=True,
            )
            if not decision.allowed:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        UPDATE strategy_order_intents
                        SET status = 'ai_rejected', updated_at = NOW()
                        WHERE id = %s
                        """,
                        (int(intent.id),),
                    )
                    db.commit()
                    cur.close()
                self._latch_ai_rejection(request)
                return None

        payload = {
            "strategy_id": request.strategy_id,
            "strategy_run_id": request.strategy_run_id,
            "order_intent_id": intent.id,
            "idempotency_key": key,
            "symbol": request.symbol,
            "signal_type": request.action,
            "market_type": request.market_type,
            "amount": request.quantity,
            "price": request.limit_price or request.reference_price,
            "ref_price": request.reference_price,
            "leverage": request.leverage,
            "margin_mode": request.margin_mode,
            "execution_mode": request.execution_mode,
            "notification_config": request.notification_config or {},
            "signal_ts": request.signal_timestamp,
            "reason": request.reason,
            "order_type": request.order_type,
            "execution_algo": request.execution_algo,
            "limit_price": request.limit_price,
            "maker_wait_sec": request.maker_wait_sec,
            "maker_offset_bps": request.maker_offset_bps,
            "protection": request.protection or {},
            "sizing": request.sizing or {},
            "client_order_id": client_order_id,
            "ai_decision_filter": bool(request.ai_decision_filter),
            "runtime_fencing_token": int(request.runtime_fencing_token or 0),
        }
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO pending_orders
                  (user_id, strategy_id, symbol, signal_type, signal_ts, market_type,
                   order_type, amount, price, execution_mode, status, priority,
                   attempts, max_attempts, last_error, payload_json, strategy_run_id,
                   order_intent_id, idempotency_key, runtime_fencing_token,
                   created_at, updated_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0,
                   0, 10, '', %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (
                    request.user_id,
                    request.strategy_id,
                    request.symbol,
                    request.action,
                    request.signal_timestamp,
                    request.market_type,
                    request.order_type,
                    request.quantity,
                    request.limit_price or request.reference_price,
                    request.execution_mode,
                    json.dumps(payload, ensure_ascii=False),
                    request.strategy_run_id,
                    intent.id,
                    key,
                    max(0, int(request.runtime_fencing_token or 0)),
                ),
            )
            row = cur.fetchone() or {}
            db.commit()
            cur.close()
        pending_id = int(row.get("id") or 0)
        return pending_id or self._pending_id(key)

    @staticmethod
    def _assert_runtime_fence(request: LiveOrderRequest) -> None:
        token = int(request.runtime_fencing_token or 0)
        if token <= 0:
            return
        with get_db_connection() as db:
            cur = db.cursor()
            try:
                cur.execute(
                    """
                    SELECT 1
                    FROM qd_strategy_runtime_leases
                    WHERE strategy_id = %s AND fencing_token = %s
                      AND lease_expires_at >= NOW()
                    LIMIT 1
                    """,
                    (int(request.strategy_id), token),
                )
                valid = cur.fetchone() is not None
            finally:
                cur.close()
        if not valid:
            raise RuntimeError("strategyRuntime.staleFencingToken")

    @staticmethod
    def _pending_id(key: str) -> int | None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT id FROM pending_orders WHERE idempotency_key = %s LIMIT 1",
                (key,),
            )
            row = cur.fetchone() or {}
            cur.close()
        value = int(row.get("id") or 0)
        return value or None

    @staticmethod
    def _validate(request: LiveOrderRequest) -> LiveOrderRequest:
        if request.strategy_id <= 0 or request.user_id <= 0:
            raise ValueError("strategyV2.invalidRuntimeIdentity")
        if request.strategy_run_id <= 0:
            raise ValueError("strategyV2.invalidRunIdentity")
        if not request.symbol:
            raise ValueError("strategyV2.orderSymbolRequired")
        if request.action not in {
            "open_long",
            "open_short",
            "add_long",
            "add_short",
            "reduce_long",
            "reduce_short",
            "close_long",
            "close_short",
        }:
            raise ValueError("strategyV2.orderActionUnsupported")
        is_close_all = request.action in {"close_long", "close_short"} and request.quantity == 0
        if (request.quantity <= 0 and not is_close_all) or request.reference_price <= 0:
            raise ValueError("strategyV2.invalidOrderSize")
        if request.execution_mode not in {"signal", "live"}:
            raise ValueError("strategyV2.invalidExecutionMode")
        if request.market_type == "spot" and "short" in request.action:
            raise ValueError("strategyV2.spotShortUnsupported")
        if request.order_type not in {"market", "limit"}:
            raise ValueError("strategyV2.orderTypeUnsupported")
        if request.execution_algo not in {"market", "limit", "maker_then_market"}:
            raise ValueError("strategyV2.executionAlgoUnsupported")
        if request.execution_algo == "limit" and request.limit_price <= 0:
            raise ValueError("strategyV2.limitPriceRequired")
        return request
