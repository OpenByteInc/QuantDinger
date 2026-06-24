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
import html
import hmac
import hashlib
import json
import os
import smtplib
import time
import urllib.parse
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

import requests

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.notification_display import with_display

logger = get_logger(__name__)


# ============================================================
# Webhook dialect detection & payload adaptation
# ============================================================
#
# QuantDinger's webhook channel was originally generic: it POSTed our
# own JSON schema (event/strategy/instrument/signal/order/...) to whatever
# URL the user supplied. That works fine for self-hosted automation
# endpoints, but most users actually point this at a group-chat bot —
# Feishu/Lark, DingTalk, WeCom, Slack. Those bots reject any envelope
# that isn't theirs and (worse for Feishu/DingTalk/WeCom) typically
# return HTTP 200 with an error body, making the failure silent: the
# user clicks "Test", sees a success toast, and nothing arrives in the
# group.
#
# The helpers below auto-detect the dialect from the URL host and
# translate our payload into the vendor's required schema. For
# generic/self-hosted URLs we keep emitting the original schema so
# existing integrations keep working untouched.

_WEBHOOK_DIALECT_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ('feishu', (
        'open.feishu.cn/open-apis/bot/v2/hook/',
        'open.larksuite.com/open-apis/bot/v2/hook/',
        'open.larkoffice.com/open-apis/bot/v2/hook/',
        'www.larksuite.com/open-apis/bot/v2/hook/',
    )),
    ('dingtalk', ('oapi.dingtalk.com/robot/send',)),
    ('wecom', ('qyapi.weixin.qq.com/cgi-bin/webhook/send',)),
    ('slack', ('hooks.slack.com/services/',)),
)


def _detect_webhook_dialect(url: str) -> str:
    """
    Sniff the URL and return the vendor dialect name, or 'generic' for
    self-hosted endpoints. Keep the prefix check substring-based so
    minor URL variations (regional subdomains, query suffixes) still
    match.
    """
    u = (url or '').lower()
    for dialect, prefixes in _WEBHOOK_DIALECT_PATTERNS:
        if any(p in u for p in prefixes):
            return dialect
    return 'generic'


def _shorten(s: str, limit: int = 4000) -> str:
    s = str(s or '')
    return s if len(s) <= limit else (s[:limit] + '…')


def _build_webhook_text(payload: Dict[str, Any]) -> Tuple[str, str]:
    """
    Distill our internal payload into (title, body) plain text suitable
    for forwarding to a group-chat bot. We deliberately do NOT trust
    the bot to render arbitrary HTML — Feishu accepts markdown only in
    "post"/"interactive" envelopes, not in "text". Markdown bullets are
    kept lightweight (newline-separated key/value pairs) so they look
    OK in every vendor that supports text or markdown.
    """
    p = payload or {}

    explicit_title = str(p.get('title') or '').strip()
    explicit_msg = str(p.get('message') or '').strip()
    if explicit_title or explicit_msg:
        return (explicit_title or 'QuantDinger'), (explicit_msg or '')

    strategy = p.get('strategy') or {}
    instrument = p.get('instrument') or {}
    sig = p.get('signal') or {}
    order = p.get('order') or {}

    sname = str(strategy.get('name') or '').strip()
    sym = str(instrument.get('symbol') or '').strip()
    stype = str(sig.get('type') or sig.get('action') or '').strip()
    side = str(sig.get('side') or '').strip()

    title_bits: List[str] = []
    if sname:
        title_bits.append(sname)
    if sym:
        title_bits.append(sym)
    if stype:
        title_bits.append(stype.upper())
    title = ' · '.join(title_bits) if title_bits else 'QuantDinger 信号'

    body_lines: List[str] = []
    if sname:
        body_lines.append(f"策略: {sname}")
    if sym:
        body_lines.append(f"标的: {sym}")
    if stype:
        body_lines.append(f"信号: {stype}")
    if side:
        body_lines.append(f"方向: {side}")
    try:
        ref_price = float(order.get('ref_price') or 0)
        if ref_price > 0:
            body_lines.append(f"价格: {_fmt_float(ref_price)}")
    except Exception:
        pass
    try:
        stake = float(order.get('stake_amount') or 0)
        if stake > 0:
            body_lines.append(f"金额: {_fmt_float(stake)}")
    except Exception:
        pass
    ts_iso = str(p.get('timestamp_iso') or '').strip()
    if ts_iso:
        body_lines.append(f"时间: {ts_iso}")

    if not body_lines:
        body_lines.append(_shorten(json.dumps(p, ensure_ascii=False), 800))

    return title, "\n".join(body_lines)


