"""Provider timestamps, cache replays, and live equity protection boundaries."""

from types import SimpleNamespace

import pandas as pd
import pytest

from app.data_sources.quote_time import quote_timestamp
from app.data_sources.us_stock import USStockDataSource
from app.services.strategy_runtime.price_freshness import filter_execution_prices
from app.services.strategy_runtime.price_freshness import daily_signal_wait_reason
from app.services.trading_executor import TradingExecutor


NOW = pd.Timestamp("2026-11-25T15:00:30Z")
KEY = "USStock:WDAY"


def test_daily_wait_diagnostic_tracks_missing_quotes_and_recovery():
    members = [{"key": KEY}, {"key": "USStock:AAPL"}]
    quotes = {KEY: {"quote_source": "yahoo", "quote_time": NOW.timestamp() - 600}}
    reason = daily_signal_wait_reason(members, {"USStock:AAPL": 100}, {"USStock:AAPL": 10},
                                     clock=10, max_age=90, quotes=quotes, now=NOW)
    assert "1/2" in reason
    assert f"{KEY}[source=yahoo,age=600s]" in reason
    prices = {KEY: 171.17, "USStock:AAPL": 100}
    seen = {KEY: 10, "USStock:AAPL": 10}
    assert daily_signal_wait_reason(members, prices, seen, clock=10, max_age=90, quotes={}, now=NOW) == ""
    assert "2/2" in daily_signal_wait_reason(members, prices, seen, clock=101, max_age=90, quotes={}, now=NOW)


def filtered(observed, now=NOW, *, market="USStock", mode="live"):
    key = f"{market}:WDAY"
    return filter_execution_prices(
        {key: 171.17}, {key: {"last": 171.17, "quote_time": observed}},
        now=now, execution_mode=mode,
    )


@pytest.mark.parametrize("value", [None, "", "bad-time", False, float("nan"), float("inf"), -1])
def test_unknown_or_invalid_time_is_not_request_time(value):
    assert quote_timestamp(value) == 0


def test_provider_times_have_explicit_timezone_and_seconds():
    assert quote_timestamp(NOW.timestamp()) == NOW.timestamp()
    assert quote_timestamp(NOW.timestamp() * 1000) == NOW.timestamp()
    assert quote_timestamp("2026-11-25 10:00:30") == 0
    assert quote_timestamp("2026-11-25 10:00:30", timezone="America/New_York") == NOW.timestamp()
    assert quote_timestamp(NOW.isoformat()) == NOW.timestamp()


@pytest.mark.parametrize("age,valid", [(0, True), (60, True), (90, True), (91, False), (900, False), (-1, False)])
def test_quote_age_is_based_on_provider_time(age, valid):
    assert bool(filtered(NOW.timestamp() - age)) is valid


@pytest.mark.parametrize("observed", [None, 0, "broken", float("nan")])
def test_missing_observation_time_cannot_trigger_equity_exit(observed):
    assert filtered(observed) == {}


@pytest.mark.parametrize("price", [None, 0, "bad-price", 172.])
def test_fresh_timestamp_cannot_validate_a_different_cached_price(price):
    assert filter_execution_prices(
        {KEY: 171.17}, {KEY: {"last": price, "quote_time": NOW.timestamp()}},
        now=NOW, execution_mode="live",
    ) == {}


@pytest.mark.parametrize("market,now,observed", [
    ("USStock", "2026-11-25T14:30:10Z", "2026-11-25T14:29:59Z"),
    ("USStock", "2026-11-25T21:00:01Z", "2026-11-25T20:59:59Z"),
    ("USStock", "2026-11-27T18:00:01Z", "2026-11-27T17:59:59Z"),
    ("USStock", "2026-11-26T15:00:00Z", "2026-11-26T15:00:00Z"),
    ("HKStock", "2026-11-25T05:00:10Z", "2026-11-25T04:59:59Z"),
    ("CNStock", "2026-11-25T05:00:10Z", "2026-11-25T04:59:59Z"),
])
def test_recent_price_must_belong_to_current_regular_session(market, now, observed):
    assert filtered(pd.Timestamp(observed).timestamp(), pd.Timestamp(now), market=market) == {}


def test_asian_equity_quotes_and_crypto_signal_behavior():
    clock = pd.Timestamp("2026-11-25T05:01:00Z")
    for market in ("HKStock", "CNStock", "AStock"):
        assert filtered(clock.timestamp(), clock, market=market)
    assert filtered(None, market="Crypto")
    assert filtered(None, mode="signal")


