import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.market_price_stream import PublicMarketPriceFeed
from app.services.shared_market_price_feed import (
    SharedPublicMarketPriceFeedRegistry,
    public_feed_key,
)


def _feed(exchange_id="binance", market_type="swap", fallback=None):
    return PublicMarketPriceFeed(
        exchange_id=exchange_id,
        market_type=market_type,
        instruments=[{
            "key": "Crypto:BTC/USDT@binance:swap",
            "symbol": "BTC/USDT",
        }],
        rest_fallback=fallback or (lambda: {}),
    )


def test_public_price_feed_normalizes_binance_and_okx_symbols():
    binance = _feed("binance")
    for symbol, price in binance._parse({"data": {"s": "BTCUSDT", "p": "101.5"}}):
        binance._update(symbol, price)
    snapshot = binance.snapshot(max_age_seconds=5)
    assert snapshot.prices["Crypto:BTC/USDT@binance:swap"] == 101.5
    assert snapshot.source == "public_websocket"

    okx = PublicMarketPriceFeed(
        exchange_id="okx",
        market_type="swap",
        instruments=[{"key": "Crypto:BTC/USDT@okx:swap", "symbol": "BTC/USDT"}],
        rest_fallback=lambda: {},
    )
    for symbol, price in okx._parse({"data": [{"instId": "BTC-USDT-SWAP", "last": "102"}]}):
        okx._update(symbol, price)
    assert okx.snapshot().prices["Crypto:BTC/USDT@okx:swap"] == 102


@pytest.mark.parametrize("market_type", ("spot", "swap"))
def test_okx_public_price_feed_uses_port_443_websocket(market_type):
    assert _feed("okx", market_type)._url() == "wss://ws.okx.com/ws/v5/public"


def test_binance_spot_ticker_uses_last_price_instead_of_price_change():
    feed = _feed("binance", market_type="spot")

    rows = feed._parse({
        "data": {
            "s": "BTCUSDT",
            "p": "118.52",
            "c": "2590.81",
        },
    })

    assert rows == [("BTCUSDT", 2590.81)]


def test_spot_uses_last_trade_while_derivatives_prefer_mark_price():
    fixtures = {
        "okx": ({"data": [{"instId": "BTC-USDT-SWAP", "last": "100", "markPx": "101"}]}, 100, 101),
        "bybit": ({"data": {"symbol": "BTCUSDT", "lastPrice": "100", "markPrice": "101"}}, 100, 101),
        "bitget": ({"data": [{"instId": "BTCUSDT", "lastPr": "100", "markPrice": "101"}]}, 100, 101),
        "gate": ({"result": {"contract": "BTC_USDT", "currency_pair": "BTC_USDT", "last": "100", "mark_price": "101"}}, 100, 101),
    }

    for exchange_id, (payload, spot_price, swap_price) in fixtures.items():
        assert _feed(exchange_id, "spot")._parse(payload)[0][1] == spot_price
        assert _feed(exchange_id, "swap")._parse(payload)[0][1] == swap_price


def test_price_payload_without_a_supported_price_is_ignored_by_the_cache():
    feed = _feed("bybit", "spot")
    rows = feed._parse({"data": {"symbol": "BTCUSDT"}})

    assert rows == [("BTCUSDT", 0.0)]
    for symbol, price in rows:
        feed._update(symbol, price)
    assert feed.snapshot(max_age_seconds=5).prices == {}


def test_public_price_feed_uses_rest_only_for_missing_or_stale_prices():
    feed = _feed(fallback=lambda: {"Crypto:BTC/USDT@binance:swap": 99.0})
    fallback = feed.snapshot(max_age_seconds=1)
    assert fallback.source == "rest_fallback"
    assert fallback.prices["Crypto:BTC/USDT@binance:swap"] == 99.0

    feed._prices["Crypto:BTC/USDT@binance:swap"] = (101.0, time.monotonic())
    streamed = feed.snapshot(max_age_seconds=1)
    assert streamed.source == "public_websocket"
    assert streamed.prices["Crypto:BTC/USDT@binance:swap"] == 101.0


def test_reality_feed_uses_rest_until_reality_websocket_is_verified():
    feed = PublicMarketPriceFeed(
        exchange_id="bitget",
        market_type="spot",
        instruments=[{
            "key": "Crypto:RAAPL/USDT@bitget:spot",
            "symbol": "RAAPL/USDT",
            "instrument_id": "rAAPLUSDT",
            "api_family": "reality",
        }],
        rest_fallback=lambda: {},
    )

    assert feed.supported is False
    assert feed._symbols() == ["rAAPLUSDT"]


def test_gate_stock_feed_does_not_subscribe_to_crypto_spot_channel():
    feed = PublicMarketPriceFeed(
        exchange_id="gate",
        market_type="spot",
        instruments=[{
            "key": "Crypto:AAPL/USD@gate:spot",
            "symbol": "AAPL/USD",
            "instrument_id": "AAPL",
            "api_family": "stock",
        }],
        rest_fallback=lambda: {"Crypto:AAPL/USD@gate:spot": 200.0},
    )

    assert feed.supported is False
    assert feed.snapshot().prices["Crypto:AAPL/USD@gate:spot"] == 200.0


