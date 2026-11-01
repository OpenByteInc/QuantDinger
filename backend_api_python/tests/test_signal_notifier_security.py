import socket

from app.services import signal_notifier as notifier_module
from app.services.signal_notifier import (
    SignalNotifier,
    _detect_webhook_dialect,
    _validate_outbound_http_url,
)


class _Response:
    status_code = 200
    text = "{}"


def _public_dns(*_args, **_kwargs):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
    ]


def test_outbound_webhook_rejects_private_and_loopback_addresses(monkeypatch):
    monkeypatch.delenv("SIGNAL_WEBHOOK_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(
        notifier_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80))
        ],
    )

    validated, error = _validate_outbound_http_url("http://localhost/internal")

    assert validated == ""
    assert error == "webhook_private_address_blocked"


def test_outbound_webhook_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setattr(notifier_module.socket, "getaddrinfo", _public_dns)

    validated, error = _validate_outbound_http_url("https://user:password@example.com/hook")

    assert validated == ""
    assert error == "webhook_credentials_not_allowed"


def test_vendor_detection_requires_real_hostname_and_path():
    assert _detect_webhook_dialect("https://discord.com/api/webhooks/1/token") == "discord"
    assert _detect_webhook_dialect("https://discord.com.attacker.example/api/webhooks/1/token") == "generic"
    assert _detect_webhook_dialect("https://attacker.example/discord.com/api/webhooks/1") == "generic"


def test_webhook_delivery_blocks_private_target_before_request(monkeypatch):
    monkeypatch.delenv("SIGNAL_WEBHOOK_ALLOW_PRIVATE_HOSTS", raising=False)
    monkeypatch.setattr(
        notifier_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.7", 443))
        ],
    )

    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("private webhook must not be requested")

    monkeypatch.setattr(notifier_module.requests, "post", unexpected_post)

    ok, error = SignalNotifier()._notify_webhook(
        url="https://internal.example/hook",
        payload={"event": "test"},
    )

    assert not ok
    assert error == "webhook_private_address_blocked"


def test_webhook_delivery_disables_redirects(monkeypatch):
    monkeypatch.setattr(notifier_module.socket, "getaddrinfo", _public_dns)
    calls = []

    def capture_post(*args, **kwargs):
        calls.append((args, kwargs))
        return _Response()

    monkeypatch.setattr(notifier_module.requests, "post", capture_post)

    ok, error = SignalNotifier()._notify_webhook(
        url="https://example.com/hook",
        payload={"event": "test"},
    )

    assert ok
    assert error == ""
    assert calls[0][1]["allow_redirects"] is False


def test_discord_channel_rejects_lookalike_host(monkeypatch):
    monkeypatch.setattr(notifier_module.socket, "getaddrinfo", _public_dns)

    ok, error = SignalNotifier()._notify_discord(
        url="https://discord.com.attacker.example/api/webhooks/1/token",
        payload={"event": "test"},
        fallback_text="test",
    )

    assert not ok
    assert error == "webhook_host_not_allowed"
