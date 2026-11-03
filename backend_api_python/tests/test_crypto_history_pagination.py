"""Exercise native REST pagination with exchange-shaped, bounded responses."""

from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.data_sources import crypto as crypto_module
from app.data_sources.crypto import CryptoDataSource
from app.data_sources.native_crypto import NativeCryptoAPIError, NativeCryptoPublicClient
from app.services.backtest_limits import backtest_warmup_calendar_days


NOW = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)
BAR_MS = 4 * 60 * 60 * 1000


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(crypto_module, "datetime", FrozenDateTime)
    monkeypatch.setattr("app.data_sources.native_crypto.time.time", lambda: NOW.timestamp())


def candle(timestamp):
    return [timestamp, 100.0, 110.0, 90.0, 105.0, 10.0]


def exchange_response(exchange_id, timestamps):
    if exchange_id == "binance":
        return [candle(ts) for ts in timestamps]
    if exchange_id == "gate":
        return [[str(ts // 1000), "1050", "105", "110", "90", "100", "10"] for ts in timestamps]
    if exchange_id == "bybit":
        return {"retCode": 0, "result": {"list": [candle(ts) for ts in timestamps]}}
    if exchange_id in {"okx", "bitget"}:
        return {"code": "0" if exchange_id == "okx" else "00000", "data": [candle(ts) for ts in timestamps]}
    return {
        "status": "ok",
        "data": [dict(id=ts // 1000, open=100, high=110, low=90, close=105, amount=10) for ts in timestamps],
    }


def native_source(monkeypatch, exchange_id, timestamps, market_type="spot"):
    client = NativeCryptoPublicClient(exchange_id, market_type)
    calls = []
    available = timestamps
    if timestamps and exchange_id != "htx":
        available = [timestamps[0] - BAR_MS * 2, timestamps[0] - BAR_MS, *timestamps, timestamps[-1] + BAR_MS]

    def get_page(url, params):
        calls.append((url, dict(params)))
        limit = int(params.get("limit", params.get("size", 1000)))
        if exchange_id == "gate":
            assert "limit" not in params
            start, end = params["from"] * 1000, params["to"] * 1000
            assert (end - start) // BAR_MS + 1 <= 1000
        elif exchange_id == "okx":
            assert url.endswith("/market/history-candles")
            start, end = 0, params["after"] - 1
            assert limit <= 300
        elif exchange_id == "bybit":
            start, end = params["start"], params["end"]
            assert limit <= 1000
        elif exchange_id == "bitget":
            assert url.endswith("/market/history-candles")
            assert limit <= 100
            assert params["endTime"] - params["startTime"] <= 90 * 86400 * 1000
            # Historical results are the latest limit bars before endTime;
            # rounding an unaligned end down can shift the entire page back.
            start, end = 0, (params["endTime"] // BAR_MS - 1) * BAR_MS
        elif exchange_id == "htx":
            assert params["size"] == 2000
            return exchange_response(exchange_id, list(reversed(timestamps[-2000:])))
        else:
            start, end = params["startTime"], params["endTime"]
            assert limit <= 1000
        selected = [ts for ts in available if start <= ts <= end]
        if exchange_id in {"okx", "bybit", "bitget"}:
            selected.reverse()
        selected = selected[:limit]
        if exchange_id == "gate" and market_type == "swap":
            return [dict(t=ts // 1000, o=100, h=110, l=90, c=105, v=10) for ts in selected]
        return exchange_response(exchange_id, selected)

    monkeypatch.setattr(client, "_get", get_page)
    source = object.__new__(CryptoDataSource)
    source.exchange = client
    return source, calls


@pytest.mark.parametrize("exchange_id", ["binance", "okx", "bybit", "bitget", "gate"])
@pytest.mark.parametrize("count", [999, 1000, 1001, 2790])
def test_full_historical_window_has_no_missing_or_duplicate_bars(monkeypatch, exchange_id, count):
    end = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [end - (count - 1 - index) * BAR_MS for index in range(count)]
    source, calls = native_source(monkeypatch, exchange_id, timestamps)

    rows = source._fetch_ohlcv("BTC/USDT", "4h", count + 200, end // 1000, "4H", timestamps[0] // 1000)

    assert rows == [candle(ts) for ts in timestamps]
    cap = source.exchange.ohlcv_page_limit(historical=True)
    assert len(calls) == (count + cap - 1) // cap
    if exchange_id == "gate":
        assert calls[-1][1]["to"] == end // 1000


@pytest.mark.parametrize("start_date", ["2026-08-28", "2026-08-29"])
def test_gate_reported_date_boundary_with_400_warmup_bars(monkeypatch, start_date):
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    days = backtest_warmup_calendar_days("4H", 400)
    fetch_start = start - timedelta(days=days, hours=4)
    end = datetime(2026, 11, 3, tzinfo=timezone.utc)
    timestamps = list(range(int(fetch_start.timestamp() * 1000), int(end.timestamp() * 1000) + 1, BAR_MS))
    source, calls = native_source(monkeypatch, "gate", timestamps)

    rows = source._fetch_ohlcv("BTC/USDT", "4h", 2000, int(end.timestamp()), "4H", int(fetch_start.timestamp()))

    assert days == 100
    assert rows == [candle(ts) for ts in timestamps]
    assert len(calls) == (2 if start_date == "2026-08-28" else 1)


def test_gate_swap_keeps_its_native_contract_endpoint(monkeypatch):
    end = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [end - (1000 - index) * BAR_MS for index in range(1001)]
    source, calls = native_source(monkeypatch, "gate", timestamps, market_type="swap")

    rows = source._fetch_ohlcv("BTC/USDT:USDT", "4h", 1200, end // 1000, "4H", timestamps[0] // 1000)

    assert rows == [candle(ts) for ts in timestamps]
    assert all(url.endswith("/futures/usdt/candlesticks") for url, _params in calls)
    assert all(params["contract"] == "BTC_USDT" for _url, params in calls)


def test_htx_uses_full_recent_window_and_filters_the_requested_end(monkeypatch):
    end = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [end - (1499 - index) * BAR_MS for index in range(1500)]
    source, calls = native_source(monkeypatch, "htx", timestamps)
    requested_end = timestamps[-11]

    rows = source._fetch_ohlcv("BTC/USDT", "4h", 1700, requested_end // 1000, "4H", timestamps[0] // 1000)

    assert rows == [candle(ts) for ts in timestamps[:-10]]
    assert len(calls) == 1


def test_htx_old_history_fails_instead_of_returning_a_recent_page(monkeypatch):
    end = int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp() * 1000)
    timestamps = [end - (1999 - index) * BAR_MS for index in range(2000)]
    source, _calls = native_source(monkeypatch, "htx", timestamps)

    with pytest.raises(NativeCryptoAPIError, match="recent 2000 candles"):
        source.exchange.fetch_ohlcv("BTC/USDT", "4h", since=timestamps[0] - BAR_MS * 10)


def test_gate_retention_rejection_keeps_a_structured_failure(monkeypatch):
    source, calls = native_source(monkeypatch, "gate", [])
    end = int(NOW.timestamp())

    assert source._fetch_ohlcv("BTC/USDT", "1m", 12000, end, "1m", end - 12000 * 60) == []
    assert calls == []
    assert source.get_last_failure().code == "incomplete_market_data"
    assert "10000-candle" in source.get_last_failure().technical_detail


def test_http_failure_preserves_the_exchange_error_message(monkeypatch):
    client = NativeCryptoPublicClient("gate", "spot")
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"label":"INVALID_PARAM_VALUE","message":"Candlestick range too broad"}'
    monkeypatch.setattr(client._http_session(), "get", lambda *_args, **_kwargs: response)

    with pytest.raises(NativeCryptoAPIError, match="Candlestick range too broad"):
        client.fetch_ohlcv("BTC/USDT", "4h", since=1700000000000)


@pytest.mark.parametrize("timeframe, seconds", [("1m", 60), ("5m", 300), ("1h", 3600), ("4h", 14400), ("1d", 86400), ("1w", 604800)])
@pytest.mark.parametrize("limit", [1, 999, 1000, 1001])
def test_gate_page_boundaries_across_native_timeframes(monkeypatch, timeframe, seconds, limit):
    client = NativeCryptoPublicClient("gate", "spot")
    captured = {}
    start = int(NOW.timestamp()) - seconds * 2000

    def get_page(_url, params):
        captured.update(params)
        return []

    monkeypatch.setattr(client, "_get", get_page)
    client.fetch_ohlcv("BTC/USDT", timeframe, since=start * 1000, limit=limit)

    assert captured["from"] == start
    assert captured["to"] == start + (min(limit, 1000) - 1) * seconds
    assert "limit" not in captured


def test_final_page_stops_at_the_requested_end(monkeypatch):
    client = NativeCryptoPublicClient("gate", "spot")
    captured = {}
    start = int(NOW.timestamp()) - 14400 * 10

    def get_page(_url, params):
        captured.update(params)
        return []

    monkeypatch.setattr(client, "_get", get_page)
    client.fetch_ohlcv("BTC/USDT", "4h", since=start * 1000, limit=1000, until=(start + 14400 * 2) * 1000)

    assert captured["to"] == start + 14400 * 2
