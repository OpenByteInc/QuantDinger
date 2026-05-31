"""
QuantDinger Python API - Flask application factory.
"""
import math
import os
import logging
import traceback
from datetime import date, datetime

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS

from app.utils.logger import setup_logger, get_logger
from app.utils.timeutil import to_utc_iso


class SafeJSONProvider(DefaultJSONProvider):
    """JSON provider with two cross-cutting behaviors.

    1. NaN / Infinity → null.  ``json.dumps`` (allow_nan=True) emits literal
       ``NaN`` / ``Infinity`` tokens which are **not** valid JSON per RFC 8259
       and crash ``JSON.parse()`` on the frontend.

    2. ``datetime`` → UTC ISO 8601 (``...Z``).  Database columns are stored as
       naive ``TIMESTAMP`` in the container's local time zone (``TZ`` env
       var).  Sending them out as a naive string forces the browser to
       interpret them as *local* time, which breaks every user whose locale
       differs from the server.  We normalize all datetimes to UTC with an
       explicit ``Z`` suffix so the frontend can safely call
       ``new Date(text).toLocaleString()``.

    ``date`` objects (without a time component) are passed through as plain
    ISO date strings since they don't carry a time-of-day to reinterpret.
    """

    @staticmethod
    def default(o):
        """Handle non-serializable objects (datetimes first, then super)."""
        if isinstance(o, datetime):
            return to_utc_iso(o)
        if isinstance(o, date):
            return o.isoformat()
        return DefaultJSONProvider.default(o)

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", self.default)
        return _safe_json_dumps(obj, **kwargs)


def _safe_json_dumps(obj, **kwargs):
    """Recursively sanitize NaN/Inf and normalize datetimes, then serialize."""
    import json
    return json.dumps(_sanitize(obj), **kwargs)


def _sanitize(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, datetime):
        return to_utc_iso(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj

logger = get_logger(__name__)

# Global singletons (avoid duplicate strategy threads).
_trading_executor = None
_pending_order_worker = None


def get_trading_executor():
    """Get the trading executor singleton."""
    global _trading_executor
    if _trading_executor is None:
        from app.services.trading_executor import TradingExecutor
        _trading_executor = TradingExecutor()
    return _trading_executor


def get_pending_order_worker():
    """Get the pending order worker singleton."""
    global _pending_order_worker
    if _pending_order_worker is None:
        from app.services.pending_order_worker import PendingOrderWorker
        _pending_order_worker = PendingOrderWorker()
    return _pending_order_worker


def start_portfolio_monitor():
    """Start the portfolio monitor service if enabled.
    
    To enable it, set ENABLE_PORTFOLIO_MONITOR=true.
    """
    import os
    enabled = os.getenv("ENABLE_PORTFOLIO_MONITOR", "true").lower() == "true"
    if not enabled:
        logger.info("Portfolio monitor is disabled. Set ENABLE_PORTFOLIO_MONITOR=true to enable.")
        return
    
    # Avoid running twice with Flask reloader
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    if debug:
        if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return
    
    try:
        from app.services.portfolio_monitor import start_monitor_service
        start_monitor_service()
    except Exception as e:
        logger.error(f"Failed to start portfolio monitor: {e}")


def start_pending_order_worker():
    """Start the pending order worker (disabled by default in paper mode).

    To enable it, set ENABLE_PENDING_ORDER_WORKER=true.
    """
    import os
    # Local deployment: default to enabled so queued orders can be dispatched automatically.
    # To disable it, set ENABLE_PENDING_ORDER_WORKER=false explicitly.
    if os.getenv('ENABLE_PENDING_ORDER_WORKER', 'true').lower() != 'true':
        logger.info("Pending order worker is disabled (paper mode). Set ENABLE_PENDING_ORDER_WORKER=true to enable.")
        return
    try:
        get_pending_order_worker().start()
    except Exception as e:
        logger.error(f"Failed to start pending order worker: {e}")


def start_grid_fill_poller():
    """Poll exchange for grid resting limit order fills."""
    import os
    if os.getenv("ENABLE_GRID_FILL_POLLER", "true").lower() != "true":
        logger.info("Grid fill poller disabled (ENABLE_GRID_FILL_POLLER=false)")
        return
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    try:
        from app.services.grid.poller import get_grid_fill_poller
        get_grid_fill_poller().start()
    except Exception as e:
        logger.error(f"Failed to start grid fill poller: {e}")


def start_usdt_order_worker():
    """Start the USDT order background worker.

    Periodically scans pending/paid USDT orders and checks on-chain status.
    Ensures orders are confirmed even if the user closes the browser after payment.
    Only starts if USDT_PAY_ENABLED=true.

    Boot logs intentionally include the resolved env values (truthy/falsy only
    — no secrets) so operators can confirm what the worker actually sees,
    rather than guessing why nothing is happening when the .env file looks
    correct on disk.
    """
    import os

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
            "USDT order worker NOT started — USDT_PAY_ENABLED is %r. "
            "Set USDT_PAY_ENABLED=true in .env and restart the container.",
            raw_enabled,
        )
        return

    # Avoid running twice with Flask reloader (local dev only)
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
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
            "USDT order worker boot OK — thread alive=%s, scanning every %ss",
            worker.is_alive() if hasattr(worker, "is_alive") else "n/a",
            poll_interval,
        )
    except Exception as e:
        logger.error(f"Failed to start USDT order worker: {e}", exc_info=True)


