import pytest

from app.services.strategy import (
    redact_strategy_row,
    reject_inline_strategy_secrets,
)


def test_reject_inline_strategy_secrets_without_credential_id():
    with pytest.raises(ValueError, match="credential_id"):
        reject_inline_strategy_secrets({"exchange_id": "binance", "secret_key": "s"})


def test_allow_strategy_credential_reference_without_inline_keys():
    reject_inline_strategy_secrets({"exchange_id": "binance", "credential_id": 12, "secret_key": "s"})


def test_redact_strategy_row_masks_nested_secrets():
    row = {
        "id": 1,
        "exchange_config": {"api_key": "k"},
        "trading_config": {"exchange_config": {"secretKey": "s"}},
        "notification_config": {"webhook_secret": "w"},
    }
    out = redact_strategy_row(row)
    assert out["exchange_config"]["api_key"] == "***"
    assert out["trading_config"]["exchange_config"]["secretKey"] == "***"
    assert out["notification_config"]["webhook_secret"] == "***"
