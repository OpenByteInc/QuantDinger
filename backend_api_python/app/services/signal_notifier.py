"""
Strategy signal notification service.

This module implements per-strategy notification channels based on the frontend schema:

notification_config = {
  "channels": ["browser", "email", "phone", "telegram", "discord", "webhook"],
  "targets": {
    "email": "foo@example.com",
    "phone": "+15551234567",
    "telegram": "12345678 or @username",
    "discord": "https://discord.com/api/webhooks/...",
    "webhook": "https://example.com/webhook"
  }
}
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import socket
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests

from app.services.notifications.email_templates import (
    email_messages,
    render_detail_email,
    render_strategy_event_email,
    render_test_email,
    strategy_messages,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.notification_display import with_display

logger = get_logger(__name__)


def _shorten(value: Any, max_len: int = 200) -> str:
    text = str(value or "")
    return text if len(text) <= max_len else text[: max(0, max_len - 3)] + "..."


def _host_matches(host: str, *allowed_hosts: str) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    return any(
        normalized == allowed or normalized.endswith(f".{allowed}")
        for allowed in allowed_hosts
    )


def _validate_outbound_http_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] = (),
) -> Tuple[str, str]:
    raw = str(url or "").strip()
    if not raw or len(raw) > 4096:
        return "", "invalid_webhook_url"
    try:
        parsed = urlsplit(raw)
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return "", "invalid_webhook_url"
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return "", "invalid_webhook_url"
    if parsed.username is not None or parsed.password is not None:
        return "", "webhook_credentials_not_allowed"
    if allowed_hosts and not _host_matches(host, *allowed_hosts):
        return "", "webhook_host_not_allowed"

    allow_private = str(os.getenv("SIGNAL_WEBHOOK_ALLOW_PRIVATE_HOSTS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not allow_private:
        try:
            addresses = {
                str(info[4][0]).split("%", 1)[0]
                for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            return "", "webhook_host_unresolvable"
        if not addresses:
            return "", "webhook_host_unresolvable"
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                return "", "webhook_private_address_blocked"
        except ValueError:
            return "", "webhook_host_unresolvable"
    return raw, ""


def _fmt_float(value: Any, max_decimals: int = 8) -> str:
    try:
        num = float(value or 0)
    except Exception:
        return "0"
    if not num:
        return "0"
    text = f"{num:.{max(0, int(max_decimals))}f}".rstrip("0").rstrip(".")
    return text or "0"


def _signal_payload_text(payload: Dict[str, Any]) -> str:
    strategy = (payload or {}).get("strategy") or {}
    instrument = (payload or {}).get("instrument") or {}
    sig = (payload or {}).get("signal") or {}
    order = (payload or {}).get("order") or {}
    trace = (payload or {}).get("trace") or {}

    symbol = str(instrument.get("symbol") or "-")
    action = str(sig.get("action") or "signal").upper()
    side = str(sig.get("side") or "").upper()
    signal_type = str(sig.get("type") or "-")
    price = _fmt_float(order.get("ref_price") or 0.0, max_decimals=10)
    stake = _fmt_float(order.get("stake_amount") or 0.0, max_decimals=12)
    strategy_text = f"{strategy.get('name') or '-'} (#{int(strategy.get('id') or 0)})"

    lines = [
        f"{email_messages()['signal_title']} - {symbol} - {' '.join([action, side]).strip()}",
        f"Strategy: {strategy_text}",
        f"Symbol: {symbol}",
        f"Signal: {signal_type}",
        f"Reference price: {price}",
        f"Stake: {stake}",
    ]
    if trace.get("pending_order_id"):
        lines.append(f"Pending order: {int(trace.get('pending_order_id'))}")
    if trace.get("mode"):
        lines.append(f"Mode: {trace.get('mode')}")
    if payload.get("timestamp_display") or payload.get("timestamp_iso"):
        label = str(payload.get("time_label") or "Time")
        lines.append(f"{label}: {payload.get('timestamp_display') or payload.get('timestamp_iso')}")
    return "\n".join(lines)


def _profile_test_text(payload: Dict[str, Any]) -> str:
    title = str((payload or {}).get("title") or email_messages()["test_title"])
    message = str((payload or {}).get("message") or "This channel is configured correctly.")
    return f"{title}\n{message}"


def _payload_text(payload: Dict[str, Any]) -> str:
    event = str((payload or {}).get("event") or "")
    if event == "qd.signal":
        return _signal_payload_text(payload)
    if event == "qd.profile_test":
        return _profile_test_text(payload)

    display = (payload or {}).get("display") or {}
    if isinstance(display, dict):
        for key in ("plain", "message", "title"):
            value = display.get(key)
            if value:
                return str(value)
    for key in ("plain", "message", "title", "event"):
        value = (payload or {}).get(key)
        if value:
            return str(value)
    return json.dumps(payload or {}, ensure_ascii=False, default=str)


def _detect_webhook_dialect(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    host = str(parsed.hostname or "").lower().rstrip(".")
    path = str(parsed.path or "")
    if _host_matches(host, "discord.com", "discordapp.com") and path.startswith("/api/webhooks/"):
        return "discord"
    if _host_matches(host, "open.feishu.cn", "open.larksuite.com"):
        return "feishu"
    if _host_matches(host, "oapi.dingtalk.com"):
        return "dingtalk"
    if _host_matches(host, "qyapi.weixin.qq.com", "work.weixin.qq.com"):
        return "wecom"
    if _host_matches(host, "hooks.slack.com"):
        return "slack"
    return "generic"


def _adapt_payload_for_dialect(dialect: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    text = _payload_text(payload)
    if dialect == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if dialect in ("dingtalk", "wecom"):
        return {"msgtype": "text", "text": {"content": text}}
    if dialect == "slack":
        return {"text": text}
    return payload or {}


def _feishu_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _dingtalk_signed_url(url: str, secret: str) -> str:
    ts = str(int(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(digest).decode("utf-8"))
    parts = urlsplit(str(url))
    sep = "&" if parts.query else ""
    query = f"{parts.query}{sep}timestamp={ts}&sign={sign}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _check_vendor_response(dialect: str, status_code: int, text: str) -> Tuple[bool, str]:
    if not (200 <= int(status_code or 0) < 300):
        return False, f"http_{status_code}:{_shorten(text, 300)}"
    try:
        data = json.loads(text or "{}")
    except Exception:
        data = {}
    if dialect in ("feishu", "dingtalk", "wecom") and isinstance(data, dict):
        code = data.get("StatusCode", data.get("code", data.get("errcode", 0)))
        if str(code) not in ("", "0", "200"):
            msg = data.get("msg") or data.get("errmsg") or data.get("StatusMessage") or text
            return False, f"vendor_{code}:{_shorten(msg, 300)}"
    return True, ""


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    # Allow comma-separated inputs.
    if "," in s:
        return [x.strip() for x in s.split(",") if x.strip()]
    return [s]


def _safe_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _signal_meta(signal_type: str) -> Dict[str, str]:
    st = (signal_type or "").strip().lower()
    action = "signal"
    if st.startswith("open_"):
        action = "open"
    elif st.startswith("add_"):
        action = "add"
    elif st.startswith("close_"):
        action = "close"
    elif st.startswith("reduce_"):
        action = "reduce"

    side = "long" if "long" in st else ("short" if "short" in st else "")
    return {"action": action, "side": side, "type": st}


def _load_user_notification_preferences_for_strategy(strategy_id: int) -> Dict[str, Any]:
    try:
        sid = int(strategy_id)
    except Exception:
        return {}
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COALESCE(u.timezone, '') AS tz, u.notification_settings
                FROM qd_strategies_trading s
                JOIN qd_users u ON u.id = s.user_id
                WHERE s.id = ?
                """,
                (sid,),
            )
            row = cur.fetchone() or {}
            cur.close()
        settings = _safe_json(row.get("notification_settings"))
        return {"timezone": str(row.get("tz") or "").strip(), "language": settings.get("language")}
    except Exception:
        return {}


