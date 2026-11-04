"""Branding and public app metadata settings."""

from __future__ import annotations

import os
from typing import Dict, List


BRAND_DEFAULTS = {
    "app_name": "QuantDinger",
    "copyright": "© 2025-2026 QuantDinger. All rights reserved.",
    "contact_email": "support@quantdinger.com",
    "contact_support_url": "https://t.me/quantdinger",
    "contact_feature_request_url": "https://github.com/OpenByteInc/QuantDinger/issues",
    "contact_live_chat_url": "https://t.me/quantdinger",
    "social_github": "https://github.com/OpenByteInc/QuantDinger",
    "social_x": "https://x.com/quantdinger_en",
    "social_discord": "https://discord.com/invite/tyx5B6TChr",
    "social_telegram": "https://t.me/quantdinger",
    "social_youtube": "https://youtube.com/@quantdinger",
}


def build_brand_config() -> Dict[str, object]:
    """Build public branding, legal, social, and mobile metadata."""
    social_accounts: List[Dict[str, str]] = []
    for name, icon, env_key, default_key in _social_specs():
        url = brand_env(env_key, default_key)
        if url:
            social_accounts.append({"name": name, "icon": icon, "url": url})

    return {
        "app_name": brand_env("BRAND_APP_NAME", "app_name"),
        "copyright": brand_env("BRAND_COPYRIGHT", "copyright"),
        "logos": {
            "light": brand_env("BRAND_LOGO_LIGHT_URL"),
            "dark": brand_env("BRAND_LOGO_DARK_URL"),
            "collapsed": brand_env("BRAND_LOGO_COLLAPSED_URL"),
            "favicon": brand_env("BRAND_FAVICON_URL"),
        },
        "contact": {
            "email": brand_env("BRAND_CONTACT_EMAIL", "contact_email"),
            "support_url": brand_env("BRAND_CONTACT_SUPPORT_URL", "contact_support_url"),
            "feature_request_url": brand_env(
                "BRAND_CONTACT_FEATURE_REQUEST_URL",
                "contact_feature_request_url",
            ),
            "live_chat_url": brand_env("BRAND_CONTACT_LIVE_CHAT_URL", "contact_live_chat_url"),
        },
        "social_accounts": social_accounts,
        "legal": {
            "user_agreement_url": brand_env("BRAND_LEGAL_USER_AGREEMENT_URL"),
            "user_agreement_text": brand_env("BRAND_LEGAL_USER_AGREEMENT_TEXT"),
            "privacy_policy_url": brand_env("BRAND_LEGAL_PRIVACY_POLICY_URL"),
            "privacy_policy_text": brand_env("BRAND_LEGAL_PRIVACY_POLICY_TEXT"),
        },
        "mobile_app": {
            "latest_version": brand_env("MOBILE_APP_LATEST_VERSION"),
            "download_url": brand_env("MOBILE_APP_DOWNLOAD_URL"),
        },
    }


def brand_env(name: str, default: str = "") -> str:
    """Read a BRAND_* env var and fall back to the bundled default."""
    value = os.getenv(name, "")
    if value is None:
        value = ""
    value = value.strip()
    if value:
        return value
    return BRAND_DEFAULTS.get(default, "")


def _social_specs():
    return [
        ("GitHub", "github", "BRAND_SOCIAL_GITHUB", "social_github"),
        ("X", "x", "BRAND_SOCIAL_X", "social_x"),
        ("Discord", "discord", "BRAND_SOCIAL_DISCORD", "social_discord"),
        ("Telegram", "telegram", "BRAND_SOCIAL_TELEGRAM", "social_telegram"),
        ("YouTube", "youtube", "BRAND_SOCIAL_YOUTUBE", "social_youtube"),
    ]
