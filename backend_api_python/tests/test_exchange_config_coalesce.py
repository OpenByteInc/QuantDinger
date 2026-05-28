"""exchange_config coalescing for strategy create/update payloads."""
from app.services.broker_market_policy import validate_strategy_config
from app.services.exchange_execution import coalesce_exchange_config_from_payload


def test_coalesce_prefers_explicit_exchange_config():
    payload = {
        "exchange_config": {"exchange_id": "binance"},
        "trading_config": {"exchange_id": "okx"},
    }
    assert coalesce_exchange_config_from_payload(payload)["exchange_id"] == "binance"


def test_coalesce_reads_exchange_id_from_trading_config():
    payload = {
        "trading_config": {
            "exchange_id": "bybit",
            "symbol": "BTC/USDT",
            "market_type": "swap",
        },
    }
    cfg = coalesce_exchange_config_from_payload(payload)
    assert cfg["exchange_id"] == "bybit"


def test_coalesce_reads_credential_id_from_trading_config():
    payload = {
        "trading_config": {"credential_id": 42},
    }
    cfg = coalesce_exchange_config_from_payload(payload)
    assert cfg["credential_id"] == "42"


def test_coalesce_reads_root_exchange_id():
    payload = {
        "exchange_id": "gate",
        "trading_config": {"symbol": "ETH/USDT"},
    }
    cfg = coalesce_exchange_config_from_payload(payload)
    assert cfg["exchange_id"] == "gate"


def test_live_script_strategy_validates_after_trading_config_coalesce():
    cfg = coalesce_exchange_config_from_payload(
        {
            "execution_mode": "live",
            "market_category": "Crypto",
            "strategy_type": "ScriptStrategy",
            "trading_config": {
                "exchange_id": "binance",
                "market_type": "swap",
                "trade_direction": "both",
            },
        }
    )
    validate_strategy_config(
        exchange_id=cfg.get("exchange_id"),
        market_category="Crypto",
        market_type="swap",
        trade_direction="both",
        require_exchange=True,
    )
