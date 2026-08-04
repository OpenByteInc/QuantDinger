"""Qveris market-data adapter tests."""

from __future__ import annotations

import requests

from app.data_sources.base import BaseDataSource
from app.data_sources.factory import DataSourceFactory
from app.data_sources.qveris import QverisDataSource


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads=None, error=None):
        self.payloads = list(payloads or [])
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return FakeResponse(self.payloads.pop(0))


class FallbackDataSource(BaseDataSource):
    name = "fallback"

    def __init__(self):
        self.kline_calls = 0
        self.ticker_calls = 0

    def get_kline(self, symbol, timeframe, limit, before_time=None, after_time=None):
        self.kline_calls += 1
        return [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    def get_ticker(self, symbol):
        self.ticker_calls += 1
        return {"last": 42.0, "symbol": symbol}


def kline_discovery():
    return {
        "search_id": "search-kline",
        "results": [
            {
                "tool_id": "market.history.execute.v1",
                "name": "Historical OHLCV",
                "description": "Historical candlestick time series",
                "parameters": [
                    {"name": "symbol", "required": True, "type": "string"},
                    {"name": "interval", "required": True, "type": "string", "enum": ["1h", "1d"]},
                    {"name": "limit", "required": False, "type": "integer"},
                ],
            }
        ],
    }


def ticker_discovery():
    return {
        "search_id": "search-ticker",
        "results": [
            {
                "tool_id": "market.quote.execute.v1",
                "name": "Latest quote",
                "description": "Current market price quote",
                "parameters": [{"name": "ticker", "required": True, "type": "string"}],
            }
        ],
    }


def test_qveris_normalizes_row_oriented_klines():
    session = FakeSession(
        [
            kline_discovery(),
            {
                "success": True,
                "result": {
                    "data": [
                        {
                            "datetime": "2026-08-01T00:00:00Z",
                            "open": "100",
                            "high": 105,
                            "low": 99,
                            "close": 104,
                            "volume": "1,200",
                        },
                        {
                            "datetime": "2026-08-02T00:00:00Z",
                            "open": 104,
                            "high": 108,
                            "low": 103,
                            "close": 107,
                            "volume": 900,
                        },
                    ]
                },
            },
        ]
    )
    fallback = FallbackDataSource()
    source = QverisDataSource("USStock", fallback, api_key="test-key", session=session)

    rows = source.get_kline("AAPL", "1D", 2)

    assert [row["close"] for row in rows] == [104.0, 107.0]
    assert rows[0]["volume"] == 1200.0
    assert fallback.kline_calls == 0
    assert session.calls[1]["params"] == {"tool_id": "market.history.execute.v1"}
    assert session.calls[1]["json"]["parameters"] == {
        "symbol": "AAPL",
        "interval": "1d",
        "limit": 2,
    }
    assert session.calls[1]["headers"]["Authorization"] == "Bearer test-key"


def test_qveris_normalizes_column_oriented_klines():
    session = FakeSession(
        [
            kline_discovery(),
            {
                "success": True,
                "result": {
                    "timestamps": [1785542400, 1785628800],
                    "open": [10, 11],
                    "high": [12, 13],
                    "low": [9, 10],
                    "close": [11, 12],
                    "volume": [100, 200],
                },
            },
        ]
    )
    source = QverisDataSource("USStock", FallbackDataSource(), api_key="test-key", session=session)

    rows = source.get_kline("MSFT", "1D", 2)

    assert [row["time"] for row in rows] == [1785542400, 1785628800]
    assert [row["close"] for row in rows] == [11.0, 12.0]


def test_qveris_prefers_market_match_and_maps_eodhd_stock_parameters():
    discovery = {
        "search_id": "search-eod",
        "results": [
            {
                "tool_id": "crypto-history",
                "name": "Historical Data for Cryptocurrency",
                "description": "Historical OHLCV data",
                "params": [
                    {"name": "symbol", "required": True, "type": "string"},
                    {"name": "period", "required": False, "enum": ["d", "w", "m"]},
                ],
                "stats": {"success_rate": 1.0},
            },
            {
                "tool_id": "stock-history",
                "name": "Historical Stock Market Data",
                "description": "Historical end-of-day OHLCV for equities",
                "params": [
                    {
                        "name": "symbol_exchange",
                        "required": True,
                        "type": "string",
                        "description": "Ticker with exchange suffix, for example AAPL.US",
                    },
                    {"name": "fmt", "required": False, "enum": ["json", "csv"]},
                    {"name": "period", "required": False, "enum": ["d", "w", "m"]},
                    {"name": "from", "required": False, "description": "Start date in YYYY-MM-DD format"},
                    {"name": "to", "required": False, "description": "End date in YYYY-MM-DD format"},
                ],
                "stats": {"success_rate": 0.8},
            },
        ],
    }
    session = FakeSession(
        [
            discovery,
            {
                "success": True,
                "result": [{"date": "2026-08-01", "open": 200, "high": 205, "low": 199, "close": 204, "volume": 1000}],
            },
        ]
    )
    source = QverisDataSource("USStock", FallbackDataSource(), api_key="test-key", session=session)

    rows = source.get_kline("AAPL", "1D", 1, before_time=1785715200)

    assert rows[0]["close"] == 204.0
    assert session.calls[1]["params"] == {"tool_id": "stock-history"}
    assert session.calls[1]["json"]["parameters"] == {
        "symbol_exchange": "AAPL.US",
        "fmt": "json",
        "period": "d",
        "from": "2026-08-01",
        "to": "2026-08-03",
    }


def test_qveris_normalizes_dated_time_series():
    session = FakeSession(
        [
            kline_discovery(),
            {
                "success": True,
                "result": {
                    "Time Series (Daily)": {
                        "2026-08-01": {
                            "1. open": "20",
                            "2. high": "22",
                            "3. low": "19",
                            "4. close": "21",
                            "5. volume": "300",
                        }
                    }
                },
            },
        ]
    )
    source = QverisDataSource("USStock", FallbackDataSource(), api_key="test-key", session=session)

    rows = source.get_kline("NVDA", "1D", 1)

    assert rows[0]["close"] == 21.0
    assert rows[0]["volume"] == 300.0


def test_qveris_normalizes_ticker():
    session = FakeSession(
        [
            ticker_discovery(),
            {
                "success": True,
                "result": {
                    "quote": {
                        "regularMarketPrice": 215.5,
                        "regularMarketPreviousClose": 210,
                        "regularMarketDayHigh": 217,
                        "regularMarketDayLow": 209,
                    }
                },
            },
        ]
    )
    fallback = FallbackDataSource()
    source = QverisDataSource("USStock", fallback, api_key="test-key", session=session)

    quote = source.get_ticker("AAPL")

    assert quote["last"] == 215.5
    assert quote["previousClose"] == 210.0
    assert quote["symbol"] == "AAPL"
    assert fallback.ticker_calls == 0


def test_qveris_falls_back_without_exposing_failure():
    fallback = FallbackDataSource()
    session = FakeSession(error=requests.ConnectionError("offline"))
    source = QverisDataSource("USStock", fallback, api_key="secret-key", session=session)

    rows = source.get_kline("AAPL", "1D", 1)
    quote = source.get_ticker("AAPL")

    assert rows[0]["close"] == 1
    assert quote["last"] == 42.0
    assert fallback.kline_calls == 1
    assert fallback.ticker_calls == 1


def test_qveris_is_disabled_until_key_and_market_are_configured(monkeypatch):
    monkeypatch.delenv("QVERIS_API_KEY", raising=False)
    monkeypatch.setenv("QVERIS_DATA_SOURCE_MARKETS", "USStock")
    assert not QverisDataSource.is_enabled_for("USStock")

    monkeypatch.setenv("QVERIS_API_KEY", "configured")
    assert QverisDataSource.is_enabled_for("USStock")
    assert not QverisDataSource.is_enabled_for("Crypto")

    monkeypatch.setenv("QVERIS_DATA_SOURCE_MARKETS", "*")
    assert QverisDataSource.is_enabled_for("Crypto")


def test_factory_wraps_existing_source_only_when_enabled(monkeypatch):
    fallback = FallbackDataSource()
    monkeypatch.delenv("QVERIS_API_KEY", raising=False)
    monkeypatch.setenv("QVERIS_DATA_SOURCE_MARKETS", "USStock")
    assert DataSourceFactory._wrap_optional_source("USStock", fallback) is fallback

    monkeypatch.setenv("QVERIS_API_KEY", "configured")
    wrapped = DataSourceFactory._wrap_optional_source("USStock", fallback)
    assert isinstance(wrapped, QverisDataSource)
    assert wrapped.fallback is fallback
