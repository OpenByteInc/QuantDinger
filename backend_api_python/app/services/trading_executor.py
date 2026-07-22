"""Strategy API V2 live execution supervisor."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.data_sources import DataSourceFactory
from app.services.fundamental_data import get_fundamental_data_service
from app.services.script_source import get_script_source_service
from app.services.strategy_runtime.health import record_runtime_heartbeat
from app.services.strategy_runtime.identity import ensure_strategy_run, finish_strategy_run
from app.services.strategy_runtime.state import RuntimeStateStore
from app.services.strategy_v2 import (
    OrderIntent,
    StrategyV2BacktestService,
    StrategyV2LiveSession,
    compile_strategy_v2,
)
from app.services.strategy_v2.live_execution import LiveOrderRequest, StrategyV2OrderGateway
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log


logger = get_logger(__name__)

MIN_LIVE_ORDER_NOTIONAL = 1.0


def live_history_days(frequency: str, warmup_bars: int) -> int:
    """Return a frequency-aware live lookback with a small startup buffer."""
    value = str(frequency or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)\s*(m|h|d|w)", value)
    if not match:
        return 30
    count = max(1, int(match.group(1)))
    unit_seconds = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    bars = max(10, max(1, int(warmup_bars or 0)) * 3)
    return max(1, int(math.ceil(count * unit_seconds * bars / 86400)))


class TradingExecutor:
    """Own worker threads and run the single supported strategy runtime."""

    def __init__(self) -> None:
        self.running_strategies: dict[int, threading.Thread] = {}
        self.lock = threading.Lock()
        self.max_threads = max(1, int(os.getenv("STRATEGY_MAX_THREADS", "64")))
        self.order_gateway = StrategyV2OrderGateway()
        self._last_start_failure = ""
        self._last_exit_reason: dict[int, str] = {}

    def start_strategy(self, strategy_id: int) -> bool:
        strategy_id = int(strategy_id)
        with self.lock:
            self._discard_dead_threads()
            self._last_start_failure = ""
            if strategy_id in self.running_strategies:
                self._last_start_failure = "Strategy is already running."
                return False
            if len(self.running_strategies) >= self.max_threads:
                self._last_start_failure = f"Thread limit reached ({self.max_threads})."
                return False
            try:
                self._preflight_live_strategy(strategy_id)
            except Exception as exc:
                self._last_start_failure = str(exc or "strategyV2.livePreflightFailed")
                logger.warning("Strategy %s live preflight rejected: %s", strategy_id, exc)
                return False
            thread = threading.Thread(
                target=self._run_strategy_loop,
                args=(strategy_id,),
                name=f"strategy-{strategy_id}",
                daemon=True,
            )
            self.running_strategies[strategy_id] = thread
            try:
                thread.start()
            except Exception as exc:
                self.running_strategies.pop(strategy_id, None)
                self._last_start_failure = f"Failed to start strategy thread: {exc}"
                logger.exception("Failed to start strategy %s", strategy_id)
                return False
        append_strategy_log(strategy_id, "info", "Strategy execution thread started")
        return True

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
        bot_type = str(trading_config.get("bot_type") or "").strip().lower()
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

        from app.services.exchange_execution import resolve_exchange_config
        from app.services.grid.exchange_requirements import detect_hedge_position_mode
        from app.services.live_trading.factory import create_client

        exchange_config = resolve_exchange_config(
            _json_object(strategy.get("exchange_config")),
            user_id=user_id,
        )
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
        if is_hedge is not True:
            if is_hedge is None:
                raise RuntimeError(f"strategyV2.hedgeModeUnknown:{label}")
        conflict = find_live_strategy_conflict(
            strategy,
            user_id,
            allow_opposite_leg=is_hedge is True and not owns_both_legs,
        )
        if conflict:
            raise RuntimeError(live_conflict_message(conflict))

    def wait_strategy_running(self, strategy_id: int, timeout: float = 3.0) -> Tuple[bool, str]:
        strategy_id = int(strategy_id)
        deadline = time.monotonic() + max(0.5, float(timeout))
        while time.monotonic() < deadline:
            with self.lock:
                thread = self.running_strategies.get(strategy_id)
                alive = bool(thread and thread.is_alive())
            if not alive:
                return False, self._last_exit_reason.pop(strategy_id, "") or "Strategy runtime exited during startup."
            time.sleep(0.1)
        return True, ""

    def is_running(self, strategy_id: int) -> bool:
        with self.lock:
            self._discard_dead_threads()
            thread = self.running_strategies.get(int(strategy_id))
            return bool(thread and thread.is_alive())

    def stop_strategy(self, strategy_id: int, *, persist_status: bool = True) -> bool:
        strategy_id = int(strategy_id)
        try:
            if persist_status:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        "UPDATE qd_strategies_trading SET status = 'stopped', updated_at = NOW() WHERE id = %s",
                        (strategy_id,),
                    )
                    db.commit()
                    cur.close()
            with self.lock:
                self.running_strategies.pop(strategy_id, None)
            append_strategy_log(strategy_id, "info", "Strategy stop requested")
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

    def _discard_dead_threads(self) -> None:
        for strategy_id, thread in list(self.running_strategies.items()):
            if not thread.is_alive():
                self.running_strategies.pop(strategy_id, None)

    def _run_strategy_loop(self, strategy_id: int) -> None:
        current = threading.current_thread()
        run_id = 0
        exit_reason = "strategy stopped"
        try:
            strategy = self._load_strategy(strategy_id)
            if not strategy:
                raise RuntimeError("strategyV2.strategyNotFound")
            source_id, code = self._load_source(strategy)
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
                start_date=now - timedelta(days=7),
                end_date=now,
            )
            account_exchange = str(
                exchange_config.get("exchange_id") or exchange_config.get("exchangeId") or ""
            ).strip().lower()
            if execution_mode == "live" and account_exchange:
                for member in candidates:
                    if member.get("market") == "Crypto":
                        member["exchange_id"] = account_exchange
                        member["key"] = _member_key(member)

            frequency = program.manifest.primary_frequency
            history_days = live_history_days(
                frequency,
                int(program.manifest.warmup_bars or 0),
            )

            def fetch_frames() -> dict[str, pd.DataFrame]:
                end = datetime.now(timezone.utc)
                frames, skipped = service.fetch_frames(
                    candidates,
                    frequency,
                    end - timedelta(days=history_days),
                    end,
                )
                if skipped:
                    append_strategy_log(
                        strategy_id,
                        "warning",
                        f"Skipped {len(skipped)} instrument(s) without usable market data",
                    )
                if program.manifest.fundamental_dependencies:
                    frames = get_fundamental_data_service().enrich_panel(frames, candidates)
                    service.validate_fundamental_dependencies(frames, program.manifest)
                if not frames:
                    raise RuntimeError("strategyV2.noMarketData")
                return frames

            def resolve_universe(reference: str, timestamp: pd.Timestamp) -> list[str]:
                del reference
                if not universe_id:
                    return [str(item["key"]) for item in candidates]
                members = service.universe_service.resolve_members(
                    user_id,
                    universe_id,
                    as_of=timestamp.date(),
                )
                return [_member_key(item) for item in members]

            frames = fetch_frames()
            runtime_price_client: Dict[str, Any] = {}

            def runtime_prices() -> dict[str, float]:
                if execution_mode != "live":
                    return self._live_prices(candidates)
                return self._execution_account_prices(
                    candidates,
                    exchange_config,
                    runtime_price_client,
                )

            if execution_mode == "live":
                frames = self._align_latest_frame_prices(frames, runtime_prices())
            initial_capital = float(
                strategy.get("initial_capital") or trading_config.get("initial_capital") or 0
            )
            if initial_capital <= 0:
                raise RuntimeError("strategyV2.invalidInitialCapital")
            session = StrategyV2LiveSession(
                code=code,
                frames=frames,
                initial_capital=initial_capital,
                params=dict(trading_config.get("params") or {}),
                universe_resolver=resolve_universe,
                schedule_timezone=self._load_schedule_timezone(user_id),
            )
            primary = candidates[0]
            runtime_run = ensure_strategy_run(
                strategy_id=strategy_id,
                user_id=user_id,
                code=code,
                parameter_snapshot=trading_config,
                source_version_id=str(source_id),
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
            self._heartbeat(strategy_id, run_id, primary, last_prices, 0)
            bot_type = str(
                strategy.get("bot_type") or trading_config.get("bot_type") or ""
            ).strip().lower()
            if execution_mode == "live" and bot_type == "grid":
                self._run_grid_resting_loop(
                    strategy_id=strategy_id,
                    strategy_run_id=run_id,
                    current_thread=current,
                    strategy_name=str(strategy.get("strategy_name") or f"strategy_{strategy_id}"),
                    primary=primary,
                    candidates=candidates,
                    frames=frames,
                    trading_config=trading_config,
                    exchange_config=exchange_config,
                    initial_capital=initial_capital,
                    notification_config=_json_object(strategy.get("notification_config")),
                )
                exit_reason = "grid strategy stopped"
                return
            state_store = RuntimeStateStore(
                strategy_id=strategy_id,
                strategy_run_id=run_id,
                state_key="protection",
            )
            session.restore_protection_snapshot(state_store.load())

            signal_poll = max(1.0, min(30.0, float(trading_config.get("data_poll_seconds") or 5)))
            risk_tick = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
            next_signal_poll = 0.0
            consecutive_errors = 0
            strategy_name = str(strategy.get("strategy_name") or f"strategy_{strategy_id}")
            notification_config = _json_object(strategy.get("notification_config"))
            leverage = max(1.0, float(trading_config.get("leverage") or strategy.get("leverage") or 1))
            append_strategy_log(
                strategy_id,
                "info",
                f"Strategy runtime ready: instruments={len(candidates)}, timeframe={frequency}, mode={execution_mode}",
            )

            while self._is_strategy_running(strategy_id, current):
                cycle_started = time.monotonic()
                try:
                    positions = self._positions_by_symbol(strategy_id, candidates)
                    session.synchronize_positions(positions)
                    last_prices.update(runtime_prices())
                    protection_intents = session.evaluate_protections(
                        last_prices,
                        timestamp=pd.Timestamp.now(tz="UTC"),
                    )
                    for intent in protection_intents:
                        submitted = self._execute_strategy_v2_intent(
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            intent=intent,
                            frames=frames,
                            candidates=candidates,
                            initial_capital=initial_capital,
                            leverage=leverage,
                            execution_mode=execution_mode,
                            notification_config=notification_config,
                            trading_config=trading_config,
                            exchange_config=exchange_config,
                            signal_ts=int(time.time()),
                            strategy_run_id=run_id,
                            current_price_override=last_prices.get(str(intent.symbol)),
                        )
                        if not submitted:
                            session.release_protection_exit(intent.symbol)

                    # Suppress normal strategy orders for a symbol until its
                    # asynchronous protection close has completed and the
                    # strategy position snapshot becomes flat.
                    protected = session.pending_protection_exit_symbols()

                    pending_count = len(protection_intents)
                    if cycle_started >= next_signal_poll:
                        frames = fetch_frames()
                        if execution_mode == "live":
                            frames = self._align_latest_frame_prices(frames, runtime_prices())
                        intents, messages, timestamp = session.process(frames)
                        intents = [intent for intent in intents if str(intent.symbol) not in protected]
                        pending_count += len(intents)
                        for message in messages:
                            append_strategy_log(strategy_id, "info", message)
                        for intent in intents:
                            self._execute_strategy_v2_intent(
                                strategy_id=strategy_id,
                                strategy_name=strategy_name,
                                intent=intent,
                                frames=frames,
                                candidates=candidates,
                                initial_capital=initial_capital,
                                leverage=leverage,
                                execution_mode=execution_mode,
                                notification_config=notification_config,
                                trading_config=trading_config,
                                exchange_config=exchange_config,
                                signal_ts=self._intent_signal_timestamp(intent, timestamp),
                                strategy_run_id=run_id,
                            )
                        next_signal_poll = cycle_started + signal_poll
                    state_store.save(session.protection_snapshot())
                    self._heartbeat(
                        strategy_id,
                        run_id,
                        primary,
                        last_prices,
                        pending_count,
                        loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                    )
                    consecutive_errors = 0
                except Exception as exc:
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
                remaining = risk_tick - (time.monotonic() - cycle_started)
                if remaining > 0:
                    time.sleep(remaining)
        except Exception as exc:
            exit_reason = str(exc)
            self._last_exit_reason[strategy_id] = exit_reason
            logger.exception("Strategy %s stopped after runtime failure", strategy_id)
            append_strategy_log(strategy_id, "error", exit_reason)
            self._mark_stopped(strategy_id)
        finally:
            if run_id > 0:
                finish_strategy_run(run_id, reason=exit_reason)
            with self.lock:
                if self.running_strategies.get(strategy_id) is current:
                    self.running_strategies.pop(strategy_id, None)

    def _execute_strategy_v2_intent(
        self,
        *,
        strategy_id: int,
        strategy_name: str,
        intent: OrderIntent,
        frames: Dict[str, pd.DataFrame],
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
        target_amount = self._target_amount(
            intent,
            current_amount,
            initial_capital,
            price,
            leverage=leverage,
            market_type=market_type,
        )
        if market_type == "spot" and target_amount < -1e-12:
            raise RuntimeError("strategyV2.spotShortUnsupported")

        closes_position = abs(target_amount) <= 1e-12 and abs(current_amount) > 1e-12
        if abs(target_amount - current_amount) * price < MIN_LIVE_ORDER_NOTIONAL and not closes_position:
            return False

        requests = self._order_plan(current_amount, target_amount)
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
                signal_ts=signal_ts,
                strategy_run_id=strategy_run_id,
                price_exchange_id=str(member.get("exchange_id") or ""),
            )) or submitted
        return submitted

    def _execute_signal(self, **values: Any) -> bool:
        strategy_id = int(values["strategy_id"])
        strategy = self._load_strategy(strategy_id) or {}
        if str(values.get("execution_mode") or "signal").strip().lower() == "live":
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
        quantity = float(values.get("script_base_qty") or 0)
        reference_price = float(values.get("current_price") or 0)
        initial_capital = float(values.get("initial_capital") or 0)
        leverage = float(values.get("leverage") or 1)
        nominal_capacity = initial_capital * max(1.0, leverage)
        entry_pct = ((quantity * reference_price) / nominal_capacity * 100.0) if nominal_capacity > 0 else 0.0
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
            reason=str(values.get("signal_reason") or ""),
            notification_config=dict(values.get("notification_config") or {}),
            order_type=str(values.get("order_type") or "market"),
            execution_algo=str(values.get("execution_algo") or "market"),
            limit_price=float(values.get("limit_price") or 0.0),
            maker_wait_sec=float(values.get("maker_wait_sec") or 0.0),
            maker_offset_bps=float(values.get("maker_offset_bps") or 0.0),
            protection=dict(values.get("protection") or {}),
            sizing={
                "initial_capital": initial_capital,
                "entry_pct": entry_pct,
                "leverage": leverage,
                "source": "strategy_v2",
            },
        )
        pending_id = self.order_gateway.submit(request)
        if pending_id:
            append_strategy_log(
                strategy_id,
                "trade",
                f"Order queued: {request.action} {request.symbol} quantity={request.quantity:.12f}",
            )
        return bool(pending_id)

    def _run_grid_resting_loop(
        self,
        *,
        strategy_id: int,
        strategy_run_id: int,
        current_thread: threading.Thread,
        strategy_name: str,
        primary: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        frames: Dict[str, pd.DataFrame],
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        initial_capital: float,
        notification_config: Dict[str, Any],
    ) -> None:
        from app.services.grid.runner import GridRestingRunner
        from app.services.live_trading.account_configuration import configure_derivatives_account
        from app.services.live_trading.factory import create_client

        symbol = str(primary.get("symbol") or "")
        market_type = str(primary.get("market_type") or "swap").strip().lower()
        market_category = str(primary.get("market") or "Crypto")
        exchange_id = str(primary.get("exchange_id") or exchange_config.get("exchange_id") or "")
        leverage = max(1.0, float(trading_config.get("leverage") or 1))
        margin_mode = str(
            trading_config.get("margin_mode") or trading_config.get("marginMode") or "cross"
        )
        client_holder: Dict[str, Any] = {}

        def create_grid_client():
            client = client_holder.get("client")
            if client is None:
                client = create_client(exchange_config, market_type=market_type)
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
        runtime_grid_config = self._materialize_grid_anchor(trading_config, initial_price)

        runner = GridRestingRunner(
            strategy_id,
            symbol,
            runtime_grid_config,
            exchange_config,
            user_id=int((self._load_strategy(strategy_id) or {}).get("user_id") or 1),
            initial_capital=initial_capital,
            enqueue_market_fn=enqueue_market,
            create_client_fn=create_grid_client,
            risk_exit_fn=lambda price: self._grid_bot_risk_exits(
                strategy_id=strategy_id,
                symbol=symbol,
                current_price=float(price),
                trading_config=runtime_grid_config,
                timeframe_seconds=60,
                initial_capital=initial_capital,
            ),
        )
        ok, message = runner.startup(initial_price, bars_df=frame)
        if not ok:
            raise RuntimeError(f"grid.startupFailed:{message}")
        tick_seconds = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
        last_prices: dict[str, float] = {}
        try:
            while self._is_strategy_running(strategy_id, current_thread):
                cycle_started = time.monotonic()
                prices = self._live_prices(candidates)
                current_price = float(prices.get(key) or 0)
                if current_price > 0:
                    last_prices[key] = current_price
                    runner.tick(current_price, high=current_price, low=current_price, bars_df=frame)
                self._heartbeat(
                    strategy_id,
                    strategy_run_id,
                    primary,
                    last_prices,
                    0,
                    loop_latency_ms=int((time.monotonic() - cycle_started) * 1000),
                )
                if runner.should_stop:
                    break
                time.sleep(tick_seconds)
        finally:
            runner.shutdown()

    @staticmethod
    def _materialize_grid_anchor(
        trading_config: Dict[str, Any],
        initial_price: float,
    ) -> Dict[str, Any]:
        runtime_config = dict(trading_config or {})
        grid_params = (
            dict(runtime_config.get("bot_params") or {})
            if isinstance(runtime_config.get("bot_params"), dict)
            else {}
        )
        if not bool(grid_params.get("dynamicAnchor")) or initial_price <= 0:
            return runtime_config
        lower_ratio = float(grid_params.get("lowerPrice") or 0.0)
        upper_ratio = float(grid_params.get("upperPrice") or 0.0)
        reference = (lower_ratio + upper_ratio) / 2.0
        if lower_ratio <= 0 or upper_ratio <= 0 or reference <= 0:
            return runtime_config
        grid_params["lowerPrice"] = initial_price * lower_ratio / reference
        grid_params["upperPrice"] = initial_price * upper_ratio / reference
        grid_params["dynamicAnchor"] = False
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
        buffer_ratio = self._ratio(config.get("grid_oob_buffer_pct"), 0.05)
        if upper > lower > 0 and current_price > 0 and buffer_ratio > 0:
            if current_price >= upper * (1 + buffer_ratio):
                return close_all("grid_out_of_bounds_up", oob_threshold=upper * (1 + buffer_ratio))
            if current_price <= lower * (1 - buffer_ratio):
                return close_all("grid_out_of_bounds_down", oob_threshold=lower * (1 - buffer_ratio))
        capital = float(initial_capital or config.get("initial_capital") or 0)
        stop_ratio = self._ratio(config.get("stop_loss_pct"), 0)
        take_ratio = self._ratio(config.get("take_profit_pct"), 0)
        if capital > 0 and (stop_ratio > 0 or take_ratio > 0):
            equity = self._calculate_current_equity(
                strategy_id,
                capital,
                current_positions=positions,
                current_price=current_price,
                symbol=symbol,
            )
            change = (equity - capital) / capital
            if stop_ratio > 0 and change <= -stop_ratio:
                return close_all("grid_equity_stop_loss", equity=equity, equity_pct=change)
            if take_ratio > 0 and change >= take_ratio:
                return close_all("grid_equity_take_profit", equity=equity, equity_pct=change)
        return []

    def _calculate_current_equity(
        self,
        strategy_id: int,
        initial_capital: float,
        current_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None,
        symbol: str = "",
    ) -> float:
        realized = 0.0
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(profit, 0) - COALESCE(commission_quote, 0)), 0) AS realized_pnl
                    FROM qd_strategy_trades WHERE strategy_id = %s
                    """,
                    (strategy_id,),
                )
                realized = float((cur.fetchone() or {}).get("realized_pnl") or 0)
                cur.close()
        except Exception as exc:
            logger.warning("Failed to calculate realized PnL for strategy %s: %s", strategy_id, exc)
        unrealized = 0.0
        base_symbol = str(symbol or "").split(":", 1)[0]
        for row in current_positions or []:
            side = str(row.get("side") or "").lower()
            size = float(row.get("size") or 0)
            entry = float(row.get("entry_price") or 0)
            mark = float(row.get("current_price") or 0)
            if current_price and str(row.get("symbol") or "").split(":", 1)[0] == base_symbol:
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
        source = get_script_source_service().get_source(
            source_id,
            user_id=int(strategy.get("user_id") or 0),
        )
        code = str((source or {}).get("code") or "").strip()
        if not code:
            raise RuntimeError("strategyV2.codeRequired")
        return source_id, code

    def _is_strategy_running(self, strategy_id: int, thread: threading.Thread) -> bool:
        with self.lock:
            if self.running_strategies.get(strategy_id) is not thread:
                return False
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT status FROM qd_strategies_trading WHERE id = %s", (strategy_id,))
            row = cur.fetchone() or {}
            cur.close()
        return str(row.get("status") or "").lower() == "running"

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
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, symbol, side, size, entry_price, current_price,
                       highest_price, lowest_price, updated_at
                FROM qd_strategy_positions
                WHERE strategy_id = %s AND split_part(symbol, ':', 1) = split_part(%s, ':', 1)
                """,
                (strategy_id, symbol),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [dict(row) for row in rows]

    def _positions_by_symbol(
        self,
        strategy_id: int,
        candidates: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for member in candidates:
            key = str(member.get("key") or "")
            rows = self._get_current_positions(strategy_id, str(member.get("symbol") or ""))
            if rows:
                row = rows[0]
                output[key] = {
                    "amount": row.get("size") or 0,
                    "side": row.get("side") or "long",
                    "avg_cost": row.get("entry_price") or 0,
                    "last_price": row.get("current_price") or 0,
                }
        return output

    @staticmethod
    def _live_prices(candidates: list[dict[str, Any]]) -> dict[str, float]:
        prices: dict[str, float] = {}
        for member in candidates:
            try:
                ticker = DataSourceFactory.get_ticker(
                    str(member.get("market") or ""),
                    str(member.get("symbol") or ""),
                    exchange_id=str(member.get("exchange_id") or "") or None,
                    market_type=str(member.get("market_type") or "") or None,
                )
                price = float((ticker or {}).get("last") or (ticker or {}).get("close") or 0)
                if price > 0:
                    prices[str(member.get("key") or "")] = price
            except Exception as exc:
                logger.warning("Price fetch failed for %s: %s", member.get("key"), exc)
        return prices

    @classmethod
    def _execution_account_prices(
        cls,
        candidates: list[dict[str, Any]],
        exchange_config: dict[str, Any],
        client_holder: dict[str, Any],
    ) -> dict[str, float]:
        prices = cls._live_prices(candidates)
        try:
            from app.services.live_trading.factory import create_client
            from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id

            market_type = str((candidates[0] if candidates else {}).get("market_type") or "swap")
            client = client_holder.get("client")
            if client is None:
                client = create_client(exchange_config, market_type=market_type)
                client_holder["client"] = client
            exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
            for member in candidates:
                if str(member.get("market") or "") != "Crypto":
                    continue
                symbol = str(member.get("symbol") or "")
                price = 0.0
                if hasattr(client, "get_mark_price"):
                    price = float(client.get_mark_price(symbol=symbol) or 0.0)
                elif hasattr(client, "get_ticker"):
                    if exchange_id == "okx":
                        is_spot = str(member.get("market_type") or "").lower() == "spot"
                        inst_id = to_okx_spot_inst_id(symbol) if is_spot else to_okx_swap_inst_id(symbol)
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
            logger.warning("Execution-account price fetch failed: %s", exc)
        return prices

    @staticmethod
    def _align_latest_frame_prices(
        frames: dict[str, pd.DataFrame],
        prices: dict[str, float],
    ) -> dict[str, pd.DataFrame]:
        for key, price in prices.items():
            frame = frames.get(str(key))
            if frame is None or frame.empty or float(price or 0.0) <= 0:
                continue
            latest = frame.index[-1]
            for column in ("open", "high", "low", "close"):
                if column in frame.columns:
                    frame.at[latest, column] = float(price)
        return frames

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
