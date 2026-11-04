import pytest

from app.routes.settings import _effective_schema
from app.services import ai_calibration
from app.services.settings.capabilities import get_feature_flags
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService


def test_feature_flags_parse_explicit_values():
    flags = get_feature_flags({
        "BILLING_ENABLED": "false",
        "MARKETPLACE_ENABLED": "0",
        "ENABLE_REGISTRATION": "yes",
        "BROKER_ACCOUNTS_ENABLED": "off",
        "LIVE_TRADING_ENABLED": "no",
        "AGENT_GATEWAY_ENABLED": "false",
    })

    assert flags == {
        "billing_enabled": False,
        "marketplace_enabled": False,
        "registration_enabled": True,
        "broker_accounts_enabled": False,
        "live_trading_enabled": False,
        "agent_gateway_enabled": False,
    }


def test_fresh_install_disables_commercial_features():
    flags = get_feature_flags({})

    assert flags["billing_enabled"] is False
    assert flags["marketplace_enabled"] is False
    assert flags["registration_enabled"] is True
    assert flags["broker_accounts_enabled"] is True
    assert flags["live_trading_enabled"] is True
    assert flags["agent_gateway_enabled"] is True


def test_billing_configuration_is_hidden_until_billing_is_enabled():
    disabled_schema = _effective_schema({"BILLING_ENABLED": "false"})
    enabled_schema = _effective_schema({"BILLING_ENABLED": "true"})

    assert "features" in disabled_schema
    assert "billing" not in disabled_schema
    assert "billing" in enabled_schema
    feature_keys = {item["key"] for item in disabled_schema["features"]["items"]}
    assert feature_keys == {
        "BILLING_ENABLED",
        "MARKETPLACE_ENABLED",
        "ENABLE_REGISTRATION",
        "BROKER_ACCOUNTS_ENABLED",
        "LIVE_TRADING_ENABLED",
        "AGENT_GATEWAY_ENABLED",
    }


def test_live_deployment_is_rejected_when_product_capability_is_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.strategy_v2.deployment.is_live_trading_enabled",
        lambda: False,
    )

    with pytest.raises(StrategyV2ContractError, match="strategyV2.liveTradingDisabled"):
        StrategyV2DeploymentService().save(
            user_id=1,
            payload={"executionMode": "live"},
        )


def test_agent_tuning_and_live_order_defaults_are_curated_for_the_settings_ui():
    schema = _effective_schema({})
    agent = {item["key"]: item for item in schema["agent"]["items"]}
    ai = {item["key"]: item for item in schema["ai"]["items"]}
    trading = {item["key"]: item for item in schema["trading"]["items"]}
    runtime = {item["key"]: item for item in schema["strategy_runtime"]["items"]}

    assert "QUANTDINGER_DEPLOYMENT_MODE" not in agent
    assert "AGENT_LIVE_TRADING_ENABLED" not in agent
    assert "ENABLE_AI_ENSEMBLE" not in agent
    assert "AI_ENSEMBLE_MODELS" not in agent
    assert agent["ENABLE_REFLECTION_WORKER"]["is_advanced"] is False
    assert agent["ENABLE_CONFIDENCE_CALIBRATION"]["is_advanced"] is False
    assert ai["ENABLE_AI_ENSEMBLE"]["is_advanced"] is False
    assert ai["AI_ENSEMBLE_MODELS"]["is_advanced"] is True
    assert ai["AI_ENSEMBLE_MODELS"]["default"] == ""
    assert trading["AGENT_LIVE_TRADING_ENABLED"]["is_advanced"] is False
    assert set(trading) == {
        "AGENT_LIVE_TRADING_ENABLED",
        "MAKER_WAIT_SEC",
        "MAKER_OFFSET_BPS",
    }
    assert runtime["AGENT_JOBS_MAX_WORKERS"]["is_advanced"] is True


def test_calibration_worker_uses_every_configured_market(monkeypatch):
    calls = []

    def fake_calibrate(self, *, market, lookback_days, min_samples):
        del self
        calls.append((market, lookback_days, min_samples))
        return None

    monkeypatch.setenv("ENABLE_CONFIDENCE_CALIBRATION", "true")
    monkeypatch.setenv("AI_CALIBRATION_MARKETS", "Crypto,USStock")
    monkeypatch.setenv("AI_CALIBRATION_LOOKBACK_DAYS", "45")
    monkeypatch.setenv("AI_CALIBRATION_MIN_SAMPLES", "120")
    monkeypatch.setattr(ai_calibration.AICalibrationService, "calibrate_market", fake_calibrate)

    ai_calibration.start_ai_calibration_worker()

    assert calls == [("Crypto", 45, 120), ("USStock", 45, 120)]
