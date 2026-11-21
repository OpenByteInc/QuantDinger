"""Notification target resolution for portfolio monitors."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.utils.db import get_db_connection
from app.utils.json_helpers import safe_json_loads
from app.utils.logger import get_logger
from app.utils.language import normalize_language

logger = get_logger(__name__)


def resolve_notification_delivery(user_id: int, notification_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge monitor notification config with user-level notification settings."""
    cfg: Dict[str, Any] = dict(notification_config) if isinstance(notification_config, dict) else {}
    raw_channels = cfg.get("channels")
    if isinstance(raw_channels, str):
        raw_channels = raw_channels.split(",")
    elif not isinstance(raw_channels, list):
        raw_channels = []

    channels = [str(channel).strip().lower() for channel in raw_channels if str(channel or "").strip()]
    if not channels:
        channels = ["browser"]

    targets: Dict[str, Any] = dict(cfg.get("targets") or {})
    if not (targets.get("webhook") or "").strip() and (targets.get("webhook_url") or "").strip():
        targets["webhook"] = (targets.get("webhook_url") or "").strip()
    if not (targets.get("webhook_signing_secret") or "").strip() and (targets.get("webhookSigningSecret") or "").strip():
        targets["webhook_signing_secret"] = (targets.get("webhookSigningSecret") or "").strip()
    if not (targets.get("webhook_token") or "").strip() and (targets.get("webhookToken") or "").strip():
        targets["webhook_token"] = (targets.get("webhookToken") or "").strip()

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT email, notification_settings FROM qd_users WHERE id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            cur.close()

        if not row:
            account_email = ""
            settings = {}
        else:
            account_email = (row.get("email") or "").strip()
            settings = safe_json_loads(row.get("notification_settings"), {})

        if not (targets.get("email") or "").strip():
            configured_email = (settings.get("email") or "").strip()
            targets["email"] = configured_email or account_email
        cfg["language"] = normalize_language(cfg.get("language") or settings.get("language"))
        if not (targets.get("phone") or "").strip():
            targets["phone"] = (settings.get("phone") or "").strip()
        if not (targets.get("discord") or "").strip():
            targets["discord"] = (settings.get("discord_webhook") or "").strip()
        if not (targets.get("telegram") or "").strip():
            targets["telegram"] = (settings.get("telegram_chat_id") or "").strip()
        if not (targets.get("telegram_bot_token") or "").strip():
            targets["telegram_bot_token"] = (settings.get("telegram_bot_token") or "").strip()
        if not (targets.get("webhook") or "").strip():
            targets["webhook"] = (settings.get("webhook_url") or "").strip()
        if not (targets.get("webhook_token") or "").strip():
            targets["webhook_token"] = (settings.get("webhook_token") or "").strip()
        if not (targets.get("webhook_signing_secret") or "").strip():
            targets["webhook_signing_secret"] = (settings.get("webhook_signing_secret") or "").strip()
    except Exception as exc:
        logger.warning("Failed to load notification settings for user %s: %s", user_id, exc)

    def can_deliver(channel: str) -> bool:
        if channel == "browser":
            return True
        if channel == "email":
            return bool((targets.get("email") or "").strip())
        if channel == "telegram":
            return bool((targets.get("telegram") or "").strip())
        if channel == "webhook":
            return bool((targets.get("webhook") or "").strip())
        if channel in {"phone", "discord"}:
            return bool((targets.get(channel) or "").strip())
        return False

    if not any(can_deliver(channel) for channel in channels):
        channels = list(dict.fromkeys([*channels, "browser"]))

    cfg["channels"] = channels
    cfg["targets"] = targets
    return cfg
