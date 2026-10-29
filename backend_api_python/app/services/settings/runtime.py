"""Runtime refresh helpers after settings changes."""

from __future__ import annotations

import importlib
import os
import threading
import time
from typing import Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

PROCESS_OWNED_ENV_KEYS = (
    # Security roots must remain identical for every worker until the process
    # group is restarted. Rotating either key in just the worker that handled a
    # settings request makes JWTs or encrypted credentials unreadable by the
    # other workers.
    "SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    "QD_PROCESS_ROLE",
    "STRATEGY_COMMANDS_ENABLED",
    "STRATEGY_MAX_ACTIVE",
    "STRATEGY_EVALUATOR_THREADS",
    "SHARED_PUBLIC_MARKET_FEED_ENABLED",
    "SHARED_PUBLIC_MARKET_FALLBACK_TTL_SEC",
    "BAR_CLOSE_EVENT_SCHEDULER_ENABLED",
    "BAR_CLOSE_EVENT_GRACE_SEC",
    "BAR_EVENT_FALLBACK_WAKE_SEC",
    "KAFKA_EVENT_PUBLISH_ENABLED",
    "STRATEGY_SHARD_COUNT",
    "STRATEGY_EVALUATION_BATCH_SIZE",
    "STRATEGY_EVALUATOR_MODE",
    "STRATEGY_DISTRIBUTED_BAR_ENABLED",
    "STRATEGY_EVALUATOR_LEASE_SEC",
    "STRATEGY_EVALUATOR_MAX_ATTEMPTS",
    "STRATEGY_EVALUATOR_BATCH_WORKERS",
    "STRATEGY_EVALUATION_TIMEOUT_SEC",
    "KAFKA_MAX_POLL_INTERVAL_MS",
    "BAR_IDLE_SCHEDULER_ENABLED",
    "BAR_IDLE_WAKE_INTERVAL_SEC",
)


RUNTIME_SETTINGS_POLL_SEC = 10

_sync_lock = threading.Lock()
_sync_started = False


def apply_runtime_settings(values: Dict[str, str]) -> None:
    """Overlay saved Settings page values onto this process environment."""
    for key, value in values.items():
        # Runtime topology and security roots belong to the process supervisor
        # (Docker/systemd/Railway), not to the mutable settings store.
        if key not in PROCESS_OWNED_ENV_KEYS:
            os.environ[key] = value


def reload_runtime_env(values: Optional[Dict[str, str]] = None) -> None:
    """Apply the latest saved settings to the current process."""
    if values is None:
        from app.services.settings.store import load_values

        values = load_values()
    apply_runtime_settings(values)
    try:
        registry = importlib.import_module("app.markets.registry")
        if hasattr(registry, "clear_runtime_env_cache"):
            registry.clear_runtime_env_cache()
    except Exception as exc:
        logger.warning("clear_runtime_env_cache skipped: %s", exc)


def refresh_runtime_services() -> None:
    """Reset singleton services so new env/config is picked up lazily."""
    try:
        search_mod = importlib.import_module("app.services.search")
        if hasattr(search_mod, "reset_search_service"):
            search_mod.reset_search_service()
    except Exception as exc:
        logger.warning("reset_search_service skipped: %s", exc)

    singleton_fields = [
        ("app.services.fast_analysis", "_fast_analysis_service"),
        ("app.services.billing_service", "_billing_service"),
        ("app.services.security_service", "_security_service"),
        ("app.services.mfa_service", "_mfa_service"),
        ("app.services.oauth_service", "_oauth_service"),
        ("app.services.user_service", "_user_service"),
        ("app.services.email_service", "_email_service"),
        ("app.services.community_service", "_community_service"),
        ("app.services.usdt_payment_service", "_svc"),
        ("app.services.usdt_payment_service", "_worker"),
        ("app.services.analysis_memory", "_memory_instance"),
    ]

    for module_name, field_name in singleton_fields:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, field_name):
                setattr(module, field_name, None)
        except Exception as exc:
            logger.warning("Singleton reset skipped: %s.%s: %s", module_name, field_name, exc)


def start_runtime_settings_sync() -> None:
    """Apply saved settings now and follow later saves from any process."""
    global _sync_started
    from app.services.settings.store import load_snapshot, snapshot_values
    from app.utils.config_loader import clear_config_cache

    version = 0
    try:
        snapshot = load_snapshot()
        version = int(snapshot["version"])
        apply_runtime_settings(snapshot_values(snapshot))
        clear_config_cache()
    except Exception as exc:
        logger.warning("Saved runtime settings unavailable at startup: %s", exc)

    with _sync_lock:
        if _sync_started:
            return
        _sync_started = True
    threading.Thread(
        target=_follow_runtime_settings,
        args=(version,),
        name="runtime-settings-sync",
        daemon=True,
    ).start()


def sync_runtime_settings(version: int) -> int:
    """Apply settings saved after ``version``; return the version now applied."""
    from app.services.settings.store import load_snapshot, snapshot_values
    from app.utils.config_loader import clear_config_cache

    snapshot = load_snapshot()
    latest = int(snapshot["version"])
    if latest != version:
        clear_config_cache()
        reload_runtime_env(snapshot_values(snapshot))
        refresh_runtime_services()
        logger.info("Runtime settings version %s applied", latest)
    return latest


def _follow_runtime_settings(version: int) -> None:
    while True:
        time.sleep(RUNTIME_SETTINGS_POLL_SEC)
        try:
            version = sync_runtime_settings(version)
        except Exception as exc:
            logger.warning("Runtime settings sync failed: %s", exc)
