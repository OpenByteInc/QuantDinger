"""Grid resting runner — integrates GridEngine with TradingExecutor."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from app.services.grid.config import GridBotConfig
from app.services.grid.engine import GridEngine
from app.services.grid.validator import validate_grid_config
from app.services.bot_scripts.grid_runtime import prepare_bot_market_guards
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

# Active runners for fill poller
_ACTIVE_RUNNERS: Dict[int, "GridRestingRunner"] = {}


def register_runner(runner: "GridRestingRunner") -> None:
    _ACTIVE_RUNNERS[int(runner.strategy_id)] = runner


def unregister_runner(strategy_id: int) -> None:
    _ACTIVE_RUNNERS.pop(int(strategy_id), None)


def get_runner(strategy_id: int) -> Optional["GridRestingRunner"]:
    return _ACTIVE_RUNNERS.get(int(strategy_id))


def all_runners() -> Dict[int, "GridRestingRunner"]:
    return dict(_ACTIVE_RUNNERS)


def shutdown_grid_for_strategy(strategy_id: int) -> None:
    """Cancel open grid limits on the exchange even when no runner thread is alive."""
    sid = int(strategy_id or 0)
    if sid <= 0:
        return
    gr = get_runner(sid)
    if gr is not None:
        gr.shutdown()
        return
    try:
        from app.services.exchange_execution import coalesce_exchange_config_from_payload, load_strategy_configs, resolve_exchange_config
        from app.services.live_trading.factory import create_client
        from app.services.pending_orders.live_order_support import bind_instrument_product_contract

        sc = load_strategy_configs(sid) or {}
        tc = sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {}
        from app.services.strategy_runtime.bot_type import resolve_bot_type

        bot_type = resolve_bot_type(sc, tc)
        if bot_type != "grid":
            return
        symbol = str(tc.get("symbol") or sc.get("symbol") or "").strip()
        if not symbol:
            return
        user_id = int(sc.get("user_id") or 1)
        ex_cfg = resolve_exchange_config(coalesce_exchange_config_from_payload(sc), user_id=user_id)
        mt = str(tc.get("market_type") or sc.get("market_type") or "swap").strip().lower()
        ex_cfg = bind_instrument_product_contract(
            ex_cfg,
            tc,
            symbol=symbol,
            exchange_id=str(ex_cfg.get("exchange_id") or ""),
            market_type=mt,
        )

        def _create_client():
            return create_client(ex_cfg, market_type=mt)

        engine = GridEngine(
            sid,
            symbol,
            tc,
            ex_cfg,
            user_id=user_id,
            create_client_fn=_create_client,
            enqueue_market=lambda *a, **k: False,
        )
        engine.shutdown()
        append_strategy_log(sid, "info", "strategyRuntime.gridStopCleanup")
    except Exception as e:
        logger.warning("shutdown_grid_for_strategy sid=%s: %s", sid, e)
        append_strategy_log(sid, "warning", f"Grid stop cancel failed: {e}")


class GridRestingRunner:
    """Orchestrates professional resting grid for one live strategy."""

    def __init__(
        self,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        *,
        user_id: int = 1,
        strategy_run_id: int = 0,
        initial_capital: float,
        enqueue_market_fn: Callable[[str, float, float, str], bool],
        create_client_fn: Callable[[], Any],
        risk_exit_fn: Optional[Callable[[float], list]] = None,
    ) -> None:
        self.strategy_id = int(strategy_id)
        self.strategy_run_id = int(strategy_run_id or 0)
        self.user_id = int(user_id or 1)
        self.symbol = str(symbol or "")
        self.trading_config = dict(trading_config or {})
        self.trading_config["initial_capital"] = float(initial_capital or 0)
        self.exchange_config = dict(exchange_config or {})
        self._risk_exit_fn = risk_exit_fn
        self._runtime_params: Dict[str, Any] = {}
        self._engine = GridEngine(
            strategy_id,
            symbol,
            self.trading_config,
            self.exchange_config,
            user_id=self.user_id,
            strategy_run_id=self.strategy_run_id,
            create_client_fn=create_client_fn,
            enqueue_market=enqueue_market_fn,
        )
        from app.services.grid.actor import GridActorRepository

        self._actor_repository = GridActorRepository()
        if self.strategy_run_id > 0:
            try:
                actor_state = self._actor_repository.load_state(
                    strategy_id=self.strategy_id,
                    strategy_run_id=self.strategy_run_id,
                )
                self._engine.restore_actor_snapshot(actor_state)
            except Exception:
                logger.warning(
                    "Grid actor state restore failed: strategy=%s run=%s",
                    self.strategy_id,
                    self.strategy_run_id,
                    exc_info=True,
                )
        self._started = False
        self._last_actor_checkpoint_ts = 0.0
        self._last_sync_ts = 0.0
        self._last_exit_sync_ts = 0.0
        self._last_operational_check_ts = 0.0
        self._last_operational_snapshot: Dict[str, Any] = {}

    @property
    def engine(self) -> GridEngine:
        return self._engine

    @property
    def should_stop(self) -> bool:
        return self._engine.stop_requested

    @property
    def stop_reason(self) -> str:
        return self._engine.stop_reason

    def startup(self, current_price: float, *, bars_df: Any = None) -> tuple[bool, str]:
        self._engine.prepare_runtime_state()
        cfg = GridBotConfig.from_trading_config(self.trading_config)
        try:
            fee_rate = float(self.trading_config.get("commission") or 0.0) / 100.0
        except Exception:
            fee_rate = 0.0
        if fee_rate <= 0:
            fee_rate = 0.001
        ok, msg, warnings = validate_grid_config(
            cfg,
            initial_capital=float(self.trading_config.get("initial_capital") or 0),
            fee_rate=fee_rate,
        )
        if not ok:
            return False, msg
        for w in warnings:
            append_strategy_log(self.strategy_id, "warning", f"Grid config: {w}")
        bp = self.trading_config.get("bot_params") if isinstance(self.trading_config.get("bot_params"), dict) else {}
        self._runtime_params = dict(bp)
        self._engine.set_runtime_params(self._runtime_params)
        ok2, msg2 = self._engine.bootstrap(current_price)
        if not ok2:
            return False, msg2
        self._engine.reconcile_grid_ladder_orders()
        try:
            client = self._engine._create_client()
        except Exception as e:
            msg = str(e or "exchange client failed")
            append_strategy_log(self.strategy_id, "error", f"Grid exchange client failed: {msg}")
            return False, msg
        from app.services.grid.exchange_requirements import (
            fetch_exchange_dual_leg_snapshot,
            validate_neutral_grid_exchange_support,
        )

        ok_mode, mode_msg = validate_neutral_grid_exchange_support(
            cfg,
            client,
            symbol=self.symbol,
            exchange_config=self.exchange_config,
        )
        if not ok_mode:
            append_strategy_log(self.strategy_id, "error", mode_msg)
            return False, mode_msg
        try:
            snap = fetch_exchange_dual_leg_snapshot(
                client,
                symbol=self.symbol,
                market_type=cfg.market_type,
                exchange_config=self.exchange_config,
            )
            append_strategy_log(
                self.strategy_id,
                "info",
                "Grid exchange snapshot before initial position: "
                f"long={snap.get('long_size', 0):.6f} short={snap.get('short_size', 0):.6f} "
                f"mode={snap.get('position_mode_label') or 'unknown'}",
            )
            self._engine.set_initial_exchange_baseline(
                long_size=float(snap.get("long_size") or 0.0),
                short_size=float(snap.get("short_size") or 0.0),
            )
        except Exception as e:
            logger.debug("grid startup exchange snapshot sid=%s: %s", self.strategy_id, e)
        if self._engine.handle_boundary(current_price):
            if self._engine.stop_requested:
                return False, self._engine.stop_reason or "grid out of bounds"
            if self._engine._paused_entries:
                self._started = True
                register_runner(self)
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    "Grid resting startup paused because current price is outside configured bounds",
                )
                self.checkpoint(force=True, status="running")
                return True, ""
        self._engine.run_initial_market_position(current_price)
        n = self._engine.sync_grid_orders(current_price)
        if self._engine.cfg.grid_direction == "neutral":
            self._engine.sync_held_cell_exits(current_price)
        elif self._engine.cfg.initial_position_pct <= 0 or self._engine._initial_done:
            self._engine.sync_exit_coverage(current_price)
        startup_snapshot = self.operational_snapshot(force=True)
        has_new_initial_fills = self._engine.has_new_startup_initial_fills
        startup_coverage_failed = bool(
            has_new_initial_fills
            and not startup_snapshot.get("healthy")
        )
        if self._engine.stop_requested or startup_coverage_failed:
            if has_new_initial_fills or startup_coverage_failed:
                self._engine.cancel_all_orders_on_exchange()
            else:
                self._engine.cancel_entry_orders_on_exchange()
            rollback_ok = (
                self._engine.rollback_startup_initial_fills(current_price)
                if has_new_initial_fills
                else True
            )
            log_key = "strategyRuntime.gridStartupCoverageFailed"
            if has_new_initial_fills:
                log_key = (
                    "strategyRuntime.gridStartupCoverageFailedRolledBack"
                    if rollback_ok
                    else "strategyRuntime.gridStartupCoverageFailedRollbackFailed"
                )
            append_strategy_log(
                self.strategy_id,
                "error",
                log_key,
            )
            if not has_new_initial_fills:
                return False, "strategyRuntime.gridStartupCoverageFailed"
            if rollback_ok:
                return False, "strategyRuntime.gridStartupCoverageFailedRolledBack"
            return False, "strategyRuntime.gridStartupCoverageFailedRollbackFailed"
        self._started = True
        register_runner(self)
        self.checkpoint(force=True, status="running")
        try:
            from app.services.grid.poller import sync_strategy_grid_orders

            polled = sync_strategy_grid_orders(self.strategy_id)
            if polled:
                append_strategy_log(
                    self.strategy_id,
                    "info",
                    f"Grid startup fill sync: polled {polled} resting order(s)",
                )
        except Exception as e:
            logger.warning("grid startup fill sync sid=%s: %s", self.strategy_id, e)
        append_strategy_log(self.strategy_id, "info", f"Grid resting live started, placed {n} entry limits")
        return True, ""

    def shutdown(self) -> None:
        try:
            preserve_exits = bool(
                self._engine.stop_requested
                and self._engine.stop_reason == "exchange error while placing grid resting order"
            )
            self._engine.shutdown(preserve_exit_orders=preserve_exits)
        finally:
            self.checkpoint(force=True, status="stopped")
            unregister_runner(self.strategy_id)
            self._started = False

    def detach(self) -> None:
        """Release process-local ownership while leaving exchange orders active."""
        self.checkpoint(force=True, status="handoff")
        unregister_runner(self.strategy_id)
        self._started = False

    def checkpoint(
        self,
        *,
        force: bool = False,
        status: str = "running",
        last_execution_event_id: int = 0,
    ) -> bool:
        if self.strategy_run_id <= 0:
            return False
        now = time.monotonic()
        if not force and now - self._last_actor_checkpoint_ts < 5.0:
            return False
        state = self._engine.actor_snapshot()
        state["started"] = bool(self._started)
        try:
            saved = self._actor_repository.checkpoint(
                strategy_id=self.strategy_id,
                strategy_run_id=self.strategy_run_id,
                state=state,
                status=status,
                last_execution_event_id=last_execution_event_id,
            )
        except Exception:
            logger.warning(
                "Grid actor checkpoint failed: strategy=%s run=%s",
                self.strategy_id,
                self.strategy_run_id,
                exc_info=True,
            )
            return False
        if saved:
            self._last_actor_checkpoint_ts = now
        return saved

    def operational_snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        """Return whether exchange-resting coverage is verifiably active."""
        now = time.time()
        if (
            not force
            and self._last_operational_snapshot
            and now - self._last_operational_check_ts < 5.0
        ):
            return dict(self._last_operational_snapshot)
        orders = list(self._engine._orders.list_open(self.strategy_id) or [])
        entries = [order for order in orders if str(order.purpose or "") in {"long_entry", "short_entry"}]
        unverified = [order for order in orders if not str(order.exchange_order_id or "").strip()]
        expected = bool(
            int(self._engine.cfg.max_open_orders or 0) > 0
            and not self._engine._paused_entries
            and not self._engine.stop_requested
            and not self._runtime_params.get("waterfall_pause")
        )
        error = ""
        if unverified:
            error = "grid_unverified_exchange_orders"
        elif expected and not entries:
            error = "grid_no_exchange_resting_orders"
        snapshot = {
            "open_orders": len(orders),
            "open_entries": len(entries),
            "unverified_orders": len(unverified),
            "expected_entries": expected,
            "healthy": not bool(error),
            "error": error,
        }
        self._last_operational_check_ts = now
        self._last_operational_snapshot = snapshot
        return dict(snapshot)

    def tick(
        self,
        current_price: float,
        *,
        high: Optional[float] = None,
        low: Optional[float] = None,
        bars_df: Any = None,
        is_closed_bar: bool = False,
    ) -> None:
        if not self._started or current_price <= 0:
            return
        if self._engine.stop_requested:
            return
        hi = float(high if high is not None else current_price)
        lo = float(low if low is not None else current_price)
        prepare_bot_market_guards(
            "grid",
            self._runtime_params,
            price=current_price,
            high=hi,
            low=lo,
            bars_df=bars_df,
            is_closed_bar=is_closed_bar,
        )
        self._engine.set_runtime_params(self._runtime_params)

        if self._risk_exit_fn:
            try:
                exits = self._risk_exit_fn(current_price) or []
                if exits:
                    self._engine.cancel_entry_orders_on_exchange()
                    self._engine._paused_entries = True
                    exit_reasons = []
                    all_queued = True
                    queued_any = False
                    for ex in exits:
                        st = str(ex.get("type") or "").strip().lower()
                        if st:
                            queued_any = True
                            exit_reasons.append(str(ex.get("reason") or "grid_risk"))
                            if st != "stop":
                                all_queued = bool(self._engine._enqueue_market(
                                    st, 0, current_price, str(ex.get("reason") or "grid_risk")
                                )) and all_queued
                    if queued_any and all_queued:
                        self._engine._stop_requested = True
                        self._engine._stop_reason = (
                            "grid risk exit: " + ", ".join(sorted(set(exit_reasons)))
                        )
                        append_strategy_log(self.strategy_id, "warning", "Grid risk exit triggered")
                    else:
                        append_strategy_log(
                            self.strategy_id,
                            "error",
                            "Grid risk exit could not be queued; entries remain paused and the close will retry",
                        )
                    return
            except Exception as e:
                logger.debug("grid risk exit: %s", e)

        if self._engine.handle_boundary(current_price):
            return

        if (
            self._engine.cfg.initial_position_pct > 0
            and not self._engine._initial_done
        ):
            self._engine.run_initial_market_position(current_price)

        if self._engine.cfg.grid_direction in ("long", "short", "neutral"):
            if self._engine.cfg.initial_position_pct <= 0 or self._engine._initial_done:
                now_exit = time.time()
                if now_exit - self._last_exit_sync_ts >= 15.0:
                    if self._engine.cfg.grid_direction == "neutral":
                        self._engine.sync_held_cell_exits(current_price)
                    else:
                        self._engine.sync_exit_coverage(current_price)
                    self._last_exit_sync_ts = now_exit

        now = time.time()
        if now - self._last_sync_ts >= 15.0:
            self._engine.sync_grid_orders(current_price)
            self._last_sync_ts = now
