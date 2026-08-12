import pytest
from marshmallow import ValidationError

from app.openapi.schemas.high_risk import CredentialCreateRequestSchema
from app.services.exchange_execution import safe_exchange_config_for_log
from app.services.futu_trading.config import (
    FutuConfig,
    config_from_exchange_config,
    is_local_or_private_opend_host,
    normalize_security_firm,
    normalize_trade_env,
    normalize_trade_market,
    validate_opend_host,
)


def test_normalize_trade_env():
    assert normalize_trade_env("demo") == "demo"
    assert normalize_trade_env("paper") == "demo"
    assert normalize_trade_env("simulate") == "demo"
    assert normalize_trade_env("live") == "live"
    assert normalize_trade_env("REAL") == "live"
    assert normalize_trade_env("") == "demo"


def test_normalize_trade_market():
    assert normalize_trade_market("HK") == "HK"
    assert normalize_trade_market("", market_category="HKStock") == "HK"
    assert normalize_trade_market("", market_category="USStock") == "US"
    assert normalize_trade_market("USStock") == "US"


def test_normalize_security_firm():
    assert normalize_security_firm("") == "FUTUSECURITIES"
    assert normalize_security_firm("futu") == "FUTUSECURITIES"
    assert normalize_security_firm("FUTUINC") == "FUTUINC"


def test_config_from_exchange_config_demo_default():
    cfg = config_from_exchange_config({
        "futu_host": "host.docker.internal",
        "futu_port": 11111,
        "environment": "demo",
        "trade_market": "HK",
    })
    assert isinstance(cfg, FutuConfig)
    assert cfg.host == "host.docker.internal"
    assert cfg.is_simulate is True
    assert cfg.trade_market == "HK"
    redacted = cfg.redacted_dict()
    assert "unlock_password" not in redacted
    assert redacted["has_unlock_password"] is False


def test_config_live_with_password_flag():
    cfg = config_from_exchange_config({
        "host": "127.0.0.1",
        "port": 11111,
        "trade_env": "live",
        "unlock_password": "secret",
        "market_category": "USStock",
    })
    assert cfg.trade_env == "live"
    assert cfg.trade_market == "US"
    assert cfg.redacted_dict()["has_unlock_password"] is True


def test_opend_host_validation_accepts_only_local_and_private_ranges():
    assert is_local_or_private_opend_host("127.0.0.1")
    assert is_local_or_private_opend_host("localhost")
    assert is_local_or_private_opend_host("host.docker.internal")
    assert is_local_or_private_opend_host("10.1.2.3")
    assert is_local_or_private_opend_host("172.16.0.1")
    assert is_local_or_private_opend_host("172.31.255.254")
    assert is_local_or_private_opend_host("192.168.1.2")
    assert is_local_or_private_opend_host("fd12::1")
    assert not is_local_or_private_opend_host("172.15.255.255")
    assert not is_local_or_private_opend_host("172.32.0.1")
    assert not is_local_or_private_opend_host("8.8.8.8")
    assert not is_local_or_private_opend_host("example.com")


def test_validate_opend_host_rejects_remote_by_default(monkeypatch):
    monkeypatch.delenv("FUTU_ALLOW_REMOTE_OPEND", raising=False)
    with pytest.raises(ValueError, match="private LAN"):
        validate_opend_host("169.254.169.254")


def test_validate_opend_host_allows_remote_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("FUTU_ALLOW_REMOTE_OPEND", "true")
    assert validate_opend_host("203.0.113.8") == "203.0.113.8"


def test_futu_credential_requires_and_cross_validates_market():
    schema = CredentialCreateRequestSchema()
    with pytest.raises(ValidationError, match="trade_market"):
        schema.load({"exchange_id": "futu"})
    with pytest.raises(ValidationError, match="does not match"):
        schema.load({
            "exchange_id": "futu",
            "trade_market": "US",
            "market_category": "HKStock",
        })
    with pytest.raises(ValidationError, match="HK/HKStock"):
        schema.load({"exchange_id": "futu", "trade_market": "unsupported"})
    loaded = schema.load({
        "exchange_id": "futu",
        "trade_market": "US",
        "market_category": "USStock",
    })
    assert loaded["trade_market"] == "US"


def test_safe_exchange_config_masks_futu_unlock_password():
    safe = safe_exchange_config_for_log({
        "exchange_id": "futu",
        "unlock_password": "super-secret-password",
        "unlockPassword": "second-secret-password",
    })
    assert "super-secret-password" not in str(safe)
    assert "second-secret-password" not in str(safe)
