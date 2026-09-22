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
    "legal_user_agreement_url": "/legal/user-agreement",
    "legal_user_agreement_text": (
        "QuantDinger 用户协议(摘要)。本平台为量化研究、回测与自动化交易提供基础设施,"
        "所有 AI 产出仅供研究参考,不构成任何投资建议。用户使用本平台进行实盘交易的风险"
        "由用户自行承担。对接第三方券商/交易所前,用户须确认已阅读该平台的服务条款与"
        "风险规则。严禁使用本平台从事任何违法违规交易活动。"
    ),
    "legal_privacy_policy_url": "/legal/privacy-policy",
    "legal_privacy_policy_text": (
        "隐私政策(摘要)。QuantDinger 仅收集为提供服务所必需的最少数据,包括登录凭证、"
        "券商 API 授权信息(加密存储)、会话与操作日志。除法律强制要求外,我们不会向"
        "第三方出售用户数据。用户可随时申请导出或删除其数据。券商 API Key/Secret "
        "采用 AES-256 加密存储,前端仅展示掩码。"
    ),
}


def build_brand_config(app_version: str) -> Dict[str, object]:
    """Build public branding, legal, social, and mobile metadata."""
    social_accounts: List[Dict[str, str]] = []
    for name, icon, env_key, default_key in _social_specs():
        url = brand_env(env_key, default_key)
        if url:
            social_accounts.append({"name": name, "icon": icon, "url": url})

    return {
        "app_name": brand_env("BRAND_APP_NAME", "app_name"),
        "app_version": app_version,
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
            "user_agreement_url": brand_env(
                "BRAND_LEGAL_USER_AGREEMENT_URL",
                "legal_user_agreement_url",
            ),
            "user_agreement_text": brand_env(
                "BRAND_LEGAL_USER_AGREEMENT_TEXT",
                "legal_user_agreement_text",
            ),
            "privacy_policy_url": brand_env(
                "BRAND_LEGAL_PRIVACY_POLICY_URL",
                "legal_privacy_policy_url",
            ),
            "privacy_policy_text": brand_env(
                "BRAND_LEGAL_PRIVACY_POLICY_TEXT",
                "legal_privacy_policy_text",
            ),
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