def _utc_ts_to_user_display(now: int, user_timezone: str) -> Tuple[str, str, str]:
    """Return (utc_iso, display_local_str, label_for_plaintext)."""
    iso = datetime.fromtimestamp(int(now), tz=timezone.utc).isoformat()
    utz = (user_timezone or "").strip()
    if not utz:
        return iso, iso, "Time (UTC)"
    try:
        dt = datetime.fromtimestamp(int(now), tz=timezone.utc).astimezone(ZoneInfo(utz))
        return iso, dt.strftime("%Y-%m-%d %H:%M:%S"), f"Time ({utz})"
    except Exception:
        return iso, iso, "Time (UTC)"




class SignalNotifier:
    """
    Notify signal events across channels.

    通知配置说明:
    - 用户在个人中心配置自己的通知设置（telegram_bot_token, telegram_chat_id, email 等）
    - 创建策略/监控时，系统自动使用用户配置的通知目标

    公共服务配置（管理员在系统设置中配置）:
    - SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_USE_TLS
      (邮件服务，所有用户共用)
    - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
      (短信服务，所有用户共用)

    可选的环境变量:
    - SIGNAL_NOTIFY_TIMEOUT_SEC: HTTP timeout (default: 6)
    """

    def __init__(self) -> None:
        try:
            self.timeout_sec = float(os.getenv("SIGNAL_NOTIFY_TIMEOUT_SEC") or "6")
        except Exception:
            self.timeout_sec = 6.0

        self.smtp_host = (os.getenv("SMTP_HOST") or "").strip()
        try:
            self.smtp_port = int(os.getenv("SMTP_PORT") or "587")
        except Exception:
            self.smtp_port = 587
        self.smtp_user = (os.getenv("SMTP_USER") or "").strip()
        self.smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
        self.smtp_from = (os.getenv("SMTP_FROM") or self.smtp_user or "").strip()
        self.smtp_use_tls = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() == "true"
        # Some providers require implicit SSL (port 465). Support it via SMTP_USE_SSL.
        self.smtp_use_ssl = (os.getenv("SMTP_USE_SSL") or "").strip().lower() == "true"

        self.twilio_sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
        self.twilio_token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
        self.twilio_from = (os.getenv("TWILIO_FROM_NUMBER") or "").strip()

    def notify_strategy_stopped(
        self, *, strategy_id: int, strategy_name: str, reason: str,
        consecutive_failures: int, notification_config: Dict[str, Any],
        failure_unit: str = "order",
    ) -> Dict[str, Dict[str, Any]]:
        cfg = _safe_json(notification_config)
        messages = strategy_messages(cfg.get("language"))
        title = messages["title"]
        body_key = "batch_body" if failure_unit == "rebalance" else "body"
        message = messages[body_key].format(name=strategy_name, id=strategy_id, count=consecutive_failures, reason=reason)
        payload = {"event": "qd.strategy.stopped", "strategy_id": strategy_id, "reason": reason,
                   "consecutive_failures": consecutive_failures, "failure_unit": failure_unit}
        # Always leave an in-app stop notice; external channels follow opt-ins.
        channels = list(dict.fromkeys(["browser", *_as_list(cfg.get("channels"))]))
        return self._notify_strategy_event(
            strategy_id=strategy_id, strategy_name=strategy_name, signal_type="strategy_stopped",
            cfg={**cfg, "channels": channels}, title=title, message=message, payload=payload,
        )

    def notify_rebalance_summary(
        self, *, strategy_id: int, strategy_name: str, group_id: str,
        execution_mode: str, orders: List[Dict[str, Any]], notification_config: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        cfg = _safe_json(notification_config)
        messages = strategy_messages(cfg.get("language"))["rebalance"]
        counts = {key: sum(order["summary_status"] == key for order in orders) for key in messages["statuses"]}
        lines = [messages["header"].format(name=strategy_name, id=strategy_id, batch=group_id[:12], total=len(orders)),
                 messages["modes"].get(execution_mode, execution_mode),
                 " | ".join(f"{label}: {counts[key]}" for key, label in messages["statuses"].items()), ""]
        for order in orders:
            action = str(order.get("signal_type") or "")
            lines.append(messages["row"].format(
                symbol=str(order.get("symbol") or ""), action=messages["actions"].get(action, action),
                quantity=_fmt_float(order.get("amount"), 12), filled=_fmt_float(order.get("filled"), 12),
                status=messages["statuses"][order["summary_status"]],
                error=_shorten(order.get("last_error"), 300),
            ))
        lines.extend(["", messages["footnote"]])
        message = "\n".join(lines)
        payload = {
            "event": "qd.strategy.rebalance", "strategy_id": strategy_id,
            "group_id": group_id, "execution_mode": execution_mode, "counts": counts,
            "orders": [{
                "id": order.get("id"), "symbol": str(order.get("symbol") or ""),
                "signal_type": str(order.get("signal_type") or ""),
                "amount": _fmt_float(order.get("amount"), 12),
                "filled": _fmt_float(order.get("filled"), 12),
                "status": order["summary_status"], "error": _shorten(order.get("last_error"), 300),
            } for order in orders],
        }
        return self._notify_strategy_event(
            strategy_id=strategy_id, strategy_name=strategy_name, signal_type="rebalance_summary",
            cfg=cfg, title=messages["title"], message=message, payload=payload,
        )

    def _notify_strategy_event(self, *, strategy_id, strategy_name, signal_type, cfg, title, message, payload):
        payload = {**payload, "version": 1, "timestamp": int(time.time()),
                   "strategy": {"id": strategy_id, "name": strategy_name}, "title": title, "message": message}
        return self._deliver_channels(
            strategy_id=strategy_id, symbol="", signal_type=signal_type,
            cfg=cfg, channels=_as_list(cfg.get("channels")) or ["browser"], payload=payload,
            rendered={"title": title, "plain": message,
                      "email_html": render_strategy_event_email(payload, cfg.get("language", "en-US"))},
        )

    def notify_signal(
        self,
        *,
        strategy_id: int,
        strategy_name: str,
        symbol: str,
        signal_type: str,
        price: float = 0.0,
        stake_amount: float = 0.0,
        direction: str = "long",
        notification_config: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        cfg = dict(_safe_json(notification_config or {}))
        channels = _as_list(cfg.get("channels"))
        if not channels:
            channels = ["browser"]

        extra = extra if isinstance(extra, dict) else {}

        preferences = _load_user_notification_preferences_for_strategy(int(strategy_id))
        cfg["language"] = cfg.get("language") or preferences.get("language") or "en-US"
        user_tz = preferences.get("timezone", "")
        payload = self._build_payload(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            symbol=symbol,
            signal_type=signal_type,
            price=price,
            stake_amount=stake_amount,
            direction=direction,
            extra=extra,
            user_timezone=user_tz,
        )
        rendered = self._render_messages(payload, language=cfg.get("language", "en-US"))

        strategy = (payload or {}).get("strategy") or {}
        instrument = (payload or {}).get("instrument") or {}
        sig = (payload or {}).get("signal") or {}
        order = (payload or {}).get("order") or {}
        trace = (payload or {}).get("trace") or {}
        payload = with_display(
            payload,
            "signal.trade",
            {
                "strategyName": str(strategy.get("name") or ""),
                "strategyId": int(strategy.get("id") or 0),
                "symbol": str(instrument.get("symbol") or ""),
                "signalType": str(sig.get("type") or ""),
                "action": str(sig.get("action") or "").upper(),
                "side": str(sig.get("side") or "").upper(),
                "price": _fmt_float(order.get("ref_price") or 0.0, max_decimals=10),
                "stake": _fmt_float(order.get("stake_amount") or 0.0, max_decimals=12),
                "pendingOrderId": int(trace.get("pending_order_id") or 0) or "",
                "mode": str(trace.get("mode") or ""),
                "timestampDisplay": str(payload.get("timestamp_display") or ""),
                "timeLabel": str(payload.get("time_label") or "Time"),
            },
        )

        return self._deliver_channels(
            strategy_id=strategy_id, symbol=symbol, signal_type=signal_type, cfg=cfg,
            channels=channels, payload=payload, rendered=rendered,
        )

    def _deliver_channels(self, *, strategy_id, symbol, signal_type, cfg, channels, payload, rendered):
        targets = _safe_json(cfg.get("targets") or {})
        title = rendered.get("title") or ""
        message_plain = rendered.get("plain") or ""
        channels = list(dict.fromkeys(str(ch or "").strip().lower() for ch in channels))
        results: Dict[str, Dict[str, Any]] = {}
        for ch in channels:
            c = (ch or "").strip().lower()
            if not c:
                continue
            try:
                if c == "browser":
                    ok, err = self._notify_browser(
                        strategy_id=strategy_id,
                        symbol=symbol,
                        signal_type=signal_type,
                        channels=channels,
                        title=title,
                        message=message_plain,
                        payload=payload,
                    )
                elif c == "webhook":
                    url = (targets.get("webhook") or "").strip()
                    ok, err = self._notify_webhook(
                        url=url,
                        payload=payload,
                        headers_override=(targets.get("webhook_headers") or targets.get("webhookHeaders") or None),
                        token_override=(targets.get("webhook_token") or targets.get("webhookToken") or None),
                        signing_secret_override=(
                            targets.get("webhook_signing_secret")
                            or targets.get("webhookSigningSecret")
                            or None
                        ),
                    )
                elif c == "discord":
                    url = (targets.get("discord") or "").strip()
                    ok, err = self._notify_discord(url=url, payload=payload, fallback_text=message_plain)
                elif c == "telegram":
                    chat_id = (targets.get("telegram") or "").strip()
                    # User's token takes priority, then falls back to env TELEGRAM_BOT_TOKEN.
                    token_override = ""
                    try:
                        token_override = str(
                            targets.get("telegram_bot_token")
                            or targets.get("telegram_token")
                            or cfg.get("telegram_bot_token")
                            or cfg.get("telegram_token")
                            or ""
                        ).strip()
                    except Exception:
                        token_override = ""
                    ok, err = self._notify_telegram(
                        chat_id=chat_id,
                        text=rendered.get("telegram_html") or message_plain,
                        token_override=token_override,
                        parse_mode="HTML" if rendered.get("telegram_html") else "",
                    )
                elif c == "email":
                    to_email = (targets.get("email") or "").strip()
                    ok, err = self._notify_email(
                        to_email=to_email,
                        subject=title,
                        body_text=message_plain,
                        body_html=rendered.get("email_html") or "",
                    )
                elif c == "phone":
                    to_phone = (targets.get("phone") or "").strip()
                    ok, err = self._notify_phone(to_phone=to_phone, body=message_plain)
                else:
                    ok, err = False, f"unsupported_channel:{c}"
            except Exception as e:
                ok, err = False, str(e)

            results[c] = {"ok": bool(ok), "error": (err or "")}
            if not ok and c in ("webhook", "discord"):
                # Keep logs high-signal and avoid leaking full URLs (webhook URLs contain secrets).
                logger.info(
                    f"notify failed: channel={c} strategy_id={strategy_id} symbol={symbol} signal={signal_type} err={err}"
                )

        return results

    def _build_payload(
        self,
        *,
        strategy_id: int,
        strategy_name: str,
        symbol: str,
        signal_type: str,
        price: float,
        stake_amount: float,
        direction: str,
        extra: Dict[str, Any],
        user_timezone: str = "",
    ) -> Dict[str, Any]:
        now = int(time.time())
        iso, disp, tlab = _utc_ts_to_user_display(now, user_timezone)
        meta = _signal_meta(signal_type)

        pending_id = None
        try:
            pending_id = int((extra or {}).get("pending_order_id") or 0) or None
        except Exception:
            pending_id = None

        return {
            "event": "qd.signal",
            "version": 1,
            "timestamp": now,
            "timestamp_iso": iso,
            "timestamp_display": disp,
            "time_label": tlab,
            "strategy": {
                "id": int(strategy_id),
                "name": str(strategy_name or ""),
            },
            "instrument": {
                "symbol": str(symbol or ""),
            },
            "signal": {
                "type": meta.get("type") or str(signal_type or ""),
                "action": meta.get("action") or "signal",
                "side": meta.get("side") or "",
                "direction": str(direction or ""),
            },
            "order": {
                "ref_price": float(price or 0.0),
                "stake_amount": float(stake_amount or 0.0),
            },
            "trace": {
                "pending_order_id": pending_id,
                "mode": str((extra or {}).get("mode") or ""),
            },
            "extra": extra or {},
        }

    def _render_messages(self, payload: Dict[str, Any], language: str = "en-US") -> Dict[str, str]:
        messages = email_messages(language)
        labels = messages["signal_labels"]
        actions = strategy_messages(language)["rebalance"]["actions"]
        strategy = payload.get("strategy") or {}
        instrument = payload.get("instrument") or {}
        sig = payload.get("signal") or {}
        order = payload.get("order") or {}
        trace = payload.get("trace") or {}
        symbol = str(instrument.get("symbol") or "")
        stype = str(sig.get("type") or "")
        action_label = actions.get(stype, stype or labels["signal"])
        title = f'{messages["signal_title"]} | {symbol} | {action_label}'
        price_s = _fmt_float(order.get("ref_price") or 0.0, max_decimals=10)
        stake_s = _fmt_float(order.get("stake_amount") or 0.0, max_decimals=12)
        pending_id = int(trace.get("pending_order_id") or 0)
        mode = str(trace.get("mode") or "")
        mode_label = messages["signal_modes"].get(mode, mode)
        ts_disp = str(payload.get("timestamp_display") or payload.get("timestamp_iso") or "")
        original_label = str(payload.get("time_label") or "Time")
        ts_lbl = labels["time"] + original_label[4:] if original_label.startswith("Time") else original_label
        strategy_text = f"{strategy.get('name') or ''} (#{int(strategy.get('id') or 0)})"
        rows = [
            (labels["strategy"], strategy_text), (labels["symbol"], symbol),
            (labels["signal"], action_label), (labels["price"], price_s), (labels["stake"], stake_s),
        ]
        if pending_id:
            rows.append((labels["pending"], str(pending_id)))
        if mode:
            rows.append((labels["mode"], mode_label))
        if ts_disp:
            rows.append((ts_lbl, ts_disp))
        plain = "\n".join([messages["signal_title"], *[f"{key}: {value}" for key, value in rows]])
        telegram_html = "\n".join([
            f'<b>{html.escape(messages["signal_title"])}</b>', "",
            *[f"<b>{html.escape(key)}</b>: {html.escape(value)}" for key, value in rows],
            "", f'<i>{html.escape(messages["signal_note"])}</i>',
        ])
        email_html = self._build_email_html(
            title_text=messages["signal_title"], action_text=action_label,
            strategy_text=strategy_text, symbol=symbol, signal_type=action_label,
            price_text=price_s, stake_text=stake_s, pending_id=pending_id or None,
            mode_text=mode_label, timestamp_display=ts_disp, time_row_label=ts_lbl,
            language=language,
        )
        return {"title": title, "plain": plain, "telegram_html": telegram_html, "email_html": email_html}

    def _build_email_html(
        self,
        *,
        title_text: str,
        action_text: str,
        strategy_text: str,
        symbol: str,
        signal_type: str,
        price_text: str,
        stake_text: str,
        pending_id: Optional[int],
        mode_text: str,
        timestamp_display: str,
        time_row_label: str,
        language: str = "en-US",
    ) -> str:
        messages = email_messages(language)
        labels = messages["signal_labels"]
        rows: List[Tuple[str, str]] = [
            (labels["strategy"], strategy_text),
            (labels["symbol"], symbol),
            (labels["signal"], signal_type),
            (labels["price"], price_text),
            (labels["stake"], stake_text),
        ]
        if pending_id:
            rows.append((labels["pending"], str(int(pending_id))))
        if mode_text:
            rows.append((labels["mode"], mode_text))
        if timestamp_display:
            rows.append((time_row_label or labels["time"], timestamp_display))
        return render_detail_email(
            title_text, rows, subtitle=f"{symbol} · {action_text}", note=messages["signal_note"], language=language,
        )

    def _notify_browser(
        self,
        *,
        strategy_id: Optional[int] = None,
        symbol: str,
        signal_type: str,
        channels: List[str],
        title: str,
        message: str,
        payload: Dict[str, Any],
        user_id: int = None,
    ) -> Tuple[bool, str]:
        try:
            # Get user_id from strategy if not provided
            if user_id is None:
                if strategy_id is not None:
                    try:
                        with get_db_connection() as db:
                            cur = db.cursor()
                            cur.execute("SELECT user_id FROM qd_strategies_trading WHERE id = ?", (int(strategy_id),))
                            row = cur.fetchone()
                            cur.close()
                        user_id = int((row or {}).get('user_id') or 1)
                    except Exception:
                        user_id = 1
                else:
                    user_id = 1
            sid = None if strategy_id is None else int(strategy_id)
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_strategy_notifications
                    (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NOW())
                    """,
                    (
                        int(user_id),
                        sid,
                        str(symbol or ""),
                        str(signal_type or ""),
                        ",".join([str(c) for c in (channels or [])]),
                        str(title or ""),
                        str(message or ""),
                        json.dumps(payload or {}, ensure_ascii=False),
                    ),
                )
                db.commit()
                cur.close()
            return True, ""
        except Exception as e:
            logger.warning(f"browser notify persist failed: {e}")
            logger.exception("browser.error")
            return False, str(e)

    def _notify_webhook(
        self,
        *,
        url: str,
        payload: Dict[str, Any],
        headers_override: Any = None,
        token_override: Any = None,
        signing_secret_override: Any = None,
    ) -> Tuple[bool, str]:
        """
        Webhook delivery with auto-detected vendor dialect.

        URL hosts recognised automatically:
          - 飞书/Lark   open.feishu.cn / open.larksuite.com / ...
          - 钉钉机器人  oapi.dingtalk.com/robot/send
          - 企微机器人  qyapi.weixin.qq.com/cgi-bin/webhook/send
          - Slack       hooks.slack.com/services/...
          - Generic     everything else (keeps QuantDinger's own schema)

        Per-strategy overrides via notification_config.targets:
          - webhook_headers         dict / JSON string of extra headers
          - webhook_token           Bearer token (Authorization header)
          - webhook_signing_secret  signing secret. Semantics depend on
                                    dialect:
                                      * feishu  -> in-body timestamp/sign
                                      * dingtalk -> URL query timestamp/sign
                                      * generic  -> X-QD-Timestamp /
                                                    X-QD-Signature headers
                                      * wecom / slack -> ignored (those
                                        platforms use a key embedded in
                                        the URL itself)

        Retries once on 429/5xx. Vendor 200-with-error-code responses
        are surfaced as failures (see ``_check_vendor_response``).
        """
        if not url:
            return False, "missing_webhook_url"
        validated_url, validation_error = _validate_outbound_http_url(url)
        if validation_error:
            return False, validation_error

        dialect = _detect_webhook_dialect(validated_url)
        adapted_payload = _adapt_payload_for_dialect(dialect, payload)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "QuantDinger/1.0 (+https://www.quantdinger.com)",
        }

        # Per-strategy header overrides (only meaningful for generic
        # endpoints; vendor bots reject unknown headers either silently
        # or with errors, so we still allow them but warn in logs).
        wh = headers_override
        if isinstance(wh, str) and wh.strip():
            try:
                obj = json.loads(wh)
                wh = obj if isinstance(obj, dict) else None
            except Exception:
                wh = None
        if isinstance(wh, dict):
            if dialect != 'generic':
                logger.info(
                    "webhook.headers_override.ignored_by_vendor dialect=%s keys=%s",
                    dialect, list(wh.keys()),
                )
            for k, v in wh.items():
                kk = str(k or "").strip()
                if not kk:
                    continue
                headers[kk] = str(v if v is not None else "")

        # Bearer token: Discord/Slack don't use it, Feishu/Dingtalk/WeCom
        # have their own auth mechanism (secret or key-in-URL), so we
        # only attach Authorization for generic endpoints.
        tok = str(token_override or "").strip()
        if tok and dialect == 'generic' and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {tok}"

        signing_secret = str(signing_secret_override or "").strip() or (os.getenv("SIGNAL_WEBHOOK_SIGNING_SECRET") or "").strip()

        # Vendor-specific signing rewrites either the URL (dingtalk) or
        # the body (feishu). We accept failure here gracefully — if
        # the user provided a secret but the algorithm fails, we fail
        # loud rather than send unsigned.
        post_url = validated_url
        post_body_bytes: Optional[bytes] = None
        if signing_secret and dialect == 'feishu':
            try:
                ts = str(int(time.time()))
                sign = _feishu_sign(signing_secret, ts)
                signed = dict(adapted_payload or {})
                signed['timestamp'] = ts
                signed['sign'] = sign
                adapted_payload = signed
            except Exception as e:
                return False, f"feishu_sign_failed:{e}"
        elif signing_secret and dialect == 'dingtalk':
            try:
                post_url = _dingtalk_signed_url(validated_url, signing_secret)
            except Exception as e:
                return False, f"dingtalk_sign_failed:{e}"
        elif signing_secret and dialect == 'generic':
            # Original QuantDinger contract: body-bound HMAC-SHA256 hex
            # written to X-QD-Signature with X-QD-Timestamp. We send
            # raw bytes so the signed string matches exactly what is
            # transmitted (json.dumps doing different whitespace).
            try:
                ts = str(int(time.time()))
                body = json.dumps(adapted_payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                sig_base = (ts + ".").encode("utf-8") + body
                sig = hmac.new(signing_secret.encode("utf-8"), sig_base, hashlib.sha256).hexdigest()
                headers["X-QD-Timestamp"] = ts
                headers["X-QD-Signature"] = sig
                post_body_bytes = body
            except Exception as e:
                return False, f"webhook_signing_failed:{e}"
        # wecom/slack: no signing — secret embedded in URL already.

        def _post_once(timeout: float) -> requests.Response:
            if post_body_bytes is not None:
                return requests.post(
                    post_url,
                    data=post_body_bytes,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                )
            return requests.post(
                post_url,
                json=adapted_payload,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
            )

        try:
            resp = _post_once(self.timeout_sec)
            ok, err = _check_vendor_response(dialect, resp.status_code, resp.text)
            if ok:
                return True, ""

            should_retry = resp.status_code in (429, 500, 502, 503, 504)
            if should_retry:
                try:
                    time.sleep(1.0)
                except Exception:
                    pass
                resp2 = _post_once(self.timeout_sec)
                ok2, err2 = _check_vendor_response(dialect, resp2.status_code, resp2.text)
                if ok2:
                    return True, ""
                return False, err2

            return False, err
        except requests.exceptions.Timeout:
            logger.warning("webhook.timeout dialect=%s", dialect)
            return False, "timeout"
        except requests.exceptions.ConnectionError as e:
            logger.warning("webhook.connection_error dialect=%s err=%s", dialect, str(e)[:200])
            return False, f"connection_error:{_shorten(str(e), 200)}"
        except Exception as e:
            logger.exception("webhook.error dialect=%s", dialect)
            return False, str(e)

    def _notify_discord(self, *, url: str, payload: Dict[str, Any], fallback_text: str) -> Tuple[bool, str]:
        if not url:
            return False, "missing_discord_webhook_url"
        validated_url, validation_error = _validate_outbound_http_url(
            url,
            allowed_hosts=("discord.com", "discordapp.com"),
        )
        if validation_error:
            return False, validation_error
        if not str(urlsplit(validated_url).path or "").startswith("/api/webhooks/"):
            return False, "invalid_discord_webhook_url"

        if payload.get("event") in {"qd.strategy.stopped", "qd.strategy.rebalance"}:
            return self._post_discord_embed(
                url=validated_url,
                embed={"title": str(payload.get("title") or "")[:256],
                       "description": _payload_text(payload)[:3800],
                       "color": 0xE74C3C if payload["event"] == "qd.strategy.stopped" else 0x3498DB},
                fallback_text=fallback_text,
            )

        if str((payload or {}).get("event") or "") == "qd.profile_test":
            embed = {
                "title": str((payload or {}).get("title") or email_messages()["test_title"]),
                "description": str((payload or {}).get("message") or fallback_text or "Channel configured correctly.")[:3800],
                "color": 0x22C55E,
                "footer": {"text": email_messages()["test_note"]},
            }
            if (payload or {}).get("timestamp_iso"):
                embed["timestamp"] = str((payload or {}).get("timestamp_iso") or "")
            return self._post_discord_embed(url=validated_url, embed=embed, fallback_text=fallback_text)

        strategy = (payload or {}).get("strategy") or {}
        instrument = (payload or {}).get("instrument") or {}
        sig = (payload or {}).get("signal") or {}
        order = (payload or {}).get("order") or {}
        trace = (payload or {}).get("trace") or {}

        action = str(sig.get("action") or "").lower()
        color = 0x3498DB
        if action in ("open", "add"):
            color = 0x2ECC71
        if action in ("close", "reduce"):
            color = 0xE74C3C

        action_label = " ".join([
            str(sig.get("action") or "").upper(),
            str(sig.get("side") or "").upper(),
        ]).strip() or "SIGNAL"
        price_text = _fmt_float(order.get("ref_price") or 0.0, max_decimals=10)
        stake_text = _fmt_float(order.get("stake_amount") or 0.0, max_decimals=12)

        embed: Dict[str, Any] = {
            "title": f"{email_messages()['signal_title']} | {str(instrument.get('symbol') or '-')}",
            "description": f"**{action_label}**",
            "color": int(color),
            "fields": [
                {"name": "Strategy", "value": f"{strategy.get('name') or ''} (#{int(strategy.get('id') or 0)})", "inline": True},
                {"name": "Symbol", "value": str(instrument.get("symbol") or ""), "inline": True},
                {"name": "Signal", "value": str(sig.get("type") or ""), "inline": False},
                {"name": "Reference price", "value": price_text, "inline": True},
                {"name": "Stake", "value": stake_text, "inline": True},
            ],
            "footer": {"text": email_messages()["footer"]},
        }
        if payload.get("timestamp_iso"):
            embed["timestamp"] = str(payload.get("timestamp_iso") or "")
        if trace.get("pending_order_id"):
            embed["footer"] = {"text": f"{email_messages()['footer']} | pending_order_id={int(trace.get('pending_order_id'))}"}
        return self._post_discord_embed(url=validated_url, embed=embed, fallback_text=fallback_text)

    def _post_discord_embed(self, *, url: str, embed: Dict[str, Any], fallback_text: str) -> Tuple[bool, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "QuantDinger/1.0 (+https://www.quantdinger.com)",
        }

        def _post(payload_json: Dict[str, Any]) -> requests.Response:
            return requests.post(
                url,
                json=payload_json,
                headers=headers,
                timeout=self.timeout_sec,
                allow_redirects=False,
            )

        try:
            resp = _post({"content": "", "embeds": [embed]})
            if 200 <= resp.status_code < 300:
                return True, ""

            # Rate limit: retry once if Discord asks us to.
            if resp.status_code == 429:
                try:
                    data = resp.json() if resp is not None else {}
                    retry_after = float((data or {}).get("retry_after") or 1.0)
                    time.sleep(min(max(retry_after, 0.5), 3.0))
                except Exception:
                    try:
                        time.sleep(1.0)
                    except Exception:
                        pass
                resp_retry = _post({"content": "", "embeds": [embed]})
                if 200 <= resp_retry.status_code < 300:
                    return True, ""
                resp = resp_retry

            # Fallback: plain text (some servers reject embeds)
            try:
                resp2 = _post({"content": str(fallback_text or "")[:1900]})
                if 200 <= resp2.status_code < 300:
                    return True, ""
                # If fallback also fails, return the original error (more useful than fallback sometimes).
            except Exception:
                pass
            return False, f"http_{resp.status_code}:{(resp.text or '')[:300]}"
        except Exception as e:
            logger.exception("discord.error")
            return False, str(e)

    def _notify_telegram(
        self,
        *,
        chat_id: str,
        text: str,
        token_override: str = "",
        parse_mode: str = "",
    ) -> Tuple[bool, str]:
        token = (token_override or "").strip()
        if not token:
            return False, "missing_telegram_bot_token (请在个人中心配置 Telegram Bot Token)"
        if not chat_id:
            return False, "missing_telegram_chat_id"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            data: Dict[str, Any] = {
                "chat_id": chat_id,
                "text": str(text or "")[:3900],
                "disable_web_page_preview": True,
            }
            if (parse_mode or "").strip():
                data["parse_mode"] = str(parse_mode).strip()
            resp = requests.post(
                url,
                data=data,
                timeout=self.timeout_sec,
            )
            if 200 <= resp.status_code < 300:
                return True, ""
            return False, f"http_{resp.status_code}:{(resp.text or '')[:300]}"
        except Exception as e:
            logger.exception("telegram.error")
            return False, str(e)

    def _notify_email(self, *, to_email: str, subject: str, body_text: str, body_html: str = "") -> Tuple[bool, str]:
        if not to_email:
            logger.warning("email.skip: missing recipient (to_email empty)")
            return False, "missing_email_target"
        if not self.smtp_host:
            logger.warning(
                "email.skip: SMTP_HOST not configured (set in system env / admin Email settings); "
                "test notification and all outbound mail require it"
            )
            return False, "missing_SMTP_HOST"
        if not self.smtp_from:
            logger.warning("email.skip: SMTP_FROM not configured (usually same as SMTP_USER or a verified sender)")
            return False, "missing_SMTP_FROM"

        msg = EmailMessage()
        msg["From"] = self.smtp_from
        msg["To"] = to_email
        msg["Subject"] = str(subject or "Signal")
        msg.set_content(str(body_text or ""))
        if (body_html or "").strip():
            msg.add_alternative(str(body_html or ""), subtype="html")

        try:
            # Heuristic: if port is 465 and SMTP_USE_SSL is not explicitly set, assume SSL.
            use_ssl = bool(self.smtp_use_ssl) or int(self.smtp_port or 0) == 465
            smtp_timeout = max(self.timeout_sec, 20)
            if use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=smtp_timeout) as server:
                    server.ehlo()
                    if self.smtp_use_tls:
                        server.starttls()
                        server.ehlo()
                    if self.smtp_user and self.smtp_password:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            return True, ""
        except Exception as e:
            logger.exception("email.error")
            return False, str(e)

    def _notify_phone(self, *, to_phone: str, body: str) -> Tuple[bool, str]:
        # Optional provider: Twilio via REST (no extra dependency).
        if not to_phone:
            return False, "missing_phone_target"
        if not (self.twilio_sid and self.twilio_token and self.twilio_from):
            return False, "missing_TWILIO_config"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_sid}/Messages.json"
        data = {"To": to_phone, "From": self.twilio_from, "Body": str(body or "")[:1500]}
        try:
            resp = requests.post(url, data=data, auth=(self.twilio_sid, self.twilio_token), timeout=self.timeout_sec)
            if 200 <= resp.status_code < 300:
                return True, ""
            return False, f"http_{resp.status_code}:{(resp.text or '')[:300]}"
        except Exception as e:
            logger.exception("phone.error")
            return False, str(e)

    def send_profile_test_notifications(
        self,
        *,
        user_id: int,
        channels: List[str],
        targets: Dict[str, Any],
        language: str = "en-US",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Send a short test message to each selected channel (profile / notification settings).
        Used by POST /api/users/notification-settings/test.
        """
        messages = email_messages(language)
        title = messages["test_title"]
        plain = messages["test_body"]
        html_body = render_test_email(language)
        telegram_html = (
            f"<b>{html.escape(title)}</b>\n\n{html.escape(plain)}"
            f"\n\n<i>{html.escape(messages['test_note'])}</i>"
        )

        now = int(time.time())
        iso = datetime.now(timezone.utc).isoformat()
        test_payload: Dict[str, Any] = with_display(
            {
                "event": "qd.profile_test",
                "version": 1,
                "title": title,
                "message": plain,
                "timestamp": now,
                "timestamp_iso": iso,
                "strategy": {"id": 0, "name": "Profile Test"},
                "instrument": {"symbol": "TEST"},
                "signal": {"type": "profile_test", "action": "test", "side": ""},
                "order": {"ref_price": 0.0, "stake_amount": 0.0},
                "trace": {},
                "extra": {"kind": "profile_test"},
            },
            "profile.test",
            {},
        )

        results: Dict[str, Dict[str, Any]] = {}
        ch_list = _as_list(channels)
        if not ch_list:
            ch_list = ["browser"]

        for ch in ch_list:
            c = (ch or "").strip().lower()
            if not c:
                continue
            ok, err = False, ""
            try:
                if c == "browser":
                    ok, err = self._notify_browser(
                        strategy_id=None,
                        symbol="TEST",
                        signal_type="profile_test",
                        channels=ch_list,
                        title=title,
                        message=plain,
                        payload=test_payload,
                        user_id=int(user_id),
                    )
                elif c == "telegram":
                    chat_id = str((targets or {}).get("telegram") or "").strip()
                    token_override = str(
                        (targets or {}).get("telegram_bot_token")
                        or (targets or {}).get("telegram_token")
                        or ""
                    ).strip()
                    ok, err = self._notify_telegram(
                        chat_id=chat_id,
                        text=telegram_html,
                        token_override=token_override,
                        parse_mode="HTML",
                    )
                elif c == "email":
                    to_email = str((targets or {}).get("email") or "").strip()
                    ok, err = self._notify_email(
                        to_email=to_email,
                        subject=title,
                        body_text=plain,
                        body_html=html_body,
                    )
                elif c == "phone":
                    to_phone = str((targets or {}).get("phone") or "").strip()
                    ok, err = self._notify_phone(to_phone=to_phone, body=f"{title}\n\n{plain}")
                elif c == "discord":
                    url = str((targets or {}).get("discord") or "").strip()
                    ok, err = self._notify_discord(url=url, payload=test_payload, fallback_text=f"{title}\n\n{plain}")
                elif c == "webhook":
                    url = str((targets or {}).get("webhook") or "").strip()
                    tok = str((targets or {}).get("webhook_token") or "").strip()
                    sec = str(
                        (targets or {}).get("webhook_signing_secret")
                        or (targets or {}).get("webhookSigningSecret")
                        or ""
                    ).strip()
                    # The payload we pass downstream uses the same
                    # title/message contract as the strategy-signal
                    # path so vendor dialect adaptation (Feishu /
                    # DingTalk / WeCom / Slack) can extract a clean
                    # human-readable message — see _build_webhook_text.
                    wh_payload = {
                        "event": "qd.profile_test",
                        "title": title,
                        "message": plain,
                        "timestamp": now,
                        "timestamp_iso": iso,
                    }
                    ok, err = self._notify_webhook(
                        url=url,
                        payload=wh_payload,
                        token_override=tok or None,
                        signing_secret_override=sec or None,
                    )
                else:
                    ok, err = False, f"unsupported_channel:{c}"
            except Exception as e:
                ok, err = False, str(e)
            results[c] = {"ok": bool(ok), "error": (err or "")}

        return results


