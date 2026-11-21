"""Transactional email rendering without SMTP or application-side effects."""

import json
import html
import re
from contextlib import nullcontext
from email import message_from_string
from inspect import unwrap
from pathlib import Path
from string import Formatter
from unittest.mock import Mock

import pytest
from flask import Flask, g, request

from app.services.email_service import EmailService
from app.services.notifications.email_templates import (
    email_messages,
    render_detail_email,
    render_strategy_event_email,
    render_test_email,
    render_verification_email,
    strategy_messages,
)
from app.services.signal_notifier import SignalNotifier
from app.utils.language import SUPPORTED_LANGS, detect_request_language, normalize_language


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
@pytest.mark.parametrize("purpose", ["register", "login", "reset_password", "change_password", "change_email", "unknown"])
def test_verification_purpose_code_expiry_and_plain_alternative(language, purpose):
    subject, body, plain = render_verification_email("012345", purpose, 7, language)
    messages = email_messages(language)["verification"]
    expected = messages["types"].get(purpose, messages["types"]["default"])
    assert subject == expected["subject"]
    assert expected["title"] in body
    assert "012345" in body and "012345" in plain
    assert messages["expires"].format(minutes=7) in body
    assert messages["notice"] in plain
    assert "<" not in plain
    assert f'lang="{language}"' in body


def test_email_palette_and_no_remote_assets():
    samples = [render_verification_email("012345", "login", 10)[1], render_test_email(),
               render_detail_email("Trade", [("Symbol", "META")])]
    for body in samples:
        colors = re.findall(r"#[0-9a-f]{6}", body)
        assert colors and all(color[1:3] == color[3:5] == color[5:7] for color in colors)
        assert "max-width:600px" in body
        assert 'width="100%"' in body
        assert 'name="viewport"' in body
        assert "linear-gradient" not in body
        assert "<img" not in body and "<script" not in body and "http" not in body


def test_all_dynamic_detail_fields_are_escaped():
    body = render_detail_email('<img src="x">', [("<label>", "<script>alert(1)</script>")],
                               subtitle="<strategy>", note="<note>")
    assert "<script>" not in body and "<img" not in body
    for expected in ["&lt;label&gt;", "&lt;script&gt;", "&lt;strategy&gt;", "&lt;note&gt;"]:
        assert expected in body


@pytest.mark.parametrize("unit", ["order", "rebalance"])
def test_stop_details_preserve_non_liquidation_warning(unit):
    body = render_strategy_event_email({
        "event": "qd.strategy.stopped", "strategy": {"id": 9, "name": "<Portfolio>"},
        "reason": "<Error>", "consecutive_failures": 5, "failure_unit": unit,
    }, "zh-CN")
    assert "&lt;Portfolio&gt;" in body and "&lt;Error&gt;" in body
    assert "不会自动平仓" in body
    assert ("连续调仓批次失败" if unit == "rebalance" else "连续下单失败") in body


def test_rebalance_snapshot_keeps_every_status_and_failure_reason():
    statuses = ["filled", "partial", "accepted", "failed", "cancelled", "pending"]
    body = render_strategy_event_email({
        "event": "qd.strategy.rebalance", "strategy": {"id": 9, "name": "Portfolio"},
        "group_id": "abc123", "execution_mode": "signal", "counts": dict.fromkeys(statuses, 1),
        "orders": [{"symbol": f"<META-{status}>", "signal_type": "close_long", "amount": "0.012345678901",
                    "filled": "0", "status": status, "error": "<insufficient balance>"} for status in statuses],
    }, "zh-CN")
    assert "未下真实订单" in body and "不等于已成交" in body
    assert "个别订单失败不会撤销" in body
    for status in statuses:
        assert f"&lt;META-{status}&gt;" in body
    assert "0.012345678901" in body and "&lt;insufficient balance&gt;" in body


