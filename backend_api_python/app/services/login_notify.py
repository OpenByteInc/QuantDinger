"""
Post-login notifications: security audit log enrichment, email & in-app alerts
for new device / new region sign-ins.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from app.utils.db import get_db_connection
from app.utils.notification_display import with_display
from app.utils.logger import get_logger

logger = get_logger(__name__)

LOGIN_ACTIONS = frozenset({"login_success", "login_via_code", "oauth_login", "mfa_login_success"})


def _parse_user_agent(ua: str) -> str:
    s = (ua or "").strip()
    if not s:
        return "Unknown device"
    low = s.lower()
    if "mobile" in low or "android" in low or "iphone" in low or "ipad" in low:
        platform = "Mobile"
    elif "tablet" in low:
        platform = "Tablet"
    else:
        platform = "Desktop"
    browser = "Browser"
    for name, pat in (
        ("Edge", r"Edg/"),
        ("Chrome", r"Chrome/"),
        ("Firefox", r"Firefox/"),
        ("Safari", r"Safari/"),
    ):
        if re.search(pat, s):
            browser = name
            break
    return f"{platform} · {browser}"


def _device_fingerprint(user_agent: str) -> str:
    raw = (user_agent or "").strip() or "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address((ip or "").strip())
        return bool(
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
        )
    except ValueError:
        return True


def _lookup_geo(ip: str) -> Dict[str, str]:
    """Best-effort GeoIP via ip-api.com (no API key). Returns empty dict on failure."""
    ip = (ip or "").strip()
    if not ip or _is_private_ip(ip):
        return {}
    try:
        resp = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,regionName,city,isp", "lang": "en"},
            timeout=3,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if data.get("status") != "success":
            return {}
        country = str(data.get("country") or "").strip()
        region = str(data.get("regionName") or "").strip()
        city = str(data.get("city") or "").strip()
        isp = str(data.get("isp") or "").strip()
        parts = [p for p in (country, region, city) if p]
        location = " · ".join(parts) if parts else ""
        return {
            "country": country,
            "region": region,
            "city": city,
            "isp": isp,
            "location": location,
        }
    except Exception as e:
        logger.debug("GeoIP lookup failed for %s: %s", ip, e)
        return {}


def _location_key(geo: Dict[str, str]) -> str:
    if not geo:
        return ""
    country = (geo.get("country") or "").strip().lower()
    city = (geo.get("city") or "").strip().lower()
    if country and city:
        return f"{country}|{city}"
    return country or city


def _load_prior_login_fingerprints(user_id: int) -> Tuple[Set[str], Set[str]]:
    """Known device fingerprints and location keys from past successful logins."""
    devices: Set[str] = set()
    locations: Set[str] = set()
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT details FROM qd_security_logs
                WHERE user_id = ?
                  AND action IN ('login_success', 'login_via_code', 'oauth_login', 'mfa_login_success')
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (int(user_id),),
            )
            rows = cur.fetchall() or []
            cur.close()
        for row in rows:
            raw = row.get("details") if isinstance(row, dict) else None
            if not raw:
                continue
            try:
                det = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                continue
            if not isinstance(det, dict):
                continue
            df = str(det.get("device_fingerprint") or "").strip()
            if df:
                devices.add(df)
            lk = str(det.get("location_key") or "").strip()
            if lk:
                locations.add(lk)
    except Exception as e:
        logger.warning("load prior login fingerprints failed: %s", e)
    return devices, locations


def _action_label(action: str, details: Dict[str, Any], zh: bool = True) -> str:
    if action == "login_via_code":
        return "验证码登录" if zh else "Email code login"
    if action == "oauth_login":
        provider = str(details.get("provider") or "OAuth").title()
        return f"{provider} 登录" if zh else f"{provider} login"
    if action == "mfa_login_success":
        return "密码 + 二次验证" if zh else "Password + MFA"
    return "密码登录" if zh else "Password login"


def notify_successful_login(
    *,
    user_id: int,
    action: str,
    ip_address: str = "",
    user_agent: str = "",
    extra_details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Record login in qd_security_logs (with device/geo) and notify on new device/region.
    Best-effort; never raises to callers.
    """
    if action not in LOGIN_ACTIONS:
        action = "login_success"

    try:
        from app.services.security_service import get_security_service

        security = get_security_service()
        ua = (user_agent or "")[:500]
        device_label = _parse_user_agent(ua)
        device_fp = _device_fingerprint(ua)
        geo = _lookup_geo(ip_address or "")
        loc_key = _location_key(geo)

        prior_devices, prior_locations = _load_prior_login_fingerprints(user_id)
        is_new_device = device_fp not in prior_devices
        is_new_region = bool(loc_key) and loc_key not in prior_locations
        is_first_login = not prior_devices and not prior_locations

        details: Dict[str, Any] = {
            "device_label": device_label,
            "device_fingerprint": device_fp,
            "location": geo.get("location") or "",
            "location_key": loc_key,
            "country": geo.get("country") or "",
            "region": geo.get("region") or "",
            "city": geo.get("city") or "",
            "isp": geo.get("isp") or "",
            "is_new_device": is_new_device,
            "is_new_region": is_new_region,
            "is_first_login": is_first_login,
        }
        if extra_details:
            details.update(extra_details)

        security.log_security_event(
            action,
            int(user_id),
            ip_address,
            ua,
            details,
        )

        if is_first_login or not (is_new_device or is_new_region):
            return

        _send_login_alerts(
            user_id=int(user_id),
            action=action,
            ip_address=ip_address or "",
            device_label=device_label,
            geo=geo,
            details=details,
            is_new_device=is_new_device,
            is_new_region=is_new_region,
        )
    except Exception as e:
        logger.warning("notify_successful_login failed for user %s: %s", user_id, e)