def test_expired_quote_cannot_be_revived_by_cache_hit_or_receipt_clock(monkeypatch):
    from app.services import trading_executor

    source = USStockDataSource.__new__(USStockDataSource)
    source.clear_quote_cache()
    monkeypatch.setenv("US_STOCK_QUOTE_BATCH_WINDOW_MS", "0")
    monkeypatch.setenv("US_STOCK_QUOTE_CACHE_TTL_SEC", "3600")
    monkeypatch.setattr(source, "_fetch_ticker", lambda _: {
        "last": 171.17, "quote_time": NOW.timestamp(), "quote_source": "test",
    })
    monkeypatch.setattr(trading_executor.DataSourceFactory, "get_tickers", lambda _market, symbols, **kw: source.get_tickers(symbols))
    quotes = {}
    candidates = [{"market": "USStock", "symbol": "WDAY", "key": KEY}]
    try:
        first = TradingExecutor._execution_account_prices(candidates, {}, {}, equity_quotes=quotes)
        assert filter_execution_prices(first, quotes, now=NOW, execution_mode="live") == first
        again = TradingExecutor._execution_account_prices(candidates, {}, {}, equity_quotes=quotes)
        assert again == first
        assert quotes[KEY]["quote_time"] == NOW.timestamp()
        assert filter_execution_prices(again, quotes, now=NOW + pd.Timedelta(seconds=91), execution_mode="live") == {}
    finally:
        source.clear_quote_cache()


def test_finnhub_preserves_trade_timestamp(monkeypatch):
    source = USStockDataSource.__new__(USStockDataSource)
    monkeypatch.setattr(source, "_fetch_finnhub_quote", lambda _: {"c": 171.17, "t": NOW.timestamp()})
    assert source._fetch_ticker("WDAY")["quote_time"] == NOW.timestamp()


def test_yahoo_batch_keeps_last_non_null_bar_timestamp(monkeypatch):
    from app.data_sources import us_stock

    index = pd.DatetimeIndex([NOW - pd.Timedelta(minutes=1), NOW])
    frame = pd.DataFrame({("WDAY", field): [171., float("nan")] for field in ("Open", "High", "Low", "Close")}, index=index)
    monkeypatch.setattr(us_stock.yf, "download", lambda *a, **kw: frame)
    quote = USStockDataSource.__new__(USStockDataSource)._fetch_yfinance_batch_quotes(["WDAY"])["WDAY"]
    assert quote["quote_time"] == index[0].timestamp()


@pytest.mark.parametrize("meta,closes,expected_time", [
    ({"regularMarketPrice": 171., "regularMarketTime": NOW.timestamp() - 600}, [170., 172.], NOW.timestamp() - 600),
    ({"regularMarketPrice": 171.}, [170., 172.], 0),
    ({}, [170., None], NOW.timestamp() - 60),
])
def test_yahoo_price_and_time_are_from_same_observation(monkeypatch, meta, closes, expected_time):
    from app.data_sources import us_stock

    result = {"meta": meta, "timestamp": [NOW.timestamp() - 60, NOW.timestamp()], "indicators": {"quote": [{"close": closes}]}}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"chart": {"result": [result]}})
    monkeypatch.setattr(us_stock.requests, "get", lambda *a, **kw: response)
    quote = USStockDataSource.__new__(USStockDataSource)._fetch_yahoo_chart_quote("WDAY")
    assert quote["quote_time"] == expected_time


def test_nasdaq_time_is_market_time_not_server_timezone(monkeypatch):
    from app.data_sources import us_stock

    primary = {"lastSalePrice": "$171.17", "lastTradeTimestamp": "Nov 25, 2026 10:00 AM ET"}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"primaryData": primary}})
    monkeypatch.setattr(us_stock.requests, "get", lambda *a, **kw: response)
    quote = USStockDataSource.__new__(USStockDataSource)._fetch_nasdaq_quote("WDAY")
    assert quote["quote_time"] == NOW.timestamp() - 30


@pytest.mark.parametrize("module_name,code", [("hk_stock", "00700"), ("cn_stock", "600519")])
def test_tencent_adapters_retain_original_quote_time(monkeypatch, module_name, code):
    from importlib import import_module

    module = import_module("app.data_sources." + module_name)
    parts = [""] * 35
    parts[3], parts[30] = "100", "20261125100030"
    monkeypatch.setattr(module, "fetch_quote", lambda _: parts)
    cls = module.HKStockDataSource if module_name == "hk_stock" else module.CNStockDataSource
    quote = cls().get_ticker(code)
    assert quote["quote_time"] == pd.Timestamp("2026-11-25T02:00:30Z").timestamp()
