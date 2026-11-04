from importlib import import_module
from urllib.parse import urlencode

from flask import Flask


def _call_route(monkeypatch, query):
    route = import_module("app.routes.kline")
    app = Flask(__name__)
    captured = {}
    rows = [{"time": 60, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 3}]
    monkeypatch.setattr(route, "guarded_cached", lambda _key, loader, **_kwargs: loader())
    monkeypatch.setattr(route, "validate_watchlist_pair", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        route.kline_service,
        "get_kline",
        lambda **kwargs: captured.update(kwargs) or rows,
    )

    with app.test_request_context(f"/api/indicator/kline?{urlencode(query)}"):
        response = route.get_kline()

    return response, captured


def test_kline_route_preserves_explicit_perpetual_venue(monkeypatch):
    response, captured = _call_route(
        monkeypatch,
        {
            "market": "Crypto",
            "symbol": "Crypto:XAUT/USDT:USDT@binance:swap",
            "timeframe": "1m",
            "limit": 1,
        },
    )

    payload = response.get_json()
    assert payload["code"] == 1
    assert captured["symbol"] == "XAUT/USDT"
    assert captured["exchange_id"] == "binance"
    assert captured["market_type"] == "swap"
    assert payload["meta"]["market_context"]["exchange_id"] == "binance"


def test_kline_route_preserves_stock_perpetual_native_identity(monkeypatch):
    response, captured = _call_route(
        monkeypatch,
        {
            "market": "Crypto",
            "symbol": "QQQ/USDT",
            "exchange_id": "binance",
            "market_type": "swap",
            "instrument_id": "QQQUSDT",
            "timeframe": "1m",
            "limit": 1,
        },
    )

    assert response.get_json()["code"] == 1
    assert captured["symbol"] == "QQQ/USDT"
    assert captured["exchange_id"] == "binance"
    assert captured["market_type"] == "swap"
    assert captured["instrument_id"] == "QQQUSDT"


def test_kline_route_keeps_unqualified_research_request_unpinned(monkeypatch):
    response, captured = _call_route(
        monkeypatch,
        {
            "market": "Crypto",
            "symbol": "Crypto:XAUT/USDT@swap",
            "timeframe": "1m",
            "limit": 1,
        },
    )

    payload = response.get_json()
    assert payload["code"] == 1
    assert captured["symbol"] == "XAUT/USDT"
    assert captured["exchange_id"] is None
    assert captured["market_type"] == "swap"
    assert payload["meta"]["market_context"]["exchange_id"] == ""


def test_kline_route_rejects_conflicting_identity_before_fetch(monkeypatch):
    route = import_module("app.routes.kline")
    app = Flask(__name__)
    calls = []
    monkeypatch.setattr(
        route.kline_service,
        "get_kline",
        lambda **kwargs: calls.append(kwargs),
    )
    query = urlencode({
        "market": "Crypto",
        "symbol": "Crypto:BTC/USDT@binance:swap",
        "exchange_id": "okx",
    })

    with app.test_request_context(f"/api/indicator/kline?{query}"):
        result = route.get_kline()

    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code

    assert status == 400
    assert response.get_json()["msg"] == "marketData.cryptoExchangeConflict"
    assert calls == []
