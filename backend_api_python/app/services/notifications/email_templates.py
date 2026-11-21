"""Monochrome, table-based transactional emails with escaped dynamic content."""

import html
import json
from functools import lru_cache
from pathlib import Path

from app.services.settings.branding import brand_env
from app.utils.language import normalize_language


@lru_cache(maxsize=2)
def _catalog(filename):
    return json.loads(Path(__file__).with_name(filename).read_text(encoding="utf-8"))


def _brand_name():
    return " ".join(brand_env("BRAND_APP_NAME", "app_name").split())


def _bind_brand(messages, brand):
    if isinstance(messages, dict):
        return {key: _bind_brand(value, brand) for key, value in messages.items()}
    return messages.replace("{brand}", brand)


def email_messages(language="en-US"):
    locale = normalize_language(language)
    return _bind_brand(_catalog("email_messages.json")[locale], _brand_name())


def strategy_messages(language="en-US"):
    messages = _catalog("strategy_stop_messages.json")[normalize_language(language)]
    return _bind_brand(messages, _brand_name())


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _paragraph(text, *, muted=False):
    color = "#666666" if muted else "#222222"
    return f'<p style="margin:0 0 20px;color:{color};font-size:14px;line-height:1.7;overflow-wrap:anywhere;">{_esc(text)}</p>'


def _note(text):
    return ('<div style="margin:24px 0 0;padding:16px 18px;background-color:#f5f5f5;'
            'border-left:3px solid #222222;color:#444444;font-size:13px;line-height:1.7;'
            f'overflow-wrap:anywhere;">{_esc(text)}</div>')