def test_verification_send_uses_template_and_explicit_plain_text(monkeypatch):
    service = EmailService()
    service.code_expire_minutes = 9
    create = Mock(return_value=(True, "012345"))
    send = Mock(return_value=(True, "sent"))
    monkeypatch.setattr(service, "create_verification_code", create)
    monkeypatch.setattr(service, "send_email", send)
    assert service.send_verification_code("test@example.com", "login", "192.0.2.1") == (True, "sent")
    create.assert_called_once_with("test@example.com", "login", "192.0.2.1")
    assert "012345" in send.call_args.args[2]
    assert "9 minutes" in send.call_args.kwargs["text_body"]
    create.return_value = (False, "rate limited")
    assert service.send_verification_code("test@example.com", "login") == (False, "rate limited")
    send.assert_called_once()


def test_smtp_mime_contains_both_real_alternatives(monkeypatch):
    service = EmailService()
    service.email_enabled = True
    service.smtp_use_ssl = False
    server = Mock()
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", Mock(return_value=server))
    subject, body, plain = render_verification_email("001234", "login", 10)
    assert service.send_email("test@example.com", subject, body, text_body=plain)[0]
    message = message_from_string(server.sendmail.call_args.args[2])
    parts = message.get_payload()
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]
    assert parts[0].get_payload(decode=True).decode("utf-8") == plain
    assert parts[1].get_payload(decode=True).decode("utf-8") == body


def test_profile_test_email_uses_same_shell_without_affecting_other_channels(monkeypatch):
    notifier = SignalNotifier()
    email = Mock(return_value=(True, ""))
    telegram = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_email", email)
    monkeypatch.setattr(notifier, "_notify_telegram", telegram)
    notifier.send_profile_test_notifications(user_id=1, channels=["email", "telegram"],
                                            targets={"email": "test@example.com", "telegram": "demo"}, language="zh-CN")
    email.assert_called_once()
    telegram.assert_called_once()
    assert "max-width:600px" in email.call_args.kwargs["body_html"]
    assert "通知测试" in email.call_args.kwargs["body_text"] or "测试消息" in email.call_args.kwargs["body_text"]


def test_single_signal_email_retains_execution_details():
    body = SignalNotifier()._build_email_html(
        title_text="QuantDinger Signal", action_text="OPEN LONG", strategy_text="<Moving Average>",
        symbol="BTC/USDT", signal_type="open_long", price_text="90000", stake_text="500",
        pending_id=12, mode_text="live", timestamp_display="2026-11-21 09:00:00", time_row_label="Time (UTC)",
    )
    for value in ["&lt;Moving Average&gt;", "BTC/USDT", "90000", "500", "12", "live", "Time (UTC)"]:
        assert value in body
    assert "linear-gradient" not in body


@pytest.mark.parametrize("filename", ["email_messages.json", "strategy_stop_messages.json"])
def test_catalogs_cover_all_languages_with_identical_keys_and_placeholders(filename):
    path = Path(__file__).parents[1] / "app" / "services" / "notifications" / filename
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert set(catalog) == SUPPORTED_LANGS

    def check(reference, translated):
        if isinstance(reference, dict):
            assert isinstance(translated, dict) and reference.keys() == translated.keys()
            for key, value in reference.items():
                check(value, translated[key])
        else:
            assert isinstance(translated, str) and translated.strip()
            def fields(text):
                return {field for _, field, _, _ in Formatter().parse(text) if field}

            assert fields(reference) == fields(translated)

    for language in SUPPORTED_LANGS:
        check(catalog["en-US"], catalog[language])


@pytest.mark.parametrize("raw,expected", [
    ("zh-TW", "zh-TW"), ("zh_Hant", "zh-TW"), ("zh-HK", "zh-TW"),
    ("ja", "ja-JP"), ("ko-kr", "ko-KR"), ("vi", "vi-VN"), ("th", "th-TH"),
    ("ar-EG", "ar-SA"), ("fr-CA,fr;q=0.9", "fr-FR"), ("de", "de-DE"),
    ("ru", "ru-RU"), ("en-GB", "en-US"), ("unsupported", "en-US"), (None, "en-US"),
])
def test_locale_aliases_and_fallback(raw, expected):
    assert normalize_language(raw) == expected
    assert email_messages(raw) == email_messages(expected)


