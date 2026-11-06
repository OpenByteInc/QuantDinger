import pytest

from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2 import deployment
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.services.market import product_catalog


@pytest.mark.parametrize("config", [
    {"environment": "testnet"}, {"environment": "demo"}, {"sandbox": True},
    {"use_testnet": True}, {"enableDemoTrading": "true"},
])
def test_gate_stock_environment_validation_preserves_account_configuration(config):
    account = {"exchange_id": "gate", **config}
    snapshot = dict(account)
    with pytest.raises(ValueError, match="gateStockTestnetUnsupported"):
        product_catalog.validate_product_account_environment(
            [{"exchange_id": "gate", "api_family": "stock"}], account,
        )
    assert account == snapshot


@pytest.mark.parametrize("exchange,api_family,environment", [
    ("gate", "stock", "live"), ("gate", "spot", "testnet"),
    ("gate", "swap", "testnet"), ("binance", "spot", "demo"),
    ("okx", "swap", "demo"), ("bitget", "spot", "demo"),
    ("bybit", "swap", "demo"), ("htx", "spot", "live"),
])
def test_gate_stock_guard_does_not_change_other_product_environments(exchange, api_family, environment):
    product_catalog.validate_product_account_environment(
        [{"exchange_id": exchange, "api_family": api_family}],
        {"exchange_id": exchange, "environment": environment},
    )


def _manifest(exchange_id="bybit", market_type="spot"):
    return {
        "universe": {
            "instruments": [{
                "market": "Crypto",
                "symbol": "AAPLX/USDT",
                "exchange_id": exchange_id,
                "market_type": market_type,
            }],
        },
    }


def _product(product_type="tokenized_equity"):
    return {
        "asset_id": 51,
        "instrument_id": "AAPLXUSDT",
        "product_type": product_type,
        "api_family": "spot",
        "underlying_market": "USStock",
        "underlying_symbol": "AAPL",
        "product_meta": {"multiplier": "0.01"},
    }


def test_gate_stock_grid_robot_is_blocked_before_deployment():
    with pytest.raises(StrategyV2ContractError, match="equityRobotUnsupported"):
        StrategyV2DeploymentService._validate_bot_product_compatibility(
            bot_type="grid",
            instrument_products=[{"api_family": "stock", "product_type": "direct_equity"}],
        )


def test_gate_stock_strategy_api_execution_is_allowed():
    StrategyV2DeploymentService._validate_bot_product_compatibility(
        bot_type="",
        instrument_products=[{"api_family": "stock", "product_type": "direct_equity"}],
    )


def test_live_product_preflight_persists_catalog_contract(monkeypatch):
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", lambda **kwargs: _product())

    rows = StrategyV2DeploymentService._validate_manifest_products(
        _manifest(), "bybit", "live",
    )

    assert rows == [{
        "market": "Crypto",
        "asset_id": 51,
        "symbol": "AAPLX/USDT",
        "exchange_id": "bybit",
        "market_type": "spot",
        "instrument_id": "AAPLXUSDT",
        "settle_currency": "",
        "product_type": "tokenized_equity",
        "api_family": "spot",
        "underlying_market": "USStock",
        "underlying_symbol": "AAPL",
        "product_meta": {"multiplier": "0.01"},
    }]


def test_live_product_preflight_rejects_missing_account_product(monkeypatch):
    monkeypatch.setattr(
        deployment,
        "resolve_runtime_instrument",
        lambda **kwargs: (_ for _ in ()).throw(deployment.AssetResolutionError("strategyV2.instrumentUnavailableForAccount")),
    )

    with pytest.raises(StrategyV2ContractError, match="strategyV2.instrumentUnavailableForAccount"):
        StrategyV2DeploymentService._validate_manifest_products(
            _manifest(exchange_id="gate"), "gate", "live",
        )


def test_live_product_preflight_uses_credential_venue_instead_of_source_venue(monkeypatch):
    captured = {}
    def resolve(**kwargs):
        captured.update(kwargs)
        return {**_product(), "api_family": "reality"}
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", resolve)

    rows = StrategyV2DeploymentService._validate_manifest_products(
        _manifest(exchange_id="bybit"), "bitget", "live",
    )

    assert captured["exchange_id"] == "bitget"
    assert rows[0]["exchange_id"] == "bitget"


def test_live_product_preflight_does_not_require_source_venue(monkeypatch):
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", lambda **kwargs: _product())

    rows = StrategyV2DeploymentService._validate_manifest_products(
        _manifest(exchange_id=""), "bybit", "live",
    )

    assert rows[0]["exchange_id"] == "bybit"


def test_live_product_preflight_rejects_unsupported_venue_product(monkeypatch):
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", lambda **kwargs: _product())

    with pytest.raises(StrategyV2ContractError, match="strategyV2.equityProductUnsupported"):
        StrategyV2DeploymentService._validate_manifest_products(
            _manifest(exchange_id="htx"), "htx", "live",
        )


