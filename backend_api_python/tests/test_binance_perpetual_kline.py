from importlib import import_module
from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from flask import Flask

from app.data_sources.crypto import CryptoDataSource
from app.data_sources.factory import DataSourceFactory
from app.data_sources.native_crypto import create_native_crypto_client
from app.services.market_context import MarketContext
from app.services.strategy_v2 import StrategyV2BacktestService, StrategyV2ContractError, market_data


def _binance_market(**overrides):
    market = {
        "id": "BTCUSDT",
        "symbol": "BTC/USDT:USDT",
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "spot": False,
        "swap": True,
        "contract": True,
        "linear": True,
        "future": False,
        "type": "swap",
        "info": {
            "contractType": "PERPETUAL",
            "status": "TRADING",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
        },
    }
    market.update(overrides)
    return market


def _strict_binance_source(rows, *, server_time_ms=179_999):
    market = _binance_market()
    source = object.__new__(CryptoDataSource)
    source._allow_public_fallback = False
    source._scoped_exchange_id = "binance"
    source._scoped_market_type = "swap"
    source._preferred_public_exchange_id = ""
    source._markets_load_lock = Lock()
    source._markets_loaded = True
    source._markets_cache = {
        "BTC/USDT:USDT": market,
        "BTC/USDT": market,
    }
    source._failure_local = None
    source.exchange = SimpleNamespace(
        id="binance",
        market_type="swap",
        timeframes={"1m": "1m"},
        fetch_server_time=lambda: server_time_ms,
    )
    source._fetch_ohlcv = lambda *_args, **_kwargs: rows
    source.log_result = lambda *_args, **_kwargs: None
    return source


def test_btc_swap_identifier_normalizes_to_binance_usdm_identity():
    context = MarketContext.from_mapping({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT@swap",
    })

    assert context.symbol == "BTC/USDT"
    assert context.exchange_id == "binance"
    assert context.market_type == "swap"


def test_plain_crypto_symbol_remains_spot_and_settlement_suffix_is_parsed():
    spot = MarketContext.from_mapping({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT",
    })
    perpetual = MarketContext.from_mapping({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT:USDT@binance:swap",
    })

    assert (spot.symbol, spot.market_type) == ("BTC/USDT", "spot")
    assert (perpetual.symbol, perpetual.exchange_id, perpetual.market_type) == (
        "BTC/USDT",
        "binance",
        "swap",
    )


@pytest.mark.parametrize(
    ("symbol", "market_type", "exchange_id"),
    [
        ("Crypto:BTC/USDT@quarterly", None, None),
        ("Crypto:BTC/USDT@swap", "spot", None),
        ("Crypto:BTC/USDT@binance:swap", None, "okx"),
        ("Crypto:BTC/USDT:BTC@swap", None, None),
    ],
)
def test_crypto_identifier_rejects_unknown_or_conflicting_product_metadata(
    symbol,
    market_type,
    exchange_id,
):
    payload = {"market": "Crypto", "symbol": symbol}
    if market_type:
        payload["market_type"] = market_type
    if exchange_id:
        payload["exchange_id"] = exchange_id

    with pytest.raises(ValueError):
        MarketContext.from_mapping(payload)