def test_arabic_email_is_rtl_but_verification_digits_remain_ltr():
    _, body, _ = render_verification_email("001234", "login", 10, "ar-SA")
    assert '<html lang="ar-SA" dir="rtl">' in body
    assert 'direction:rtl;text-align:right' in body
    assert '<div dir="ltr" style="direction:ltr;' in body
    assert ">001234</div>" in body


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
@pytest.mark.parametrize("brand", ["QuantDinger", "Acme {Trading} & Co"])
def test_all_notification_emails_use_selected_language(monkeypatch, language, brand):
    monkeypatch.setenv("BRAND_APP_NAME", brand)
    notifier = SignalNotifier()
    email = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_notify_email", email)
    monkeypatch.setattr(notifier, "_notify_browser", Mock(return_value=(True, "")))
    monkeypatch.setattr("app.services.signal_notifier._load_user_notification_preferences_for_strategy",
                        lambda _: {"timezone": "UTC", "language": language})
    cfg = {"channels": ["email"], "targets": {"email": "test@example.com"}, "language": language}
    labels = email_messages(language)
    events = strategy_messages(language)
    notifier.notify_strategy_stopped(strategy_id=1, strategy_name="Demo", reason="insufficient_balance",
                                     consecutive_failures=5, notification_config=cfg, failure_unit="rebalance")
    assert email.call_args.kwargs["subject"] == events["title"]
    assert labels["stop_title"] in email.call_args.kwargs["body_html"]
    assert email.call_args.kwargs["body_text"] == events["batch_body"].format(
        name="Demo", id=1, count=5, reason="insufficient_balance",
    )
    notifier.notify_rebalance_summary(strategy_id=1, strategy_name="Demo", group_id="batch-123",
                                     execution_mode="signal", notification_config=cfg, orders=[
                                         {"symbol": "META", "signal_type": "close_long", "amount": 1,
                                          "filled": 0, "summary_status": "accepted"},
                                     ])
    assert email.call_args.kwargs["subject"] == events["rebalance"]["title"]
    assert events["rebalance"]["modes"]["signal"] in email.call_args.kwargs["body_html"]
    assert events["rebalance"]["footnote"] in email.call_args.kwargs["body_text"]
    notifier.notify_signal(strategy_id=1, strategy_name="Demo", symbol="META", signal_type="open_long",
                           price=450, stake_amount=100, notification_config={k: v for k, v in cfg.items() if k != "language"},
                           extra={"mode": "signal"})
    assert labels["signal_title"] in email.call_args.kwargs["subject"]
    assert labels["signal_modes"]["signal"] in email.call_args.kwargs["body_text"]
    assert f'lang="{language}"' in email.call_args.kwargs["body_html"]
    notifier.send_profile_test_notifications(user_id=1, channels=cfg["channels"], targets=cfg["targets"], language=language)
    assert email.call_args.kwargs["subject"] == labels["test_title"]
    assert email.call_args.kwargs["body_text"] == labels["test_body"]
    assert f'lang="{language}"' in email.call_args.kwargs["body_html"]
    assert email.call_count == 4
    for call in email.call_args_list:
        assert brand in call.kwargs["subject"]
        assert html.escape(brand) in call.kwargs["body_html"]
        if brand != "QuantDinger":
            assert "QuantDinger" not in call.kwargs["subject"] + call.kwargs["body_html"] + call.kwargs["body_text"]


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
def test_verification_route_forwards_ui_language_without_bypassing_security(monkeypatch, language):
    from app.routes.auth import send_verification_code

    security = Mock()
    security.verify_turnstile_or_clearance.return_value = (True, "ok")
    security.can_send_verification_code.return_value = (True, "ok")
    service = Mock()
    service.is_valid_email.return_value = True
    service.send_verification_code.return_value = (True, "sent")
    monkeypatch.setattr("app.services.security_service.get_security_service", lambda: security)
    monkeypatch.setattr("app.services.email_service.get_email_service", lambda: service)
    app = Flask(__name__)
    with app.test_request_context("/api/auth/send-code", method="POST",
                                  json={"email": "test@example.com", "type": "login"},
                                  headers={"X-App-Lang": language, "Accept-Language": "en-US"}):
        assert send_verification_code().get_json()["code"] == 1
    assert service.send_verification_code.call_args.kwargs["language"] == language
    security.verify_turnstile_or_clearance.assert_called_once()
    security.can_send_verification_code.assert_called_once()


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
def test_saved_language_is_used_by_background_delivery_and_profile_tests(monkeypatch, language):
    from app.services import portfolio_monitor_notifications, signal_notifier, user_preferences

    db = Mock()
    monkeypatch.setattr(user_preferences, "get_db_connection", lambda: nullcontext(db))
    saved = user_preferences.update_notification_settings(1, {"default_channels": ["email"], "language": language})
    stored = json.loads(db.cursor().execute.call_args.args[1][0])
    assert stored == saved and saved["language"] == language
    row = {"tz": "UTC", "email": "test@example.com", "notification_settings": json.dumps(saved)}
    db.cursor().fetchone.return_value = row
    monkeypatch.setattr(portfolio_monitor_notifications, "get_db_connection", lambda: nullcontext(db))
    delivery = portfolio_monitor_notifications.resolve_notification_delivery(1, {"channels": ["email"]})
    assert delivery["language"] == language
    assert delivery["targets"]["email"] == "test@example.com"
    monkeypatch.setattr(signal_notifier, "get_db_connection", lambda: nullcontext(db))
    assert signal_notifier._load_user_notification_preferences_for_strategy(9) == {"timezone": "UTC", "language": language}
    monkeypatch.setattr(user_preferences, "_fetch_user_settings_row", lambda _: row)
    send = Mock(return_value={"email": {"ok": True}})
    monkeypatch.setattr(SignalNotifier, "send_profile_test_notifications", send)
    assert user_preferences.send_test_notification(1)[0]
    assert send.call_args.kwargs["language"] == language