def _send_login_alerts(
    *,
    user_id: int,
    action: str,
    ip_address: str,
    device_label: str,
    geo: Dict[str, str],
    details: Dict[str, Any],
    is_new_device: bool,
    is_new_region: bool,
) -> None:
    from app.services.user_service import get_user_service

    user = get_user_service().get_user_by_id(user_id) or {}
    nickname = user.get("nickname") or user.get("username") or "User"
    location = geo.get("location") or ip_address or "—"

    reasons: List[str] = []
    if is_new_device:
        reasons.append("newDevice")
    if is_new_region:
        reasons.append("newRegion")
    if len(reasons) >= 2:
        reason_key = "both"
    elif reasons:
        reason_key = reasons[0]
    else:
        reason_key = "unknown"

    method = _action_label(action, details, zh=False)
    reason_text = {
        "newDevice": "new device",
        "newRegion": "new region",
        "both": "new device and region",
    }.get(reason_key, "unusual sign-in")

    title = f"QuantDinger login alert · {reason_key}"
    message = (
        f"Account {nickname} signed in from a new environment.\n"
        f"Method: {method}\n"
        f"Device: {device_label}\n"
        f"Location: {location}\n"
        f"IP: {ip_address or '—'}\n"
        f"If this was not you, change your password and review exchange API permissions."
    )

    alert_payload = with_display(
        {
            "event": "security.login",
            "action": action,
            "ip": ip_address,
            "device": device_label,
            "location": location,
            "is_new_device": is_new_device,
            "is_new_region": is_new_region,
            "details": details,
        },
        "security.login",
        {
            "nickname": nickname,
            "action": action,
            "provider": str(details.get("provider") or "").strip(),
            "device": device_label,
            "location": location,
            "ip": ip_address or "—",
            "reasonKey": reason_key,
        },
    )

    # In-app (browser) notification
    try:
        from app.services.signal_notifier import SignalNotifier

        SignalNotifier()._notify_browser(
            strategy_id=None,
            symbol="SECURITY",
            signal_type="security_login",
            channels=["browser"],
            title=title,
            message=message,
            payload=alert_payload,
            user_id=user_id,
        )
    except Exception as e:
        logger.warning("login browser notify failed: %s", e)

    # Email to registered address
    email = (user.get("email") or "").strip()
    if not email or "@" not in email:
        return
    try:
        from app.services.email_service import get_email_service

        email_svc = get_email_service()
        if not email_svc.is_configured():
            return
        subject = title
        body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #d4380d;">登录安全提醒</h2>
            <p>您好，{nickname}：</p>
            <p>We detected a sign-in to your QuantDinger account from a <strong>{reason_text}</strong>:</p>
            <ul>
                <li>登录方式：{method}</li>
                <li>设备：{device_label}</li>
                <li>大致位置：{location}</li>
                <li>IP 地址：{ip_address or '—'}</li>
            </ul>
            <p>如非本人操作，请尽快登录平台修改密码，并检查交易所 API 密钥权限与白名单。</p>
            <p style="color: #888; font-size: 12px;">此邮件由系统自动发送，请勿回复。</p>
        </div>
        """
        email_svc.send_email(email, subject, body)
    except Exception as e:
        logger.warning("login email notify failed: %s", e)


def list_login_logs(
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Paginated login history for profile UI."""
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    offset = (page - 1) * page_size
    actions = tuple(LOGIN_ACTIONS)

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM qd_security_logs
                WHERE user_id = ? AND action IN ({})
                """.format(",".join("?" * len(actions))),
                (int(user_id), *actions),
            )
            total_row = cur.fetchone()
            total = int((total_row or {}).get("cnt") or 0)

            cur.execute(
                """
                SELECT id, action, ip_address, user_agent, details, created_at
                FROM qd_security_logs
                WHERE user_id = ? AND action IN ({})
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """.format(",".join("?" * len(actions))),
                (int(user_id), *actions, page_size, offset),
            )
            rows = cur.fetchall() or []
            cur.close()
    except Exception as e:
        logger.error("list_login_logs failed: %s", e)
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    items: List[Dict[str, Any]] = []
    for row in rows:
        row = dict(row or {})
        det: Dict[str, Any] = {}
        raw = row.get("details")
        if raw:
            try:
                det = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
            except Exception:
                det = {}
        ua = str(row.get("user_agent") or "")
        items.append({
            "id": row.get("id"),
            "action": row.get("action"),
            "method": _action_label(str(row.get("action") or ""), det, zh=True),
            "ip_address": row.get("ip_address") or "",
            "device": det.get("device_label") or _parse_user_agent(ua),
            "location": det.get("location") or "",
            "isp": det.get("isp") or "",
            "is_new_device": bool(det.get("is_new_device")),
            "is_new_region": bool(det.get("is_new_region")),
            "provider": det.get("provider") or "",
            "created_at": row.get("created_at"),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