def _adapt_payload_for_dialect(dialect: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Translate the internal payload to the vendor's required JSON.

    Each branch is documented inline because the field names differ
    across platforms in ways that are easy to mis-remember:

    - 飞书/Lark text 机器人: ``{msg_type: 'text', content: {text}}``
    - 钉钉机器人 markdown:    ``{msgtype: 'markdown', markdown: {title, text}}``
    - 企微/WeCom markdown:    ``{msgtype: 'markdown', markdown: {content}}``
    - Slack incoming webhook: ``{text}``
    """
    title, body = _build_webhook_text(payload)

    if dialect == 'feishu':
        # Feishu/Lark text content cap is around 30k chars but the chat
        # UI gets unreadable far before that; clamp at ~4k.
        return {
            "msg_type": "text",
            "content": {"text": f"{title}\n{_shorten(body)}"},
        }
    if dialect == 'dingtalk':
        # DingTalk markdown body must be non-empty *and* contain at
        # least one of the keywords the bot was created with — but
        # that's a user-config issue we can't fix here. The "###" prefix
        # at least makes title visible.
        return {
            "msgtype": "markdown",
            "markdown": {"title": _shorten(title, 64), "text": f"### {title}\n\n{_shorten(body)}"},
        }
    if dialect == 'wecom':
        return {
            "msgtype": "markdown",
            "markdown": {"content": f"### {title}\n\n{_shorten(body, 4000)}"},
        }
    if dialect == 'slack':
        return {"text": f"*{title}*\n{_shorten(body)}"}
    return payload


def _feishu_sign(secret: str, timestamp_str: str) -> str:
    """
    Feishu custom-bot signing algorithm.

    Algorithm (per docs):
      key = timestamp + "\\n" + secret  (utf-8)
      digest = HMAC-SHA256(key, b"")
      sign = base64(digest)

    The timestamp and sign are then placed *inside* the JSON body, not
    in headers. See:
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    key = f"{timestamp_str}\n{secret}".encode('utf-8')
    digest = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode('utf-8')


def _dingtalk_signed_url(url: str, secret: str) -> str:
    """
    DingTalk custom-bot signing.

    Algorithm:
      string_to_sign = timestamp_ms + "\\n" + secret
      digest = HMAC-SHA256(secret, string_to_sign)
      sign = url_encode(base64(digest))
    Then append &timestamp=...&sign=... to the URL.
    """
    ts_ms = str(int(time.time() * 1000))
    string_to_sign = f"{ts_ms}\n{secret}"
    digest = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode('utf-8'))
    sep = '&' if ('?' in url) else '?'
    return f"{url}{sep}timestamp={ts_ms}&sign={sign}"


def _check_vendor_response(dialect: str, status_code: int, text: str) -> Tuple[bool, str]:
    """
    Group-chat bots typically return HTTP 200 even on logical failures
    (invalid signature, wrong msg_type, missing keyword, etc.) and put
    the real result inside the JSON body. This checker normalises
    that so a "silent failure" actually surfaces as an error to the
    caller.

    Vendor success codes:
      - 飞书:   {"code": 0, "msg": "ok"} or {"StatusCode": 0}
      - 钉钉:   {"errcode": 0, "errmsg": "ok"}
      - 企微:   {"errcode": 0, "errmsg": "ok"}
      - Slack:  plain text "ok" with HTTP 200
    """
    if status_code < 200 or status_code >= 300:
        return False, f"http_{status_code}:{_shorten(text, 300)}"

    body = (text or '').strip()
    if dialect == 'slack':
        return (body.lower() == 'ok' or body.startswith('{')), (
            '' if (body.lower() == 'ok' or body.startswith('{')) else f"slack_unexpected:{_shorten(body, 300)}"
        )

    if dialect in ('feishu', 'dingtalk', 'wecom'):
        if not body or not body.startswith('{'):
            # Non-JSON 200 — vendor SDK still sometimes does this.
            return True, ""
        try:
            obj = json.loads(body)
        except Exception:
            return True, ""
        code = obj.get('code', obj.get('errcode', obj.get('StatusCode')))
        if code in (0, '0', None):
            return True, ""
        msg = obj.get('msg') or obj.get('errmsg') or ''
        return False, f"{dialect}_error:code={code}:{_shorten(msg, 200)}"

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


