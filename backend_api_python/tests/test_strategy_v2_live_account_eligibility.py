import pytest

from app.services.live_trading.capabilities import supported_crypto_exchange_ids
from app.services import exchange_execution
from app.services.live_trading import factory
from app.services import strategy_live_guard
from app.services.trading_executor import TradingExecutor
from app.services.strategy_v2 import compile_strategy_v2
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService


CRYPTO_PORTFOLIO_SOURCE = '''
def initialize(context):
    context.set_universe([
        "Crypto:BTC/USDT@swap",
        "Crypto:ETH/USDT@swap",
    ])
    context.subscribe(frequency="4h")
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    pass
'''


def test_crypto_portfolio_manifest_accepts_every_supported_crypto_credential():
    manifest = compile_strategy_v2(CRYPTO_PORTFOLIO_SOURCE).manifest

    assert manifest.strategy_type == "portfolio"
    assert manifest.markets == ("Crypto",)
    for exchange_id in supported_crypto_exchange_ids():
        StrategyV2DeploymentService._validate_execution_account(
            manifest.markets,
            exchange_id,
            "live",
        )


@pytest.mark.parametrize("exchange_id", ["alpaca", "ibkr", "unsupported"])
def test_crypto_portfolio_rejects_non_crypto_credentials(exchange_id):
    manifest = compile_strategy_v2(CRYPTO_PORTFOLIO_SOURCE).manifest

    with pytest.raises(StrategyV2ContractError, match="strategyV2.cryptoCredentialRequired"):
        StrategyV2DeploymentService._validate_execution_account(
            manifest.markets,
            exchange_id,
            "live",
        )


def test_mixed_market_portfolio_remains_signal_only():
    with pytest.raises(StrategyV2ContractError, match="strategyV2.mixedMarketLiveUnsupported"):
        StrategyV2DeploymentService._validate_execution_account(
            ("Crypto", "USStock"),
            "binance",
            "live",
        )


@pytest.mark.parametrize("exchange_id", ["alpaca", "ibkr", "tiger", "webull"])
def test_us_stock_live_execution_accepts_configured_stock_brokers(exchange_id):
    StrategyV2DeploymentService._validate_execution_account(
        ("USStock",),
        exchange_id,
        "live",
    )


def test_signal_mode_does_not_require_a_live_credential():
    StrategyV2DeploymentService._validate_execution_account(
        ("Crypto", "USStock"),
        "",
        "signal",
    )


def test_hk_stock_live_execution_accepts_tiger_only():
    StrategyV2DeploymentService._validate_execution_account(
        ("HKStock",),
        "tiger",
        "live",
    )
    with pytest.raises(StrategyV2ContractError, match="strategyV2.stockCredentialRequired"):
        StrategyV2DeploymentService._validate_execution_account(
            ("HKStock",),
            "webull",
            "live",
        )


def _stock_live_strategy(exchange_id="alpaca"):
    return {
        "id": 19,
        "user_id": 7,
        "execution_mode": "live",
        "market_type": "spot",
        "symbol": "AAPL",
        "exchange_config": {"credential_id": 31},
        "trading_config": {"market_type": "spot"},
        "resolved_exchange_id": exchange_id,
    }


@pytest.mark.parametrize(
    ("exchange_id", "summary"),
    [
        (
            "alpaca",
            {
                "success": True,
                "status": "ACTIVE",
                "transfers_blocked": True,
            },
        ),
        ("ibkr", {"success": True, "account": "DU123"}),
    ],
)
def test_stock_live_preflight_validates_the_execution_account(
    monkeypatch,
    exchange_id,
    summary,
):
    strategy = _stock_live_strategy(exchange_id)
    executor = TradingExecutor()
    executor._load_strategy = lambda _strategy_id: strategy
    monkeypatch.setattr(
        exchange_execution,
        "resolve_exchange_config",
        lambda *_args, **_kwargs: {"exchange_id": exchange_id},
    )
    monkeypatch.setattr(
        strategy_live_guard,
        "find_live_strategy_conflict",
        lambda *_args, **_kwargs: None,
    )
    disconnected = []
    client = type(
        "Client",
        (),
        {
            "get_account_summary": lambda self: summary,
            "disconnect": lambda self: disconnected.append(True),
        },
    )()
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: client)

    executor._preflight_live_strategy(19)

    assert disconnected == [True]


def test_stock_live_preflight_rejects_a_blocked_broker_account(monkeypatch):
    executor = TradingExecutor()
    executor._load_strategy = lambda _strategy_id: _stock_live_strategy("alpaca")
    monkeypatch.setattr(
        exchange_execution,
        "resolve_exchange_config",
        lambda *_args, **_kwargs: {"exchange_id": "alpaca"},
    )
    monkeypatch.setattr(
        strategy_live_guard,
        "find_live_strategy_conflict",
        lambda *_args, **_kwargs: None,
    )
    client = type(
        "Client",
        (),
        {
            "get_account_summary": lambda self: {
                "success": True,
                "status": "ACTIVE",
                "trading_blocked": True,
            },
            "disconnect": lambda self: None,
        },
    )()
    monkeypatch.setattr(factory, "create_client", lambda *_args, **_kwargs: client)

    with pytest.raises(RuntimeError, match="strategyV2.connectionFailed"):
        executor._preflight_live_strategy(19)
