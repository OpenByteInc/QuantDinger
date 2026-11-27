from app.data_providers import gate_public_market
from app.services import kline as kline_module


class _Response:
    status_code = 200
    headers = {}
    text = "[]"

    def raise_for_status(self):
        return None

    def json(self):
        return [
            ["200", "22", "105", "108", "99", "100", "11", "false"],
            ["100", "20", "101", "103", "98", "99", "10", "true"],
        ]


class _Cache:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ttl):
        self.data[key] = value


def test_gate_native_candles_are_normalized_to_pure_ohlcv(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(gate_public_market.requests, "get", fake_get)
    rows = gate_public_market.get_gate_spot_klines("BTC/USDT", "1D", 120)

    assert captured["params"] == {"currency_pair": "BTC_USDT", "interval": "1d", "limit": 120}
    assert rows == [
        {"time": 100, "open": 99.0, "high": 103.0, "low": 98.0, "close": 101.0, "volume": 10.0},
        {"time": 200, "open": 100.0, "high": 108.0, "low": 99.0, "close": 105.0, "volume": 11.0},
    ]
    assert all(set(row) == {"time", "open", "high", "low", "close", "volume"} for row in rows)


def test_gate_native_retries_one_transient_timeout(monkeypatch):
    calls = []

    def flaky_get(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise gate_public_market.requests.Timeout("tls handshake jitter")
        return _Response()

    monkeypatch.setattr(gate_public_market.requests, "get", flaky_get)
    rows = gate_public_market.get_gate_spot_klines("ETH/USDT", "1W", 10)

    assert len(calls) == 2
    assert calls[-1][1]["params"]["interval"] == "7d"
    assert len(rows) == 2


def test_kline_service_prefers_native_gate_spot_and_caches(monkeypatch):
    native_calls = []
    fallback_calls = []
    rows = [{"time": 100, "open": 99, "high": 103, "low": 98, "close": 101, "volume": 10}]
    monkeypatch.setattr(
        kline_module,
        "get_gate_spot_klines",
        lambda symbol, timeframe, limit: native_calls.append((symbol, timeframe, limit)) or rows,
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **kwargs: fallback_calls.append(kwargs) or [],
    )
    service = kline_module.KlineService()
    service.cache = _Cache()

    first = service.get_kline("Crypto", "BTC/USDT", "1D", 120, exchange_id="gate", market_type="spot")
    second = service.get_kline("Crypto", "BTC/USDT", "1D", 120, exchange_id="gate", market_type="spot")

    assert first == second == rows
    assert native_calls == [("BTC/USDT", "1D", 120)]
    assert fallback_calls == []
    assert service.cache.data["kline:latest:Crypto:gate:spot::BTC/USDT:1D"] == rows


def test_kline_cache_separates_native_instrument_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(kline_module, "get_gate_spot_klines", lambda *_args: [])
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **kwargs: calls.append(kwargs) or [{"time": len(calls), "close": 1}],
    )
    service = kline_module.KlineService()
    service.cache = _Cache()

    first = service.get_kline(
        "Crypto", "AAPL/USDT", "1D", 10,
        exchange_id="gate", market_type="spot", instrument_id="AAPL_USDT",
    )
    second = service.get_kline(
        "Crypto", "AAPL/USDT", "1D", 10,
        exchange_id="gate", market_type="spot", instrument_id="AAPLX_USDT",
    )

    assert first != second
    assert len(calls) == 2


def test_existing_exact_cache_backfills_latest_snapshot(monkeypatch):
    service = kline_module.KlineService()
    service.cache = _Cache()
    rows = [{"time": index, "close": index} for index in range(600)]
    exact_key = "kline:Crypto:gate:spot::ETH/USDT:1m:600"
    latest_key = "kline:latest:Crypto:gate:spot::ETH/USDT:1m"
    service.cache.data[exact_key] = rows
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("cache miss")),
    )

    assert service.get_kline(
        "Crypto", "ETH/USDT", "1m", 600, exchange_id="gate", market_type="spot"
    ) == rows
    assert service.cache.data[latest_key] == rows[-120:]


def test_kline_service_uses_native_path_when_default_exchange_is_gate(monkeypatch):
    native_calls = []
    rows = [{"time": 100, "open": 99, "high": 103, "low": 98, "close": 101, "volume": 10}]

    class _GateDefaultConfig:
        DEFAULT_EXCHANGE = "gate"

    monkeypatch.setattr(kline_module, "CryptoPublicConfig", _GateDefaultConfig)
    monkeypatch.setattr(
        kline_module,
        "get_gate_spot_klines",
        lambda symbol, timeframe, limit: native_calls.append((symbol, timeframe, limit)) or rows,
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected standard-provider fallback")),
    )
    service = kline_module.KlineService()
    service.cache = _Cache()

    assert service.get_kline("Crypto", "BTC/USDT", "4H", 120) == rows
    assert native_calls == [("BTC/USDT", "4H", 120)]


def test_kline_service_falls_back_when_native_gate_is_unavailable(monkeypatch):
    fallback_rows = [{"time": 100, "open": 99, "high": 103, "low": 98, "close": 101, "volume": 10}]

    def fail_native(*_args, **_kwargs):
        raise TimeoutError("gate timeout")

    monkeypatch.setattr(kline_module, "get_gate_spot_klines", fail_native)
    monkeypatch.setattr(kline_module.DataSourceFactory, "get_kline", lambda **_kwargs: fallback_rows)
    service = kline_module.KlineService()
    service.cache = _Cache()

    assert service.get_kline(
        "Crypto", "BTC/USDT", "1D", 120, exchange_id="gate", market_type="spot"
    ) == fallback_rows


def test_gate_direct_stock_kline_uses_underlying_equity_series(monkeypatch):
    calls = []
    rows = [{"time": 100, "open": 199, "high": 202, "low": 198, "close": 201, "volume": 10}]
    monkeypatch.setattr(
        kline_module,
        "get_catalog_product",
        lambda **kwargs: {
            "product_type": "direct_equity",
            "api_family": "stock",
            "underlying_market": "USStock",
            "underlying_symbol": "AAPL",
        },
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **kwargs: calls.append(kwargs) or rows,
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_ticker",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("crypto ticker must not be used")),
    )
    service = kline_module.KlineService()
    service.cache = _Cache()

    result = service.get_realtime_price(
        "Crypto",
        "AAPL/USD",
        exchange_id="gate",
        market_type="spot",
        instrument_id="AAPL",
    )

    assert result["price"] == 201
    assert calls[0]["market"] == "USStock"
    assert calls[0]["symbol"] == "AAPL"


def test_gate_hk_direct_stock_kline_uses_hk_equity_series(monkeypatch):
    calls = []
    rows = [{"time": 100, "open": 490, "high": 502, "low": 488, "close": 500, "volume": 10}]
    monkeypatch.setattr(
        kline_module,
        "get_catalog_product",
        lambda **kwargs: {
            "product_type": "direct_equity",
            "api_family": "stock",
            "underlying_market": "HKStock",
            "underlying_symbol": "00700",
        },
    )
    monkeypatch.setattr(
        kline_module.DataSourceFactory,
        "get_kline",
        lambda **kwargs: calls.append(kwargs) or rows,
    )
    service = kline_module.KlineService()
    service.cache = _Cache()

    result = service.get_kline(
        "Crypto",
        "00700/HKD",
        "1D",
        10,
        exchange_id="gate",
        market_type="spot",
        instrument_id="00700",
    )

    assert result == rows
    assert calls == [{
        "market": "HKStock",
        "symbol": "00700",
        "timeframe": "1D",
        "limit": 10,
        "before_time": None,
    }]
