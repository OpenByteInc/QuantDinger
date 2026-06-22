"""Application startup hooks and process-local service singletons."""
from __future__ import annotations

import os
import threading
import time
import traceback

from flask import Flask

from app.utils.logger import get_logger


logger = get_logger(__name__)

_trading_executor = None
_pending_order_worker = None


def get_trading_executor():
    """Get the process-local trading executor singleton."""
    global _trading_executor
    if _trading_executor is None:
        from app.services.trading_executor import TradingExecutor
        _trading_executor = TradingExecutor()
    return _trading_executor


def get_pending_order_worker():
    """Get the process-local pending order worker singleton."""
    global _pending_order_worker
    if _pending_order_worker is None:
        from app.services.pending_order_worker import PendingOrderWorker
        _pending_order_worker = PendingOrderWorker()
    return _pending_order_worker


def _is_debug_reloader_parent() -> bool:
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    return debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true"


def start_portfolio_monitor():
    """Start the portfolio monitor service if enabled."""
    if os.getenv("ENABLE_PORTFOLIO_MONITOR", "true").lower() != "true":
        logger.info("Portfolio monitor is disabled. Set ENABLE_PORTFOLIO_MONITOR=true to enable.")
        return
    if _is_debug_reloader_parent():
        return
    try:
        from app.services.portfolio_monitor import start_monitor_service
        start_monitor_service()
    except Exception as e:
        logger.error(f"Failed to start portfolio monitor: {e}")


def start_pending_order_worker():
    """Start the pending order worker."""
    if os.getenv("ENABLE_PENDING_ORDER_WORKER", "true").lower() != "true":
        logger.info("Pending order worker is disabled. Set ENABLE_PENDING_ORDER_WORKER=true to enable.")
        return
    try:
        get_pending_order_worker().start()
    except Exception as e:
        logger.error(f"Failed to start pending order worker: {e}")


def start_grid_fill_poller():
    """Poll exchange for grid resting limit order fills."""
    if os.getenv("ENABLE_GRID_FILL_POLLER", "true").lower() != "true":
        logger.info("Grid fill poller disabled (ENABLE_GRID_FILL_POLLER=false)")
        return
    if _is_debug_reloader_parent():
        return
    try:
        from app.services.grid.poller import get_grid_fill_poller
        get_grid_fill_poller().start()
    except Exception as e:
        logger.error(f"Failed to start grid fill poller: {e}")


def start_usdt_order_worker():
    """Start the USDT order background worker."""
    raw_enabled = os.getenv("USDT_PAY_ENABLED", "")
    enabled = raw_enabled.strip().lower() in ("1", "true", "yes")
    enabled_chains = os.getenv("USDT_PAY_ENABLED_CHAINS", "")
    poll_interval = os.getenv("USDT_WORKER_POLL_INTERVAL", "30")

    logger.info(
        "USDT pay boot check: USDT_PAY_ENABLED=%r (parsed=%s) chains=%r poll=%ss",
        raw_enabled, enabled, enabled_chains, poll_interval,
    )
    if not enabled:
        logger.info(
            "USDT order worker NOT started; USDT_PAY_ENABLED is %r. "
            "Set USDT_PAY_ENABLED=true in .env and restart the container.",
            raw_enabled,
        )
        return
    if _is_debug_reloader_parent():
        logger.info(
            "USDT order worker skipped in this Flask reloader parent "
            "(WERKZEUG_RUN_MAIN!=true); the child process will start it."
        )
        return
    try:
        from app.services.usdt_payment_service import get_usdt_order_worker
        worker = get_usdt_order_worker()
        worker.start()
        logger.info(
            "USDT order worker boot OK; thread alive=%s, scanning every %ss",
            worker.is_alive() if hasattr(worker, "is_alive") else "n/a",
            poll_interval,
        )
    except Exception as e:
        logger.error(f"Failed to start USDT order worker: {e}", exc_info=True)


def restore_running_strategies():
    """Restore running strategies on startup."""
    if os.getenv("DISABLE_RESTORE_RUNNING_STRATEGIES", "false").lower() == "true":
        logger.info("Startup strategy restore is disabled via DISABLE_RESTORE_RUNNING_STRATEGIES")
        return
    if _is_debug_reloader_parent():
        return
    try:
        from app.services.strategy import StrategyService

        strategy_service = StrategyService()
        trading_executor = get_trading_executor()
        running_strategies = strategy_service.get_running_strategies_with_type()
        if not running_strategies:
            logger.info("No running strategies to restore.")
            return

        logger.info(f"Restoring {len(running_strategies)} running strategies...")
        restored_count = 0
        for strategy_info in running_strategies:
            strategy_id = strategy_info["id"]
            strategy_type = strategy_info.get("strategy_type", "")
            try:
                success = trading_executor.start_strategy(strategy_id)
                strategy_type_name = strategy_type or "Strategy"
                if success:
                    restored_count += 1
                    logger.info(f"[OK] {strategy_type_name} {strategy_id} restored")
                else:
                    logger.warning(f"[FAIL] {strategy_type_name} {strategy_id} restore failed (state may be stale)")
                    try:
                        strategy_service.update_strategy_status(strategy_id, "stopped")
                        logger.info(f"[FIX] Updated strategy {strategy_id} status to 'stopped' after restore failure")
                    except Exception as e:
                        logger.error(f"Failed to update strategy {strategy_id} status after restore failure: {e}")
            except Exception as e:
                logger.error(f"Error restoring strategy {strategy_id}: {str(e)}")
                logger.error(traceback.format_exc())

        logger.info(f"Strategy restore completed: {restored_count}/{len(running_strategies)} restored")
        _schedule_post_restore_position_sync()
    except Exception as e:
        logger.error(f"Failed to restore running strategies: {str(e)}")
        logger.error(traceback.format_exc())


def _schedule_post_restore_position_sync() -> None:
    """Run one delayed position-sync pass after restored strategies start."""
    if os.getenv("POSITION_SYNC_ENABLED", "true").lower() != "true":
        return
    try:
        delay = float(os.getenv("POST_RESTORE_SYNC_DELAY_SEC", "12"))
    except Exception:
        delay = 12.0
    if delay < 0:
        delay = 0.0

    def _run() -> None:
        if delay > 0:
            time.sleep(delay)
        try:
            get_pending_order_worker()._sync_positions_best_effort()
            logger.info("Post-restore position sync finished (broken live strategies should be stopped)")
        except Exception as exc:
            logger.warning(f"Post-restore position sync failed: {exc}")

    threading.Thread(target=_run, name="PostRestorePositionSync", daemon=True).start()


def run_startup_hooks(app: Flask) -> None:
    """Run optional background startup hooks after route registration."""
    skip_hooks = os.getenv("SKIP_STARTUP_HOOKS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if skip_hooks:
        return
    with app.app_context():
        start_pending_order_worker()
        start_grid_fill_poller()
        start_portfolio_monitor()
        start_usdt_order_worker()
        try:
            from app.services.ai_calibration import start_ai_calibration_worker
            start_ai_calibration_worker()
        except Exception:
            pass
        try:
            from app.services.reflection import start_reflection_worker
            start_reflection_worker()
        except Exception:
            pass
        restore_running_strategies()
