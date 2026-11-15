"""Security helpers for agent_v1 routes."""
from __future__ import annotations

import pytest

from app.routes.agent_v1._security import (
    MAX_INDICATOR_CODE_BYTES,
    assert_indicator_code_size,
    redact_secrets,
    redact_strategy_row,
)
from app.utils.agent_auth import _redact


def test_redact_secrets_masks_known_keys():
    raw = {
        "api_key": "secret123",
        "secret-key": "dash",
        "nested": {"passphrase": "p", "clientSecret": "c", "label": "ok"},
        "items": [{"secret": "x"}],
    }
    out = redact_secrets(raw)
    assert out["api_key"] == "***"
    assert out["secret-key"] == "***"
    assert out["nested"]["passphrase"] == "***"
    assert out["nested"]["clientSecret"] == "***"
    assert out["nested"]["label"] == "ok"
    assert out["items"][0]["secret"] == "***"


def test_redact_secrets_masks_notification_credentials_and_headers():
    raw = {
        "targets": {
            "telegram_bot_token": "telegram-secret",
            "telegramToken": "telegram-alias",
            "webhook.token": "webhook-secret",
            "webhookSigningSecret": "signing-secret",
            "webhook_headers": {
                "Authorization": "Bearer private",
                "X-Api-Key": "header-secret",
                "Content-Type": "application/json",
            },
            "telegram": True,
            "telegram_chat_id": "12345",
            "webhook_url": "https://example.com/hook",
        }
    }

    out = redact_secrets(raw)

    assert out["targets"]["telegram_bot_token"] == "***"
    assert out["targets"]["telegramToken"] == "***"
    assert out["targets"]["webhook.token"] == "***"
    assert out["targets"]["webhookSigningSecret"] == "***"
    assert out["targets"]["webhook_headers"]["Authorization"] == "***"
    assert out["targets"]["webhook_headers"]["X-Api-Key"] == "***"
    assert out["targets"]["webhook_headers"]["Content-Type"] == "application/json"
    assert out["targets"]["telegram_chat_id"] == "12345"
    assert out["targets"]["webhook_url"] == "https://example.com/hook"


def test_redact_secrets_masks_json_encoded_webhook_headers():
    out = redact_secrets({
        "webhook_headers": '{"Authorization":"Bearer private","Content-Type":"application/json"}',
    })

    assert "Bearer private" not in out["webhook_headers"]
    assert '"Authorization":"***"' in out["webhook_headers"]
    assert '"Content-Type":"application/json"' in out["webhook_headers"]


def test_redact_strategy_row_covers_config_blocks():
    row = {
        "id": 1,
        "exchange_config": {"apiKey": "k"},
        "notification_config": {"webhook_secret": "wh"},
    }
    out = redact_strategy_row(row)
    assert out["exchange_config"]["apiKey"] == "***"
    assert out["notification_config"]["webhook_secret"] == "***"


def test_agent_audit_redact_masks_common_secret_keys():
    out = _redact({
        "secret-key": "dash",
        "secret_key": "s",
        "secretKey": "camel",
        "passphrase": "p",
        "nested": {"client-secret": "c"},
    })
    assert out["secret-key"] == "<redacted>"
    assert out["secret_key"] == "<redacted>"
    assert out["secretKey"] == "<redacted>"
    assert out["passphrase"] == "<redacted>"
    assert out["nested"]["client-secret"] == "<redacted>"


def test_indicator_code_size_limit():
    ok = "x" * 100
    assert_indicator_code_size(ok)
    huge = "x" * (MAX_INDICATOR_CODE_BYTES + 1)
    with pytest.raises(ValueError, match="KiB"):
        assert_indicator_code_size(huge)