def _rows(rows):
    cells = "".join(
        '<tr><td valign="top" width="34%" style="padding:12px 12px 12px 0;'
        'border-bottom:1px solid #eeeeee;color:#666666;font-size:13px;line-height:1.6;overflow-wrap:anywhere;word-break:break-word;">'
        f'{_esc(label)}</td><td valign="top" style="padding:12px 0;border-bottom:1px solid #eeeeee;'
        'color:#222222;font-size:14px;line-height:1.6;overflow-wrap:anywhere;word-break:break-word;">'
        f'{_esc(value)}</td></tr>' for label, value in rows
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;border-collapse:collapse;">{cells}</table>'


def _shell(title, content, language):
    locale = normalize_language(language)
    direction = "rtl" if locale == "ar-SA" else "ltr"
    alignment = "right" if direction == "rtl" else "left"
    footer = _esc(email_messages(language)["footer"])
    return f'''<!doctype html>
<html lang="{locale}" dir="{direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{_esc(title)}</title></head>
<body style="margin:0;padding:0;background-color:#f4f4f4;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;border-collapse:collapse;">
<tr><td align="center" style="padding:24px 12px;">
<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0"><tr><td><![endif]-->
<table role="presentation" dir="{direction}" width="100%" cellpadding="0" cellspacing="0" style="direction:{direction};text-align:{alignment};max-width:600px;border:1px solid #e5e5e5;border-top:3px solid #111111;background-color:#ffffff;color:#222222;font-family:Arial,'Microsoft YaHei',sans-serif;border-collapse:collapse;">
<tr><td style="padding:24px;border-bottom:1px solid #eeeeee;font-size:20px;font-weight:700;letter-spacing:-0.5px;color:#111111;overflow-wrap:anywhere;word-break:break-word;">{_esc(_brand_name())}</td></tr>
<tr><td style="padding:28px 24px 32px;">
<h1 style="margin:0 0 20px;font-size:24px;line-height:1.4;font-weight:700;color:#111111;overflow-wrap:anywhere;">{_esc(title)}</h1>
{content}
</td></tr><tr><td style="padding:18px 24px;border-top:1px solid #eeeeee;color:#777777;font-size:12px;line-height:1.6;">{footer}</td></tr>
</table><!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def render_verification_email(code, code_type, expire_minutes, language="en-US"):
    messages = email_messages(language)["verification"]
    purpose = messages["types"].get(code_type, messages["types"]["default"])
    intro = messages["intro"].format(action=purpose["action"])
    expiry = messages["expires"].format(minutes=expire_minutes)
    content = _paragraph(intro)
    content += ('<div dir="ltr" style="direction:ltr;padding:24px 12px;margin:24px 0 16px;background-color:#f5f5f5;'
                'border:1px solid #e5e5e5;text-align:center;color:#111111;'
                'font-family:Consolas,monospace;font-size:36px;line-height:1.4;'
                f'font-weight:700;letter-spacing:5px;">{_esc(code)}</div>')
    content += _paragraph(expiry, muted=True) + _note(messages["notice"])
    plain = "\n\n".join([purpose["title"], intro, str(code), expiry, messages["notice"]])
    return purpose["subject"], _shell(purpose["title"], content, language), plain


def render_detail_email(title, rows, *, subtitle="", note="", language="en-US"):
    content = _paragraph(subtitle) if subtitle else ""
    content += _rows(rows)
    if note:
        content += _note(note)
    return _shell(title, content, language)


def render_test_email(language="en-US"):
    messages = email_messages(language)
    return _shell(messages["test_title"], _paragraph(messages["test_body"]), language)


def _order_detail(order, labels, rebalance):
    status = rebalance["statuses"].get(order.get("status"), order.get("status", ""))
    action = rebalance["actions"].get(order.get("signal_type"), order.get("signal_type", ""))
    content = ('<div style="padding:16px 0;border-bottom:1px solid #eeeeee;overflow-wrap:anywhere;">'
               f'<p style="margin:0 0 6px;font-size:14px;line-height:1.6;color:#222222;"><strong>{_esc(order.get("symbol"))}</strong>'
               f' · {_esc(action)} · <strong>{_esc(status)}</strong></p>'
               f'<p style="margin:0;font-size:13px;line-height:1.7;color:#666666;">{_esc(labels["requested"])}: {_esc(order.get("amount"))}'
               f' · {_esc(labels["filled"])}: {_esc(order.get("filled"))}</p>')
    if order.get("error"):
        content += ('<p style="margin:8px 0 0;font-size:13px;line-height:1.7;color:#222222;">'
                    f'{_esc(order["error"])}</p>')
    return content + "</div>"


def render_strategy_event_email(payload, language="en-US"):
    labels = email_messages(language)
    strategy = payload["strategy"]
    rows = [(labels["strategy"], f'{strategy["name"]} (#{strategy["id"]})')]
    if payload["event"] == "qd.strategy.stopped":
        batch = payload.get("failure_unit") == "rebalance"
        rows += [(labels["batch_failures" if batch else "order_failures"], payload["consecutive_failures"]),
                 (labels["reason"], payload["reason"])]
        return render_detail_email(
            labels["stop_title"], rows, language=language,
            subtitle=labels["batch_note"] if batch else "", note=labels["stop_note"],
        )

    rebalance = strategy_messages(language)["rebalance"]
    rows += [(labels["batch"], payload["group_id"][:12]), (labels["total"], len(payload["orders"]))]
    content = _paragraph(rebalance["modes"].get(payload["execution_mode"], payload["execution_mode"]))
    content += _rows(rows)
    counts = " · ".join(f'{label}: {payload["counts"][key]}' for key, label in rebalance["statuses"].items()
                        if payload["counts"].get(key, 0))
    if counts:
        content += _note(counts)
    content += f'<h2 style="margin:28px 0 0;font-size:16px;line-height:1.5;color:#111111;">{_esc(labels["details"])}</h2>'
    content += "".join(_order_detail(order, labels, rebalance) for order in payload["orders"])
    content += _note(rebalance["footnote"])
    return _shell(labels["rebalance_title"], content, language)
