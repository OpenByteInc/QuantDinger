"""Strategy API V2 live execution supervisor."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from app.data_sources import DataSourceFactory
from app.data_sources.errors import (
    MarketDataUnavailableError,
    classify_market_data_failure,
)
from app.events.bar_clock import (
    BarStreamKey,
    MarketBarCloseClock,
    supports_bar_close_events,
)
from app.events.kafka import build_runtime_event_bus
from app.services.script_source import get_script_source_service
from app.services.ai_decision_context import build_strategy_decision_context
from app.services.market_context import (
    default_crypto_exchange_id,
    normalize_exchange_id,
    normalize_market_type,
)
from app.services.strategy_runtime.health import record_runtime_heartbeat
from app.services.strategy_runtime.identity import ensure_strategy_run, finish_strategy_run
from app.services.strategy_runtime.order_intents import OrderIntentService
from app.services.strategy_runtime.scheduler import CooperativeRuntimeScheduler
from app.services.strategy_runtime.state import RuntimeStateStore
from app.services.strategy_runtime.live_portfolio import refresh_members, positions_by_symbol, available_strategy_cash, pricing_members
from app.services.strategy_runtime.cancellations import persist_cancellations
from app.services.strategy_runtime.timeframes import (
    completed_bar_token,
    daily_equity_execution_policy,
    equity_daily_frames_ready,
    live_history_days,
    load_live_frequency_frames,
    seconds_until_next_completed_bar,
)
from app.services.strategy_v2 import (
    OrderIntent,
    StrategyV2BacktestService,
    StrategyV2LiveSession,
    compile_strategy_v2,
)
from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.numeric_precision import format_decimal
from app.utils.strategy_runtime_logs import append_strategy_log, format_market_data_log


logger = get_logger(__name__)

MIN_LIVE_ORDER_NOTIONAL = 1.0
__all__ = ["TradingExecutor", "live_history_days"]


def _runtime_position_key(symbol: object, position_side: object = "") -> str:
    base = str(symbol or "")
    side = str(position_side or "").strip().lower()
    return f"{base}::{side}" if side in {"long", "short"} else base


class TradingExecutor:
    """Schedule active Strategy V2 runtimes on a bounded evaluator pool."""

    def __init__(self, *, external_bar_triggers: bool | None = None) -> None:
        self.running_strategies: dict[int, Any] = {}
        self._runtime_stop_events: dict[int, threading.Event] = {}
        self._runtime_fencing_tokens: dict[int, int] = {}
        self._runtime_handoff_ids: set[int] = set()
        self._bar_event_pending: dict[int, threading.Event] = {}
        self._routing_streams_by_strategy: dict[int, set[BarStreamKey]] = {}
        self._routing_stream_subscriptions: dict[BarStreamKey, Any] = {}
        self._routing_stream_ref_counts: dict[BarStreamKey, int] = {}
        self.lock = threading.Lock()
        self.max_active_strategies = max(
            1,
            int(os.getenv("STRATEGY_MAX_ACTIVE", "100000")),
        )
        self.evaluator_threads = max(
            1,
            int(os.getenv("STRATEGY_EVALUATOR_THREADS", "16")),
        )
        self._scheduler = CooperativeRuntimeScheduler(
            worker_count=self.evaluator_threads,
        )
        self._event_bus = build_runtime_event_bus()
        self._bar_close_clock = MarketBarCloseClock(self._event_bus)
        self.stop_join_timeout = max(
            1.0,
            float(os.getenv("STRATEGY_STOP_JOIN_TIMEOUT_SEC", "15")),
        )
        self.order_gateway = StrategyV2OrderGateway()
        self._last_start_failure = ""
        self._last_exit_reason: dict[int, str] = {}
        self.runtime_guard = None
        self.external_bar_triggers = (
            _env_enabled("STRATEGY_EXTERNAL_BAR_TRIGGERS_ENABLED", default=False)
            if external_bar_triggers is None
            else bool(external_bar_triggers)
        )

    def start_strategy(self, strategy_id: int) -> bool:
        strategy_id = int(strategy_id)
        with self.lock:
            self._discard_dead_runtimes()
            self._last_start_failure = ""
            if strategy_id in self.running_strategies:
                self._last_start_failure = "Strategy is already running."
                return False
            if len(self.running_strategies) >= self.max_active_strategies:
                self._last_start_failure = (
                    f"Active strategy limit reached ({self.max_active_strategies})."
                )
                return False
            try:
                self._preflight_live_strategy(strategy_id)
            except Exception as exc:
                self._last_start_failure = str(exc or "strategyV2.livePreflightFailed")
                logger.warning("Strategy %s live preflight rejected: %s", strategy_id, exc)
                return False
            if self.runtime_guard and not self.runtime_guard(strategy_id):
                self._last_start_failure = "strategyRuntime.leaseLost"
                return False
            stop_event = threading.Event()
            self._runtime_handoff_ids.discard(strategy_id)
            self._bar_event_pending[strategy_id] = threading.Event()
            runtime = self._run_strategy_loop(strategy_id, stop_event)
            handle = self._scheduler.create(
                strategy_id=strategy_id,
                runtime=runtime,
                stop_event=stop_event,
            )
            self.running_strategies[strategy_id] = handle
            self._runtime_stop_events[strategy_id] = stop_event
            try:
                handle.start()
            except Exception as exc:
                self.running_strategies.pop(strategy_id, None)
                self._runtime_stop_events.pop(strategy_id, None)
                self._runtime_fencing_tokens.pop(strategy_id, None)
                self._bar_event_pending.pop(strategy_id, None)
                self._last_start_failure = f"Failed to schedule strategy runtime: {exc}"
                logger.exception("Failed to start strategy %s", strategy_id)
                return False
        append_strategy_log(strategy_id, "info", "Strategy runtime scheduled")
        return True

    def set_runtime_fencing_token(self, strategy_id: int, fencing_token: int) -> None:
        with self.lock:
            self._runtime_fencing_tokens[int(strategy_id)] = max(
                0,
                int(fencing_token or 0),
            )

    def runtime_fencing_token(self, strategy_id: int) -> int:
        lock = getattr(self, "lock", None)
        tokens = getattr(self, "_runtime_fencing_tokens", {})
        if lock is None:
            return int(tokens.get(int(strategy_id), 0))
        with lock:
            return int(tokens.get(int(strategy_id), 0))

    def clear_runtime_fencing_token(self, strategy_id: int) -> None:
        with self.lock:
            self._runtime_fencing_tokens.pop(int(strategy_id), None)

    def trigger_bar_evaluation(
        self,
        strategy_id: int,
        event: Any,
        *,
        timeout: float = 30.0,
    ) -> bool:
        strategy_id = int(strategy_id)
        with self.lock:
            pending = self._bar_event_pending.get(strategy_id)
        if pending is None:
            return False
        pending.set()
        return self._scheduler.wake_strategy_and_wait(
            strategy_id,
            event,
            timeout=timeout,
        )

    def register_market_streams(
        self,
        strategy_id: int,
        streams: set[BarStreamKey],
    ) -> None:
        strategy_id = int(strategy_id)
        subscriptions_to_close = []
        with self.lock:
            normalized = set(streams)
            current = set(self._routing_streams_by_strategy.get(strategy_id, set()))
            if current == normalized:
                return

            for stream in current - normalized:
                remaining = self._routing_stream_ref_counts.get(stream, 0) - 1
                if remaining > 0:
                    self._routing_stream_ref_counts[stream] = remaining
                    continue
                self._routing_stream_ref_counts.pop(stream, None)
                subscription = self._routing_stream_subscriptions.pop(stream, None)
                if subscription is not None:
                    subscriptions_to_close.append(subscription)

            for stream in normalized - current:
                count = self._routing_stream_ref_counts.get(stream, 0)
                if count == 0:
                    self._routing_stream_subscriptions[stream] = (
                        self._bar_close_clock.subscribe(stream, lambda _event: None)
                    )
                self._routing_stream_ref_counts[stream] = count + 1
            if normalized:
                self._routing_streams_by_strategy[strategy_id] = normalized
            else:
                self._routing_streams_by_strategy.pop(strategy_id, None)
        for subscription in subscriptions_to_close:
            subscription.close()

    def unregister_market_streams(self, strategy_id: int) -> None:
        subscriptions = []
        with self.lock:
            streams = self._routing_streams_by_strategy.pop(int(strategy_id), set())
            for stream in streams:
                remaining = self._routing_stream_ref_counts.get(stream, 0) - 1
                if remaining > 0:
                    self._routing_stream_ref_counts[stream] = remaining
                    continue
                self._routing_stream_ref_counts.pop(stream, None)
                subscription = self._routing_stream_subscriptions.pop(stream, None)
                if subscription is not None:
                    subscriptions.append(subscription)
        for subscription in subscriptions:
            subscription.close()

    def _register_event_subscriptions(
        self,
        strategy_id: int,
        stream_keys: set[BarStreamKey],
    ):
        if self.external_bar_triggers:
            return None
        from app.services.strategy_event_subscriptions import (
            StrategyEventSubscriptionRepository,
        )

        repository = StrategyEventSubscriptionRepository()
        repository.replace_bar_streams(strategy_id, stream_keys)
        return repository

    def _preflight_live_strategy(self, strategy_id: int) -> None:
        strategy = self._load_strategy(int(strategy_id))
        if not strategy:
            raise RuntimeError("strategyV2.strategyNotFound")
        execution_mode = str(strategy.get("execution_mode") or "signal").strip().lower()
        if execution_mode != "live":
            return

        user_id = int(strategy.get("user_id") or 0)
        from app.services.strategy_live_guard import (
            find_live_strategy_conflict,
            live_conflict_message,
            resolve_strategy_direction_mode,
        )

        trading_config = _json_object(strategy.get("trading_config"))
        from app.services.exchange_execution import resolve_exchange_config
        from app.services.market.product_catalog import (
            validate_product_account_environment,
            validate_runtime_products,
        )

        exchange_config = resolve_exchange_config(
            _json_object(strategy.get("exchange_config")),
            user_id=user_id,
        )
        exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
        validate_product_account_environment(trading_config.get("instrument_products"), exchange_config)
        validate_runtime_products(
            trading_config.get("instrument_products")
            if isinstance(trading_config.get("instrument_products"), list)
            else [],
            credential_exchange_id=exchange_id,
        )
        market_type = str(
            strategy.get("market_type")
            or trading_config.get("market_type")
            or "spot"
        ).strip().lower()
        if market_type in {"future", "futures", "perp", "perpetual"}:
            market_type = "swap"
        if market_type != "swap":
            conflict = find_live_strategy_conflict(strategy, user_id)
            if conflict:
                raise RuntimeError(live_conflict_message(conflict))
            return

        direction_mode = resolve_strategy_direction_mode(strategy)
        from app.services.strategy_runtime.bot_type import resolve_bot_type

        bot_type = resolve_bot_type(strategy, trading_config)
        bot_params = (
            trading_config.get("bot_params")
            if isinstance(trading_config.get("bot_params"), dict)
            else {}
        )
        grid_direction = str(
            bot_params.get("gridDirection") or bot_params.get("grid_direction") or ""
        ).strip().lower()
        neutral_grid = bot_type == "grid" and grid_direction == "neutral"
        if not direction_mode:
            raise RuntimeError("strategyV2.directionModeRequired")

        from app.services.grid.exchange_requirements import detect_hedge_position_mode
        from app.services.live_trading.factory import create_client
        client = create_client(exchange_config, market_type=market_type)
        is_hedge, label = detect_hedge_position_mode(
            client,
            symbol=str(strategy.get("symbol") or trading_config.get("symbol") or ""),
            market_type=market_type,
            exchange_config=exchange_config,
        )
        owns_both_legs = direction_mode in {"both", "neutral"} or neutral_grid
        if owns_both_legs and is_hedge is not True:
            raise RuntimeError(f"strategyV2.dualDirectionHedgeModeRequired:{label}")
        if is_hedge is None:
            raise RuntimeError(f"strategyV2.hedgeModeUnknown:{label}")

        # ``one_way`` describes a strategy that owns one signed exposure and
        # never keeps long and short open together. It does not require the
        # exchange account itself to use net-position mode: the pending-order
        # worker maps every action to the correct LONG/SHORT leg in hedge mode,
        # while reversal execution closes and synchronizes before re-entry.
        # Only fixed-side strategies may share opposite hedge-account legs; a
        # reversing strategy continues to reserve the whole instrument.
        allow_opposite_leg = (
            is_hedge is True
            and direction_mode in {"long_only", "short_only"}
            and not neutral_grid
        )
        conflict = find_live_strategy_conflict(
            strategy,
            user_id,
            allow_opposite_leg=allow_opposite_leg,
        )
        if conflict:
            raise RuntimeError(live_conflict_message(conflict))

    def wait_strategy_running(self, strategy_id: int, timeout: float = 3.0) -> Tuple[bool, str]:
        strategy_id = int(strategy_id)
        deadline = time.monotonic() + max(0.5, float(timeout))
        while time.monotonic() < deadline:
            with self.lock:
                runtime = self.running_strategies.get(strategy_id)
                alive = bool(runtime and runtime.is_alive())
            if not alive:
                return False, self._last_exit_reason.pop(strategy_id, "") or "Strategy runtime exited during startup."
            is_ready = getattr(runtime, "is_ready", None)
            if callable(is_ready) and is_ready():
                return True, ""
            time.sleep(0.1)
        return True, ""

    def runtime_capacity_snapshot(self) -> Dict[str, int]:
        scheduler = self._scheduler.snapshot()
        bar_clock = self._bar_close_clock.snapshot()
        event_bus = self._event_bus.snapshot()
        with self.lock:
            active = len(self.running_strategies)
            routed_bar_streams = len(self._routing_stream_subscriptions)
        return {
            "active_strategies": active,
            "running_strategies": active,
            "evaluator_threads": int(scheduler.get("workers") or 0),
            "queued_runtimes": int(scheduler.get("queued_runtimes") or 0),
            "scheduled_entries": int(scheduler.get("scheduled_entries") or 0),
            "event_wakes": int(scheduler.get("event_wakes") or 0),
            "scheduler_queue_compactions": int(scheduler.get("queue_compactions") or 0),
            **bar_clock,
            **event_bus,
            "max_active_strategies": self.max_active_strategies,
            "external_bar_triggers": int(self.external_bar_triggers),
            "routed_bar_streams": routed_bar_streams,
        }

    def close(self) -> None:
        with self.lock:
            routed_strategy_ids = list(self._routing_streams_by_strategy)
        for strategy_id in routed_strategy_ids:
            self.unregister_market_streams(strategy_id)
        self._bar_close_clock.close()
        self._scheduler.close(timeout=self.stop_join_timeout)
        self._event_bus.close()

    def is_running(self, strategy_id: int) -> bool:
        with self.lock:
            self._discard_dead_runtimes()
            thread = self.running_strategies.get(int(strategy_id))
            return bool(thread and thread.is_alive())

    def stop_strategy(
        self,
        strategy_id: int,
        *,
        persist_status: bool = True,
        preserve_run: bool = False,
    ) -> bool:
        strategy_id = int(strategy_id)
        try:
            if not preserve_run:
                try:
                    strategy = self._load_strategy(strategy_id) or {}
                    if str(strategy.get("execution_mode") or "signal").strip().lower() == "signal":
                        from app.services.virtual_trading import cancel_virtual_limit_orders

                        cancel_virtual_limit_orders(strategy_id)
                except Exception as exc:
                    logger.warning(
                        "Virtual limit-order cancellation failed during strategy stop: strategy_id=%s error=%s",
                        strategy_id,
                        exc,
                    )
                # A resting grid owns exchange-side limit orders independently of the
                # strategy thread.  Cancelling only the local runtime would leave
                # those orders live after the UI reports the strategy as stopped.
                # The shutdown helper is idempotent and ignores non-grid strategies.
                from app.services.grid.runner import shutdown_grid_for_strategy

                shutdown_grid_for_strategy(strategy_id)
            if persist_status and not preserve_run:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE qd_strategies_trading SET status = 'stopped', updated_at = NOW() WHERE id = %s",
                        (strategy_id,),
                    )
                    db.commit()
                    cur.close()
            with self.lock:
                thread = self.running_strategies.get(strategy_id)
                stop_event = self._runtime_stop_events.get(strategy_id)
                if preserve_run:
                    self._runtime_handoff_ids.add(strategy_id)
                if stop_event is not None:
                    stop_event.set()
                cancel = getattr(thread, "cancel", None)
                if callable(cancel):
                    cancel()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=self.stop_join_timeout)
                if thread.is_alive():
                    self._last_exit_reason[strategy_id] = "strategyRuntime.stopTimeout"
                    append_strategy_log(strategy_id, "error", "strategyRuntime.stopTimeout")
                    return False
            with self.lock:
                if self.running_strategies.get(strategy_id) is thread:
                    self.running_strategies.pop(strategy_id, None)
                if self._runtime_stop_events.get(strategy_id) is stop_event:
                    self._runtime_stop_events.pop(strategy_id, None)
                self._runtime_handoff_ids.discard(strategy_id)
            append_strategy_log(
                strategy_id,
                "info",
                (
                    "Strategy runtime handoff requested"
                    if preserve_run
                    else "Strategy stop requested"
                ),
            )
            return True
        except Exception as exc:
            logger.exception("Failed to stop strategy %s", strategy_id)
            self._last_exit_reason[strategy_id] = str(exc)
            return False

    def stop_strategy_with_policy(
        self,
        strategy_id: int,
        *,
        close_positions: bool = False,
    ) -> Dict[str, Any]:
        """Pause a strategy and optionally queue reduce-only closes for its owned legs."""
        sid = int(strategy_id)
        strategy = self._load_strategy(sid) or {}
        execution_mode = str(strategy.get("execution_mode") or "live").strip().lower()
        if close_positions and execution_mode == "signal":
            return self._stop_signal_strategy_with_virtual_close(sid, strategy)
        positions: List[Dict[str, Any]] = []
        run_id = 0
        if close_positions:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT symbol, side, size, entry_price, current_price, market_type
                    FROM qd_strategy_positions
                    WHERE strategy_id = %s AND size > 0
                    ORDER BY symbol, side
                    """,
                    (sid,),
                )
                positions = [dict(row) for row in (cur.fetchall() or [])]
                cur.execute(
                    """
                    SELECT id
                    FROM strategy_runs
                    WHERE strategy_id = %s
                    ORDER BY CASE WHEN runtime_status IN ('running', 'recovering', 'paused') THEN 0 ELSE 1 END,
                             id DESC
                    LIMIT 1
                    """,
                    (sid,),
                )
                run_id = int((cur.fetchone() or {}).get("id") or 0)
                cur.close()

        stopped = self.stop_strategy(sid)
        result: Dict[str, Any] = {
            "success": bool(stopped),
            "status": "stopped" if stopped else "running",
            "close_requested": bool(close_positions),
            "close_positions_found": len(positions),
            "close_orders_queued": 0,
            "close_errors": [],
        }
        if not stopped or not close_positions or not positions:
            return result
        if run_id <= 0:
            result["success"] = False
            result["close_errors"].append("strategyV2.closeRunIdentityMissing")
            return result

        trading_config = _json_object(strategy.get("trading_config"))
        leverage = max(1.0, float(trading_config.get("leverage") or strategy.get("leverage") or 1.0))
        notification_config = _json_object(strategy.get("notification_config"))
        signal_ts = int(time.time())
        for row in positions:
            side = str(row.get("side") or "").strip().lower()
            if side not in {"long", "short"}:
                result["close_errors"].append("strategyV2.closePositionSideInvalid")
                continue
            price = float(row.get("current_price") or row.get("entry_price") or 0.0)
            quantity = max(0.0, float(row.get("size") or 0.0))
            if price <= 0 or quantity <= 0:
                result["close_errors"].append("strategyV2.closePositionQuoteMissing")
                continue
            try:
                pending_id = self.order_gateway.submit(LiveOrderRequest(
                    strategy_id=sid,
                    strategy_run_id=run_id,
                    user_id=int(strategy.get("user_id") or 0),
                    symbol=str(row.get("symbol") or ""),
                    action="close_long" if side == "long" else "close_short",
                    quantity=quantity,
                    reference_price=price,
                    signal_timestamp=signal_ts,
                    market_type=str(row.get("market_type") or strategy.get("market_type") or "swap"),
                    execution_mode="live",
                    leverage=leverage,
                    reason="user_stop_and_close",
                    notification_config=notification_config,
                    execution_algo="market",
                    order_type="market",
                ))
                if pending_id:
                    result["close_orders_queued"] += 1
                else:
                    result["close_errors"].append("strategyV2.closeOrderQueueFailed")
            except Exception as exc:
                logger.exception("Failed to queue stop-and-close for strategy %s", sid)
                result["close_errors"].append(str(exc or "strategyV2.closeOrderQueueFailed"))
        if result["close_errors"]:
            result["success"] = False
        return result

    def _stop_signal_strategy_with_virtual_close(
        self,
        strategy_id: int,
        strategy: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Stop a signal runtime and synchronously settle all virtual positions."""
        sid = int(strategy_id)
        stopped = self.stop_strategy(sid)
        result: Dict[str, Any] = {
            "success": bool(stopped),
            "status": "stopped" if stopped else "running",
            "close_requested": True,
            "close_positions_found": 0,
            "close_orders_queued": 0,
            "close_orders_completed": 0,
            "close_errors": [],
        }
        if not stopped:
            return result

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT symbol, side, size, entry_price, current_price, market_type,
                       strategy_run_id
                FROM qd_strategy_virtual_positions
                WHERE strategy_id = %s AND size > 0
                ORDER BY symbol, side
                """,
                (sid,),
            )
            positions = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        result["close_positions_found"] = len(positions)
        if not positions:
            return result

        from app.services.virtual_trading import settle_virtual_pending_order

        trading_config = _json_object(strategy.get("trading_config"))
        leverage = max(1.0, float(trading_config.get("leverage") or strategy.get("leverage") or 1.0))
        notification_config = _json_object(strategy.get("notification_config"))
        signal_ts = int(time.time())
        for row in positions:
            side = str(row.get("side") or "").strip().lower()
            if side not in {"long", "short"}:
                result["close_errors"].append("strategyV2.closePositionSideInvalid")
                continue
            price = float(row.get("current_price") or row.get("entry_price") or 0.0)
            quantity = max(0.0, float(row.get("size") or 0.0))
            run_id = int(row.get("strategy_run_id") or 0)
            if run_id <= 0:
                result["close_errors"].append("strategyV2.closeRunIdentityMissing")
                continue
            if price <= 0 or quantity <= 0:
                result["close_errors"].append("strategyV2.closePositionQuoteMissing")
                continue
            try:
                pending_id = self.order_gateway.submit(LiveOrderRequest(
                    strategy_id=sid,
                    strategy_run_id=run_id,
                    user_id=int(strategy.get("user_id") or 0),
                    symbol=str(row.get("symbol") or ""),
                    action="close_long" if side == "long" else "close_short",
                    quantity=quantity,
                    reference_price=price,
                    signal_timestamp=signal_ts,
                    market_type=str(row.get("market_type") or strategy.get("market_type") or "spot"),
                    execution_mode="signal",
                    leverage=leverage,
                    reason="user_stop_and_close",
                    notification_config=notification_config,
                    execution_algo="market",
                    order_type="market",
                ))
                if not pending_id:
                    result["close_errors"].append("strategyV2.closeOrderQueueFailed")
                    continue
                result["close_orders_queued"] += 1
                settlement = settle_virtual_pending_order(pending_id)
                if str(settlement.get("status") or "") != "filled":
                    result["close_errors"].append("strategyV2.virtualCloseSettlementFailed")
                    continue
                result["close_orders_completed"] += 1
            except Exception as exc:
                logger.exception("Failed to settle virtual stop-and-close for strategy %s", sid)
                result["close_errors"].append(str(exc or "strategyV2.virtualCloseSettlementFailed"))
        if result["close_errors"]:
            result["success"] = False
        return result

    def _discard_dead_runtimes(self) -> None:
        for strategy_id, runtime in list(self.running_strategies.items()):
            if not runtime.is_alive():
                self.running_strategies.pop(strategy_id, None)
                self._runtime_stop_events.pop(strategy_id, None)
                self._runtime_handoff_ids.discard(strategy_id)

    def _run_strategy_loop(
        self,
        strategy_id: int,
        stop_event: threading.Event | None = None,
    ):
        stop_event = stop_event or threading.Event()
        run_id = 0
        exit_reason = "strategy stopped"
        market_price_feed = None
        bar_subscriptions = []
        with self.lock:
            bar_event_pending = self._bar_event_pending.setdefault(
                int(strategy_id),
                threading.Event(),
            )
        event_subscription_repository = None
        state_store: RuntimeStateStore | None = None
        try:
            strategy = self._load_strategy(strategy_id)
            if not strategy:
                raise RuntimeError("strategyV2.strategyNotFound")
            source_version_id, code = self._load_source(strategy)
            program = compile_strategy_v2(code)
            user_id = int(strategy.get("user_id") or 0)
            trading_config = _json_object(strategy.get("trading_config"))
            exchange_config = _json_object(strategy.get("exchange_config"))
            execution_mode = str(strategy.get("execution_mode") or "signal").strip().lower()
            if execution_mode not in {"signal", "live"}:
                raise RuntimeError("strategyV2.invalidExecutionMode")
            if execution_mode == "live":
                from app.services.exchange_execution import resolve_exchange_config

                exchange_config = resolve_exchange_config(exchange_config, user_id=user_id)

            service = StrategyV2BacktestService()
            now = datetime.now(timezone.utc)
            candidates, universe_id = service.resolve_candidates(
                user_id=user_id,
                manifest=program.manifest,
                start_date=now,
                end_date=now,
            )
            _apply_market_data_defaults(candidates)
            account_exchange = str(
                exchange_config.get("exchange_id") or exchange_config.get("exchangeId") or ""
            ).strip().lower()
            if execution_mode == "live" and account_exchange:
                for member in candidates:
                    if member.get("market") == "Crypto":
                        member["exchange_id"] = account_exchange
                        member["key"] = _member_key(member)
                from app.services.pending_orders.live_order_support import (
                    attach_instrument_product_contracts,
                )

                attach_instrument_product_contracts(
                    candidates,
                    trading_config,
                    exchange_id=account_exchange,
                )
                from app.services.market.product_catalog import validate_product_account_environment

                validate_product_account_environment(candidates, exchange_config)

            frequency = program.manifest.driving_frequency

            def fetch_runtime_frames() -> dict[str, dict[str, pd.DataFrame]]:
                refresh_members(service, candidates, program.manifest, user_id, strategy_id, datetime.now(timezone.utc), account_exchange)
                _apply_market_data_defaults(candidates)
                if execution_mode == "live" and account_exchange:
                    attach_instrument_product_contracts(
                        candidates,
                        trading_config,
                        exchange_id=account_exchange,
                    )
                return load_live_frequency_frames(
                    service=service,
                    candidates=candidates,
                    manifest=program.manifest,
                    end_date=datetime.now(timezone.utc),
                    warn=lambda message: append_strategy_log(
                        strategy_id,
                        "warning",
                        message,
                    ),
                )

            def resolve_universe(reference: str, timestamp: pd.Timestamp) -> list[str]:
                del reference
                if not universe_id:
                    return [str(item["key"]) for item in candidates]
                members = service.universe_service.resolve_members(
                    user_id,
                    universe_id,
                    as_of=timestamp.date(),
                )
                _apply_market_data_defaults(members)
                if account_exchange:
                    for item in members:
                        if item.get("market") == "Crypto":
                            item["exchange_id"] = account_exchange
                return [_member_key(item) for item in members]

            frequency_frames = fetch_runtime_frames()
            frames = frequency_frames[frequency]
            runtime_price_client: Dict[str, Any] = {}

            positions = {}
            def runtime_prices() -> dict[str, float]:
                priced = pricing_members(candidates, positions)
                if execution_mode != "live":
                    return self._live_prices(priced)
                return self._execution_account_prices(
                    priced,
                    exchange_config,
                    runtime_price_client,
                )

            initial_capital = float(
                strategy.get("initial_capital") or trading_config.get("initial_capital") or 0
            )
            if initial_capital <= 0:
                raise RuntimeError("strategyV2.invalidInitialCapital")
            runtime_params = dict(trading_config.get("params") or {})
            runtime_params.setdefault("execution_mode", execution_mode)
            runtime_params.setdefault(
                "commission",
                self._to_ratio(trading_config.get("commission") or 0.001),
            )
            session = StrategyV2LiveSession(
                code=code,
                frames=frames,
                frequency_frames=frequency_frames,
                initial_capital=initial_capital,
                params=runtime_params,
                universe_resolver=resolve_universe,
                schedule_timezone=self._load_schedule_timezone(user_id),
            )
            primary = candidates[0]
            runtime_run = ensure_strategy_run(
                strategy_id=strategy_id,
                user_id=user_id,
                code=code,
                parameter_snapshot=trading_config,
                source_version_id=str(source_version_id),
                exchange_id=str(primary.get("exchange_id") or account_exchange),
                credential_id=int(
                    exchange_config.get("credential_id") or 0
                ),
                symbol=str(primary.get("symbol") or ""),
                market_type=str(primary.get("market_type") or "spot"),
                position_mode=str(trading_config.get("position_mode") or ""),
            )
            run_id = int(runtime_run.strategy_run_id or 0)
            last_prices: dict[str, float] = {}
            last_price_seen_at: dict[str, float] = {}
            self._heartbeat(strategy_id, run_id, primary, last_prices, 0)
            from app.services.strategy_runtime.bot_type import resolve_bot_type

            bot_type = resolve_bot_type(
                strategy,
                trading_config,
                source_code=code,
            )
            if bot_type:
                # Keep all downstream risk helpers on the same canonical
                # classification, including legacy executor_type deployments.
                trading_config["bot_type"] = bot_type
            if bot_type == "grid":
                trading_config = self._recover_generated_grid_config(
                    trading_config,
                    program.namespace,
                )
            if execution_mode == "live" and bot_type == "grid":
                exit_reason = yield from self._run_grid_resting_loop(
                    strategy_id=strategy_id,
                    strategy_run_id=run_id,
                    stop_event=stop_event,
                    strategy_name=str(strategy.get("strategy_name") or f"strategy_{strategy_id}"),
                    primary=primary,
                    candidates=candidates,
                    frames=frames,
                    trading_config=trading_config,
                    exchange_config=exchange_config,
                    initial_capital=initial_capital,
                    notification_config=_json_object(strategy.get("notification_config")),
                )
                return
            price_feed_meta = {
                "source": "exchange_rest",
                "age_ms": 0,
                "connected": False,
            }
            if execution_mode == "live" and account_exchange:
                from app.services.shared_market_price_feed import acquire_public_market_price_feed

                market_price_feed = acquire_public_market_price_feed(
                    exchange_id=account_exchange,
                    market_type=str(primary.get("market_type") or "spot"),
                    instruments=candidates,
                    rest_fallback=runtime_prices,
                )

                def runtime_prices() -> dict[str, float]:
                    snapshot = market_price_feed.snapshot(
                        max_age_seconds=float(
                            trading_config.get("price_stale_after_seconds") or 10.0
                        )
                    )
                    price_feed_meta.update({
                        "source": snapshot.source,
                        "age_ms": snapshot.age_ms,
                        "connected": snapshot.connected,
                    })
                    return snapshot.prices
            state_store = RuntimeStateStore(
                strategy_id=strategy_id,
                strategy_run_id=run_id,
                state_key="strategy_v2_session",
            )
            restored_state = state_store.load()
            if restored_state:
                session.restore_session_snapshot(restored_state)
            else:
                legacy_protection_store = RuntimeStateStore(
                    strategy_id=strategy_id,
                    strategy_run_id=run_id,
                    state_key="protection",
                )
                session.restore_protection_snapshot(legacy_protection_store.load())
            order_intent_service = OrderIntentService(
                strategy_id=strategy_id,
                strategy_run_id=run_id,
            )

            daily_policy = daily_equity_execution_policy(
                frequency,
                candidates,
                execution_mode=execution_mode,
                schedules=program.manifest.schedules,
            )
            event_driven_bars = bool(
                _env_enabled("BAR_CLOSE_EVENT_SCHEDULER_ENABLED", default=True)
                and daily_policy is None
                and bot_type not in {"grid", "martingale", "layered_martingale"}
                and not callable(program.namespace.get("on_price_tick"))
                and supports_bar_close_events(frequency, candidates)
            )
            stream_keys: set[BarStreamKey] = set()
            if event_driven_bars:
                stream_keys = {
                    BarStreamKey.from_member(
                        member,
                        frequency,
                        default_venue=account_exchange,
                    )
                    for member in candidates
                }

                def wake_for_closed_bar(event) -> None:
                    if not stop_event.is_set():
                        bar_event_pending.set()
                        self._scheduler.wake_strategy(strategy_id, event)

                if not self.external_bar_triggers:
                    bar_subscriptions = [
                        self._bar_close_clock.subscribe(key, wake_for_closed_bar)
                        for key in stream_keys
                    ]
            try:
                event_subscription_repository = self._register_event_subscriptions(
                    strategy_id,
                    stream_keys,
                )
            except Exception:
                logger.exception(
                    "Strategy event subscription registration failed: strategy=%s",
                    strategy_id,
                )

            signal_poll = max(1.0, min(30.0, float(trading_config.get("data_poll_seconds") or 5)))
            risk_tick = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
            try:
                configured_state_write_interval = float(
                    trading_config.get("state_write_interval_seconds")
                    or os.getenv("STRATEGY_STATE_WRITE_INTERVAL_SEC", "5")
                )
            except (TypeError, ValueError):
                configured_state_write_interval = 5.0
            state_write_interval = max(
                1.0,
                min(60.0, configured_state_write_interval),
            )
            position_mark_interval = state_write_interval
            price_stale_after = max(
                risk_tick * 3.0,
                min(30.0, float(trading_config.get("price_stale_after_seconds") or 10.0)),
            )
            next_signal_poll = 0.0
            next_position_mark_at = 0.0
            last_signal_bar_token: int | None = None
            last_processed_frame_timestamp: pd.Timestamp | None = None
            initial_frames_pending = True
            stale_price_logged = False
            consecutive_errors = 0
            last_market_data_failure_key = ""
            last_market_data_log_at = 0.0
            strategy_name = str(strategy.get("strategy_name") or f"strategy_{strategy_id}")
            notification_config = _json_object(strategy.get("notification_config"))
            leverage = max(1.0, float(trading_config.get("leverage") or strategy.get("leverage") or 1))
            from app.services.strategy_live_guard import resolve_strategy_direction_mode

            direction_mode = resolve_strategy_direction_mode(strategy)
            append_strategy_log(
                strategy_id,
                "info",
                f"Strategy runtime ready: instruments={len(candidates)}, timeframe={frequency}, "
                f"mode={execution_mode}, trigger={'bar_close_event' if event_driven_bars else 'runtime_poll'}",
            )

            while self._is_strategy_running(strategy_id, stop_event):
                cycle_started = time.monotonic()
                bar_event_triggered = bar_event_pending.is_set()
                if bar_event_triggered:
                    bar_event_pending.clear()
                wait_seconds = risk_tick
                try:
                    references = session.context.order_references()
                    if references:
                        session.context.update_order_statuses(
                            order_intent_service.statuses_by_client_order_ids(references)
                        )
                    persist_cancellations(session.context, order_intent_service)
                    positions = self._positions_by_symbol(
                        strategy_id,
                        candidates,
                        strategy=strategy,
                    )
                    fresh_prices = runtime_prices()
                    price_clock = time.monotonic()
                    for symbol_key, value in fresh_prices.items():
                        if float(value or 0.0) > 0:
                            last_prices[symbol_key] = float(value)
                            last_price_seen_at[symbol_key] = price_clock
                    active_prices = {
                        symbol_key: value
                        for symbol_key, value in last_prices.items()
                        if price_clock - last_price_seen_at.get(symbol_key, 0.0)
                        <= price_stale_after
                    }
                    if not active_prices:
                        if not stale_price_logged:
                            append_strategy_log(
                                strategy_id,
                                "warning",
                                "Live price is stale; new entries and runtime exits are paused until recovery",
                            )
                            stale_price_logged = True
                    elif stale_price_logged:
                        append_strategy_log(strategy_id, "info", "Live price feed recovered")
                        stale_price_logged = False
                    if (
                        execution_mode == "live"
                        and active_prices
                        and positions
                        and price_clock >= next_position_mark_at
                    ):
                        from app.services.live_trading.records import mark_live_positions

                        mark_live_positions(strategy_id, active_prices)
                        next_position_mark_at = price_clock + position_mark_interval
                    if execution_mode == "signal" and active_prices:
                        from app.services.virtual_trading import (
                            mark_virtual_positions,
                            match_virtual_limit_orders,
                        )

                        virtual_fills = match_virtual_limit_orders(
                            strategy_id,
                            active_prices,
                            strategy_run_id=run_id,
                        )
                        mark_virtual_positions(
                            strategy_id,
                            active_prices,
                            strategy_run_id=run_id,
                        )
                        if virtual_fills:
                            positions = self._positions_by_symbol(
                                strategy_id,
                                candidates,
                                strategy=strategy,
                            )
                            references = session.context.order_references()
                            if references:
                                session.context.update_order_statuses(
                                    order_intent_service.statuses_by_client_order_ids(references)
                                )
                    equity_positions: list[dict[str, Any]] = []
                    positions_prices_fresh = True
                    for position_key, position in positions.items():
                        symbol_key, separator, suffix = str(position_key).rpartition("::")
                        runtime_symbol = symbol_key if separator and suffix in {"long", "short"} else str(position_key)
                        position_mark = active_prices.get(runtime_symbol)
                        if abs(float(position.get("amount") or 0.0)) > 1e-12 and not position_mark:
                            positions_prices_fresh = False
                        equity_positions.append({
                            "symbol": runtime_symbol,
                            "side": position.get("side") or "long",
                            "size": abs(float(position.get("amount") or 0.0)),
                            "entry_price": position.get("avg_cost") or 0.0,
                            "current_price": position_mark or 0.0,
                        })
                    current_equity = self._calculate_current_equity(
                        strategy_id,
                        initial_capital,
                        current_positions=equity_positions,
                        current_prices=active_prices,
                    )
                    session.synchronize_positions(
                        positions,
                        total_value=current_equity,
                        available_cash=available_strategy_cash(current_equity, positions, candidates, leverage, active_prices),
                    )
                    risk_timestamp = pd.Timestamp.now(tz="UTC")
                    equity_intents, equity_messages, equity_stop_reason = (
                        session.evaluate_equity_risk(timestamp=risk_timestamp)
                        if active_prices and positions_prices_fresh
                        else ([], [], "")
                    )
                    for message in equity_messages:
                        append_strategy_log(strategy_id, "info", message)
                    equity_submission_failed = False
                    for intent in equity_intents:
                        submitted = self._execute_strategy_v2_intent(
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            intent=intent,
                            frames=frames,
                            frequency_frames=frequency_frames,
                            candidates=candidates,
                            initial_capital=initial_capital,
                            strategy_equity=current_equity,
                            leverage=leverage,
                            execution_mode=execution_mode,
                            notification_config=notification_config,
                            trading_config=trading_config,
                            exchange_config=exchange_config,
                            signal_ts=self._intent_signal_timestamp(intent, risk_timestamp),
                            strategy_run_id=run_id,
                            current_price_override=active_prices.get(str(intent.symbol)),
                            direction_mode=direction_mode,
                        )
                        if not submitted and intent.kind in {
                            "target_quantity",
                            "target_value",
                            "target_percent",
                        } and abs(float(intent.value or 0.0)) <= 1e-12:
                            position_key = _runtime_position_key(
                                intent.symbol,
                                intent.position_side,
                            )
                            live_position = (
                                positions.get(position_key)
                                or positions.get(str(intent.symbol))
                                or {}
                            )
                            # A portfolio limit may be crossed after a prior
                            # cycle has already closed.  A zero-target order is
                            # then a successful no-op, and the robot must still
                            # enter the risk-stopped state instead of retrying
                            # forever.
                            submitted = abs(float(live_position.get("amount") or 0.0)) <= 1e-12
                        equity_submission_failed = equity_submission_failed or not submitted
                        if intent.client_order_id:
                            session.context.update_order_statuses({
                                intent.client_order_id: {
                                    "client_order_id": intent.client_order_id,
                                    "status": "submitted" if submitted else "rejected",
                                },
                            })
                    if equity_stop_reason and equity_submission_failed:
                        session.release_equity_risk_exit()
                        append_strategy_log(
                            strategy_id,
                            "warning",
                            f"Equity risk exit submission failed and will retry: {equity_stop_reason}",
                        )

                    protection_intents = [] if equity_stop_reason else session.evaluate_protections(
                        active_prices,
                        timestamp=risk_timestamp,
                    )
                    for intent in protection_intents:
                        submitted = self._execute_strategy_v2_intent(
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            intent=intent,
                            frames=frames,
                            frequency_frames=frequency_frames,
                            candidates=candidates,
                            initial_capital=initial_capital,
                            strategy_equity=current_equity,
                            leverage=leverage,
                            execution_mode=execution_mode,
                            notification_config=notification_config,
                            trading_config=trading_config,
                            exchange_config=exchange_config,
                            signal_ts=int(time.time()),
                            strategy_run_id=run_id,
                            current_price_override=active_prices.get(str(intent.symbol)),
                            direction_mode=direction_mode,
                        )
                        if not submitted:
                            session.release_protection_exit(
                                intent.symbol,
                                position_side=intent.position_side,
                            )
                        if intent.client_order_id:
                            session.context.update_order_statuses({
                                intent.client_order_id: {
                                    "client_order_id": intent.client_order_id,
                                    "status": "submitted" if submitted else "rejected",
                                },
                            })

                    # Suppress normal strategy orders for a symbol until its
                    # asynchronous protection close has completed and the
                    # strategy position snapshot becomes flat.
                    protected = session.pending_protection_exit_symbols()

                    pending_count = len(equity_intents) + len(protection_intents)
                    # Martingale templates explicitly declare a realtime
                    # price trigger.  Run only their optional tick hook here;
                    # ordinary Strategy V2 sources and DCA remain driven by
                    # completed candles/schedules below.  The generated robot
                    # keeps stable client ids and pending level state, so an
                    # async order cannot be emitted again on the next tick.
                    if (
                        execution_mode == "live"
                        and not equity_stop_reason
                        and active_prices
                        and bot_type in {"martingale", "layered_martingale"}
                    ):
                        realtime_intents, realtime_messages = session.evaluate_price_tick(
                            active_prices,
                            timestamp=risk_timestamp,
                        )
                        realtime_intents = [
                            intent
                            for intent in realtime_intents
                            if _runtime_position_key(
                                intent.symbol,
                                intent.position_side,
                            ) not in protected
                        ]
                        pending_count += len(realtime_intents)
                        for message in realtime_messages:
                            append_strategy_log(strategy_id, "info", message)
                        for intent in realtime_intents:
                            submitted = self._execute_strategy_v2_intent(
                                strategy_id=strategy_id,
                                strategy_name=strategy_name,
                                intent=intent,
                                frames=frames,
                                frequency_frames=frequency_frames,
                                candidates=candidates,
                                initial_capital=initial_capital,
                                strategy_equity=current_equity,
                                leverage=leverage,
                                execution_mode=execution_mode,
                                notification_config=notification_config,
                                trading_config=trading_config,
                                exchange_config=exchange_config,
                                signal_ts=self._intent_signal_timestamp(intent, risk_timestamp),
                                strategy_run_id=run_id,
                                current_price_override=active_prices.get(str(intent.symbol)),
                                direction_mode=direction_mode,
                            )
                            if intent.client_order_id:
                                session.context.update_order_statuses({
                                    intent.client_order_id: {
                                        "client_order_id": intent.client_order_id,
                                        "status": (
                                            "submitted" if submitted else "rejected"
                                        ),
                                    },
                                })
                    if not equity_stop_reason and _signal_cycle_due(
                        cycle_started=cycle_started,
                        next_signal_poll=next_signal_poll,
                        bar_event_triggered=bar_event_triggered,
                    ):
                        from app.services.market_schedule import equity_daily_execution_session

                        signal_session = equity_daily_execution_session(*daily_policy) if daily_policy else None
                        current_bar_token = completed_bar_token(frequency)
                        if signal_session is not None:
                            current_bar_token = int(signal_session.timestamp())
                        has_new_closed_bar = (
                            initial_frames_pending
                            or current_bar_token != last_signal_bar_token
                        ) and (not daily_policy or signal_session is not None)
                        if has_new_closed_bar:
                            # Startup already warmed the complete frame bundle.
                            # Every later trigger extends the shared cache only
                            # for the newly completed candle window.
                            if not initial_frames_pending or daily_policy:
                                frequency_frames = fetch_runtime_frames()
                                frames = frequency_frames[frequency]
                            latest_frame_timestamp = _latest_frame_timestamp(frames)
                            frame_advanced = bool(
                                initial_frames_pending
                                or last_processed_frame_timestamp is None
                                or (
                                    latest_frame_timestamp is not None
                                    and latest_frame_timestamp
                                    > last_processed_frame_timestamp
                                )
                            )
                            if daily_policy:
                                frame_advanced = bool(
                                    frame_advanced
                                    and equity_daily_execution_session(*daily_policy) == signal_session
                                    and equity_daily_frames_ready(frames, candidates, signal_session, daily_policy[0])
                                    and all(
                                        str(member["key"]) in active_prices
                                        and time.monotonic() - last_price_seen_at.get(str(member["key"]), 0.0)
                                        <= price_stale_after
                                        for member in candidates
                                    )
                                )
                            if frame_advanced:
                                begin_signal_cycle = getattr(
                                    self.order_gateway,
                                    "begin_signal_cycle",
                                    None,
                                )
                                if callable(begin_signal_cycle):
                                    begin_signal_cycle(run_id)
                                intents, messages, timestamp = session.process(
                                    frames,
                                    frequency_frames=frequency_frames,
                                )
                                intents = [
                                    intent
                                    for intent in intents
                                    if _runtime_position_key(
                                        intent.symbol,
                                        intent.position_side,
                                    ) not in protected
                                ]
                                pending_count += len(intents)
                                for message in messages:
                                    append_strategy_log(strategy_id, "info", message)
                                for intent in intents:
                                    submitted = self._execute_strategy_v2_intent(
                                        strategy_id=strategy_id,
                                        strategy_name=strategy_name,
                                        intent=intent,
                                        frames=frames,
                                        frequency_frames=frequency_frames,
                                        candidates=candidates,
                                        initial_capital=initial_capital,
                                        strategy_equity=current_equity,
                                        leverage=leverage,
                                        execution_mode=execution_mode,
                                        notification_config=notification_config,
                                        trading_config=trading_config,
                                        exchange_config=exchange_config,
                                        signal_ts=self._intent_signal_timestamp(intent, timestamp),
                                        current_price_override=active_prices.get(str(intent.symbol)) if daily_policy else None,
                                        strategy_run_id=run_id,
                                        direction_mode=direction_mode,
                                    )
                                    if intent.client_order_id:
                                        session.context.update_order_statuses({
                                            intent.client_order_id: {
                                                "client_order_id": intent.client_order_id,
                                                "status": (
                                                    "submitted" if submitted else "rejected"
                                                ),
                                            },
                                        })
                                finish_signal_cycle = getattr(
                                    self.order_gateway,
                                    "finish_signal_cycle",
                                    None,
                                )
                                if callable(finish_signal_cycle):
                                    finish_signal_cycle(run_id)
                                initial_frames_pending = False
                                last_signal_bar_token = current_bar_token
                                last_processed_frame_timestamp = latest_frame_timestamp
                        next_signal_poll = cycle_started + signal_poll
                    state_store.save(
                        session.session_snapshot(),
                        min_interval_seconds=state_write_interval,
                    )
                    self._heartbeat(
                        strategy_id,
                        run_id,
                        primary,
                        last_prices,
                        pending_count,
                        loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                        status="healthy" if active_prices else "degraded",
                        last_error="" if active_prices else "live_price_stale",
                        price_source=str(price_feed_meta.get("source") or "exchange_rest"),
                        price_age_ms=int(price_feed_meta.get("age_ms") or 0),
                        trigger_mode=(
                            "realtime_price"
                            if bot_type in {"martingale", "layered_martingale"}
                            else "closed_bar_event"
                            if event_driven_bars
                            else "closed_bar"
                        ),
                        fill_transport="private_stream_with_rest_reconciliation",
                    )
                    wait_seconds = _runtime_wait_seconds(
                        risk_tick=risk_tick,
                        frequency=frequency,
                        candidates=candidates,
                        bot_type=bot_type,
                        positions=positions,
                        order_statuses=session.context.order_status_snapshot(),
                        protected=protected,
                        pending_count=pending_count,
                        market_ready=bool(active_prices),
                        event_driven=event_driven_bars,
                    )
                    if equity_stop_reason and not equity_submission_failed:
                        exit_reason = f"equity risk stopped: {equity_stop_reason}"
                        self._last_exit_reason[strategy_id] = exit_reason
                        append_strategy_log(strategy_id, "warning", exit_reason)
                        self._mark_stopped(strategy_id)
                        break
                    consecutive_errors = 0
                    if last_market_data_failure_key:
                        append_strategy_log(strategy_id, "info", "Market data feed recovered")
                        last_market_data_failure_key = ""
                        last_market_data_log_at = 0.0
                except Exception as exc:
                    if isinstance(exc, MarketDataUnavailableError) or str(exc) == "strategyV2.noMarketData":
                        next_signal_poll = cycle_started + signal_poll
                        failure = (
                            exc.failure
                            if isinstance(exc, MarketDataUnavailableError)
                            else classify_market_data_failure("No usable market data")
                        )
                        failure_key = "|".join((
                            failure.code,
                            failure.exchange_id,
                            failure.market_type,
                            failure.symbol,
                            failure.timeframe,
                            failure.technical_detail,
                        ))
                        logger.warning(
                            "Strategy %s market data unavailable (%s): %s",
                            strategy_id,
                            failure.code,
                            failure.technical_detail or failure.message,
                        )
                        if (
                            failure_key != last_market_data_failure_key
                            or cycle_started - last_market_data_log_at >= 60.0
                        ):
                            append_strategy_log(
                                strategy_id,
                                "warning",
                                format_market_data_log(failure),
                            )
                            last_market_data_failure_key = failure_key
                            last_market_data_log_at = cycle_started
                        self._heartbeat(
                            strategy_id,
                            run_id,
                            primary,
                            last_prices,
                            0,
                            loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                            status="degraded",
                            last_error=f"marketData.{failure.code}",
                        )
                    else:
                        consecutive_errors += 1
                        logger.exception("Strategy %s runtime cycle failed", strategy_id)
                        append_strategy_log(strategy_id, "error", f"Runtime cycle failed: {exc}")
                        self._heartbeat(
                            strategy_id,
                            run_id,
                            primary,
                            last_prices,
                            0,
                            loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                            status="degraded",
                            last_error=str(exc),
                        )
                        if consecutive_errors >= 5:
                            raise RuntimeError(f"strategyV2.repeatedRuntimeFailure:{exc}") from exc
                remaining = wait_seconds - (time.monotonic() - cycle_started)
                yield max(0.0, remaining)
        except Exception as exc:
            exit_reason = str(exc)
            self._last_exit_reason[strategy_id] = exit_reason
            logger.exception("Strategy %s stopped after runtime failure", strategy_id)
            if isinstance(exc, MarketDataUnavailableError):
                append_strategy_log(strategy_id, "error", format_market_data_log(exc.failure))
            else:
                append_strategy_log(strategy_id, "error", exit_reason)
            self._mark_stopped(strategy_id)
        finally:
            clear_signal_state = getattr(
                self.order_gateway,
                "clear_signal_state",
                None,
            )
            if callable(clear_signal_state):
                clear_signal_state(run_id)
            if state_store is not None:
                state_store.flush()
            for subscription in bar_subscriptions:
                subscription.close()
            if event_subscription_repository is not None:
                try:
                    event_subscription_repository.remove_strategy(strategy_id)
                except Exception:
                    logger.exception(
                        "Strategy event subscription cleanup failed: strategy=%s",
                        strategy_id,
                    )
            if market_price_feed is not None:
                market_price_feed.release()
            with self.lock:
                preserve_run = strategy_id in self._runtime_handoff_ids
            if run_id > 0 and not preserve_run:
                finish_strategy_run(run_id, reason=exit_reason)
            with self.lock:
                self._runtime_handoff_ids.discard(strategy_id)
                if self._runtime_stop_events.get(strategy_id) is stop_event:
                    self.running_strategies.pop(strategy_id, None)
                    self._runtime_stop_events.pop(strategy_id, None)
                    self._runtime_fencing_tokens.pop(strategy_id, None)
                    self._bar_event_pending.pop(strategy_id, None)

    def _execute_strategy_v2_intent(
        self,
        *,
        strategy_id: int,
        strategy_name: str,
        intent: OrderIntent,
        frames: Dict[str, pd.DataFrame],
        frequency_frames: Dict[str, Dict[str, pd.DataFrame]] | None = None,
        candidates: List[Dict[str, Any]],
        initial_capital: float,
        leverage: float,
        execution_mode: str,
        notification_config: Dict[str, Any],
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        signal_ts: int,
        strategy_run_id: int = 0,
        current_price_override: float | None = None,
        direction_mode: str = "",
        strategy_equity: float | None = None,
    ) -> bool:
        member = next(
            (item for item in candidates if str(item.get("key") or "") == str(intent.symbol)),
            None,
        )
        if not member:
            raise RuntimeError(f"strategyV2.instrumentNotFound:{intent.symbol}")
        frame = frames.get(str(intent.symbol))
        price = float(current_price_override or 0)
        if price <= 0 and frame is not None and not frame.empty:
            price = float(frame["close"].iloc[-1])
        if price <= 0:
            raise RuntimeError(f"strategyV2.priceUnavailable:{intent.symbol}")

        symbol = str(member.get("symbol") or "")
        positions = self._get_current_positions(strategy_id, symbol)
        intent_position_side = str(intent.position_side or "").strip().lower()
        if intent_position_side in {"long", "short"}:
            positions = [
                item for item in positions
                if str(item.get("side") or "").strip().lower() == intent_position_side
            ]
        current_amount = sum(
            (-1.0 if str(item.get("side") or "").lower() == "short" else 1.0)
            * float(item.get("size") or 0)
            for item in positions
        )
        market_type = str(member.get("market_type") or "spot").lower()
        sizing_capital = max(
            0.0,
            float(initial_capital if strategy_equity is None else strategy_equity),
        )
        target_amount = self._target_amount(
            intent,
            current_amount,
            sizing_capital,
            price,
            leverage=leverage,
            market_type=market_type,
        )
        if execution_mode == "live":
            target_amount = self._direction_constrained_target(
                target_amount,
                direction_mode=direction_mode,
            )
        if market_type == "spot" and target_amount < -1e-12:
            raise RuntimeError("strategyV2.spotShortUnsupported")

        closes_position = abs(target_amount) <= 1e-12 and abs(current_amount) > 1e-12
        if (
            str(member.get("market") or "") == "Crypto"
            and symbol.upper().endswith(("/USDT", "/USDC", "/USD"))
            and intent.kind.startswith("target_")
            and current_amount * target_amount > 0
            and abs(target_amount - current_amount) * price <= 10.0 + 1e-9
        ):
            return False
        if abs(target_amount - current_amount) * price < MIN_LIVE_ORDER_NOTIONAL and not closes_position:
            return False

        requests = self._order_plan(current_amount, target_amount)
        if (
            execution_mode == "live"
            and len(requests) > 1
            and requests[0][0] in {"close_long", "close_short"}
        ):
            # Reversal orders are asynchronous.  Wait for the closing leg to
            # fill and for the strategy ledger to synchronize before sizing
            # the opposite entry.
            requests = requests[:1]
        submitted = False
        for action, quantity in requests:
            submitted = bool(self._execute_signal(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                symbol=symbol,
                current_price=price,
                signal_type=action,
                script_base_qty=quantity,
                current_positions=positions,
                leverage=leverage,
                initial_capital=initial_capital,
                strategy_equity=sizing_capital,
                market_type=market_type,
                market_category=str(member.get("market") or ""),
                execution_mode=execution_mode,
                notification_config=notification_config,
                trading_config=trading_config,
                exchange_config=exchange_config,
                signal_reason=str(intent.reason or "strategy"),
                order_type=str(intent.order_type or "market"),
                execution_algo=str(intent.execution_algo or "market"),
                limit_price=float(intent.limit_price or 0.0),
                maker_wait_sec=float(intent.maker_wait_sec or 0.0),
                maker_offset_bps=float(intent.maker_offset_bps or 0.0),
                protection=intent.protection.metadata() if intent.protection else {},
                client_order_id=str(intent.client_order_id or ""),
                signal_ts=signal_ts,
                strategy_run_id=strategy_run_id,
                price_exchange_id=str(member.get("exchange_id") or ""),
                price_instrument_id=str(member.get("instrument_id") or ""),
                market_frame=frame,
                market_frames={
                    frequency: bundle.get(str(intent.symbol))
                    for frequency, bundle in (frequency_frames or {}).items()
                    if bundle.get(str(intent.symbol)) is not None
                },
            )) or submitted
        return submitted

    def _execute_signal(self, **values: Any) -> bool:
        strategy_id = int(values["strategy_id"])
        strategy = self._load_strategy(strategy_id) or {}
        requested_execution_mode = str(
            values.get("execution_mode") or "signal"
        ).strip().lower()
        # A fatal worker error stops the strategy through the database.  The
        # current runtime callback may still hold more intents, so enforce the
        # lifecycle state again at the queue boundary to prevent a burst of
        # orders after the stop has been requested.
        persisted_status = str(strategy.get("status") or "").strip().lower()
        if (
            requested_execution_mode == "live"
            and persisted_status
            and persisted_status != "running"
        ):
            return False
        if requested_execution_mode == "live":
            from app.services.strategy_live_guard import (
                StrategyDirectionModeViolation,
                validate_strategy_signal_direction,
            )

            try:
                validate_strategy_signal_direction(strategy, values.get("signal_type"))
            except StrategyDirectionModeViolation as exc:
                message = f"Signal blocked by strategy direction guard: {exc}"
                logger.warning("Strategy %s %s", strategy_id, message)
                append_strategy_log(strategy_id, "warning", message)
                return False
            action = str(values.get("signal_type") or "").strip().lower()
            market_type = str(values.get("market_type") or "swap").strip().lower()
            if action in {"open_long", "add_long", "open_short", "add_short"}:
                try:
                    from app.services.exchange_execution import resolve_exchange_config
                    from app.services.live_trading.leg_context import credential_id_from_exchange_config
                    from app.services.live_trading.position_ownership import (
                        is_position_leg_blocked,
                        supports_position_coexistence,
                    )

                    resolved_exchange = resolve_exchange_config(
                        _json_object(strategy.get("exchange_config")),
                        user_id=int(strategy.get("user_id") or 0),
                    )
                    if supports_position_coexistence(
                        market_type, str(resolved_exchange.get("exchange_id") or "")
                    ) and is_position_leg_blocked(
                        user_id=int(strategy.get("user_id") or 0),
                        credential_id=int(credential_id_from_exchange_config(resolved_exchange) or 0),
                        market_type=market_type,
                        symbol=str(values.get("symbol") or ""),
                        side="long" if action.endswith("_long") else "short",
                    ):
                        # The worker logged the state transition with full
                        # quantities.  Suppress repeated queue/log churn until
                        # a repair or successful reconciliation clears it.
                        return False
                except Exception as exc:
                    logger.debug("Position drift queue check skipped for strategy %s: %s", strategy_id, exc)
        quantity = float(values.get("script_base_qty") or 0)
        reference_price = float(values.get("current_price") or 0)
        initial_capital = float(values.get("initial_capital") or 0)
        strategy_equity = max(
            0.0,
            float(values.get("strategy_equity") if values.get("strategy_equity") is not None else initial_capital),
        )
        leverage = float(values.get("leverage") or 1)
        trading_config = _json_object(values.get("trading_config"))
        ai_decision_filter = bool(trading_config.get("ai_decision_filter"))
        nominal_capacity = strategy_equity * max(1.0, leverage)
        entry_pct = ((quantity * reference_price) / nominal_capacity * 100.0) if nominal_capacity > 0 else 0.0
        from app.services.pending_orders.order_budget import strategy_order_budget_snapshot

        budget = strategy_order_budget_snapshot(
            action=str(values.get("signal_type") or ""),
            quantity=quantity,
            price=reference_price,
            initial_capital=strategy_equity,
            leverage=leverage,
            market_type=str(values.get("market_type") or "spot"),
            current_positions=values.get("current_positions") or (),
            buffer_ratio=float(
                trading_config.get("order_budget_buffer_ratio")
                or 0.02
            ),
        )
        if not budget["allowed"]:
            append_strategy_log(
                strategy_id,
                "error",
                "Order rejected by strategy budget guard: "
                f"action={budget['action']}, quantity={format_decimal(quantity)}, "
                f"price={format_decimal(reference_price, decimal_places=8)}, "
                f"order_notional={budget['order_notional']:.4f}, "
                f"projected_notional={budget['projected_notional']:.4f}, "
                f"limit={budget['limit']:.4f}, reason={budget['reason']}",
            )
            return False
        request = LiveOrderRequest(
            strategy_id=strategy_id,
            strategy_run_id=int(values.get("strategy_run_id") or 0),
            user_id=int(strategy.get("user_id") or 0),
            symbol=str(values.get("symbol") or ""),
            action=str(values.get("signal_type") or ""),
            quantity=quantity,
            reference_price=reference_price,
            signal_timestamp=int(values.get("signal_ts") or time.time()),
            market_type=str(values.get("market_type") or "spot"),
            execution_mode=str(values.get("execution_mode") or "signal"),
            leverage=leverage,
            margin_mode=str(
                trading_config.get("margin_mode")
                or trading_config.get("marginMode")
                or "cross"
            ),
            reason=str(values.get("signal_reason") or ""),
            notification_config=dict(values.get("notification_config") or {}),
            order_type=str(values.get("order_type") or "market"),
            execution_algo=str(values.get("execution_algo") or "market"),
            limit_price=float(values.get("limit_price") or 0.0),
            maker_wait_sec=float(values.get("maker_wait_sec") or 0.0),
            maker_offset_bps=float(values.get("maker_offset_bps") or 0.0),
            protection=dict(values.get("protection") or {}),
            client_order_id=str(values.get("client_order_id") or ""),
            ai_decision_filter=ai_decision_filter,
            strategy_type=str(trading_config.get("bot_type") or ""),
            decision_context=(
                build_strategy_decision_context(
                    values={**values, "trading_config": trading_config},
                    strategy=strategy,
                    order_budget=budget,
                    strategy_equity=strategy_equity,
                    initial_capital=initial_capital,
                    entry_percent=entry_pct,
                )
                if ai_decision_filter else None
            ),
            runtime_fencing_token=self.runtime_fencing_token(strategy_id),
            sizing={
                "initial_capital": initial_capital,
                "entry_pct": entry_pct,
                "leverage": leverage,
                "source": "strategy_v2",
                **(
                    {
                        "commission_rate": float(trading_config.get("commission") or 0.0),
                        "slippage_rate": float(trading_config.get("slippage") or 0.0),
                    }
                    if requested_execution_mode == "signal"
                    else {}
                ),
                **(
                    {"current_equity": strategy_equity}
                    if values.get("strategy_equity") is not None
                    else {}
                ),
            },
        )
        inflight_check = getattr(self.order_gateway, "has_inflight", None)
        if callable(inflight_check) and inflight_check(request):
            return False
        if self.runtime_guard and not self.runtime_guard(strategy_id):
            return False
        pending_id = self.order_gateway.submit(request)
        if pending_id:
            order_details = [f"pending_id={pending_id}"]
            if request.order_type == "limit":
                order_details.append(
                    f"limit_price={format_decimal(request.limit_price, decimal_places=8)}"
                )
            if request.client_order_id:
                order_details.append(f"client_order_id={request.client_order_id}")
            if request.execution_mode == "signal":
                append_strategy_log(
                    strategy_id,
                    "signal",
                    f"Signal notification queued: {request.action} {request.symbol} "
                    f"quantity={format_decimal(request.quantity)} "
                    + " ".join(order_details),
                )
            else:
                append_strategy_log(
                    strategy_id,
                    "trade",
                    f"Order queued: {request.action} {request.symbol} "
                    f"quantity={format_decimal(request.quantity)} "
                    + " ".join(order_details),
                )
        return bool(pending_id)

    def _run_grid_resting_loop(
        self,
        *,
        strategy_id: int,
        strategy_run_id: int,
        stop_event: threading.Event,
        strategy_name: str,
        primary: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        frames: Dict[str, pd.DataFrame],
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        initial_capital: float,
        notification_config: Dict[str, Any],
    ):
        from app.services.grid.runner import GridRestingRunner
        from app.services.live_trading.account_configuration import configure_derivatives_account
        from app.services.live_trading.factory import create_client
        from app.services.pending_orders.live_order_support import bind_instrument_product_contract

        symbol = str(primary.get("symbol") or "")
        market_type = str(primary.get("market_type") or "swap").strip().lower()
        market_category = str(primary.get("market") or "Crypto")
        exchange_id = str(primary.get("exchange_id") or exchange_config.get("exchange_id") or "")
        leverage = max(1.0, float(trading_config.get("leverage") or 1))
        margin_mode = str(
            trading_config.get("margin_mode") or trading_config.get("marginMode") or "cross"
        )
        client_holder: Dict[str, Any] = {}
        bound_exchange_config = bind_instrument_product_contract(
            exchange_config,
            trading_config,
            symbol=symbol,
            exchange_id=exchange_id,
            market_type=market_type,
        )

        def create_grid_client():
            client = client_holder.get("client")
            if client is None:
                client = create_client(bound_exchange_config, market_type=market_type)
                if market_type == "swap":
                    configure_derivatives_account(
                        client,
                        exchange_id=exchange_id,
                        symbol=symbol,
                        leverage=leverage,
                        margin_mode=margin_mode,
                    )
                client_holder["client"] = client
            return client

        def enqueue_market(signal_type: str, quantity: float, price: float, reason: str) -> bool:
            if self.runtime_guard and not self.runtime_guard(strategy_id):
                return False
            return self._execute_signal(
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                symbol=symbol,
                current_price=float(price or 0),
                signal_type=str(signal_type or ""),
                script_base_qty=max(0.0, float(quantity or 0)),
                leverage=leverage,
                initial_capital=initial_capital,
                market_type=market_type,
                market_category=market_category,
                execution_mode="live",
                notification_config=notification_config,
                trading_config=trading_config,
            exchange_config=exchange_config,
                signal_reason=str(reason or "grid"),
                signal_ts=int(time.time()),
                strategy_run_id=strategy_run_id,
                price_exchange_id=exchange_id,
            )

        key = str(primary.get("key") or "")
        frame = frames.get(key)
        initial_prices = self._live_prices(candidates)
        initial_price = float(initial_prices.get(key) or 0)
        if initial_price <= 0 and frame is not None and not frame.empty:
            initial_price = float(frame["close"].iloc[-1])
        persisted_grid_bounds = None
        try:
            from app.services.live_trading.grid_cells import GridCellRepository

            persisted_cells = GridCellRepository().list_cells(strategy_id, symbol)
            if persisted_cells:
                persisted_grid_bounds = (
                    min(float(cell.lower_price) for cell in persisted_cells),
                    max(float(cell.upper_price) for cell in persisted_cells),
                    len(persisted_cells),
                )
        except Exception as exc:
            logger.debug(
                "Grid anchor recovery skipped sid=%s symbol=%s: %s",
                strategy_id,
                symbol,
                exc,
            )
        runtime_grid_config = self._materialize_grid_anchor(
            trading_config,
            initial_price,
            persisted_grid_bounds=persisted_grid_bounds,
        )
        grid_risk_store = RuntimeStateStore(
            strategy_id=strategy_id,
            strategy_run_id=strategy_run_id,
            state_key="grid_equity_risk",
        )
        grid_risk_state = {
            "peak_return": 0.0,
            "trailing_armed": False,
            **grid_risk_store.load(),
        }

        def evaluate_grid_risk(price: float) -> List[Dict[str, Any]]:
            exits = self._grid_bot_risk_exits(
                strategy_id=strategy_id,
                symbol=symbol,
                current_price=float(price),
                trading_config=runtime_grid_config,
                timeframe_seconds=60,
                initial_capital=initial_capital,
                risk_state=grid_risk_state,
            )
            grid_risk_store.save(grid_risk_state)
            return exits

        runner = GridRestingRunner(
            strategy_id,
            symbol,
            runtime_grid_config,
            bound_exchange_config,
            user_id=int((self._load_strategy(strategy_id) or {}).get("user_id") or 1),
            strategy_run_id=strategy_run_id,
            initial_capital=initial_capital,
            enqueue_market_fn=enqueue_market,
            create_client_fn=create_grid_client,
            risk_exit_fn=evaluate_grid_risk,
        )
        runner.engine.order_guard = lambda: self.runtime_guard is None or self.runtime_guard(strategy_id)
        ok, message = runner.startup(initial_price, bars_df=frame)
        if not ok:
            raise RuntimeError(f"grid.startupFailed:{message}")
        tick_seconds = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
        try:
            position_mark_interval = float(
                trading_config.get("state_write_interval_seconds")
                or os.getenv("STRATEGY_STATE_WRITE_INTERVAL_SEC", "5")
            )
        except (TypeError, ValueError):
            position_mark_interval = 5.0
        position_mark_interval = max(1.0, min(60.0, position_mark_interval))
        next_position_mark_at = 0.0
        last_prices: dict[str, float] = {}
        stale_logged = False
        grid_exit_reason = "grid strategy stopped"
        from app.services.shared_market_price_feed import acquire_public_market_price_feed

        grid_price_feed = acquire_public_market_price_feed(
            exchange_id=exchange_id,
            market_type=market_type,
            instruments=candidates,
            rest_fallback=lambda: self._execution_account_prices(
                candidates,
                bound_exchange_config,
                client_holder,
            ),
        )
        try:
            while self._is_strategy_running(strategy_id, stop_event):
                cycle_started = time.monotonic()
                price_snapshot = grid_price_feed.snapshot(
                    max_age_seconds=float(
                        trading_config.get("price_stale_after_seconds") or 10.0
                    )
                )
                prices = price_snapshot.prices
                current_price = float(prices.get(key) or 0)
                if current_price > 0:
                    last_prices[key] = current_price
                    if cycle_started >= next_position_mark_at:
                        from app.services.live_trading.records import mark_live_positions

                        mark_live_positions(strategy_id, {key: current_price})
                        next_position_mark_at = cycle_started + position_mark_interval
                    runner.tick(current_price, high=current_price, low=current_price, bars_df=frame)
                    runner.checkpoint()
                    if stale_logged:
                        append_strategy_log(strategy_id, "info", "Live grid price feed recovered")
                        stale_logged = False
                elif not stale_logged:
                    append_strategy_log(
                        strategy_id,
                        "warning",
                        "Live grid price unavailable; risk checks are paused while exchange resting orders remain active",
                    )
                    stale_logged = True
                grid_health = runner.operational_snapshot()
                runtime_error = "" if current_price > 0 else "live_price_unavailable"
                if not runtime_error:
                    runtime_error = str(grid_health.get("error") or "")
                self._heartbeat(
                    strategy_id,
                    strategy_run_id,
                    primary,
                    last_prices,
                    int(grid_health.get("open_orders") or 0),
                    loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                    status="healthy" if current_price > 0 and grid_health.get("healthy") else "degraded",
                    last_error=runtime_error,
                    price_source=price_snapshot.source,
                    price_age_ms=price_snapshot.age_ms,
                    trigger_mode="exchange_resting_orders",
                    fill_transport="private_stream_with_rest_reconciliation",
                )
                if runner.should_stop:
                    reason = runner.stop_reason or "grid strategy stopped"
                    grid_exit_reason = reason
                    self._last_exit_reason[strategy_id] = reason
                    append_strategy_log(strategy_id, "warning", reason)
                    self._mark_stopped(strategy_id)
                    break
                yield tick_seconds
        finally:
            grid_price_feed.release()
            with self.lock:
                preserve_grid_orders = strategy_id in self._runtime_handoff_ids
            if preserve_grid_orders:
                runner.detach()
            else:
                runner.shutdown()
        return grid_exit_reason

    @staticmethod
    def _recover_generated_grid_config(
        trading_config: Dict[str, Any],
        namespace: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Recover the durable grid contract from an already-saved template.

        Some deployed visual-builder sources predate ``last_run_config``.  The
        generated constants are part of the compiled source contract, so use
        them only when the persisted grid parameters are incomplete.
        """
        runtime_config = dict(trading_config or {})
        existing = (
            dict(runtime_config.get("bot_params") or {})
            if isinstance(runtime_config.get("bot_params"), dict)
            else {}
        )
        try:
            grid_template_version = int(namespace.get("GRID_TEMPLATE_VERSION") or 0)
        except (TypeError, ValueError):
            grid_template_version = 0
        source_is_authoritative = grid_template_version >= 7
        if not source_is_authoritative and all(
            float(existing.get(key) or 0.0) > 0
            for key in ("lowerPrice", "upperPrice", "gridCount")
        ):
            return runtime_config

        lower_values = namespace.get("CELL_LOWER")
        upper_values = namespace.get("CELL_UPPER")
        if not isinstance(lower_values, (list, tuple)) or not isinstance(
            upper_values,
            (list, tuple),
        ):
            return runtime_config
        try:
            lowers = [float(value) for value in lower_values]
            uppers = [float(value) for value in upper_values]
        except (TypeError, ValueError):
            return runtime_config
        if not lowers or len(lowers) != len(uppers):
            return runtime_config
        boundaries = lowers + [uppers[-1]]
        deltas = [
            boundaries[index + 1] - boundaries[index]
            for index in range(len(boundaries) - 1)
        ]
        average_delta = sum(deltas) / len(deltas) if deltas else 0.0
        arithmetic = bool(
            average_delta > 0
            and max(abs(value - average_delta) for value in deltas)
            <= abs(average_delta) * 1e-6
        )
        count = len(lowers)
        raw_cell_budgets = namespace.get("CELL_BUDGET_PCTS")
        cell_budget_pcts = []
        if isinstance(raw_cell_budgets, (list, tuple)) and len(raw_cell_budgets) == count:
            try:
                cell_budget_pcts = [max(0.0, float(value)) for value in raw_cell_budgets]
            except (TypeError, ValueError):
                cell_budget_pcts = []
        raw_cell_roles = namespace.get("CELL_ROLES")
        cell_roles = []
        if isinstance(raw_cell_roles, (list, tuple)) and len(raw_cell_roles) == count:
            cell_roles = [str(value or "").strip().lower() for value in raw_cell_roles]
        existing.update({
            "lowerPrice": min(lowers),
            "upperPrice": max(uppers),
            "gridCount": count,
            "gridCountUnit": "cells",
            "amountPerGridPct": 1.0 / count,
            "gridMode": "arithmetic" if arithmetic else "geometric",
            "gridDirection": str(namespace.get("GRID_SIDE") or "long").strip().lower(),
            "initialPositionPct": float(namespace.get("INITIAL_POSITION_PCT") or 0.0),
            "orderMode": "maker",
            "boundaryAction": "pause",
            "maxOpenOrders": max(
                1,
                int(namespace.get("MAX_OPEN_ENTRY_ORDERS") or count),
            ),
            "dynamicAnchor": bool(namespace.get("DYNAMIC_ANCHOR")),
        })
        if cell_budget_pcts:
            existing["cellBudgetPcts"] = cell_budget_pcts
        if cell_roles:
            existing["cellRoles"] = cell_roles
        if source_is_authoritative:
            existing["adaptiveBounds"] = False
        runtime_config["strategy_family"] = "robot"
        runtime_config["executor_type"] = "grid"
        runtime_config["bot_type"] = "grid"
        runtime_config["bot_params"] = existing
        for runtime_key, source_key in (
            ("equity_take_profit_pct", "EQUITY_TAKE_PROFIT"),
            ("equity_stop_loss_pct", "EQUITY_STOP_LOSS"),
            ("equity_trailing_enabled", "EQUITY_TRAILING_ENABLED"),
            ("equity_trailing_activation_pct", "EQUITY_TRAILING_ACTIVATION"),
            ("equity_trailing_callback_pct", "EQUITY_TRAILING_CALLBACK"),
        ):
            if (
                source_key in namespace
                and (source_is_authoritative or runtime_key not in runtime_config)
            ):
                runtime_config[runtime_key] = namespace[source_key]
        return runtime_config

    @staticmethod
    def _materialize_grid_anchor(
        trading_config: Dict[str, Any],
        initial_price: float,
        *,
        persisted_grid_bounds: Optional[Tuple[float, float, int]] = None,
    ) -> Dict[str, Any]:
        runtime_config = dict(trading_config or {})
        grid_params = (
            dict(runtime_config.get("bot_params") or {})
            if isinstance(runtime_config.get("bot_params"), dict)
            else {}
        )
        if not bool(grid_params.get("dynamicAnchor")):
            return runtime_config
        lower_ratio = float(grid_params.get("lowerPrice") or 0.0)
        upper_ratio = float(grid_params.get("upperPrice") or 0.0)
        reference = (lower_ratio + upper_ratio) / 2.0
        if lower_ratio <= 0 or upper_ratio <= 0 or reference <= 0:
            return runtime_config

        anchor_price = 0.0
        anchor_source = ""
        try:
            from app.services.grid.runtime_state import load_grid_resting_state

            grid_state = load_grid_resting_state(runtime_config)
            anchor_price = float(grid_state.get("dynamic_anchor_price") or 0.0)
            if anchor_price > 0:
                anchor_source = "runtime_state"
        except Exception:
            anchor_price = 0.0

        if anchor_price <= 0 and persisted_grid_bounds:
            try:
                persisted_lower, persisted_upper, persisted_count = persisted_grid_bounds
                configured_count = max(2, int(grid_params.get("gridCount") or 10))
                count_unit = str(grid_params.get("gridCountUnit") or "lines").strip().lower()
                expected_count = (
                    configured_count
                    if count_unit == "cells"
                    else configured_count - 1
                )
                low_anchor = float(persisted_lower or 0.0) * reference / lower_ratio
                high_anchor = float(persisted_upper or 0.0) * reference / upper_ratio
                midpoint = (low_anchor + high_anchor) / 2.0
                relative_gap = (
                    abs(low_anchor - high_anchor) / midpoint
                    if midpoint > 0
                    else float("inf")
                )
                if (
                    int(persisted_count or 0) == expected_count
                    and low_anchor > 0
                    and high_anchor > 0
                    and relative_gap <= 1e-6
                ):
                    anchor_price = midpoint
                    anchor_source = "persisted_cells"
            except (TypeError, ValueError, ZeroDivisionError):
                anchor_price = 0.0

        if anchor_price <= 0 and initial_price > 0:
            anchor_price = float(initial_price)
            anchor_source = "live_price"

        if anchor_price <= 0:
            return runtime_config

        grid_params["lowerPrice"] = anchor_price * lower_ratio / reference
        grid_params["upperPrice"] = anchor_price * upper_ratio / reference
        grid_params["dynamicAnchor"] = False
        grid_params["_dynamicAnchorPrice"] = anchor_price
        grid_params["_dynamicAnchorSource"] = anchor_source
        runtime_config["bot_params"] = grid_params
        return runtime_config

    @staticmethod
    def _to_ratio(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, number / 100.0))

    @staticmethod
    def _code_risk_settings(trading_config: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], str]:
        config = trading_config if isinstance(trading_config, dict) else {}
        code = config.get("_strategy_cfg_from_code")
        code_config = code if isinstance(code, dict) else {}
        risk = code_config.get("risk")
        risk_config = risk if isinstance(risk, dict) else {}
        return config, risk_config, str(code_config.get("exitOwner") or "engine").strip().lower()

    @classmethod
    def _risk_ratio(cls, config: Dict[str, Any], risk: Dict[str, Any], code_key: str, config_key: str) -> float:
        if code_key in risk:
            try:
                return min(1.0, max(0.0, float(risk.get(code_key) or 0.0)))
            except (TypeError, ValueError):
                return 0.0
        return cls._to_ratio(config.get(config_key))

    def _server_side_stop_loss_signal(
        self,
        *,
        strategy_id: int,
        symbol: str,
        current_price: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int = 60,
        **_: Any,
    ):
        config, risk, exit_owner = self._code_risk_settings(trading_config)
        bot_type = str(config.get("bot_type") or "").strip().lower()
        if bot_type in {"grid", "dca"}:
            return None
        if exit_owner != "engine" or config.get("enable_server_side_stop_loss") is False:
            return None
        stop_ratio = self._risk_ratio(config, risk, "stopLossPct", "stop_loss_pct")
        price = float(current_price or 0.0)
        if stop_ratio <= 0 or price <= 0:
            return None
        candle = int(time.time()) // max(1, int(timeframe_seconds or 60)) * max(1, int(timeframe_seconds or 60))
        for position in self._get_current_positions(strategy_id, symbol):
            side = str(position.get("side") or "").strip().lower()
            entry = float(position.get("entry_price") or 0.0)
            size = abs(float(position.get("size") or 0.0))
            if side not in {"long", "short"} or entry <= 0 or size <= 0:
                continue
            adverse = (entry - price) / entry if side == "long" else (price - entry) / entry
            if adverse + 1e-12 < stop_ratio:
                continue
            return {
                "type": f"close_{side}",
                "position_size": size,
                "timestamp": candle,
                "reason": "server_stop_loss",
                "trigger_price": price,
                "matched_entry_price": entry,
            }
        return None

    def _server_side_take_profit_or_trailing_signal(
        self,
        *,
        strategy_id: int,
        symbol: str,
        current_price: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int = 60,
        **_: Any,
    ):
        config, risk, exit_owner = self._code_risk_settings(trading_config)
        if str(config.get("bot_type") or "").strip().lower() in {"grid", "dca"}:
            return None
        if exit_owner != "engine" or config.get("enable_server_side_take_profit") is False:
            return None
        price = float(current_price or 0.0)
        if price <= 0:
            return None
        take_ratio = self._risk_ratio(config, risk, "takeProfitPct", "take_profit_pct")
        trailing_data = risk.get("trailing") if isinstance(risk.get("trailing"), dict) else {}
        trailing_enabled = bool(trailing_data.get("enabled", config.get("trailing_stop_enabled", False)))
        trailing_ratio = (
            min(1.0, max(0.0, float(trailing_data.get("pct") or 0.0)))
            if "pct" in trailing_data
            else self._to_ratio(config.get("trailing_stop_pct"))
        )
        activation_ratio = (
            min(1.0, max(0.0, float(trailing_data.get("activationPct") or 0.0)))
            if "activationPct" in trailing_data
            else self._to_ratio(config.get("trailing_activation_pct"))
        )
        candle = int(time.time()) // max(1, int(timeframe_seconds or 60)) * max(1, int(timeframe_seconds or 60))
        fee_rate = self._to_ratio(config.get("commission"))
        from app.utils.risk_guard import trailing_exit_locks_net_profit

        for position in self._get_current_positions(strategy_id, symbol):
            side = str(position.get("side") or "").strip().lower()
            entry = float(position.get("entry_price") or 0.0)
            size = abs(float(position.get("size") or 0.0))
            if side not in {"long", "short"} or entry <= 0 or size <= 0:
                continue
            high = max(float(position.get("highest_price") or 0.0), entry, price)
            prior_low = float(position.get("lowest_price") or 0.0)
            low = min(value for value in (prior_low, entry, price) if value > 0)
            self._update_position(
                strategy_id=strategy_id,
                symbol=symbol,
                side=side,
                current_price=price,
                highest_price=high,
                lowest_price=low,
            )
            favorable = (price - entry) / entry if side == "long" else (entry - price) / entry
            if take_ratio > 0 and favorable + 1e-12 >= take_ratio:
                return {
                    "type": f"close_{side}",
                    "position_size": size,
                    "timestamp": candle,
                    "reason": "server_take_profit",
                    "trigger_price": price,
                    "matched_entry_price": entry,
                }
            if not trailing_enabled or trailing_ratio <= 0:
                continue
            peak_move = (high - entry) / entry if side == "long" else (entry - low) / entry
            callback = (high - price) / high if side == "long" else (price - low) / low
            if peak_move + 1e-12 < activation_ratio or callback + 1e-12 < trailing_ratio:
                continue
            if not trailing_exit_locks_net_profit(
                side,
                entry_price=entry,
                exit_price=price,
                fee_rate=fee_rate,
            ):
                continue
            return {
                "type": f"close_{side}",
                "position_size": size,
                "timestamp": candle,
                "reason": "server_trailing_stop",
                "trigger_price": price,
                "matched_entry_price": entry,
            }
        return None

    @staticmethod
    def _update_position(
        *,
        strategy_id: int,
        symbol: str,
        side: str,
        current_price: float,
        highest_price: float = 0.0,
        lowest_price: float = 0.0,
    ) -> bool:
        from app.services.live_trading.records import patch_position_markers

        return patch_position_markers(
            strategy_id=int(strategy_id),
            symbol=str(symbol),
            side=str(side),
            current_price=float(current_price),
            highest_price=float(highest_price or 0.0),
            lowest_price=float(lowest_price or 0.0),
        )

    @staticmethod
    def _ratio(value: Any, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(default)
        return number / 100.0 if number > 1 else max(0.0, number)

    def _grid_bot_risk_exits(
        self,
        strategy_id: int,
        symbol: str,
        current_price: float,
        trading_config: Dict[str, Any],
        timeframe_seconds: int,
        initial_capital: Optional[float] = None,
        risk_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        config = trading_config if isinstance(trading_config, dict) else {}
        if str(config.get("bot_type") or "").strip().lower() not in {"grid", "dca"}:
            return []
        positions = self._get_current_positions(strategy_id, symbol)
        long_open = any(
            str(row.get("side") or "").lower() == "long" and float(row.get("size") or 0) > 0
            for row in positions
        )
        short_open = any(
            str(row.get("side") or "").lower() == "short" and float(row.get("size") or 0) > 0
            for row in positions
        )
        if not long_open and not short_open:
            return []
        candle = int(time.time() // max(1, int(timeframe_seconds or 60))) * max(
            1, int(timeframe_seconds or 60)
        )

        def close_all(reason: str, **extra: Any) -> List[Dict[str, Any]]:
            result: List[Dict[str, Any]] = []
            if long_open:
                result.append({"type": "close_long", "position_size": 0, "timestamp": candle, "reason": reason, **extra})
            if short_open:
                result.append({"type": "close_short", "position_size": 0, "timestamp": candle, "reason": reason, **extra})
            return result

        params = config.get("bot_params") if isinstance(config.get("bot_params"), dict) else {}
        upper = float(params.get("upperPrice") or params.get("upper_price") or 0)
        lower = float(params.get("lowerPrice") or params.get("lower_price") or 0)
        boundary_action = str(
            params.get("boundaryAction") or params.get("boundary_action") or "pause"
        ).strip().lower()
        buffer_ratio = self._ratio(config.get("grid_oob_buffer_pct"), 0.05)
        if (
            boundary_action == "stop_loss"
            and upper > lower > 0
            and current_price > 0
            and buffer_ratio > 0
        ):
            if current_price >= upper * (1 + buffer_ratio):
                return close_all("grid_out_of_bounds_up", oob_threshold=upper * (1 + buffer_ratio))
            if current_price <= lower * (1 - buffer_ratio):
                return close_all("grid_out_of_bounds_down", oob_threshold=lower * (1 - buffer_ratio))
        capital = float(initial_capital or config.get("initial_capital") or 0)
        executor_config = (
            config.get("executor_config")
            if isinstance(config.get("executor_config"), dict)
            else {}
        )
        stop_ratio = self._ratio(
            config.get("equity_stop_loss_pct", executor_config.get("equity_stop_loss_pct", config.get("stop_loss_pct"))),
            0,
        )
        take_ratio = self._ratio(
            config.get("equity_take_profit_pct", executor_config.get("equity_take_profit_pct", config.get("take_profit_pct"))),
            0,
        )
        trailing_enabled = bool(
            config.get(
                "equity_trailing_enabled",
                executor_config.get("equity_trailing_enabled", False),
            )
        )
        trailing_activation = self._ratio(
            config.get(
                "equity_trailing_activation_pct",
                executor_config.get("equity_trailing_activation_pct"),
            ),
            0,
        )
        trailing_callback = self._ratio(
            config.get(
                "equity_trailing_callback_pct",
                executor_config.get("equity_trailing_callback_pct"),
            ),
            0,
        )
        if capital > 0 and (stop_ratio > 0 or take_ratio > 0 or trailing_enabled):
            equity = self._calculate_current_equity(
                strategy_id,
                capital,
                current_positions=positions,
                current_price=current_price,
                symbol=symbol,
            )
            change = (equity - capital) / capital
            state = risk_state if isinstance(risk_state, dict) else {}
            peak_return = max(float(state.get("peak_return") or 0.0), change)
            state["peak_return"] = peak_return
            if trailing_enabled and change >= trailing_activation:
                state["trailing_armed"] = True
            if stop_ratio > 0 and change <= -stop_ratio:
                return close_all("grid_equity_stop_loss", equity=equity, equity_pct=change)
            if take_ratio > 0 and change >= take_ratio:
                return close_all("grid_equity_take_profit", equity=equity, equity_pct=change)
            if (
                trailing_enabled
                and bool(state.get("trailing_armed"))
                and trailing_callback > 0
                and peak_return - change >= trailing_callback
            ):
                return close_all(
                    "grid_equity_trailing_stop",
                    equity=equity,
                    equity_pct=change,
                    equity_peak_pct=peak_return,
                )
        return []

    def _calculate_current_equity(
        self,
        strategy_id: int,
        initial_capital: float,
        current_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None,
        symbol: str = "",
        current_prices: Optional[Mapping[str, float]] = None,
    ) -> float:
        realized = 0.0
        strategy = self._load_strategy(strategy_id) or {}
        is_virtual = str(strategy.get("execution_mode") or "").strip().lower() == "signal"
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                if is_virtual:
                    cur.execute(
                        "SELECT COALESCE(realized_pnl, 0) AS realized_pnl FROM qd_strategy_virtual_accounts WHERE strategy_id = %s",
                        (strategy_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                          COALESCE((
                            SELECT SUM(COALESCE(profit, 0) - COALESCE(commission_quote, commission, 0))
                            FROM qd_strategy_trades WHERE strategy_id = %s
                          ), 0)
                          + COALESCE((
                            SELECT SUM(COALESCE(amount, 0))
                            FROM qd_strategy_funding_fees WHERE strategy_id = %s
                          ), 0)
                          + COALESCE((
                            SELECT SUM(COALESCE(amount, 0))
                            FROM qd_strategy_broker_activities WHERE strategy_id = %s
                          ), 0) AS realized_pnl
                        """,
                        (strategy_id, strategy_id, strategy_id),
                    )
                realized = float((cur.fetchone() or {}).get("realized_pnl") or 0)
                cur.close()
        except Exception as exc:
            logger.warning("Failed to calculate realized PnL for strategy %s: %s", strategy_id, exc)
        unrealized = 0.0
        def normalized_symbol(value: Any) -> str:
            text = str(value or "").strip()
            text = text.rpartition("::")[0] if "::" in text else text
            text = text.split(":", 1)[-1] if ":" in text else text
            return text.split("@", 1)[0].upper()

        base_symbol = normalized_symbol(symbol)
        marks = {
            normalized_symbol(key): float(value or 0.0)
            for key, value in (current_prices or {}).items()
            if float(value or 0.0) > 0
        }
        for row in current_positions or []:
            side = str(row.get("side") or "").lower()
            size = float(row.get("size") or 0)
            entry = float(row.get("entry_price") or 0)
            mark = float(row.get("current_price") or 0)
            row_symbol = normalized_symbol(row.get("symbol"))
            if marks.get(row_symbol, 0.0) > 0:
                mark = marks[row_symbol]
            elif current_price and row_symbol == base_symbol:
                mark = float(current_price)
            if size <= 0 or entry <= 0 or mark <= 0:
                continue
            unrealized += (mark - entry) * size if side == "long" else (entry - mark) * size
        return max(0.0, float(initial_capital or 0) + realized + unrealized)

    @staticmethod
    def _target_amount(
        intent: OrderIntent,
        current: float,
        capital: float,
        price: float,
        *,
        leverage: float = 1.0,
        market_type: str = "spot",
    ) -> float:
        notional_multiplier = (
            max(1.0, float(leverage or 1.0))
            if str(market_type or "").lower() != "spot"
            else 1.0
        )
        if intent.kind == "quantity":
            return current + float(intent.value)
        if intent.kind == "value":
            return current + float(intent.value) * notional_multiplier / price
        if intent.kind == "target_quantity":
            return float(intent.value)
        if intent.kind == "target_value":
            return float(intent.value) * notional_multiplier / price
        if intent.kind == "target_percent":
            return capital * float(intent.value) * notional_multiplier / price
        raise RuntimeError(f"strategyV2.orderKindUnsupported:{intent.kind}")

    @staticmethod
    def _direction_constrained_target(
        target: float,
        *,
        direction_mode: str = "",
    ) -> float:
        from app.services.strategy_direction import normalize_direction_mode

        mode = normalize_direction_mode(direction_mode)
        value = float(target or 0.0)
        if mode == "long_only" and value < 0:
            return 0.0
        if mode == "short_only" and value > 0:
            return 0.0
        return value

    @staticmethod
    def _order_plan(current: float, target: float) -> list[tuple[str, float]]:
        epsilon = 1e-12
        if abs(target - current) <= epsilon:
            return []
        if current > epsilon and target < -epsilon:
            return [("close_long", current), ("open_short", abs(target))]
        if current < -epsilon and target > epsilon:
            return [("close_short", abs(current)), ("open_long", target)]
        if current > epsilon:
            if target <= epsilon:
                return [("close_long", current)]
            delta = target - current
            return [("add_long" if delta > 0 else "reduce_long", abs(delta))]
        if current < -epsilon:
            if target >= -epsilon:
                return [("close_short", abs(current))]
            delta = target - current
            return [("add_short" if delta < 0 else "reduce_short", abs(delta))]
        return [("open_long" if target > 0 else "open_short", abs(target))]

    def _load_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT * FROM qd_strategies_trading WHERE id = %s", (int(strategy_id),))
            row = cur.fetchone()
            cur.close()
        if not isinstance(row, dict):
            return None
        for key in ("trading_config", "exchange_config", "notification_config"):
            row[key] = _json_object(row.get(key))
        return row

    @staticmethod
    def _load_schedule_timezone(user_id: int) -> str:
        fallback = str(os.getenv("TZ") or "UTC").strip() or "UTC"
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT COALESCE(timezone, '') AS timezone FROM qd_users WHERE id = %s",
                    (int(user_id),),
                )
                row = cur.fetchone() or {}
                cur.close()
            return str(row.get("timezone") or fallback).strip() or fallback
        except Exception:
            return fallback

    @staticmethod
    def _intent_signal_timestamp(intent: OrderIntent, fallback: Any) -> int:
        value = pd.Timestamp(intent.signal_time if intent.signal_time is not None else fallback)
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        return int(value.timestamp())

    @staticmethod
    def _load_source(strategy: dict[str, Any]) -> tuple[int, str]:
        trading_config = _json_object(strategy.get("trading_config"))
        source_id = int(trading_config.get("script_source_id") or 0)
        if source_id <= 0:
            raise RuntimeError("strategyV2.sourceRequired")
        source_version_id = int(
            strategy.get("source_version_id")
            or trading_config.get("script_source_version_id")
            or 0
        )
        if source_version_id <= 0:
            raise RuntimeError("strategyV2.sourceVersionRequired")
        source = get_script_source_service().get_version(
            source_version_id,
            user_id=int(strategy.get("user_id") or 0),
        )
        if not source or int(source.get("source_id") or 0) != source_id:
            raise RuntimeError("strategyV2.sourceVersionNotFound")
        code = str((source or {}).get("code") or "").strip()
        if not code:
            raise RuntimeError("strategyV2.codeRequired")
        from app.services.strategy_runtime.bot_type import resolve_bot_type
        from app.services.strategy_runtime.robot_v2 import migrate_legacy_robot_v2_source

        bot_type = resolve_bot_type(
            strategy,
            trading_config,
            source_code=code,
        )
        migrated = migrate_legacy_robot_v2_source(code, bot_type) if bot_type else code
        if migrated != code:
            # Upgrade generated legacy allocation units at the execution
            # boundary. The source record stays immutable; a future editor
            # save can persist the newest generated template explicitly.
            append_strategy_log(
                int(strategy.get("id") or 0),
                "warning",
                f"Legacy {bot_type} robot allocation contract upgraded for this run",
            )
            code = migrated
        return source_version_id, code

    def _is_strategy_running(
        self,
        strategy_id: int,
        stop_event: threading.Event | None = None,
    ) -> bool:
        if stop_event is not None and stop_event.is_set():
            return False
        if self.runtime_guard and not self.runtime_guard(strategy_id):
            self._last_exit_reason[strategy_id] = "strategyRuntime.leaseLost"
            return False
        with self.lock:
            if strategy_id not in self.running_strategies:
                return False
        return True

    @staticmethod
    def _mark_stopped(strategy_id: int) -> None:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "UPDATE qd_strategies_trading SET status = 'stopped', updated_at = NOW() WHERE id = %s",
                    (strategy_id,),
                )
                db.commit()
                cur.close()
        except Exception:
            logger.exception("Failed to persist stopped status for strategy %s", strategy_id)

    def _get_current_positions(self, strategy_id: int, symbol: str) -> list[dict[str, Any]]:
        strategy = self._load_strategy(strategy_id) or {}
        if str(strategy.get("execution_mode") or "").strip().lower() == "signal":
            from app.services.virtual_trading import list_virtual_positions

            return list_virtual_positions(strategy_id, symbol)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, symbol, side, size, entry_price, current_price,
                       highest_price, lowest_price, updated_at
                FROM qd_strategy_positions
                WHERE strategy_id = %s AND (%s::text IS NULL OR split_part(symbol, ':', 1) = split_part(%s, ':', 1))
                """,
                (strategy_id, symbol, symbol),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [dict(row) for row in rows]

    def _positions_by_symbol(
        self,
        strategy_id: int,
        candidates: list[dict[str, Any]],
        *,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        return positions_by_symbol(self, strategy_id, candidates, strategy or self._load_strategy(strategy_id))

    @staticmethod
    def _live_prices(candidates: list[dict[str, Any]]) -> dict[str, float]:
        prices: dict[str, float] = {}
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for member in candidates:
            identity = (
                str(member.get("market") or ""),
                str(member.get("exchange_id") or ""),
                str(member.get("market_type") or ""),
            )
            groups.setdefault(identity, []).append(member)
        for (market, exchange_id, market_type), members in groups.items():
            try:
                symbols = [str(member.get("symbol") or "") for member in members]
                quotes = DataSourceFactory.get_tickers(
                    market,
                    symbols,
                    exchange_id=exchange_id or None,
                    market_type=market_type or None,
                )
                for member in members:
                    symbol = str(member.get("symbol") or "")
                    ticker = quotes.get(symbol) or quotes.get(symbol.upper()) or {}
                    price = float(ticker.get("last") or ticker.get("close") or 0)
                    if price > 0:
                        prices[str(member.get("key") or "")] = price
            except Exception as exc:
                logger.warning(
                    "Batch price fetch failed for %s/%s (%s symbol(s)): %s",
                    market,
                    exchange_id or "default",
                    len(members),
                    exc,
                )
        return prices

    @classmethod
    def _execution_account_prices(
        cls,
        candidates: list[dict[str, Any]],
        exchange_config: dict[str, Any],
        client_holder: dict[str, Any],
    ) -> dict[str, float]:
        standard_candidates = [
            member
            for member in candidates
            if str(
                member.get("api_family") or member.get("market_type") or "spot"
            ).strip().lower() in {"spot", "swap"}
        ]
        prices = cls._live_prices(standard_candidates) if standard_candidates else {}
        from app.services.live_trading.factory import create_client
        from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id

        exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
        for member in candidates:
            if str(member.get("market") or "") != "Crypto":
                continue
            symbol = str(member.get("symbol") or "")
            market_type = str(member.get("market_type") or "swap").strip().lower()
            api_family = str(member.get("api_family") or market_type).strip().lower()
            instrument_id = str(member.get("instrument_id") or "").strip()
            client_key = f"{market_type}:{api_family}:{instrument_id}"
            try:
                client = client_holder.get(client_key)
                if client is None:
                    client_config = dict(exchange_config)
                    client_config["api_family"] = api_family
                    if instrument_id:
                        client_config["instrument_id"] = instrument_id
                    product_meta = member.get("product_meta")
                    if isinstance(product_meta, dict) and product_meta:
                        client_config["instrument_product_meta"] = dict(product_meta)
                    client = create_client(client_config, market_type=market_type)
                    client_holder[client_key] = client
                price = 0.0
                if hasattr(client, "get_mark_price"):
                    price = float(client.get_mark_price(symbol=symbol) or 0.0)
                elif hasattr(client, "get_ticker"):
                    if exchange_id == "okx":
                        inst_id = (
                            to_okx_spot_inst_id(symbol)
                            if market_type == "spot"
                            else to_okx_swap_inst_id(symbol)
                        )
                        ticker = client.get_ticker(inst_id=inst_id)
                    else:
                        ticker = client.get_ticker(symbol=symbol)
                    if isinstance(ticker, dict):
                        price = float(
                            ticker.get("last")
                            or ticker.get("lastPrice")
                            or ticker.get("lastPr")
                            or ticker.get("lastPx")
                            or ticker.get("markPrice")
                            or ticker.get("price")
                            or ticker.get("close")
                            or 0.0
                        )
                if price > 0:
                    prices[str(member.get("key") or "")] = price
            except Exception as exc:
                logger.warning(
                    "Execution-account price fetch failed for %s/%s/%s: %s",
                    exchange_id,
                    market_type,
                    instrument_id or symbol,
                    exc,
                )
        return prices

    @staticmethod
    def _heartbeat(
        strategy_id: int,
        run_id: int,
        primary: dict[str, Any],
        prices: dict[str, float],
        pending_count: int,
        *,
        loop_latency_ms: int = 0,
        status: str = "healthy",
        last_error: str = "",
        price_source: str = "",
        price_age_ms: int = 0,
        trigger_mode: str = "",
        fill_transport: str = "",
    ) -> None:
        record_runtime_heartbeat(
            strategy_id=strategy_id,
            strategy_run_id=run_id,
            symbol=str(primary.get("symbol") or ""),
            price=float(prices.get(str(primary.get("key") or ""), 0)),
            pending_signal_count=pending_count,
            loop_latency_ms=loop_latency_ms,
            status=status,
            last_error=last_error,
            price_source=price_source,
            price_age_ms=price_age_ms,
            trigger_mode=trigger_mode,
            fill_transport=fill_transport,
        )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _env_enabled(name: str, *, default: bool) -> bool:
    fallback = "1" if default else "0"
    return str(os.getenv(name, fallback)).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _signal_cycle_due(
    *,
    cycle_started: float,
    next_signal_poll: float,
    bar_event_triggered: bool,
) -> bool:
    return bool(bar_event_triggered or cycle_started >= next_signal_poll)


_TERMINAL_RUNTIME_ORDER_STATUSES = {
    "cancelled",
    "canceled",
    "closed",
    "expired",
    "failed",
    "filled",
    "rejected",
}


def _runtime_wait_seconds(
    *,
    risk_tick: float,
    frequency: str,
    candidates: list[dict[str, Any]],
    bot_type: str,
    positions: Mapping[str, Mapping[str, Any]],
    order_statuses: Mapping[str, Mapping[str, Any]],
    protected: set[str],
    pending_count: int,
    market_ready: bool,
    event_driven: bool = False,
) -> float:
    """Park idle bar runtimes without weakening active risk checks."""
    baseline = max(0.25, float(risk_tick or 0.0))
    if str(os.getenv("BAR_IDLE_SCHEDULER_ENABLED", "1")).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return baseline
    if not market_ready or protected or int(pending_count or 0) > 0:
        return baseline
    if str(bot_type or "").strip().lower() in {"grid", "martingale", "layered_martingale"}:
        return baseline
    if not candidates:
        return baseline
    if any(abs(float(item.get("amount") or 0.0)) > 1e-12 for item in positions.values()):
        return baseline
    if any(
        str(item.get("status") or "").strip().lower() not in _TERMINAL_RUNTIME_ORDER_STATUSES
        for item in order_statuses.values()
    ):
        return baseline
    if event_driven:
        try:
            return max(
                baseline,
                min(3600.0, float(os.getenv("BAR_EVENT_FALLBACK_WAKE_SEC", "300"))),
            )
        except (TypeError, ValueError):
            return max(baseline, 300.0)
    if any(str(item.get("market") or "").strip().lower() != "crypto" for item in candidates):
        return baseline
    try:
        idle_cap = max(
            baseline,
            min(60.0, float(os.getenv("BAR_IDLE_WAKE_INTERVAL_SEC", "60"))),
        )
    except (TypeError, ValueError):
        idle_cap = max(baseline, 60.0)
    return max(
        baseline,
        min(idle_cap, seconds_until_next_completed_bar(frequency)),
    )


def _member_key(member: dict[str, Any]) -> str:
    market = str(member.get("market") or "")
    symbol = str(member.get("symbol") or "")
    exchange_id = str(member.get("exchange_id") or "")
    market_type = str(member.get("market_type") or "")
    suffix = f"@{exchange_id}" if exchange_id else ""
    if suffix and market_type:
        suffix += f":{market_type}"
    elif market_type:
        suffix = f"@{market_type}"
    return f"{market}:{symbol}{suffix}"


def _apply_market_data_defaults(candidates: list[dict[str, Any]]) -> None:
    """Resolve omitted crypto venues without changing explicit strategy venues."""
    default_exchange = ""
    deduplicated: dict[str, dict[str, Any]] = {}
    for member in candidates:
        if str(member.get("market") or "").strip() == "Crypto":
            exchange_id = normalize_exchange_id(member.get("exchange_id"))
            if not exchange_id:
                if not default_exchange:
                    default_exchange = default_crypto_exchange_id()
                exchange_id = default_exchange
            member["exchange_id"] = exchange_id
            member["market_type"] = normalize_market_type(
                member.get("market_type"),
                market="Crypto",
            )
            member["key"] = _member_key(member)
        key = str(member.get("key") or _member_key(member))
        deduplicated[key] = member
    candidates[:] = list(deduplicated.values())


def _latest_frame_timestamp(
    frames: pd.DataFrame | dict[str, pd.DataFrame] | None,
) -> pd.Timestamp | None:
    """Return the newest candle timestamp in a frame or instrument panel.

    Live Strategy V2 sessions pass the driving-frequency panel as a mapping of
    instrument keys to data frames.  Accepting a single frame as well keeps the
    helper useful for focused callers and tests without confusing the panel
    itself with a pandas object.
    """
    candidates = frames.values() if isinstance(frames, dict) else (frames,)
    latest: pd.Timestamp | None = None
    for frame in candidates:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        try:
            timestamp = pd.Timestamp(frame.index[-1])
            if pd.isna(timestamp):
                continue
            timestamp = (
                timestamp.tz_localize("UTC")
                if timestamp.tzinfo is None
                else timestamp.tz_convert("UTC")
            )
        except (TypeError, ValueError, IndexError):
            continue
        if latest is None or timestamp > latest:
            latest = timestamp
    return latest