def restore_running_strategies():
    """
    Restore running strategies on startup.
    """
    import os
    # You can disable auto-restore to avoid starting many threads on low-resource hosts.
    if os.getenv('DISABLE_RESTORE_RUNNING_STRATEGIES', 'false').lower() == 'true':
        logger.info("Startup strategy restore is disabled via DISABLE_RESTORE_RUNNING_STRATEGIES")
        return

    # Avoid running twice with Flask reloader (local debug mode).
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    if debug:
        if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
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
            strategy_id = strategy_info['id']
            strategy_type = strategy_info.get('strategy_type', '')
            
            try:
                success = trading_executor.start_strategy(strategy_id)
                strategy_type_name = strategy_type or 'Strategy'
                
                if success:
                    restored_count += 1
                    logger.info(f"[OK] {strategy_type_name} {strategy_id} restored")
                else:
                    logger.warning(f"[FAIL] {strategy_type_name} {strategy_id} restore failed (state may be stale)")
                    # 如果恢复失败，更新数据库状态为stopped，避免策略处于"僵尸"状态
                    try:
                        strategy_service.update_strategy_status(strategy_id, 'stopped')
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
        # Do not raise; avoid breaking app startup.


def _schedule_post_restore_position_sync() -> None:
    """
    After restart, run one position-sync pass so strategies with dead API keys /
    unreachable IBKR are auto-stopped (DB + executor thread) instead of spamming logs.
    """
    import os
    import threading
    import time

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


def create_app(config_name='default'):
    """
    Flask application factory.
    
    Args:
        config_name: config name
        
    Returns:
        Flask app
    """
    app = Flask(__name__)
    app.json_provider_class = SafeJSONProvider
    app.json = SafeJSONProvider(app)

    app.config['JSON_AS_ASCII'] = False

    # CORS — pin to specific origins instead of '*'. FRONTEND_URL accepts a
    # comma-separated list (e.g. "http://localhost:8888,http://localhost:8000")
    # so dev and prod frontends can both be allowed. Default covers the docker
    # frontend port (8888) and the Vue dev server port (8000).
    _cors_origins = [
        o.strip() for o in os.getenv(
            "FRONTEND_URL",
            "http://localhost:8888,http://localhost:8000",
        ).split(",")
        if o.strip()
    ]

    # ------------------------------------------------------------------
    # Capacitor / Cordova / Ionic mobile app origins.
    #
    # When the H5 frontend is packaged as a native app via Capacitor, the
    # WebView loads pages from a synthetic origin (NOT from your real
    # domain), and every API request to the production backend is treated
    # as cross-origin by the WebView. The exact origin depends on the
    # Capacitor server config:
    #   - Android (capacitor 6, androidScheme="https") → https://localhost
    #   - Android (capacitor 5 or scheme="http")      → http://localhost
    #   - iOS (capacitor 6, iosScheme="https")        → capacitor://localhost
    #   - iOS legacy (ionic://)                       → ionic://localhost
    #   - Cordova / file:// loaded apps               → null  (Origin: null)
    #
    # We always allow these so a packaged QuantDinger mobile app can call
    # the backend without each user editing FRONTEND_URL. They are *fixed
    # synthetic origins controlled by the OS / Capacitor*, not user input,
    # so this does not widen exposure to real third-party sites.
    _capacitor_origins = [
        "https://localhost",        # Android, Capacitor 6, androidScheme=https
        "http://localhost",         # Android legacy
        "capacitor://localhost",    # iOS, Capacitor 6, iosScheme=capacitor
        "ionic://localhost",        # iOS legacy / Ionic
        "https://localhost:*",      # rare custom port
        "http://localhost:*",
    ]
    for origin in _capacitor_origins:
        if origin not in _cors_origins:
            _cors_origins.append(origin)

    # send_wildcard=False + supports_credentials=False is the safe default
    # for token-in-Authorization-header auth (which is what the mobile app
    # uses; see api/index.js → `Authorization: Bearer ${token}`).
    CORS(
        app,
        origins=_cors_origins,
        supports_credentials=False,
        send_wildcard=False,
    )
    logger.info(f"CORS allowed origins: {_cors_origins}")

    setup_logger()

    from app.utils.auth import _configure_jwt_secret_warnings
    _configure_jwt_secret_warnings()

    # ib_insync uses asyncio across Flask + worker threads; without this, IBKR
    # sockets often drop within seconds (nested loop / thread handoff issues).
    try:
        from ib_insync import util as _ib_util

        _ib_util.patchAsyncio()
        logger.info("ib_insync: patchAsyncio enabled for stable IBKR connections")
    except Exception as _ib_exc:
        logger.debug(f"ib_insync patchAsyncio skipped (ib_insync not installed?): {_ib_exc}")
    
    # Initialize database and ensure admin user exists
    try:
        from app.utils.db import init_database, get_db_type
        logger.info(f"Database type: {get_db_type()}")
        init_database()
        
        # Ensure admin user exists (multi-user mode)
        from app.services.user_service import get_user_service
        get_user_service().ensure_admin_exists()
    except Exception as e:
        logger.warning(f"Database initialization note: {e}")

    from app.routes import register_routes
    register_routes(app)
    
    # Startup hooks (workers, strategy restore). Skipped for OpenAPI export / CI.
    skip_hooks = os.getenv("SKIP_STARTUP_HOOKS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not skip_hooks:
        with app.app_context():
            start_pending_order_worker()
            start_grid_fill_poller()
            start_portfolio_monitor()
            start_usdt_order_worker()
            # Offline calibration to make AI thresholds self-tuning.
            try:
                from app.services.ai_calibration import start_ai_calibration_worker
                start_ai_calibration_worker()
            except Exception:
                pass
            # Reflection worker: validate past decisions, run calibration periodically.
            try:
                from app.services.reflection import start_reflection_worker
                start_reflection_worker()
            except Exception:
                pass
            restore_running_strategies()
    
    return app

