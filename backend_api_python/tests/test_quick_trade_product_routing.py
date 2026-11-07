import pytest

from app.services.live_trading.base import LiveOrderResult
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.execution import place_order_from_signal
from app.services.quick_trade import products


def _catalog_product(**overrides):
    product = {
        "asset_id": 41,
        "market": "Crypto",
        "symbol": "AAPL/USDT",
        "exchange_id": "bitget",
        "market_type": "spot",
        "instrument_id": "rAAPLUSDT",
        "settle_currency": "USDT",
        "product_type": "tokenized_equity",
        "api_family": "reality",
        "product_meta": {"status": "online"},
    }
    product.update(overrides)
    return product


def test_quick_trade_product_identity_selects_specialized_client_config(monkeypatch):
    product = _catalog_product()
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "bitget", "market_type": "spot"})
    monkeypatch.setattr(products, "resolve_runtime_instrument", lambda **kwargs: product)
    monkeypatch.setattr(products, "validate_runtime_products", lambda values, **kwargs: values)
    monkeypatch.setattr(products, "validate_product_account_environment", lambda *args, **kwargs: None)

    config, resolved = products.build_product_aware_config(
        7,
        11,
        symbol="AAPL/USDT",
        market_type="spot",
        source={"asset_id": 41, "instrument_id": "rAAPLUSDT", "product_type": "tokenized_equity", "api_family": "reality"},
    )

    assert resolved == product
    assert config["api_family"] == "reality"
    assert config["instrument_id"] == "rAAPLUSDT"
    assert config["instrument_product_type"] == "tokenized_equity"
    assert config["instrument_product_meta"] == {"status": "online"}


def test_catalog_product_type_does_not_override_bitget_execution_category(monkeypatch):
    product = _catalog_product(
        symbol="BTC/USDT",
        instrument_id="BTCUSDT",
        product_type="crypto",
        api_family="swap",
        market_type="swap",
    )
    monkeypatch.setattr(
        products,
        "build_exchange_config",
        lambda *args, **kwargs: {
            "exchange_id": "bitget",
            "market_type": "swap",
            "product_type": "USDT-FUTURES",
        },
    )
    monkeypatch.setattr(products, "resolve_runtime_instrument", lambda **kwargs: product)
    monkeypatch.setattr(products, "validate_runtime_products", lambda values, **kwargs: values)
    monkeypatch.setattr(products, "validate_product_account_environment", lambda *args, **kwargs: None)

    config, resolved = products.build_product_aware_config(
        7,
        11,
        symbol="BTC/USDT",
        market_type="swap",
        source={
            "asset_id": 42,
            "instrument_id": "BTCUSDT",
            "product_type": "crypto",
            "api_family": "swap",
        },
    )

    assert resolved == product
    assert config["product_type"] == "USDT-FUTURES"
    assert config["instrument_product_type"] == "crypto"


def test_catalog_identity_does_not_change_bitget_outbound_order_category():
    captured = {}

    class FakeBitgetMixClient(BitgetMixClient):
        def __init__(self):
            pass

        def place_market_order(self, **kwargs):
            captured.update(kwargs)
            return LiveOrderResult(
                exchange_id="bitget",
                exchange_order_id="order-1",
                filled=0.01,
                avg_price=100.0,
                raw={},
            )

    place_order_from_signal(
        client=FakeBitgetMixClient(),
        signal_type="open_long",
        symbol="BTC/USDT",
        amount=0.01,
        market_type="swap",
        exchange_config={
            "exchange_id": "bitget",
            "market_type": "swap",
            "product_type": "USDT-FUTURES",
            "instrument_product_type": "crypto",
            "instrument_id": "BTCUSDT",
            "api_family": "swap",
        },
    )

    assert captured["product_type"] == "USDT-FUTURES"


def test_quick_trade_rejects_product_contract_mismatch(monkeypatch):
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "bitget"})
    monkeypatch.setattr(products, "resolve_runtime_instrument", lambda **kwargs: _catalog_product(product_type="crypto"))

    with pytest.raises(ValueError, match="equityProductContractChanged"):
        products.build_product_aware_config(
            7,
            11,
            symbol="AAPL/USDT",
            market_type="spot",
            source={"instrument_id": "rAAPLUSDT", "product_type": "tokenized_equity", "api_family": "reality"},
        )


def test_regular_crypto_quick_trade_resolves_the_account_catalog(monkeypatch):
    product = _catalog_product(
        asset_id=7,
        symbol="BTC/USDT",
        instrument_id="BTCUSDT",
        product_type="crypto",
        api_family="spot",
    )
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "binance", "market_type": "spot"})
    monkeypatch.setattr(products, "resolve_runtime_instrument", lambda **kwargs: product)
    monkeypatch.setattr(products, "validate_runtime_products", lambda values, **kwargs: values)
    monkeypatch.setattr(products, "validate_product_account_environment", lambda *args, **kwargs: None)

    config, resolved = products.build_product_aware_config(
        3,
        5,
        symbol="BTC/USDT",
        market_type="spot",
        source={"asset_id": 7},
    )

    assert config["exchange_id"] == "binance"
    assert resolved == product


def test_uncatalogued_regular_crypto_fails_closed(monkeypatch):
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "binance", "market_type": "spot"})
    monkeypatch.setattr(
        products,
        "resolve_runtime_instrument",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("strategyV2.instrumentUnavailableForAccount")),
    )

    with pytest.raises(ValueError, match="instrumentUnavailableForAccount"):
        products.build_product_aware_config(
            3,
            5,
            symbol="BTC/USDT",
            market_type="spot",
            source={"resolve_product": True},
        )


def test_catalog_refresh_does_not_bypass_live_product_validation(monkeypatch):
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "bitget", "market_type": "spot"})
    monkeypatch.setattr(
        products,
        "resolve_runtime_instrument",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("strategyV2.instrumentUnavailableForAccount")),
    )

    with pytest.raises(ValueError, match="instrumentUnavailableForAccount"):
        products.build_product_aware_config(
            7,
            11,
            symbol="ETH/USDT",
            market_type="spot",
            source={
                "instrument_id": "ETHUSDT",
                "product_type": "crypto",
                "api_family": "spot",
                "resolve_product": True,
            },
        )


def test_specialized_equity_identity_still_requires_catalog(monkeypatch):
    monkeypatch.setattr(products, "build_exchange_config", lambda *args, **kwargs: {"exchange_id": "binance", "market_type": "swap"})
    monkeypatch.setattr(
        products,
        "resolve_runtime_instrument",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("strategyV2.instrumentUnavailableForAccount")),
    )

    with pytest.raises(ValueError, match="instrumentUnavailableForAccount"):
        products.build_product_aware_config(
            7,
            11,
            symbol="QQQ/USDT",
            market_type="swap",
            source={
                "instrument_id": "QQQUSDT",
                "product_type": "stock_perpetual",
                "api_family": "swap",
                "resolve_product": True,
            },
        )
