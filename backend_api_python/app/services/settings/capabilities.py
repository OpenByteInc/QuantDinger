"""Small deployment capability switches shared by API and UI configuration."""

import os
from typing import Mapping, Optional


def _flag(values: Optional[Mapping[str, str]], key: str, default: bool) -> bool:
    raw = values.get(key) if values is not None else None
    if raw is None and values is None:
        raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_feature_flags(values: Optional[Mapping[str, str]] = None) -> dict[str, bool]:
    """Return the non-sensitive product capabilities exposed to signed-in clients."""
    return {
        "billing_enabled": _flag(values, "BILLING_ENABLED", False),
        "marketplace_enabled": _flag(values, "MARKETPLACE_ENABLED", False),
        "registration_enabled": _flag(values, "ENABLE_REGISTRATION", True),
        "broker_accounts_enabled": _flag(values, "BROKER_ACCOUNTS_ENABLED", True),
        "live_trading_enabled": _flag(values, "LIVE_TRADING_ENABLED", True),
        "agent_gateway_enabled": _flag(values, "AGENT_GATEWAY_ENABLED", True),
    }


def is_marketplace_enabled(values: Optional[Mapping[str, str]] = None) -> bool:
    return get_feature_flags(values)["marketplace_enabled"]


def is_live_trading_enabled(values: Optional[Mapping[str, str]] = None) -> bool:
    return get_feature_flags(values)["live_trading_enabled"]


def is_agent_gateway_enabled(values: Optional[Mapping[str, str]] = None) -> bool:
    return get_feature_flags(values)["agent_gateway_enabled"]