@pytest.mark.parametrize("headers,body,expected", [
    ({"X-App-Lang": "ja-JP", "Accept-Language": "en-US"}, {"language": "de-DE"}, "ja-JP"),
    ({"Accept-Language": "en-US"}, {"language": "zh-TW"}, "zh-TW"),
    ({"Accept-Language": "fr-FR,fr;q=0.9"}, {}, "fr-FR"),
    ({"X-App-Lang": "unsupported", "Accept-Language": "ko-KR"}, {}, "ko-KR"),
    ({}, {}, "en-US"),
])
def test_request_language_priority(headers, body, expected):
    with Flask(__name__).test_request_context("/", headers=headers):
        assert detect_request_language(request, body) == expected


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
def test_profile_route_saves_ui_language_and_uses_it_for_test_delivery(monkeypatch, language):
    from app.routes import user

    save = Mock(return_value={"language": language})
    send = Mock(return_value=(True, "sent", {}))
    monkeypatch.setattr(user, "update_notification_settings_service", save)
    monkeypatch.setattr(user, "send_test_notification", send)
    app = Flask(__name__)
    with app.test_request_context("/", method="POST", json={"default_channels": ["email"]},
                                  headers={"X-App-Lang": language, "Accept-Language": "en-US"}):
        g.user_id = 1
        assert unwrap(user.update_notification_settings)().get_json()["code"] == 1
        assert unwrap(user.test_notification_settings)().get_json()["code"] == 1
    assert save.call_args.args[1]["language"] == language
    send.assert_called_once_with(1, language)
    with app.test_request_context("/"):
        g.user_id = 1
        unwrap(user.test_notification_settings)()
    send.assert_called_with(1, "")


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGS))
@pytest.mark.parametrize("purpose", ["register", "login", "reset_password", "change_password", "change_email", "unknown"])
def test_verification_brand_matches_public_settings_and_is_escaped(monkeypatch, language, purpose):
    from app.services.settings.branding import build_brand_config

    brand = 'Acme {Trading} <Desk> & "Co"'
    monkeypatch.setenv("BRAND_APP_NAME", brand)
    subject, body, plain = render_verification_email("012345", purpose, 10, language)
    assert build_brand_config()["app_name"] in subject
    assert html.escape(brand) in body
    assert brand not in body
    assert "QuantDinger" not in subject + body + plain
    assert "{brand}" not in subject + body + plain
    assert f'lang="{language}"' in body


