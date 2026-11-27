"""Runtime refresh helpers after settings changes."""

from __future__ import annotations

import importlib
import os

from dotenv import load_dotenv

from app.services.settings.env_file import ENV_FILE_PATH
from app.utils.logger import get_logger

logger = get_logger(__name__)

_PROCESS_OWNED_ENV_KEYS = (
    "EQUITY_QUOTE_MAX_AGE_MINUTES",
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


def reload_runtime_env() -> None:
    """Reload .env files into the current process."""
    process_owned = {
        key: os.environ[key]
        for key in _PROCESS_OWNED_ENV_KEYS
        if key in os.environ
    }
    process_owned.setdefault("EQUITY_QUOTE_MAX_AGE_MINUTES", "16")

    load_dotenv(ENV_FILE_PATH, override=True)
    # Runtime topology belongs to the process supervisor (Docker/systemd), not
    # to the mutable settings file.
    os.environ.update(process_owned)
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
