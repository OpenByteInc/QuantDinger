import pytest
import requests

from app.data_sources.native_crypto import NativeCryptoAPIError, NativeCryptoPublicClient


@pytest.mark.parametrize(
    ("exchange_id", "market_type", "expected"),
    [
        ("binance", "spot", "BTCUSDT"),
        ("bybit", "swap", "BTCUSDT"),
        ("bitget", "spot", "BTCUSDT"),
        ("okx", "swap", "BTC-USDT-SWAP"),
        ("gate", "spot", "BTC_USDT"),
        ("htx", "spot", "btcusdt"),
        ("htx", "swap", "BTC-USDT"),
    ],
)
def test_native_symbol_mapping(exchange_id, market_type, expected):
    client = NativeCryptoPublicClient(exchange_id, market_type)
    assert client._native_symbol("BTC/USDT:USDT") == expected


@pytest.mark.parametrize(
    ("exchange_id", "market_type", "payload", "expected_close"),
    [
        (
            "binance",
            "spot",
            [[1700000000000, "100", "110", "90", "105", "12"]],
            105.0,
        ),
        (
            "okx",
            "swap",
            {"code": "0", "data": [["1700000000000", "100", "110", "90", "106", "13"]]},
            106.0,
        ),
        (
            "bybit",
            "spot",
            {
                "retCode": 0,
                "result": {"list": [["1700000000000", "100", "110", "90", "107", "14"]]},
            },
            107.0,
        ),
        (
            "bitget",
            "swap",
            {"code": "00000", "data": [["1700000000000", "100", "110", "90", "108", "15"]]},
            108.0,
        ),
        (
            "gate",
            "spot",
            [["1700000000", "1000", "109", "110", "90", "100", "16"]],
            109.0,
        ),
        (
            "htx",
            "spot",
            {
                "status": "ok",
                "data": [{"id": 1700000000, "open": 100, "high": 110, "low": 90, "close": 111, "amount": 17}],
            },
            111.0,
        ),
    ],
)
def test_ohlcv_parsing(exchange_id, market_type, payload, expected_close, monkeypatch):
    client = NativeCryptoPublicClient(exchange_id, market_type)
    monkeypatch.setattr(client, "_get", lambda *_args, **_kwargs: payload)

    rows = client.fetch_ohlcv("BTC/USDT", "1m", limit=1)

    assert rows[0][:5] == [1700000000000, 100.0, 110.0, 90.0, expected_close]
    assert rows[0][5] > 0


def test_okx_historical_window_uses_forward_compatible_after_cursor(monkeypatch):
    client = NativeCryptoPublicClient("okx", "spot")
    captured = {}

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return {"code": "0", "data": [["1700000000000", "1", "2", "0.5", "1.5", "3"]]}

    monkeypatch.setattr(client, "_get", fake_get)
    client.fetch_ohlcv("BTC/USDT", "1m", since=1699999880000, limit=2)

    assert captured["url"].endswith("/api/v5/market/history-candles")
    assert captured["params"]["after"] == 1699999940001


def test_bitget_historical_window_is_capped_at_ninety_days(monkeypatch):
    client = NativeCryptoPublicClient("bitget", "swap")
    captured = {}

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return {"code": "00000", "data": []}

    monkeypatch.setattr(client, "_get", fake_get)
    start = 1700000000000
    client.fetch_ohlcv("BTC/USDT", "1d", since=start, limit=1000)

    assert captured["url"].endswith("/api/v3/market/history-candles")
    assert captured["params"]["limit"] <= 100
    assert captured["params"]["endTime"] - start <= 90 * 24 * 60 * 60 * 1000


def test_bitget_weekly_is_resampled_instead_of_sent_as_unsupported_native_interval():
    client = NativeCryptoPublicClient("bitget", "swap")

    assert "1w" not in client.timeframes


@pytest.mark.parametrize(
    ("exchange_id", "historical", "expected"),
    [
        ("binance", True, 1000),
        ("bybit", True, 1000),
        ("gate", True, 1000),
        ("okx", True, 300),
        ("bitget", True, 100),
        ("bitget", False, 1000),
        ("htx", True, 2000),
    ],
)
def test_ohlcv_page_limit_matches_endpoint(exchange_id, historical, expected):
    client = NativeCryptoPublicClient(exchange_id, "spot")

    assert client.ohlcv_page_limit(historical=historical) == expected