def test_shared_feed_key_is_exchange_and_market_scoped():
    instruments = [{
        "key": "Crypto:BTC/USDT",
        "symbol": "BTC/USDT",
        "instrument_id": "BTCUSDT",
    }]

    binance_swap = public_feed_key(
        exchange_id="binance",
        market_type="swap",
        instruments=instruments,
    )
    bybit_swap = public_feed_key(
        exchange_id="bybit",
        market_type="swap",
        instruments=instruments,
    )
    binance_spot = public_feed_key(
        exchange_id="binance",
        market_type="spot",
        instruments=instruments,
    )

    assert binance_swap != bybit_swap
    assert binance_swap != binance_spot


def test_shared_registry_reuses_only_the_same_exchange_subscription(monkeypatch):
    starts = []
    stops = []
    monkeypatch.setattr(PublicMarketPriceFeed, "start", lambda self: starts.append(self.exchange_id))
    monkeypatch.setattr(PublicMarketPriceFeed, "stop", lambda self, timeout=3.0: stops.append(self.exchange_id))
    registry = SharedPublicMarketPriceFeedRegistry()
    binance_instruments = [{
        "key": "Crypto:BTC/USDT@binance:swap",
        "symbol": "BTC/USDT",
        "instrument_id": "BTCUSDT",
    }]
    bybit_instruments = [{
        "key": "Crypto:BTC/USDT@bybit:swap",
        "symbol": "BTC/USDT",
        "instrument_id": "BTCUSDT",
    }]

    first = registry.acquire(
        exchange_id="binance",
        market_type="swap",
        instruments=binance_instruments,
        rest_fallback=lambda: {},
    )
    second = registry.acquire(
        exchange_id="binance",
        market_type="perpetual",
        instruments=binance_instruments,
        rest_fallback=lambda: {},
    )
    bybit = registry.acquire(
        exchange_id="bybit",
        market_type="swap",
        instruments=bybit_instruments,
        rest_fallback=lambda: {},
    )

    assert first._entry is second._entry
    assert first._entry is not bybit._entry
    assert starts == ["binance", "bybit"]
    assert registry.snapshot() == {"feeds": 2, "references": 3}

    first.release()
    assert stops == []
    second.release()
    assert stops == ["binance"]
    bybit.release()
    assert stops == ["binance", "bybit"]


def test_shared_prices_do_not_cross_exchange_boundaries(monkeypatch):
    monkeypatch.setattr(PublicMarketPriceFeed, "start", lambda self: None)
    monkeypatch.setattr(PublicMarketPriceFeed, "stop", lambda self, timeout=3.0: None)
    registry = SharedPublicMarketPriceFeedRegistry()
    binance_key = "Crypto:BTC/USDT@binance:swap"
    bybit_key = "Crypto:BTC/USDT@bybit:swap"
    binance = registry.acquire(
        exchange_id="binance",
        market_type="swap",
        instruments=[{"key": binance_key, "symbol": "BTC/USDT"}],
        rest_fallback=lambda: {},
    )
    bybit = registry.acquire(
        exchange_id="bybit",
        market_type="swap",
        instruments=[{"key": bybit_key, "symbol": "BTC/USDT"}],
        rest_fallback=lambda: {},
    )

    binance._entry.feed._update("BTCUSDT", 101.0)
    bybit._entry.feed._update("BTCUSDT", 99.0)

    assert binance.snapshot().prices == {binance_key: 101.0}
    assert bybit.snapshot().prices == {bybit_key: 99.0}

    binance.release()
    bybit.release()


def test_shared_feed_coalesces_rest_fallback(monkeypatch):
    monkeypatch.setattr(PublicMarketPriceFeed, "start", lambda self: None)
    monkeypatch.setattr(PublicMarketPriceFeed, "stop", lambda self, timeout=3.0: None)
    registry = SharedPublicMarketPriceFeedRegistry()
    key = "Crypto:BTC/USDT@binance:swap"
    calls = []

    def fallback():
        calls.append(1)
        return {key: 100.0}

    first = registry.acquire(
        exchange_id="binance",
        market_type="swap",
        instruments=[{"key": key, "symbol": "BTC/USDT"}],
        rest_fallback=fallback,
        fallback_ttl_seconds=5.0,
    )
    second = registry.acquire(
        exchange_id="binance",
        market_type="swap",
        instruments=[{"key": key, "symbol": "BTC/USDT"}],
        rest_fallback=fallback,
        fallback_ttl_seconds=5.0,
    )

    assert first.snapshot().prices[key] == 100.0
    assert second.snapshot().prices[key] == 100.0
    assert len(calls) == 1

    first.release()
    second.release()


def test_concurrent_shared_feed_acquire_creates_one_connection(monkeypatch):
    starts = []
    stops = []
    monkeypatch.setattr(PublicMarketPriceFeed, "start", lambda self: starts.append(self.exchange_id))
    monkeypatch.setattr(PublicMarketPriceFeed, "stop", lambda self, timeout=3.0: stops.append(self.exchange_id))
    registry = SharedPublicMarketPriceFeedRegistry()
    instruments = [{
        "key": "Crypto:BTC/USDT@binance:swap",
        "symbol": "BTC/USDT",
        "instrument_id": "BTCUSDT",
    }]

    def acquire(_index):
        return registry.acquire(
            exchange_id="binance",
            market_type="swap",
            instruments=instruments,
            rest_fallback=lambda: {},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        handles = list(pool.map(acquire, range(64)))

    assert starts == ["binance"]
    assert registry.snapshot() == {"feeds": 1, "references": 64}

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda handle: handle.release(), handles))

    assert stops == ["binance"]
    assert registry.snapshot() == {"feeds": 0, "references": 0}