@pytest.mark.parametrize("brand", [None, "", " \t\r\n"])
def test_empty_brand_falls_back_to_default(monkeypatch, brand):
    if brand is None:
        monkeypatch.delenv("BRAND_APP_NAME", raising=False)
    else:
        monkeypatch.setenv("BRAND_APP_NAME", brand)
    subject, body, _ = render_verification_email("012345", "login", 10)
    assert subject.startswith("QuantDinger - ")
    assert ">QuantDinger</td>" in body
    assert strategy_messages()["title"].startswith("QuantDinger | ")


def test_brand_refresh_does_not_mutate_cached_catalog_or_user_content(monkeypatch):
    monkeypatch.setenv("BRAND_APP_NAME", "First Brand")
    first = email_messages()
    first["signal_labels"]["strategy"] = "Modified"
    monkeypatch.setenv("BRAND_APP_NAME", "Second Brand")
    second = email_messages()
    assert second["signal_labels"]["strategy"] == "Strategy"
    assert second["signal_title"] == "Second Brand Signal"
    assert first["signal_title"] == "First Brand Signal"
    body = render_detail_email("QuantDinger strategy", [("Name", "QuantDinger custom")])
    assert "QuantDinger strategy" in body and "QuantDinger custom" in body
    assert ">Second Brand</td>" in body
    assert strategy_messages()["title"].startswith("Second Brand | ")


def test_brand_line_breaks_cannot_inject_mail_headers(monkeypatch):
    monkeypatch.setenv("BRAND_APP_NAME", "Acme\r\nBcc: nobody@example.com")
    service = EmailService()
    service.email_enabled = True
    service.smtp_use_ssl = False
    service.smtp_from = "sender@example.com"
    server = Mock()
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", Mock(return_value=server))
    subject, body, plain = render_verification_email("012345", "login", 10)
    assert "\n" not in subject and "\r" not in subject
    assert service.send_email("test@example.com", subject, body, text_body=plain)[0]
    message = message_from_string(server.sendmail.call_args.args[2])
    assert message.get("Bcc") is None
    assert message["From"] == "sender@example.com"
    assert message["To"] == "test@example.com"


@pytest.mark.parametrize("event", ["qd.signal", "qd.profile_test"])
def test_webhook_and_discord_brand_follow_settings(monkeypatch, event):
    from app.services import signal_notifier

    monkeypatch.setenv("BRAND_APP_NAME", "Acme Trading")
    monkeypatch.setattr(signal_notifier, "_validate_outbound_http_url", lambda url, **_: (url, ""))
    notifier = SignalNotifier()
    post = Mock(return_value=(True, ""))
    monkeypatch.setattr(notifier, "_post_discord_embed", post)
    payload = {"event": event, "instrument": {"symbol": "META"}, "trace": {"pending_order_id": 1}}
    fallback = signal_notifier._payload_text(payload)
    assert "Acme Trading" in fallback and "QuantDinger" not in fallback
    assert notifier._notify_discord(url="https://discord.com/api/webhooks/1/demo", payload=payload, fallback_text=fallback)[0]
    embed = post.call_args.kwargs["embed"]
    assert "Acme Trading" in embed["title"] and "Acme Trading" in embed["footer"]["text"]
    assert "QuantDinger" not in json.dumps(embed)