def test_signal_mode_does_not_require_live_product_catalog(monkeypatch):
    monkeypatch.setattr(
        deployment,
        "resolve_runtime_instrument",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected catalog lookup")),
    )

    assert StrategyV2DeploymentService._validate_manifest_products(
        _manifest(), "", "signal",
    ) == []


def test_dynamic_live_universe_uses_resolved_member_products(monkeypatch):
    monkeypatch.setattr(deployment, "resolve_runtime_instrument", lambda **kwargs: _product())
    members = [{
        "market": "Crypto",
        "symbol": "AAPLX/USDT",
        "exchange_id": "bybit",
        "market_type": "spot",
    }]

    rows = StrategyV2DeploymentService._validate_manifest_products(
        {"universe": {"reference": "pool=my-list"}},
        "bybit",
        "live",
        instruments=members,
    )

    assert rows[0]["instrument_id"] == "AAPLXUSDT"


def test_live_quote_currency_uses_gate_hk_product_metadata():
    currency = StrategyV2DeploymentService._validate_live_quote_currency(
        markets=("Crypto",),
        instruments=[{
            "market": "Crypto",
            "symbol": "00700/HKD",
            "exchange_id": "gate",
            "market_type": "spot",
        }],
        products=[{
            "symbol": "00700/HKD",
            "exchange_id": "gate",
            "market_type": "spot",
            "product_meta": {"quote_currency": "HKD"},
        }],
        execution_mode="live",
    )

    assert currency == "HKD"


def test_live_quote_currency_uses_account_product_not_source_exchange():
    currency = StrategyV2DeploymentService._validate_live_quote_currency(
        markets=("Crypto",),
        instruments=[{
            "asset_id": 51,
            "market": "Crypto",
            "symbol": "AAPLX/USDT",
            "exchange_id": "bybit",
            "market_type": "spot",
        }],
        products=[{
            "asset_id": 51,
            "symbol": "AAPLX/USDT",
            "exchange_id": "bitget",
            "market_type": "spot",
            "settle_currency": "USD",
        }],
        execution_mode="live",
    )

    assert currency == "USD"


def test_live_quote_currency_rejects_mixed_usd_and_hkd_products():
    with pytest.raises(StrategyV2ContractError, match="mixedQuoteCurrencyLiveUnsupported"):
        StrategyV2DeploymentService._validate_live_quote_currency(
            markets=("Crypto",),
            instruments=[
                {"market": "Crypto", "symbol": "AAPL/USD", "exchange_id": "gate", "market_type": "spot"},
                {"market": "Crypto", "symbol": "00700/HKD", "exchange_id": "gate", "market_type": "spot"},
            ],
            products=[],
            execution_mode="live",
        )


@pytest.mark.parametrize(("market", "symbol", "expected"), [
    ("USStock", "AAPL", "USD"),
    ("HKStock", "00700", "HKD"),
    ("CNStock", "600519.SH", "CNY"),
    ("Crypto", "BTC/USDT", "USDT"),
    ("Forex", "EUR/USD", "USD"),
])
def test_signal_accounting_currency_follows_the_instrument(market, symbol, expected):
    currency = StrategyV2DeploymentService._validate_live_quote_currency(
        markets=(market,),
        instruments=[{"market": market, "symbol": symbol, "market_type": "spot"}],
        products=[],
        execution_mode="signal",
    )

    assert currency == expected


def test_runtime_product_validation_detects_catalog_contract_change(monkeypatch):
    monkeypatch.setattr(
        product_catalog,
        "resolve_runtime_instrument",
        lambda **kwargs: {**_product(product_type="crypto"), "api_family": "reality"},
    )
    stored = [{
        "market": "Crypto",
        "symbol": "RAAPL/USDT",
        "exchange_id": "bitget",
        "market_type": "spot",
        "instrument_id": "RAAPLUSDT",
        "product_type": "tokenized_equity",
        "api_family": "reality",
    }]

    with pytest.raises(ValueError, match="strategyV2.equityProductContractChanged"):
        product_catalog.validate_runtime_products(stored, credential_exchange_id="bitget")


def test_runtime_product_validation_rejects_non_tradable_status(monkeypatch):
    monkeypatch.setattr(
        product_catalog,
        "resolve_runtime_instrument",
        lambda **kwargs: {
            **_product(),
            "api_family": "reality",
            "product_meta": {"status": "restrictedAPI"},
        },
    )
    stored = [{
        "market": "Crypto",
        "symbol": "RAAPL/USDT",
        "exchange_id": "bitget",
        "market_type": "spot",
        "instrument_id": "RAAPLUSDT",
        "product_type": "tokenized_equity",
        "api_family": "reality",
    }]

    with pytest.raises(ValueError, match="strategyV2.equityProductNotTradable"):
        product_catalog.validate_runtime_products(stored, credential_exchange_id="bitget")