def _load_user_timezone_for_strategy(strategy_id: int) -> str:
    try:
        sid = int(strategy_id)
    except Exception:
        return ""
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COALESCE(u.timezone, '') AS tz
                FROM qd_strategies_trading s
                JOIN qd_users u ON u.id = s.user_id
                WHERE s.id = ?
                """,
                (sid,),
            )
            row = cur.fetchone() or {}
            cur.close()
        return str(row.get("tz") or "").strip()
    except Exception:
        return ""


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


def _fmt_float(value: Any, *, max_decimals: int = 10) -> str:
    try:
        v = float(value or 0.0)
    except Exception:
        v = 0.0
    s = f"{v:.{int(max_decimals)}f}"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


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
        cfg = _safe_json(notification_config or {})
        channels = _as_list(cfg.get("channels"))
        if not channels:
            channels = ["browser"]

        targets = _safe_json(cfg.get("targets") or {})
        extra = extra if isinstance(extra, dict) else {}

        user_tz = _load_user_timezone_for_strategy(int(strategy_id))
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
        rendered = self._render_messages(payload)
        title = rendered.get("title") or ""
        message_plain = rendered.get("plain") or ""

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
                        parse_mode="HTML",
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

    def _render_messages(self, payload: Dict[str, Any]) -> Dict[str, str]:
        strategy = (payload or {}).get("strategy") or {}
        instrument = (payload or {}).get("instrument") or {}
        sig = (payload or {}).get("signal") or {}
        order = (payload or {}).get("order") or {}
        trace = (payload or {}).get("trace") or {}

        symbol = str(instrument.get("symbol") or "")
        stype = str(sig.get("type") or "")
        action = str(sig.get("action") or "").upper()
        side = str(sig.get("side") or "").upper()
        title = f"QD Signal | {symbol} | {action} {side}".strip()

        price_s = _fmt_float(order.get("ref_price") or 0.0, max_decimals=10)
        stake_s = _fmt_float(order.get("stake_amount") or 0.0, max_decimals=12)
        pending_id = int(trace.get("pending_order_id") or 0) if trace.get("pending_order_id") else 0
        mode = str(trace.get("mode") or "")
        ts_iso = str(payload.get("timestamp_iso") or "")
        ts_disp = str(payload.get("timestamp_display") or "") or ts_iso
        ts_lbl = str(payload.get("time_label") or "Time")

        plain_lines = [
            "QuantDinger Signal",
            f"Strategy: {strategy.get('name') or ''} (#{int(strategy.get('id') or 0)})",
            f"Symbol: {symbol}",
            f"Signal: {stype}",
            f"Price: {price_s}",
            f"Stake: {stake_s}",
        ]
        if pending_id:
            plain_lines.append(f"PendingOrder: {pending_id}")
        if mode:
            plain_lines.append(f"Mode: {mode}")
        if ts_disp:
            plain_lines.append(f"{ts_lbl}: {ts_disp}")

        # Telegram (HTML) message. Escape all dynamic values.
        t_strategy = f"{strategy.get('name') or ''} (#{int(strategy.get('id') or 0)})"
        telegram_lines = [
            "<b>QuantDinger Signal</b>",
            "",
            f"<b>Strategy</b>: <code>{html.escape(str(t_strategy))}</code>",
            f"<b>Symbol</b>: <code>{html.escape(symbol)}</code>",
            f"<b>Signal</b>: <code>{html.escape(stype)}</code>",
            f"<b>Price</b>: <code>{html.escape(price_s)}</code>",
            f"<b>Stake</b>: <code>{html.escape(stake_s)}</code>",
        ]
        if pending_id:
            telegram_lines.append(f"<b>PendingOrder</b>: <code>{pending_id}</code>")
        if mode:
            telegram_lines.append(f"<b>Mode</b>: <code>{html.escape(mode)}</code>")
        if ts_disp:
            telegram_lines.append(f"<b>{html.escape(ts_lbl)}</b>: <code>{html.escape(ts_disp)}</code>")
        telegram_html = "\n".join([x for x in telegram_lines if x is not None])

        # Email (HTML) message. Keep inline CSS for maximum compatibility.
        email_html = self._build_email_html(
            title_text="QuantDinger Signal",
            strategy_text=t_strategy,
            symbol=symbol,
            signal_type=stype,
            price_text=price_s,
            stake_text=stake_s,
            pending_id=pending_id or None,
            mode_text=mode or "",
            timestamp_display=ts_disp or "",
            time_row_label=ts_lbl or "Time",
        )

        return {
            "title": title,
            "plain": "\n".join(plain_lines),
            "telegram_html": telegram_html,
            "email_html": email_html,
        }

    def _build_email_html(
        self,
        *,
        title_text: str,
        strategy_text: str,
        symbol: str,
        signal_type: str,
        price_text: str,
        stake_text: str,
        pending_id: Optional[int],
        mode_text: str,
        timestamp_display: str,
        time_row_label: str,
    ) -> str:
        def esc(s: Any) -> str:
            return html.escape(str(s or ""))

        rows: List[Tuple[str, str]] = [
            ("Strategy", strategy_text),
            ("Symbol", symbol),
            ("Signal", signal_type),
            ("Price", price_text),
            ("Stake", stake_text),
        ]
        if pending_id:
            rows.append(("PendingOrder", str(int(pending_id))))
        if mode_text:
            rows.append(("Mode", mode_text))
        if timestamp_display:
            rows.append((time_row_label or "Time", timestamp_display))

        tr_html = "\n".join(
            [
                (
                    "<tr>"
                    "<td style='padding:10px 12px;border-top:1px solid #eaecef;color:#57606a;width:160px;'>"
                    f"{esc(k)}"
                    "</td>"
                    "<td style='padding:10px 12px;border-top:1px solid #eaecef;color:#24292f;font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace;'>"
                    f"{esc(v)}"
                    "</td>"
                    "</tr>"
                )
                for (k, v) in rows
            ]
        )

        return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f8fa;">
    <div style="max-width:640px;margin:0 auto;padding:24px;">
      <div style="background:#111827;color:#ffffff;padding:16px 18px;border-radius:12px 12px 0 0;">
        <div style="font-size:16px;letter-spacing:0.2px;font-weight:600;">{esc(title_text)}</div>
        <div style="margin-top:6px;font-size:12px;color:#d1d5db;">{esc(timestamp_display) if timestamp_display else ""}</div>
      </div>
      <div style="background:#ffffff;border:1px solid #eaecef;border-top:0;border-radius:0 0 12px 12px;overflow:hidden;">
        <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
          {tr_html}
        </table>
        <div style="padding:14px 16px;color:#6e7781;font-size:12px;border-top:1px solid #eaecef;">
          Generated by QuantDinger
        </div>
      </div>
    </div>
  </body>
</html>
"""

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
            now = int(time.time())
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
        if not (str(url).startswith("http://") or str(url).startswith("https://")):
            return False, "invalid_webhook_url"

        dialect = _detect_webhook_dialect(url)
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
        post_url = url
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
                post_url = _dingtalk_signed_url(url, signing_secret)
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
                return requests.post(post_url, data=post_body_bytes, headers=headers, timeout=timeout)
            return requests.post(post_url, json=adapted_payload, headers=headers, timeout=timeout)

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
        if not (str(url).startswith("http://") or str(url).startswith("https://")):
            return False, "invalid_discord_webhook_url"

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

        embed: Dict[str, Any] = {
            "title": "QuantDinger Signal",
            "color": int(color),
            "fields": [
                {"name": "Strategy", "value": f"{strategy.get('name') or ''} (#{int(strategy.get('id') or 0)})", "inline": True},
                {"name": "Symbol", "value": str(instrument.get("symbol") or ""), "inline": True},
                {"name": "Signal", "value": str(sig.get("type") or ""), "inline": False},
                {"name": "Price", "value": str(float(order.get('ref_price') or 0.0)), "inline": True},
                {"name": "Stake", "value": str(float(order.get('stake_amount') or 0.0)), "inline": True},
            ],
        }
        if payload.get("timestamp_iso"):
            embed["timestamp"] = str(payload.get("timestamp_iso") or "")
        if trace.get("pending_order_id"):
            embed["footer"] = {"text": f"pending_order_id={int(trace.get('pending_order_id'))}"}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "QuantDinger/1.0 (+https://www.quantdinger.com)",
        }

        def _post(payload_json: Dict[str, Any]) -> requests.Response:
            return requests.post(url, json=payload_json, headers=headers, timeout=self.timeout_sec)

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
        lang = (language or "en-US").strip().lower()
        zh = lang.startswith("zh")
        title = "QuantDinger 通知测试" if zh else "QuantDinger notification test"
        plain = (
            "这是一条来自 QuantDinger 个人中心「通知设置」的测试消息。若您收到本条消息，说明该渠道配置正确。"
            if zh
            else "This is a test message from QuantDinger profile notification settings. "
            "If you received this, the channel is configured correctly."
        )
        html_body = f"<p>{html.escape(plain)}</p>"
        telegram_html = f"<b>{html.escape(title)}</b>\n\n{html.escape(plain)}"

        now = int(time.time())
        iso = datetime.now(timezone.utc).isoformat()
        test_payload: Dict[str, Any] = with_display(
            {
                "event": "qd.profile_test",
                "version": 1,
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