def test_kline_route_passes_normalized_binance_swap_identity(monkeypatch):
    route = import_module("app.routes.kline")
    app = Flask(__name__)
    captured = {}
    rows = [{"time": 60, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]

    monkeypatch.setattr(route, "guarded_cached", lambda _key, loader, **_kwargs: loader())
    monkeypatch.setattr(
        route.kline_service,
        "get_kline",
        lambda **kwargs: captured.update(kwargs) or rows,
    )
    query = urlencode({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT@swap",
        "timeframe": "1m",
        "limit": 1,
    })

    with app.test_request_context(f"/api/indicator/kline?{query}"):
        response = route.get_kline()

    payload = response.get_json()
    assert payload["code"] == 1
    assert captured["symbol"] == "BTC/USDT"
    assert captured["exchange_id"] == "binance"
    assert captured["market_type"] == "swap"
    assert payload["meta"]["market_context"]["symbol"] == "BTC/USDT"
    assert payload["meta"]["market_context"]["exchange_id"] == "binance"
    assert payload["meta"]["market_context"]["market_type"] == "swap"


@pytest.mark.parametrize(
    ("extra", "expected_message"),
    [
        ({"exchange_id": "okx"}, "mapped to Binance USDⓈ-M"),
        ({"market_type": "spot"}, "conflicts with requested market_type"),
    ],
)
def test_kline_route_rejects_wrong_exchange_or_spot_override(
    monkeypatch,
    extra,
    expected_message,
):
    route = import_module("app.routes.kline")
    app = Flask(__name__)
    calls = []
    monkeypatch.setattr(route, "guarded_cached", lambda _key, loader, **_kwargs: calls.append(True) or loader())
    query = urlencode({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT@swap",
        **extra,
    })

    with app.test_request_context(f"/api/indicator/kline?{query}"):
        result = route.get_kline()

    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code
    assert status == 400
    assert expected_message in response.get_json()["msg"]
    assert calls == []


@pytest.mark.parametrize(
    "mutated_market",
    [
        {"spot": True, "swap": False, "contract": False, "linear": False, "type": "spot"},
        {"linear": False, "settle": "BTC"},
        {"settle": "USDC"},
        {"id": "BTCUSDC", "info": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "marginAsset": "USDT"}},
        {"info": {"contractType": "CURRENT_QUARTER", "status": "TRADING", "quoteAsset": "USDT", "marginAsset": "USDT"}},
        {"info": {"contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "BTC", "marginAsset": "BTC"}},
    ],
)
def test_btc_usdm_market_validation_rejects_spot_inverse_or_wrong_contract(mutated_market):
    market = _binance_market(**mutated_market)
    source = object.__new__(CryptoDataSource)
    source._scoped_market_type = "swap"
    source._markets_loaded = True
    source._markets_cache = {"BTC/USDT:USDT": market}
    source.exchange = SimpleNamespace(id="binance", market_type="swap")

    with pytest.raises(ValueError):
        source._validate_binance_btc_usdm_market("BTC/USDT:USDT")


def test_btc_usdm_market_validation_returns_confirmed_contract_metadata():
    market = _binance_market()
    source = object.__new__(CryptoDataSource)
    source._scoped_market_type = "swap"
    source._markets_loaded = True
    source._markets_cache = {"BTC/USDT:USDT": market}
    source.exchange = SimpleNamespace(id="binance", market_type="swap")

    assert source._validate_binance_btc_usdm_market("BTC/USDT:USDT") is market


def test_binance_swap_data_source_is_scoped_and_has_no_public_fallback():
    source = DataSourceFactory._resolve_source(
        "Crypto",
        exchange_id="binance",
        market_type="swap",
    )

    assert source.exchange.id == "binance"
    assert source.exchange.market_type == "swap"
    assert source._allow_public_fallback is False


def test_binance_swap_source_does_not_fall_back_to_spot_or_another_venue(monkeypatch):
    source = _strict_binance_source([])
    fallback_attempts = []
    monkeypatch.setattr(
        CryptoDataSource,
        "for_exchange",
        classmethod(
            lambda _cls, exchange_id, market_type: fallback_attempts.append((exchange_id, market_type))
        ),
    )

    assert source.get_kline("BTC/USDT", "1m", 10) == []
    assert fallback_attempts == []


def test_strategy_v2_rejects_invalid_binance_contract_without_spot_fallback(monkeypatch):
    invalid_market = _binance_market(
        quote="USDC",
        settle="USDC",
        info={
            "contractType": "PERPETUAL",
            "status": "TRADING",
            "quoteAsset": "USDC",
            "marginAsset": "USDC",
        },
    )
    source = _strict_binance_source([])
    source._markets_cache = {"BTC/USDT:USDT": invalid_market}
    generic_calls = []

    def resolve_source(_cls, market, *, exchange_id=None, market_type=None):
        assert market == "Crypto"
        assert exchange_id == "binance"
        assert market_type == "swap"
        return source

    monkeypatch.setattr(DataSourceFactory, "_resolve_source", classmethod(resolve_source))
    monkeypatch.setattr(market_data, "_resolve_catalog_product", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(
        DataSourceFactory,
        "get_kline",
        lambda **kwargs: generic_calls.append(kwargs),
    )

    service = StrategyV2BacktestService(frame_fetcher=market_data.load_strategy_frame)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candidate = {
        "key": "Crypto:BTC/USDT@binance:swap",
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "exchange_id": "binance",
        "market_type": "swap",
    }
    with pytest.raises(StrategyV2ContractError, match="explicitBinancePerpetualDataUnavailable"):
        service.fetch_frequency_frames(
            [candidate],
            ("1m",),
            {"1m": start},
            start + timedelta(minutes=1),
        )

    assert generic_calls == []


def test_binance_perpetual_candles_use_public_usdm_endpoint_without_credentials(monkeypatch):
    client = create_native_crypto_client("binance", "swap")
    calls = []

    def fake_get(url, params=None):
        calls.append((url, params))
        return [[0, "1", "2", "0.5", "1.5", "10"]]

    monkeypatch.setattr(client, "_get", fake_get)
    rows = client.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=1)

    assert rows[0][0] == 0
    assert calls == [(
        "https://fapi.binance.com/fapi/v1/klines",
        {"symbol": "BTCUSDT", "interval": "1m", "limit": 1},
    )]


def test_binance_public_server_time_uses_usdm_endpoint(monkeypatch):
    client = create_native_crypto_client("binance", "swap")
    calls = []
    monkeypatch.setattr(
        client,
        "_get",
        lambda url: calls.append(url) or {"serverTime": 123_456},
    )

    assert client.fetch_server_time() == 123_456
    assert calls == ["https://fapi.binance.com/fapi/v1/time"]


def test_only_unique_closed_binance_perpetual_candles_are_returned():
    rows = [
        [120_000, 12, 13, 11, 12.5, 3],  # Still open at exchange time 179999.
        [60_000, 6, 7, 5, 6.5, 2],
        [0, 1, 2, 0.5, 1.5, 1],
        [60_000, 60, 70, 50, 65, 20],  # Duplicate timestamp; latest row wins.
    ]
    source = _strict_binance_source(rows)

    result = source.get_kline("BTC/USDT", "1m", 10)

    assert [row["time"] for row in result] == [0, 60]
    assert result[1]["close"] == 65.0
