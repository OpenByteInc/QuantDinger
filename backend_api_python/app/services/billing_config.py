import os
from typing import Any, Dict


BILLING_CONFIG_PREFIX = "BILLING_"

DEFAULT_BILLING_CONFIG = {
    "enabled": False,
    "cost_backtest": 30,
    "cost_ai_review": 10,
    "cost_ai_analysis": 10,
    "cost_ai_code_gen": 30,
    "cost_ai_indicator_to_strategy": 30,
    "cost_ai_tuning": 50,
    "cost_ai_copilot_chat": 5,
    "cost_ai_copilot_image": 15,
    "cost_ai_copilot_radar": 20,
}

FEATURE_NAMES = {
    "backtest": "Strategy Backtest",
    "ai_review": "AI Strategy Review",
    "ai_analysis": "AI Analysis",
    "ai_code_gen": "AI Code Generation",
    "ai_indicator_to_strategy": "AI Indicator to Strategy",
    "ai_tuning": "AI Parameter Tuning",
    "ai_copilot_chat": "AI Copilot Chat",
    "ai_copilot_image": "AI Copilot Image Analysis",
    "ai_copilot_radar": "AI Copilot Opportunity Radar",
}


def load_billing_config() -> Dict[str, Any]:
    """Read billing switches and feature costs from environment variables."""
    config: Dict[str, Any] = {}
    for key, default_value in DEFAULT_BILLING_CONFIG.items():
        env_key = f"{BILLING_CONFIG_PREFIX}{key.upper()}"
        value = os.getenv(env_key)

        if value is None or value == "":
            config[key] = default_value
        elif isinstance(default_value, bool):
            config[key] = str(value).lower() in ("true", "1", "yes")
        elif isinstance(default_value, int):
            try:
                config[key] = int(value)
            except (ValueError, TypeError):
                config[key] = default_value
        else:
            config[key] = value
    return config


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except Exception:
        return float(default)


def _int_env(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default)).strip()))
    except Exception:
        return int(default)


def load_membership_plans() -> Dict[str, Any]:
    """Read membership plan settings configured through the Settings UI."""
    return {
        "monthly": {
            "plan": "monthly",
            "price_usd": _float_env("MEMBERSHIP_MONTHLY_PRICE_USD", 19.9),
            "credits_once": _int_env("MEMBERSHIP_MONTHLY_CREDITS", 500),
            "duration_days": 30,
        },
        "yearly": {
            "plan": "yearly",
            "price_usd": _float_env("MEMBERSHIP_YEARLY_PRICE_USD", 199.0),
            "credits_once": _int_env("MEMBERSHIP_YEARLY_CREDITS", 8000),
            "duration_days": 365,
        },
        "lifetime": {
            "plan": "lifetime",
            "price_usd": _float_env("MEMBERSHIP_LIFETIME_PRICE_USD", 499.0),
            "credits_monthly": _int_env("MEMBERSHIP_LIFETIME_MONTHLY_CREDITS", 800),
        },
    }
