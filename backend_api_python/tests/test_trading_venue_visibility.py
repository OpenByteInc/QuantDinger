import pytest

from app.services.live_trading import factory
from app.services.live_trading.base import LiveTradingError
from app.services.strategy_v2 import deployment
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.utils.trading_venue_visibility import (
    TRADING_VENUE_IDS,
    enabled_trading_venues,
    is_trading_venue_enabled,
)


def test_missing_venue_setting_keeps_existing_installations_enabled():
    assert enabled_trading_venues({}) == set(TRADING_VENUE_IDS)


def test_explicit_empty_venue_setting_disables_every_venue():
    assert enabled_trading_venues({"ENABLED_TRADING_VENUES": ""}) == set()


def test_venue_setting_normalizes_supported_aliases_and_ignores_unknown_values():
    env = {"ENABLED_TRADING_VENUES": "alpaca,gate.io,huobi,unknown"}

    assert enabled_trading_venues(env) == {"alpaca", "gate", "htx"}
    assert is_trading_venue_enabled("gateio", env) is True
    assert is_trading_venue_enabled("webull", env) is False


def test_client_factory_blocks_a_disabled_venue_before_credentials_are_used(monkeypatch):
    monkeypatch.setattr(factory, "is_trading_venue_enabled", lambda _venue: False)

    with pytest.raises(LiveTradingError, match="TRADING_VENUE_DISABLED"):
        factory.create_client({"exchange_id": "webull"}, market_type="spot")


def test_live_strategy_validation_blocks_a_disabled_venue(monkeypatch):
    monkeypatch.setattr(deployment, "is_trading_venue_enabled", lambda _venue: False)

    with pytest.raises(StrategyV2ContractError, match="strategyV2.tradingVenueDisabled"):
        StrategyV2DeploymentService._validate_execution_account(
            ("USStock",),
            "tiger",
            "live",
        )


def test_signal_strategy_validation_does_not_require_an_enabled_venue(monkeypatch):
    monkeypatch.setattr(deployment, "is_trading_venue_enabled", lambda _venue: False)

    StrategyV2DeploymentService._validate_execution_account(
        ("USStock",),
        "tiger",
        "signal",
    )
