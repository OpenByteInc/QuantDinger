"""Order queue boundary for Strategy API V2 live sessions."""

from __future__ import annotations

import json
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


class StrategyV2OrderGateway:
    """Persist idempotent orders for the existing asynchronous dispatcher."""

    _ACTIVE_PENDING_STATUSES = ("pending", "processing", "sent", "syncing")

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
                  AND strategy_run_id = %s
                  AND symbol = %s
                  AND signal_type IN (%s, %s, %s, %s)
                  AND status IN (%s, %s, %s, %s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    int(request.strategy_id),
                    int(request.strategy_run_id),
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
        self._check_risk_guard(request)
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
            **signal.to_order_intent_kwargs(leverage=request.leverage),
        )
        if intent.existing and intent.status not in {"failed", "cancelled", "rejected"}:
            pending_id = self._pending_id(key)
            if pending_id:
                return pending_id
        if intent.id <= 0:
            raise RuntimeError("strategyV2.orderIntentPersistenceFailed")

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
        }
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO pending_orders
                  (user_id, strategy_id, symbol, signal_type, signal_ts, market_type,
                   order_type, amount, price, execution_mode, status, priority,
                   attempts, max_attempts, last_error, payload_json, strategy_run_id,
                   order_intent_id, idempotency_key, created_at, updated_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0,
                   0, 10, '', %s, %s, %s, %s, NOW(), NOW())
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
                ),
            )
            row = cur.fetchone() or {}
            db.commit()
            cur.close()
        pending_id = int(row.get("id") or 0)
        return pending_id or self._pending_id(key)

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

    @staticmethod
    def _check_risk_guard(request: LiveOrderRequest) -> None:
        """Run the platform-level risk guard before persisting the order.

        Fetches a best-effort account snapshot and checks drawdown, position
        ratio, martingale lock, order size, and rate limits. If any rule
        blocks the order, raises ValueError with the blocking reasons so the
        caller can report back to the frontend / AI strategy review.
        """
        from app.services.risk_guard import RiskGuard, RiskOrder, AccountState

        # Best-effort account snapshot; if unavailable, skip guard (fail-open
        # rather than block trading on observability gaps).
        try:
            from app.services.live_trading.account_snapshot import (
                fetch_account_snapshot,
            )
            from app.utils.auth import get_current_user_id

            user_id = request.user_id or get_current_user_id()
            snapshot = fetch_account_snapshot(
                user_id=user_id,
                credential_id=0,  # TODO: resolve from trading_config
            ) or {}
            net_value = float(
                snapshot.get("totalWalletBalance")
                or snapshot.get("net_value")
                or 0
            )
            peak = max(net_value, float(snapshot.get("peak") or net_value))
            positions = {
                str(p.get("symbol")): float(p.get("notional") or p.get("positionValue") or 0)
                for p in (snapshot.get("positions") or [])
                if p.get("symbol")
            }
            acct = AccountState(
                net_value=net_value, peak_net_value=peak, positions=positions
            )
        except Exception:
            # Observability gap — don't block trading, but log it.
            from app.utils.logger import get_logger

            get_logger(__name__).debug(
                "risk_guard: account snapshot unavailable, skipping guard"
            )
            return

        notional = float(request.quantity) * float(request.reference_price or 0)
        # Infer strategy type / martingale state from the LiveOrderRequest.
        strategy_type = "martingale" if "add_" in request.action else "signal"
        martingale_layers = 0  # TODO: wire from runtime state
        martingale_leverage = float(request.leverage or 1.0)

        order = RiskOrder(
            symbol=request.symbol,
            side="buy" if "long" in request.action else "sell",
            notional=notional,
            strategy_type=strategy_type,
            martingale_layers=martingale_layers,
            martingale_leverage=martingale_leverage,
        )

        guard = RiskGuard.shared()
        allowed, reasons = guard.check(order, acct)
        if not allowed:
            raise ValueError("strategyV2.riskGuardBlocked: " + "; ".join(reasons))
