"""User preference and self-service account helpers."""

import json
import time
from typing import Any

from app.services.signal_notifier import SignalNotifier
from app.services.user_service import get_user_service
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

VALID_NOTIFICATION_CHANNELS = {"browser", "email", "telegram", "discord", "webhook", "phone"}


def ensure_chart_templates_column() -> None:
    """Compatibility hook; canonical schema changes run in migrations."""


def get_notification_settings(user_id: int) -> dict | None:
    """Read notification settings with frontend-friendly defaults."""
    row = _fetch_user_settings_row(user_id)
    if not row:
        return None
    settings = _loads_dict(row.get("notification_settings") or "")
    settings.setdefault("default_channels", ["browser"])
    settings.setdefault("email", row.get("email") or "")
    return settings


def update_notification_settings(user_id: int, data: dict[str, Any]) -> dict:
    """Validate and persist notification settings."""
    channels = data.get("default_channels", [])
    if not isinstance(channels, list):
        channels = ["browser"]
    channels = [str(c) for c in channels if str(c) in VALID_NOTIFICATION_CHANNELS]
    if not channels:
        channels = ["browser"]

    settings = {
        "default_channels": channels,
        "telegram_bot_token": str(data.get("telegram_bot_token") or "").strip(),
        "telegram_chat_id": str(data.get("telegram_chat_id") or "").strip(),
        "email": str(data.get("email") or "").strip(),
        "discord_webhook": str(data.get("discord_webhook") or "").strip(),
        "webhook_url": str(data.get("webhook_url") or "").strip(),
        "webhook_token": str(data.get("webhook_token") or "").strip(),
        "webhook_signing_secret": str(data.get("webhook_signing_secret") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
    }
    settings = {k: v for k, v in settings.items() if v or k == "default_channels"}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE qd_users SET notification_settings = ?, updated_at = NOW() WHERE id = ?",
            (json.dumps(settings, ensure_ascii=False), user_id),
        )
        db.commit()
        cur.close()
    return settings


def send_test_notification(user_id: int, accept_language: str = "") -> tuple[bool, str, dict]:
    """Send a test notification using saved profile settings."""
    row = _fetch_user_settings_row(user_id)
    if not row:
        return False, "User not found", {}

    settings = _loads_dict(row.get("notification_settings") or "")
    channels = settings.get("default_channels") or ["browser"]
    if not isinstance(channels, list) or not channels:
        channels = ["browser"]

    notify_email = (settings.get("email") or "").strip() or (row.get("email") or "").strip()
    targets = {
        "telegram": (settings.get("telegram_chat_id") or "").strip(),
        "telegram_bot_token": (settings.get("telegram_bot_token") or "").strip(),
        "email": notify_email,
        "phone": (settings.get("phone") or "").strip(),
        "discord": (settings.get("discord_webhook") or "").strip(),
        "webhook": (settings.get("webhook_url") or "").strip(),
        "webhook_token": (settings.get("webhook_token") or "").strip(),
        "webhook_signing_secret": (settings.get("webhook_signing_secret") or "").strip(),
    }
    language = "zh-CN" if "zh" in (accept_language or "").lower() else "en-US"
    results = SignalNotifier().send_profile_test_notifications(
        user_id=int(user_id),
        channels=channels,
        targets=targets,
        language=language,
    )

    any_ok = any((v or {}).get("ok") for v in results.values())
    failed = [k for k, v in results.items() if not (v or {}).get("ok")]
    if failed:
        err_detail = {k: (results.get(k) or {}).get("error", "") for k in failed}
        logger.warning(
            "notification_settings test: user_id=%s failed_channels=%s errors=%s",
            user_id,
            failed,
            err_detail,
        )
    if not any_ok:
        detail = "; ".join(f"{k}: {(results[k] or {}).get('error', '')}" for k in failed) or "all channels failed"
        return False, detail, {"results": results}
    if failed:
        return True, f"Sent OK; failed: {', '.join(failed)}", {"results": results}
    return True, "Test notification sent", {"results": results}


def list_chart_templates(user_id: int) -> list:
    """Return a user's chart templates newest first."""
    ensure_chart_templates_column()
    templates = _load_chart_templates(user_id)
    return sorted(
        [tpl for tpl in templates if isinstance(tpl, dict)],
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )


def save_chart_template(user_id: int, data: dict[str, Any]) -> tuple[bool, str, dict | None]:
    """Create or update a chart template."""
    name = str(data.get("name") or "").strip()
    template_id = str(data.get("template_id") or "").strip()
    indicators = data.get("indicators") or []
    if not name:
        return False, "Template name is required", None
    if len(name) > 80:
        return False, "Template name is too long", None
    if not isinstance(indicators, list):
        return False, "Indicators must be a list", None

    sanitized = [_sanitize_indicator(item) for item in indicators if isinstance(item, dict)]
    sanitized = [item for item in sanitized if item]
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    templates = _load_chart_templates(user_id)

    saved = None
    updated_templates = []
    if template_id:
        for tpl in templates:
            if isinstance(tpl, dict) and str(tpl.get("id") or "") == template_id:
                tpl = {
                    **tpl,
                    "id": template_id,
                    "name": name,
                    "indicators": sanitized,
                    "updated_at": now_iso,
                }
                saved = tpl
            updated_templates.append(tpl)
    else:
        updated_templates = [tpl for tpl in templates if isinstance(tpl, dict)]

    if saved is None:
        saved = {
            "id": f"tpl_{int(time.time() * 1000)}",
            "name": name,
            "indicators": sanitized,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        updated_templates.append(saved)

    updated_templates = sorted(
        updated_templates,
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )[:20]
    _store_chart_templates(user_id, updated_templates)
    return True, "Chart template saved", saved


def delete_chart_template(user_id: int, template_id: str) -> tuple[bool, str, dict | None]:
    """Delete a chart template by id."""
    template_id = str(template_id or "").strip()
    if not template_id:
        return False, "template_id is required", None
    templates = _load_chart_templates(user_id)
    updated = [
        tpl for tpl in templates
        if not (isinstance(tpl, dict) and str(tpl.get("id") or "") == template_id)
    ]
    _store_chart_templates(user_id, updated)
    return True, "Chart template deleted", {"template_id": template_id}


def change_user_password(user_id: int, old_password: str, new_password: str) -> tuple[bool, str, int]:
    """Change or set the current user's password."""
    if not new_password:
        return False, "New password required", 400
    if len(new_password) < 6:
        return False, "New password must be at least 6 characters", 400

    user_service = get_user_service()
    user = user_service.get_user_by_id(user_id)
    if not user:
        return False, "User not found", 404

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT password_hash FROM qd_users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        cur.close()

    password_hash = row.get("password_hash", "") if row else ""
    has_password = bool(password_hash and password_hash.strip())
    if not has_password:
        success = user_service.reset_password(user_id, new_password)
        return (True, "Password set successfully", 200) if success else (False, "Failed to set password", 500)

    if not old_password:
        return False, "Old password required", 400
    success = user_service.change_password(user_id, old_password, new_password)
    return (True, "Password changed successfully", 200) if success else (False, "Old password incorrect", 400)


def _fetch_user_settings_row(user_id: int) -> dict | None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT notification_settings, email FROM qd_users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        cur.close()
    return row


def _loads_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _load_chart_templates(user_id: int) -> list:
    ensure_chart_templates_column()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT chart_templates FROM qd_users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        cur.close()
    raw = (row.get("chart_templates") if row else "") or ""
    try:
        parsed = json.loads(raw) if raw else []
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _store_chart_templates(user_id: int, templates: list) -> None:
    ensure_chart_templates_column()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "UPDATE qd_users SET chart_templates = ?, updated_at = NOW() WHERE id = ?",
            (json.dumps(templates, ensure_ascii=False), user_id),
        )
        db.commit()
        cur.close()


def _sanitize_indicator(item: dict) -> dict | None:
    indicator_id = str(item.get("id") or "").strip()
    instance_id = str(item.get("instanceId") or "").strip()
    indicator_type = str(item.get("type") or "").strip()
    if not indicator_id or not instance_id or not indicator_type:
        return None
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    style = item.get("style") if isinstance(item.get("style"), dict) else {}
    return {
        "id": indicator_id,
        "instanceId": instance_id,
        "name": str(item.get("name") or "").strip(),
        "shortName": str(item.get("shortName") or "").strip(),
        "type": indicator_type,
        "visible": bool(item.get("visible", True)),
        "params": params,
        "style": {
            "color": str(style.get("color") or "").strip(),
            "lineWidth": int(style.get("lineWidth") or 2),
        },
    }

