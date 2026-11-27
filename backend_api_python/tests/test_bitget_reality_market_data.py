from datetime import datetime, timezone

import pytest

from app.data_providers import bitget_reality_market
from app.services import kline as kline_module
from app.services.strategy_v2 import market_data
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.service import (
    _attach_catalog_products,
    _validate_product_frequencies,
)


class _Response:
    status_code = 200
    headers = {}
    text = '{"code":"00000"}'

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "code": "00000",
            "data": [["1789000000000", "100", "103", "99", "102", "12.5", "1260"]],
        }


def test_bitget_reality_public_candles_use_native_symbol_and_v3(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(bitget_reality_market.requests, "get", get)
    rows = bitget_reality_market.get_bitget_reality_klines(
        "RAAPL/USDT", "1m", 20, before_time=1789000100, after_time=1788999900,
    )

    assert rows == [{
        "time": 1789000000,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 12.5,
    }]
    assert calls[0][0].endswith("/api/v3/market/candles")
    assert calls[0][1]["params"]["symbol"] == "RAAPLUSDT"
    assert calls[0][1]["params"]["category"] == "SPOT"


def test_indicator_kline_routes_catalogued_reality_product(monkeypatch):
    monkeypatch.setattr(kline_module, "get_catalog_product", lambda **kwargs: {
        "api_family": "reality",
        "instrument_id": "RAAPLUSDT",
    })
    calls = []
    monkeypatch.setattr(
        kline_module,
        "get_bitget_reality_klines",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [{"time": 1, "close": 10}],
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generic provider should not run")),
    )
    service = kline_module.KlineService()
    service.cache = type("Cache", (), {"get": lambda *_: None, "set": lambda *_: None})()

    rows = service.get_kline(
        "Crypto", "RAAPL/USDT", "1m", 20,
        exchange_id="bitget", market_type="spot", instrument_id="RAAPLUSDT",
    )

    assert rows == [{"time": 1, "close": 10}]
    assert calls[0][0][0] == "RAAPLUSDT"


def test_backtest_catalog_contract_routes_reality_and_rejects_unsupported_interval(monkeypatch):
    monkeypatch.setattr(
        "app.services.strategy_v2.service.get_catalog_product",
        lambda **kwargs: {
            "instrument_id": "RAAPLUSDT",
            "product_type": "tokenized_equity",
            "api_family": "reality",
            "underlying_market": "USStock",
            "underlying_symbol": "AAPL",
        },
    )
    members = [{
        "key": "Crypto:RAAPL/USDT@bitget:spot",
        "market": "Crypto",
        "symbol": "RAAPL/USDT",
        "exchange_id": "bitget",
        "market_type": "spot",
        "instrument_id": "Crypto:RAAPL/USDT@bitget:spot",
    }]

    products = _attach_catalog_products(members)

    assert members[0]["instrument_id"] == "RAAPLUSDT"
    assert members[0]["api_family"] == "reality"
    assert products[0]["underlying_symbol"] == "AAPL"
    _validate_product_frequencies(products, ("1m", "1d"))
    with pytest.raises(StrategyV2ContractError, match="strategyV2.bitgetRealityTimeframeUnsupported:1w"):
        _validate_product_frequencies(products, ("1w",))


def test_strategy_market_data_uses_reality_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bitget_reality_market,
        "get_bitget_reality_klines",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [
            {"time": 1788999960, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            {"time": 1789000020, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
        ],
    )
    monkeypatch.setattr(market_data, "_covers_crypto_window", lambda *args, **kwargs: True)

    frame = market_data._load_strategy_frame_uncached(
        "Crypto",
        "RAAPL/USDT",
        "1m",
        datetime.fromtimestamp(1788999900, tz=timezone.utc),
        datetime.fromtimestamp(1789000100, tz=timezone.utc),
        market_type="spot",
        exchange_id="bitget",
        instrument_id="RAAPLUSDT",
        api_family="reality",
    )

    assert list(frame["close"]) == [1, 2]
    assert calls[0][0][0] == "RAAPLUSDT"
