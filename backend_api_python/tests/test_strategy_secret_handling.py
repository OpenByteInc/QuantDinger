import pytest

from app.services.strategy import (
    StrategyService,
    redact_strategy_row,
    reject_inline_strategy_secrets,
    strip_strategy_secrets,
)


def test_reject_inline_strategy_secrets_without_credential_id():
    with pytest.raises(ValueError, match="INLINE_STRATEGY_SECRETS_NOT_ALLOWED"):
        reject_inline_strategy_secrets({"exchange_id": "binance", "secret_key": "s"})


def test_allow_strategy_credential_reference_and_strip_inline_keys():
    cfg = strip_strategy_secrets({
        "exchange_id": "binance",
        "credential_id": 12,
        "secret_key": "s",
        "nested": {"apiKey": "k"},
    })
    reject_inline_strategy_secrets(cfg)
    assert cfg == {"exchange_id": "binance", "credential_id": 12, "nested": {}}


def test_credentials_id_alias_allows_credential_reference():
    reject_inline_strategy_secrets({
        "exchange_id": "binance",
        "credentials_id": 12,
        "secret_key": "s",
    })


def test_redact_strategy_row_masks_nested_secrets():
    row = {
        "id": 1,
        "exchange_config": {"api_key": "k"},
        "trading_config": {"exchange_config": {"secretKey": "s"}},
        "notification_config": {"webhook-secret": "w"},
    }
    out = redact_strategy_row(row)
    assert out["exchange_config"]["api_key"] == "***"
    assert out["trading_config"]["exchange_config"]["secretKey"] == "***"
    assert out["notification_config"]["webhook-secret"] == "***"


def test_redact_strategy_row_masks_notification_target_aliases():
    row = {
        "notification_config": {
            "targets": [{
                "telegramBotToken": "telegram-secret",
                "webhook_token": "webhook-secret",
                "webhook_headers": {
                    "Authorization": "Bearer secret",
                    "X-Api-Key": "header-secret",
                    "Content-Type": "application/json",
                },
                "webhook_url": "https://example.com/hook",
            }],
        },
    }

    out = redact_strategy_row(row)
    target = out["notification_config"]["targets"][0]

    assert target["telegramBotToken"] == "***"
    assert target["webhook_token"] == "***"
    assert target["webhook_headers"]["Authorization"] == "***"
    assert target["webhook_headers"]["X-Api-Key"] == "***"
    assert target["webhook_headers"]["Content-Type"] == "application/json"
    assert target["webhook_url"] == "https://example.com/hook"


def test_strategy_update_preserves_existing_notification_secrets(monkeypatch):
    from app.services import strategy_v2

    captured = {}

    class DeploymentService:
        def save(self, *, user_id, payload, strategy_id):
            captured.update({"user_id": user_id, "payload": payload, "strategy_id": strategy_id})

    service = StrategyService()
    monkeypatch.setattr(service, "get_strategy", lambda strategy_id, user_id=None: {
        "id": strategy_id,
        "user_id": user_id or 7,
        "strategy_name": "Protected strategy",
        "initial_capital": 1000,
        "execution_mode": "signal",
        "leverage": 1,
        "trading_config": {"script_source_id": 12, "params": {}},
        "notification_config": {
            "channels": ["telegram", "webhook"],
            "targets": {
                "telegram_bot_token": "stored-telegram-token",
                "telegram_chat_id": "12345",
                "webhook_token": "stored-webhook-token",
                "webhook_url": "https://example.com/old",
            },
        },
    })
    monkeypatch.setattr(strategy_v2, "get_strategy_v2_deployment_service", lambda: DeploymentService())

    changed = service.update_strategy(
        42,
        {
            "notificationTargets": {
                "telegram_bot_token": "***",
                "telegram_chat_id": "67890",
                "webhook_url": "https://example.com/new",
            },
        },
        user_id=7,
    )

    assert changed is True
    assert captured["user_id"] == 7
    assert captured["strategy_id"] == 42
    assert captured["payload"]["notificationChannels"] == ["telegram", "webhook"]
    targets = captured["payload"]["notificationTargets"]
    assert targets["telegram_bot_token"] == "stored-telegram-token"
    assert targets["webhook_token"] == "stored-webhook-token"
    assert targets["telegram_chat_id"] == "67890"
    assert targets["webhook_url"] == "https://example.com/new"
