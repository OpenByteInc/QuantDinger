import pytest

from app.routes.credentials import _crypto_credential_config, _probe_crypto_credential
from app.services.exchange_execution import resolve_exchange_config
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.factory import (
    create_client,
    exchange_market_scope,
    exchange_trading_environment,
    validate_exchange_environment,
)
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient


def _config(exchange_id, environment, market_scope="both"):
    config = {
        "exchange_id": exchange_id,
        "api_key": "key",
        "secret_key": "secret",
        "environment": environment,
        "market_scope": market_scope,
    }
    if exchange_id in ("okx", "bitget"):
        config["passphrase"] = "pass"
    return config


def test_exchange_environment_routes_match_official_demo_hosts():
    assert create_client(_config("binance", "demo", "spot"), market_type="spot").base_url == "https://demo-api.binance.com"
    assert create_client(_config("binance", "demo", "swap"), market_type="swap").base_url == "https://demo-fapi.binance.com"

    okx = create_client(_config("okx", "demo"), market_type="spot")
    assert okx.base_url == "https://openapi.okx.com"
    assert okx._headers("1", "sign")["x-simulated-trading"] == "1"

    bitget = create_client(_config("bitget", "demo"), market_type="spot")
    assert bitget.base_url == "https://api.bitget.com"
    assert bitget._headers("1", "sign", "/api/v2/spot/trade/place-order")["PAPTRADING"] == "1"

    assert create_client(_config("bybit", "demo"), market_type="spot").base_url == "https://api-demo.bybit.com"

    assert create_client(_config("gate", "testnet"), market_type="spot").base_url == "https://api-testnet.gateapi.io"
    assert create_client(_config("gate", "testnet"), market_type="swap").base_url == "https://api-testnet.gateapi.io"


def test_fee_clients_preserve_spot_vs_contract_market_routing():
    assert isinstance(create_client(_config("binance", "live"), market_type="spot"), BinanceSpotClient)
    assert isinstance(create_client(_config("binance", "live"), market_type="swap"), BinanceFuturesClient)
    assert isinstance(create_client(_config("okx", "live"), market_type="spot"), OkxClient)
    assert isinstance(create_client(_config("okx", "live"), market_type="swap"), OkxClient)
    assert isinstance(create_client(_config("bitget", "live"), market_type="spot"), BitgetSpotClient)
    assert isinstance(create_client(_config("bitget", "live"), market_type="swap"), BitgetMixClient)

    bybit_spot = create_client(_config("bybit", "live"), market_type="spot")
    bybit_swap = create_client(_config("bybit", "live"), market_type="swap")
    assert isinstance(bybit_spot, BybitClient) and bybit_spot.category == "spot"
    assert isinstance(bybit_swap, BybitClient) and bybit_swap.category == "linear"
    assert isinstance(create_client(_config("gate", "live"), market_type="spot"), GateSpotClient)
    assert isinstance(create_client(_config("gate", "live"), market_type="swap"), GateUsdtFuturesClient)

    htx_spot = create_client(_config("htx", "live"), market_type="spot")
    htx_swap = create_client(_config("htx", "live"), market_type="swap")
    assert isinstance(htx_spot, HtxClient) and htx_spot.market_type == "spot"
    assert isinstance(htx_swap, HtxClient) and htx_swap.market_type == "swap"


def test_legacy_demo_flags_map_to_exchange_specific_environment():
    assert exchange_trading_environment({"exchange_id": "binance", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "binance", "environment": "demo"}) == "demo"
    assert exchange_trading_environment({"exchange_id": "okx", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "bybit", "enable_demo_trading": True}) == "demo"
    assert exchange_trading_environment({"exchange_id": "gate", "enable_demo_trading": True}) == "testnet"


def test_market_scope_alias_and_invalid_value_are_not_silently_ignored():
    assert exchange_market_scope({"marketScope": "futures"}) == "swap"
    with pytest.raises(LiveTradingError, match="INVALID_CREDENTIAL_MARKET_SCOPE"):
        validate_exchange_environment("okx", "live", exchange_market_scope({"marketScope": "margin"}))


def test_environment_and_market_scope_fail_closed():
    with pytest.raises(LiveTradingError, match="HTX_DEMO_NOT_SUPPORTED"):
        create_client(_config("htx", "demo"), market_type="spot")

    with pytest.raises(LiveTradingError, match="CREDENTIAL_MARKET_SCOPE_MISMATCH"):
        create_client(_config("bybit", "demo", "spot"), market_type="swap")

    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        create_client(_config("gate", "banana"), market_type="spot")


def test_binance_demo_credential_supports_spot_and_futures_scope():
    config = _crypto_credential_config(_config("binance", "demo"), "binance")
    assert config["environment"] == "demo"
    assert config["market_scope"] == "both"
    assert config["enable_demo_trading"] is True


def test_binance_testnet_environment_is_not_supported():
    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        _crypto_credential_config(_config("binance", "testnet"), "binance")


def test_bybit_testnet_environment_is_not_supported():
    with pytest.raises(LiveTradingError, match="UNSUPPORTED_TRADING_ENVIRONMENT"):
        _crypto_credential_config(_config("bybit", "testnet"), "bybit")


def test_strategy_cannot_override_credential_environment_or_secret(monkeypatch):
    monkeypatch.setattr(
        "app.services.exchange_execution._load_credential_config",
        lambda credential_id, user_id: {
            "exchange_id": "bybit",
            "api_key": "vault-key",
            "secret_key": "vault-secret",
            "environment": "demo",
            "market_scope": "spot",
        },
    )

    resolved = resolve_exchange_config(
        {
            "credential_id": 7,
            "exchange_id": "binance",
            "api_key": "override-key",
            "secret_key": "override-secret",
            "environment": "live",
            "market_scope": "swap",
            "margin_mode": "isolated",
        },
        user_id=3,
    )

    assert resolved["exchange_id"] == "bybit"
    assert resolved["api_key"] == "vault-key"
    assert resolved["secret_key"] == "vault-secret"
    assert resolved["environment"] == "demo"
    assert resolved["market_scope"] == "spot"
    assert resolved["margin_mode"] == "isolated"


def test_credential_probe_calls_private_account_endpoint(monkeypatch):
    calls = []

    class Client:
        def __init__(self, market_type):
            self.market_type = market_type

        def get_account(self):
            calls.append(self.market_type)
            return {"ok": True}

    monkeypatch.setattr("app.routes.credentials.create_client", lambda config, market_type: Client(market_type))
    tested = _probe_crypto_credential(_config("binance", "testnet", "spot"))

    assert tested == ["spot"]
    assert calls == ["spot"]

    tested = _probe_crypto_credential(_config("binance", "demo", "both"))

    assert tested == ["spot", "swap"]
    assert calls == ["spot", "spot", "swap"]
