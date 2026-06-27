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
        "nested": {"passphrase": "p", "label": "ok"},
        "items": [{"secret": "x"}],
    }
    out = redact_secrets(raw)
    assert out["api_key"] == "***"
    assert out["nested"]["passphrase"] == "***"
    assert out["nested"]["label"] == "ok"
    assert out["items"][0]["secret"] == "***"


def test_agent_audit_redact_masks_common_secret_keys():
    out = _redact({
        "secret_key": "s",
        "secretKey": "camel",
        "passphrase": "p",
        "nested": {"client_secret": "c"},
    })
    assert out["secret_key"] == "<redacted>"
    assert out["secretKey"] == "<redacted>"
    assert out["passphrase"] == "<redacted>"
    assert out["nested"]["client_secret"] == "<redacted>"


def test_redact_strategy_row_covers_config_blocks():
    row = {
        "id": 1,
        "exchange_config": {"apiKey": "k"},
        "notification_config": {"webhook_secret": "wh"},
    }
    out = redact_strategy_row(row)
    assert out["exchange_config"]["apiKey"] == "***"
    assert out["notification_config"]["webhook_secret"] == "***"


def test_indicator_code_size_limit():
    ok = "x" * 100
    assert_indicator_code_size(ok)
    huge = "x" * (MAX_INDICATOR_CODE_BYTES + 1)
    with pytest.raises(ValueError, match="KiB"):
        assert_indicator_code_size(huge)