def test_gate_historical_window_stops_at_explicit_until(monkeypatch):
    client = NativeCryptoPublicClient("gate", "spot")
    captured = {}
    start = 1700000000000
    until = start + 2 * 60_000

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return []

    monkeypatch.setattr(client, "_get", fake_get)
    client.fetch_ohlcv("BTC/USDT", "1m", since=start, limit=1000, until=until)

    assert captured["params"]["from"] == start // 1000
    assert captured["params"]["to"] == until // 1000
    assert "limit" not in captured["params"]


def test_htx_swap_historical_window_uses_from_and_to(monkeypatch):
    client = NativeCryptoPublicClient("htx", "swap")
    captured = {}
    start = 1700000000000
    end = start + 1999 * 4 * 60 * 60 * 1000

    def fake_get(url, params):
        captured.update(url=url, params=params)
        return {"status": "ok", "data": []}

    monkeypatch.setattr(client, "_get", fake_get)
    client.fetch_ohlcv("BTC/USDT", "4h", since=start, limit=2000, until=end)

    assert captured["url"].endswith("/linear-swap-ex/market/history/kline")
    assert captured["params"]["from"] == start // 1000
    assert captured["params"]["to"] == end // 1000
    assert "size" not in captured["params"]


def test_http_failure_preserves_exchange_error_message(monkeypatch):
    client = NativeCryptoPublicClient("gate", "spot")
    response = requests.Response()
    response.status_code = 400
    response._content = (
        b'{"label":"INVALID_PARAM_VALUE","message":"Candlestick range too broad"}'
    )
    monkeypatch.setattr(client._http_session(), "get", lambda *_args, **_kwargs: response)

    with pytest.raises(NativeCryptoAPIError, match="Candlestick range too broad"):
        client.fetch_ohlcv("BTC/USDT", "4h", since=1700000000000)


def test_market_catalog_exposes_perpetual_alias(monkeypatch):
    client = NativeCryptoPublicClient("binance", "swap")
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                }
            ]
        },
    )

    markets = client.load_markets()

    assert markets["BTC/USDT:USDT"]["contract"] is True
    assert markets["BTC/USDT"]["linear"] is True


def test_binance_market_catalog_keeps_tradfi_perpetuals(monkeypatch):
    client = NativeCryptoPublicClient("binance", "swap")
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: {
            "symbols": [
                {
                    "symbol": "AAPLUSDT",
                    "baseAsset": "AAPL",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "status": "TRADING",
                    "contractType": "TRADIFI_PERPETUAL",
                    "underlyingType": "EQUITY",
                },
                {
                    "symbol": "BTCUSDT_261225",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "status": "TRADING",
                    "contractType": "CURRENT_QUARTER",
                },
            ]
        },
    )

    markets = client.load_markets()

    assert markets["AAPL/USDT:USDT"]["info"]["underlyingType"] == "EQUITY"
    assert "BTC/USDT:USDT" not in markets


def test_bybit_spot_catalog_does_not_send_unsupported_pagination(monkeypatch):
    client = NativeCryptoPublicClient("bybit", "spot")
    captured = []

    def fake_get(_url, params):
        captured.append(params)
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "baseCoin": "BTC",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "_get", fake_get)
    markets = client.load_markets()

    assert "BTC/USDT" in markets
    assert captured == [{"category": "spot"}]


@pytest.mark.parametrize("market_type", ["spot", "swap"])
def test_bitget_ticker_parses_change_rate_without_unbound_state(market_type, monkeypatch):
    client = NativeCryptoPublicClient("bitget", market_type)
    monkeypatch.setattr(
        client,
        "_get",
        lambda *_args, **_kwargs: {
            "code": "00000",
            "requestTime": 1700000000000,
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "lastPrice": "101.25",
                    "openPrice24h": "100",
                    "highPrice24h": "103",
                    "lowPrice24h": "99",
                    "price24hPcnt": "0.0125",
                    "turnover24h": "2500000",
                    "ts": "1700000000123",
                }
            ],
        },
    )

    ticker = client.fetch_ticker("BTC/USDT")

    assert ticker["last"] == 101.25
    assert ticker["percentage"] == 1.25
    assert ticker["changePercent"] == 1.25
    assert ticker["timestamp"] == 1700000000123
