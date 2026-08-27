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
from app.services.script_source import get_script_source_service
from app.services.strategy_runtime.health import record_runtime_heartbeat
from app.services.strategy_runtime.identity import ensure_strategy_run, finish_strategy_run
from app.services.strategy_runtime.order_intents import OrderIntentService
from app.services.strategy_runtime.state import RuntimeStateStore
from app.services.strategy_runtime.timeframes import (
    live_history_days,
    load_live_frequency_frames,
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
from app.utils.strategy_runtime_logs import append_strategy_log
from app.utils.thread_capacity import format_thread_capacity


logger = get_logger(__name__)

MIN_LIVE_ORDER_NOTIONAL = 1.0
__all__ = ["TradingExecutor", "live_history_days"]


def _runtime_position_key(symbol: object, position_side: object = "") -> str:
    base = str(symbol or "")
    side = str(position_side or "").strip().lower()
    return f"{base}::{side}" if side in {"long", "short"} else base


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
                self._last_start_failure = (
                    f"Failed to start strategy thread: {exc}; {format_thread_capacity()}"
                )
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
            # A resting grid owns exchange-side limit orders independently of the
            # strategy thread.  Cancelling only the local runtime would leave
            # those orders live after the UI reports the strategy as stopped.
            # The shutdown helper is idempotent and ignores non-grid strategies.
            from app.services.grid.runner import shutdown_grid_for_strategy

            shutdown_grid_for_strategy(strategy_id)
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
        market_price_feed = None
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
            if execution_mode == "live" and account_exchange == "futu":
                from app.services.futu_trading.config import normalize_trade_market

                credential_market = normalize_trade_market(
                    exchange_config.get("trade_market") or exchange_config.get("tradeMarket"),
                    market_category=str(exchange_config.get("market_category") or ""),
                )
                strategy_markets = {
                    str(member.get("market") or "")
                    for member in candidates
                    if str(member.get("market") or "") in {"HKStock", "USStock"}
                }
                expected_markets = {"HKStock": "HK", "USStock": "US"}
                if any(expected_markets[market] != credential_market for market in strategy_markets):
                    raise RuntimeError("strategyV2.futuCredentialMarketMismatch")
            if execution_mode == "live" and account_exchange:
                for member in candidates:
                    member_market = str(member.get("market") or "")
                    if member_market == "Crypto" or (
                        account_exchange == "futu"
                        and member_market in {"HKStock", "USStock"}
                    ):
                        member["exchange_id"] = account_exchange
                        member["key"] = _member_key(member)
            strict_futu_market_data = (
                execution_mode == "live" and account_exchange == "futu"
            )

            frequency = program.manifest.driving_frequency

            def fetch_runtime_frames() -> dict[str, dict[str, pd.DataFrame]]:
                return load_live_frequency_frames(
                    service=service,
                    candidates=candidates,
                    manifest=program.manifest,
                    end_date=datetime.now(timezone.utc),
                    exchange_config=exchange_config if strict_futu_market_data else None,
                    strict_data_source=strict_futu_market_data,
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
                return [_member_key(item) for item in members]

            frequency_frames = fetch_runtime_frames()
            frames = frequency_frames[frequency]
            runtime_price_client: Dict[str, Any] = {}

            def runtime_prices() -> dict[str, float]:
                if execution_mode != "live":
                    return self._live_prices(candidates)
                return self._execution_account_prices(
                    candidates,
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
            last_price_seen_at: dict[str, float] = {}
            self._heartbeat(strategy_id, run_id, primary, last_prices, 0)
            from app.services.strategy_runtime.bot_type import resolve_bot_type

            bot_type = resolve_bot_type(strategy, trading_config)
            if bot_type:
                # Keep all downstream risk helpers on the same canonical
                # classification, including legacy executor_type deployments.
                trading_config["bot_type"] = bot_type
            if execution_mode == "live" and bot_type == "grid":
                exit_reason = self._run_grid_resting_loop(
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
                return
            price_feed_meta = {
                "source": "exchange_rest",
                "age_ms": 0,
                "connected": False,
            }
            if execution_mode == "live" and account_exchange:
                rest_runtime_prices = runtime_prices
                stale_after = float(trading_config.get("price_stale_after_seconds") or 10.0)
                if str(account_exchange or "").strip().lower() == "futu":
                    from app.services.futu_trading.quote_feed import FutuQuoteFeed

                    market_price_feed = FutuQuoteFeed(
                        exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
                        instruments=candidates,
                        poll_interval_sec=float(trading_config.get("futu_quote_poll_sec") or 2.0),
                    )
                    market_price_feed.start()

                    def runtime_prices() -> dict[str, float]:
                        snapshot = market_price_feed.snapshot(max_age_seconds=stale_after)
                        price_feed_meta.update({
                            "source": snapshot.get("source") or "futu_quote",
                            "age_ms": int(snapshot.get("age_ms") or 0),
                            "connected": bool(snapshot.get("connected")),
                        })
                        # A Futu live account must fail closed when OpenD quotes
                        # are stale; public REST prices are not executable prices.
                        return dict(snapshot.get("prices") or {})
                else:
                    from app.services.market_price_stream import PublicMarketPriceFeed

                    market_price_feed = PublicMarketPriceFeed(
                        exchange_id=account_exchange,
                        market_type=str(primary.get("market_type") or "spot"),
                        instruments=candidates,
                        rest_fallback=runtime_prices,
                    )
                    market_price_feed.start()

                    def runtime_prices() -> dict[str, float]:
                        snapshot = market_price_feed.snapshot(max_age_seconds=stale_after)
                        price_feed_meta.update({
                            "source": snapshot.source,
                            "age_ms": snapshot.age_ms,
                            "connected": snapshot.connected,
                        })
                        return snapshot.prices or rest_runtime_prices()
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

            signal_poll = max(1.0, min(30.0, float(trading_config.get("data_poll_seconds") or 5)))
            risk_tick = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
            price_stale_after = max(
                risk_tick * 3.0,
                min(30.0, float(trading_config.get("price_stale_after_seconds") or 10.0)),
            )
            next_signal_poll = 0.0
            stale_price_logged = False
            consecutive_errors = 0
            strategy_name = str(strategy.get("strategy_name") or f"strategy_{strategy_id}")
            notification_config = _json_object(strategy.get("notification_config"))
            leverage = max(1.0, float(trading_config.get("leverage") or strategy.get("leverage") or 1))
            from app.services.strategy_live_guard import resolve_strategy_direction_mode

            direction_mode = resolve_strategy_direction_mode(strategy)
            append_strategy_log(
                strategy_id,
                "info",
                f"Strategy runtime ready: instruments={len(candidates)}, timeframe={frequency}, mode={execution_mode}",
            )

            while self._is_strategy_running(strategy_id, current):
                cycle_started = time.monotonic()
                try:
                    references = session.context.order_references()
                    if references:
                        session.context.update_order_statuses(
                            order_intent_service.statuses_by_client_order_ids(references)
                        )
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
                            candidates=candidates,
                            initial_capital=initial_capital,
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
                            candidates=candidates,
                            initial_capital=initial_capital,
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

                    # Realtime prices are an execution/risk input only. Normal
                    # strategy entries and scale-ins remain completed-candle
                    # driven through ``session.process(frames)`` below.
                    pending_count = len(equity_intents) + len(protection_intents)
                    if not equity_stop_reason and cycle_started >= next_signal_poll:
                        frequency_frames = fetch_runtime_frames()
                        frames = frequency_frames[frequency]
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
                                candidates=candidates,
                                initial_capital=initial_capital,
                                leverage=leverage,
                                execution_mode=execution_mode,
                                notification_config=notification_config,
                                trading_config=trading_config,
                                exchange_config=exchange_config,
                                signal_ts=self._intent_signal_timestamp(intent, timestamp),
                                strategy_run_id=run_id,
                                direction_mode=direction_mode,
                            )
                            if intent.client_order_id:
                                session.context.update_order_statuses({
                                    intent.client_order_id: {
                                        "client_order_id": intent.client_order_id,
                                        "status": "submitted" if submitted else "rejected",
                                    },
                                })
                        next_signal_poll = cycle_started + signal_poll
                    state_store.save(session.session_snapshot())
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
                            else "closed_bar"
                        ),
                        fill_transport="private_stream_with_rest_reconciliation",
                    )
                    if equity_stop_reason and not equity_submission_failed:
                        exit_reason = f"equity risk stopped: {equity_stop_reason}"
                        self._last_exit_reason[strategy_id] = exit_reason
                        append_strategy_log(strategy_id, "warning", exit_reason)
                        self._mark_stopped(strategy_id)
                        break
                    consecutive_errors = 0
                except Exception as exc:
                    if str(exc) == "strategyV2.noMarketData":
                        next_signal_poll = cycle_started + signal_poll
                        logger.warning(
                            "Strategy %s temporarily has no usable market data",
                            strategy_id,
                        )
                        append_strategy_log(
                            strategy_id,
                            "warning",
                            "Runtime cycle skipped because no instrument has usable market data",
                        )
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
                        continue
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
            if market_price_feed is not None:
                market_price_feed.stop()
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
        direction_mode: str = "",
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
        if execution_mode == "live":
            target_amount = self._direction_constrained_target(
                target_amount,
                direction_mode=direction_mode,
            )
        if market_type == "spot" and target_amount < -1e-12:
            raise RuntimeError("strategyV2.spotShortUnsupported")

        closes_position = abs(target_amount) <= 1e-12 and abs(current_amount) > 1e-12
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
        leverage = float(values.get("leverage") or 1)
        nominal_capacity = initial_capital * max(1.0, leverage)
        entry_pct = ((quantity * reference_price) / nominal_capacity * 100.0) if nominal_capacity > 0 else 0.0
        from app.services.pending_orders.order_budget import strategy_order_budget_snapshot

        budget = strategy_order_budget_snapshot(
            action=str(values.get("signal_type") or ""),
            quantity=quantity,
            price=reference_price,
            initial_capital=initial_capital,
            leverage=leverage,
            market_type=str(values.get("market_type") or "spot"),
            current_positions=values.get("current_positions") or (),
            buffer_ratio=float(
                (_json_object(values.get("trading_config"))).get("order_budget_buffer_ratio")
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
            reason=str(values.get("signal_reason") or ""),
            notification_config=dict(values.get("notification_config") or {}),
            order_type=str(values.get("order_type") or "market"),
            execution_algo=str(values.get("execution_algo") or "market"),
            limit_price=float(values.get("limit_price") or 0.0),
            maker_wait_sec=float(values.get("maker_wait_sec") or 0.0),
            maker_offset_bps=float(values.get("maker_offset_bps") or 0.0),
            protection=dict(values.get("protection") or {}),
            client_order_id=str(values.get("client_order_id") or ""),
            sizing={
                "initial_capital": initial_capital,
                "entry_pct": entry_pct,
                "leverage": leverage,
                "source": "strategy_v2",
            },
        )
        inflight_check = getattr(self.order_gateway, "has_inflight", None)
        if (
            request.execution_mode == "live"
            and callable(inflight_check)
            and inflight_check(request)
        ):
            return False
        pending_id = self.order_gateway.submit(request)
        if pending_id:
            append_strategy_log(
                strategy_id,
                "trade",
                f"Order queued: {request.action} {request.symbol} "
                f"quantity={format_decimal(request.quantity)}",
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
    ) -> str:
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
            exchange_config,
            user_id=int((self._load_strategy(strategy_id) or {}).get("user_id") or 1),
            initial_capital=initial_capital,
            enqueue_market_fn=enqueue_market,
            create_client_fn=create_grid_client,
            risk_exit_fn=evaluate_grid_risk,
        )
        ok, message = runner.startup(initial_price, bars_df=frame)
        if not ok:
            raise RuntimeError(f"grid.startupFailed:{message}")
        tick_seconds = max(0.25, min(5.0, float(trading_config.get("risk_tick_seconds") or 1)))
        last_prices: dict[str, float] = {}
        stale_logged = False
        grid_exit_reason = "grid strategy stopped"
        from app.services.market_price_stream import PublicMarketPriceFeed

        grid_price_feed = PublicMarketPriceFeed(
            exchange_id=exchange_id,
            market_type=market_type,
            instruments=candidates,
            rest_fallback=lambda: self._execution_account_prices(
                candidates,
                exchange_config,
                client_holder,
            ),
        )
        grid_price_feed.start()
        try:
            while self._is_strategy_running(strategy_id, current_thread):
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
                    runner.tick(current_price, high=current_price, low=current_price, bars_df=frame)
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
                time.sleep(tick_seconds)
        finally:
            grid_price_feed.stop()
            runner.shutdown()
        return grid_exit_reason

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
        buffer_ratio = self._ratio(config.get("grid_oob_buffer_pct"), 0.05)
        if upper > lower > 0 and current_price > 0 and buffer_ratio > 0:
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
        source = get_script_source_service().get_source(
            source_id,
            user_id=int(strategy.get("user_id") or 0),
        )
        code = str((source or {}).get("code") or "").strip()
        if not code:
            raise RuntimeError("strategyV2.codeRequired")
        from app.services.strategy_runtime.bot_type import resolve_bot_type
        from app.services.strategy_runtime.robot_v2 import migrate_legacy_robot_v2_source

        bot_type = resolve_bot_type(strategy, trading_config)
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
        *,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        from app.services.strategy_live_guard import resolve_strategy_direction_mode

        output: dict[str, dict[str, Any]] = {}
        strategy_row = strategy if isinstance(strategy, dict) else (self._load_strategy(strategy_id) or {})
        owns_both_legs = resolve_strategy_direction_mode(strategy_row) in {"both", "neutral"}
        for member in candidates:
            key = str(member.get("key") or "")
            rows = self._get_current_positions(strategy_id, str(member.get("symbol") or ""))
            if not rows:
                continue
            selected_rows = rows if owns_both_legs else rows[:1]
            for row in selected_rows:
                side = str(row.get("side") or "long").strip().lower()
                if side not in {"long", "short"}:
                    side = "long"
                position_key = f"{key}::{side}" if owns_both_legs else key
                output[position_key] = {
                    "amount": row.get("size") or 0,
                    "side": side,
                    "position_side": side if owns_both_legs else "",
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
        exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
        strict_futu = exchange_id == "futu"
        # A Futu live strategy must never use a public-provider price for risk
        # decisions or entry signals when its execution feed is unavailable.
        prices = {} if strict_futu else cls._live_prices(candidates)
        try:
            from app.services.live_trading.factory import create_client
            from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id

            market_type = str((candidates[0] if candidates else {}).get("market_type") or "swap")
            client = client_holder.get("client")
            if client is None:
                client = create_client(exchange_config, market_type=market_type)
                client_holder["client"] = client
            for member in candidates:
                market = str(member.get("market") or "")
                symbol = str(member.get("symbol") or "")
                price = 0.0
                if market == "Crypto":
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
                elif exchange_id == "futu" and market in ("HKStock", "USStock") and hasattr(client, "get_quote"):
                    quote = client.get_quote(symbol, market)
                    if isinstance(quote, dict) and quote.get("success"):
                        price = float(quote.get("last") or quote.get("close") or 0.0)
                    if price <= 0:
                        raise RuntimeError(
                            f"FUTU_EXECUTION_QUOTE_UNAVAILABLE:{market}:{symbol}:"
                            f"{(quote or {}).get('error') or 'empty price'}"
                        )
                if price > 0:
                    prices[str(member.get("key") or "")] = price
        except Exception as exc:
            logger.warning("Execution-account price fetch failed: %s", exc)
            if strict_futu:
                raise RuntimeError(f"strategyV2.executionMarketDataUnavailable:{exc}") from exc
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
