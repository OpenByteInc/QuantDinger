"""QuantDinger Python API Flask application factory."""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS

from app.startup import (
    get_pending_order_worker,
    get_trading_executor,
    restore_running_strategies,
    run_startup_hooks,
    start_grid_fill_poller,
    start_pending_order_worker,
    start_portfolio_monitor,
    start_usdt_order_worker,
)
from app.utils.logger import get_logger, setup_logger
from app.utils.timeutil import to_utc_iso


logger = get_logger(__name__)


class SafeJSONProvider(DefaultJSONProvider):
    """JSON provider that normalizes NaN/Inf and datetime values."""

    @staticmethod
    def default(o):
        if isinstance(o, datetime):
            return to_utc_iso(o)
        if isinstance(o, date):
            return o.isoformat()
        return DefaultJSONProvider.default(o)

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", self.default)
        return _safe_json_dumps(obj, **kwargs)


def _safe_json_dumps(obj, **kwargs):
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


def _configure_cors(app: Flask) -> None:
    origins = [
        o.strip() for o in os.getenv(
            "FRONTEND_URL",
            "http://localhost:8888,http://localhost:8000",
        ).split(",")
        if o.strip()
    ]
    capacitor_origins = [
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        "ionic://localhost",
        "https://localhost:*",
        "http://localhost:*",
    ]
    for origin in capacitor_origins:
        if origin not in origins:
            origins.append(origin)

    CORS(app, origins=origins, supports_credentials=False, send_wildcard=False)
    logger.info(f"CORS allowed origins: {origins}")


def _configure_ibkr_asyncio() -> None:
    try:
        from ib_insync import util as ib_util
        ib_util.patchAsyncio()
        logger.info("ib_insync: patchAsyncio enabled for stable IBKR connections")
    except Exception as exc:
        logger.debug(f"ib_insync patchAsyncio skipped (ib_insync not installed?): {exc}")


def _bootstrap_database() -> None:
    try:
        from app.utils.db import get_db_type, init_database
        logger.info(f"Database type: {get_db_type()}")
        init_database()

        from app.services.user_service import get_user_service
        get_user_service().ensure_admin_exists()

        try:
            from app.services.builtin_indicators import upgrade_builtin_indicator_samples
            upgrade_builtin_indicator_samples()
        except Exception as sample_exc:
            logger.warning(f"Builtin indicator sample upgrade skipped: {sample_exc}")
    except Exception as e:
        logger.warning(f"Database initialization note: {e}")


def create_app(config_name='default'):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.json_provider_class = SafeJSONProvider
    app.json = SafeJSONProvider(app)
    app.config['JSON_AS_ASCII'] = False

    _configure_cors(app)
    setup_logger()

    from app.utils.auth import _configure_jwt_secret_warnings
    _configure_jwt_secret_warnings()

    _configure_ibkr_asyncio()
    _bootstrap_database()

    from app.routes import register_routes
    register_routes(app)
    run_startup_hooks(app)

    return app
