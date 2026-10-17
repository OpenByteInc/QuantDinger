from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time

import pandas as pd
import pytest

from app.data_sources.errors import (
    MarketDataUnavailableError,
    classify_market_data_failure,
)
from app.services.strategy_v2 import market_data
from app.services.strategy_v2 import StrategyV2BacktestService, compile_strategy_v2


@pytest.fixture(autouse=True)
def _clear_shared_frame_cache():
    market_data.clear_shared_strategy_frame_cache()
    yield
    market_data.clear_shared_strategy_frame_cache()


def test_market_data_normalizes_numeric_time_series_and_lowercase_timeframe(monkeypatch):
    captured = {}

    def get_kline(**kwargs):
        captured.update(kwargs)
        return [
            {
                "time": 1767225600000,
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10,
            },
            {
                "time": 1767240000000,
                "open": 101,
                "high": 103,
                "low": 100,
                "close": 102,
                "volume": 11,
            },
        ]

    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", get_kline)
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)

    frame = market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "4h",
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, 4),
        market_type="spot",
    )

    assert len(frame) == 2
    assert frame.index.tz is None
    assert captured["timeframe"] == "4H"
    assert captured["limit"] < 250
    assert captured["after_time"] == int(datetime(2025, 12, 31, 20, tzinfo=timezone.utc).timestamp())
    assert captured["before_time"] == int(datetime(2026, 1, 1, 8, tzinfo=timezone.utc).timestamp())


def test_four_hour_year_requests_enough_bars(monkeypatch):
    captured = {}

    def get_kline(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", get_kline)
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)

    market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "4h",
        datetime(2025, 1, 1),
        datetime(2026, 1, 1),
        market_type="spot",
    )

    assert captured["limit"] > 2400


def test_market_data_normalizes_naive_and_aware_datetimes_to_utc():
    naive = datetime(2026, 7, 19, 4, 14, 13)
    shanghai = timezone(timedelta(hours=8))
    aware = datetime(2026, 7, 19, 12, 14, 13, tzinfo=shanghai)

    normalized_naive = market_data._normalize_utc_datetime(naive)
    normalized_aware = market_data._normalize_utc_datetime(aware)

    assert normalized_naive == datetime(2026, 7, 19, 4, 14, 13, tzinfo=timezone.utc)
    assert normalized_aware == normalized_naive
    assert normalized_naive.timestamp() == normalized_aware.timestamp()


def test_crypto_market_data_rejects_partial_historical_window(monkeypatch):
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: [
            {
                "time": int(datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp()),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 10,
            },
            {
                "time": int(datetime(2026, 8, 30, tzinfo=timezone.utc).timestamp()),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 10,
            },
        ],
    )

    with pytest.raises(MarketDataUnavailableError) as raised:
        market_data.load_strategy_frame(
            "Crypto",
            "ETH/USDT",
            "1m",
            datetime(2026, 8, 1),
            datetime(2026, 8, 30),
            market_type="swap",
        )

    assert raised.value.failure.code == "incomplete_market_data"


def test_crypto_market_data_ignores_partial_cached_window(monkeypatch):
    partial = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.0, 101.0],
            "volume": [10.0, 11.0],
        },
        index=pd.DatetimeIndex(["2026-07-23", "2026-07-30"]),
    )
    fresh_rows = [
        {
            "time": int(timestamp.timestamp()),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
        }
        for timestamp in pd.date_range("2026-07-01", "2026-07-30", freq="1D", tz="UTC")
    ]
    calls = []
    monkeypatch.setattr(market_data._cache, "get", lambda _key: partial)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: calls.append(True) or fresh_rows,
    )

    frame = market_data.load_strategy_frame(
        "Crypto",
        "ETH/USDT",
        "1d",
        datetime(2026, 7, 1),
        datetime(2026, 7, 30),
        market_type="swap",
    )

    assert calls == [True]
    assert frame.index.min() == pd.Timestamp("2026-07-01")


def test_shared_market_data_collapses_concurrent_identical_fetches(monkeypatch):
    calls = []

    def load(*_args, **_kwargs):
        calls.append(True)
        time.sleep(0.05)
        index = pd.date_range("2026-08-01 00:00", periods=11, freq="1min")
        return pd.DataFrame({"close": range(len(index))}, index=index)

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", load)
    args = (
        "Crypto",
        "BTC/USDT",
        "1m",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(lambda _index: market_data.load_strategy_frame(*args), range(4)))

    assert len(calls) == 1
    assert all(len(frame) == 11 for frame in frames)


def test_shared_market_data_extends_only_uncovered_tail(monkeypatch):
    windows = []

    def load(_market, _symbol, _timeframe, start_date, end_date, **_kwargs):
        windows.append((start_date, end_date))
        index = pd.date_range(
            pd.Timestamp(start_date).tz_localize(None),
            pd.Timestamp(end_date).tz_localize(None),
            freq="1min",
        )
        return pd.DataFrame({"close": range(len(index))}, index=index)

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", load)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    market_data.load_strategy_frame(
        "Crypto", "ETH/USDT", "1m", start, start + timedelta(minutes=10)
    )
    extended = market_data.load_strategy_frame(
        "Crypto", "ETH/USDT", "1m", start, start + timedelta(minutes=11)
    )

    assert len(windows) == 2
    assert windows[1][0] >= start + timedelta(minutes=8)
    assert windows[1][0] > start
    assert extended.index.is_unique
    assert extended.index.max() == pd.Timestamp("2026-08-01 00:11")


def test_last_completed_bar_cutoff_is_aligned_to_the_previous_minute():
    cutoff = market_data._last_completed_bar_open(
        60,
        now=datetime(2026, 8, 31, 18, 52, 37, tzinfo=timezone.utc),
    )

    assert cutoff == pd.Timestamp("2026-08-31 18:51:00")


def test_future_window_is_capped_before_requesting_crypto_candles(monkeypatch):
    cutoff = pd.Timestamp("2026-09-16 16:09:00")
    captured = {}

    def get_kline(**kwargs):
        captured.update(kwargs)
        return [
            {
                "time": int(timestamp.timestamp()),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 10,
            }
            for timestamp in pd.date_range(
                "2026-09-16 16:00:00",
                cutoff,
                freq="1min",
                tz="UTC",
            )
        ]

    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(market_data, "_last_completed_bar_open", lambda _seconds, **_: cutoff)
    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", get_kline)

    frame = market_data._load_strategy_frame_uncached(
        "Crypto",
        "BTC/USDT",
        "1m",
        datetime(2026, 9, 16, 16, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 23, 59, 59, tzinfo=timezone.utc),
        market_type="swap",
    )

    assert captured["before_time"] == int(
        datetime(2026, 9, 16, 16, 10, tzinfo=timezone.utc).timestamp()
    )
    assert frame.index.max() == cutoff
    assert len(frame) == 10


def test_live_one_minute_cache_survives_one_missing_bar_and_keeps_refetching(
    monkeypatch,
):
    cutoff = [pd.Timestamp("2026-08-31 18:51:00")]
    calls = []

    def load(_market, _symbol, _timeframe, start_date, end_date, **_kwargs):
        calls.append((start_date, end_date))
        if len(calls) > 1:
            raise MarketDataUnavailableError(
                classify_market_data_failure(
                    "Incomplete K-line coverage",
                    exchange_id="binance",
                    market_type="swap",
                    symbol="BTC/USDT",
                    timeframe="1m",
                )
            )
        index = pd.date_range(
            cutoff[0] - pd.Timedelta(minutes=99),
            cutoff[0],
            freq="1min",
        )
        return pd.DataFrame({"close": range(len(index))}, index=index)

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", load)
    monkeypatch.setattr(
        market_data,
        "_last_completed_bar_open",
        lambda _seconds, **_: cutoff[0],
    )
    start = cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc) - timedelta(minutes=99)
    first_end = cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc)

    first = market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "1m",
        start,
        first_end,
        market_type="swap",
        exchange_id="binance",
    )
    cutoff[0] += pd.Timedelta(minutes=1)
    next_end = cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc)
    second = market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "1m",
        start,
        next_end,
        market_type="swap",
        exchange_id="binance",
    )
    third = market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "1m",
        start,
        next_end,
        market_type="swap",
        exchange_id="binance",
    )

    assert len(first) == 100
    assert second.index.max() == pd.Timestamp("2026-08-31 18:51:00")
    assert third.index.max() == second.index.max()
    assert len(calls) == 3


def test_live_one_minute_cache_rejects_data_older_than_grace_window(monkeypatch):
    cutoff = [pd.Timestamp("2026-08-31 18:51:00")]
    calls = []

    def load(_market, _symbol, _timeframe, _start_date, _end_date, **_kwargs):
        calls.append(True)
        if len(calls) > 1:
            return pd.DataFrame()
        index = pd.date_range(
            cutoff[0] - pd.Timedelta(minutes=199),
            cutoff[0],
            freq="1min",
        )
        return pd.DataFrame({"close": range(len(index))}, index=index)

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", load)
    monkeypatch.setattr(
        market_data,
        "_last_completed_bar_open",
        lambda _seconds, **_: cutoff[0],
    )
    start = cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc) - timedelta(minutes=199)
    first_end = cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc)
    market_data.load_strategy_frame(
        "Crypto",
        "ETH/USDT",
        "1m",
        start,
        first_end,
        market_type="swap",
        exchange_id="binance",
    )
    cutoff[0] += pd.Timedelta(minutes=3)

    stale = market_data.load_strategy_frame(
        "Crypto",
        "ETH/USDT",
        "1m",
        start,
        cutoff[0].to_pydatetime().replace(tzinfo=timezone.utc),
        market_type="swap",
        exchange_id="binance",
    )

    assert stale.empty
    assert len(calls) == 2


def test_initial_incomplete_one_minute_warmup_retries_once(monkeypatch):
    calls = []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)

    def load(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise MarketDataUnavailableError(
                classify_market_data_failure(
                    "Incomplete K-line coverage",
                    exchange_id="binance",
                    market_type="swap",
                    symbol="BTC/USDT",
                    timeframe="1m",
                )
            )
        index = pd.date_range(start.replace(tzinfo=None), periods=11, freq="1min")
        return pd.DataFrame({"close": range(len(index))}, index=index)

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", load)

    frame = market_data.load_strategy_frame(
        "Crypto",
        "BTC/USDT",
        "1m",
        start,
        end,
        market_type="swap",
        exchange_id="binance",
    )

    assert len(calls) == 2
    assert len(frame) == 11


def test_gate_hk_stock_backtest_uses_underlying_hk_market(monkeypatch):
    calls = []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **kwargs: {
            "underlying_market": "HKStock",
            "underlying_symbol": "00700",
        },
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline_with_diagnostics",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("exchange crypto path must not run")),
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **kwargs: calls.append(kwargs) or [
            {"time": 1785542400, "open": 500, "high": 505, "low": 495, "close": 502, "volume": 10},
            {"time": 1785628800, "open": 502, "high": 508, "low": 500, "close": 506, "volume": 12},
        ],
    )

    frame = market_data._load_strategy_frame_uncached(
        "Crypto",
        "00700/HKD",
        "1d",
        start,
        end,
        market_type="spot",
        exchange_id="gate",
        instrument_id="00700",
        api_family="stock",
    )

    assert list(frame["close"]) == [502, 506]
    assert calls[0]["market"] == "HKStock"
    assert calls[0]["symbol"] == "00700"
    assert calls[0]["exchange_id"] is None
    assert calls[0]["market_type"] is None


def test_gate_stock_market_data_infers_catalog_contract(monkeypatch):
    calls = []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **kwargs: {
            "api_family": "stock",
            "product_type": "direct_equity",
            "underlying_market": "USStock",
            "underlying_symbol": "NVDA",
        },
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline_with_diagnostics",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("exchange crypto path must not run")),
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **kwargs: calls.append(kwargs) or [
            {"time": 1785542400, "open": 180, "high": 185, "low": 179, "close": 184, "volume": 10},
            {"time": 1785628800, "open": 184, "high": 188, "low": 183, "close": 187, "volume": 12},
        ],
    )

    frame = market_data._load_strategy_frame_uncached(
        "Crypto",
        "NVDA/USD",
        "1d",
        start,
        end,
        market_type="spot",
        exchange_id="gate",
    )

    assert list(frame["close"]) == [184, 187]
    assert calls[0]["market"] == "USStock"
    assert calls[0]["symbol"] == "NVDA"


def test_gate_stock_without_instrument_id_uses_trading_calendar(monkeypatch):
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **kwargs: {
            "api_family": "stock",
            "product_type": "direct_equity",
            "underlying_market": "USStock",
            "underlying_symbol": "NVDA",
        },
    )

    assert not market_data._uses_continuous_crypto_calendar(
        "Crypto",
        "NVDA/USD",
        exchange_id="gate",
        market_type="spot",
        instrument_id="",
        api_family="",
    )


@pytest.mark.parametrize("product_type", ["tokenized_equity", "stock_perpetual"])
def test_always_open_equity_products_keep_continuous_coverage(monkeypatch, product_type):
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **kwargs: {
            "api_family": "spot" if product_type == "tokenized_equity" else "swap",
            "product_type": product_type,
            "underlying_market": "USStock",
            "underlying_symbol": "AAPL",
        },
    )

    assert market_data._uses_continuous_crypto_calendar(
        "Crypto",
        "AAPLX/USDT",
        exchange_id="bybit",
        market_type="spot" if product_type == "tokenized_equity" else "swap",
        instrument_id="AAPLXUSDT",
        api_family="",
    )


def test_bitget_reality_market_data_infers_catalog_contract(monkeypatch):
    from app.data_providers import bitget_reality_market

    calls = []
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **kwargs: {
            "api_family": "reality",
            "product_type": "tokenized_equity",
            "underlying_market": "USStock",
            "underlying_symbol": "AAPL",
        },
    )
    monkeypatch.setattr(
        bitget_reality_market,
        "get_bitget_reality_klines",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [
            {"time": 1785542400, "open": 200, "high": 205, "low": 199, "close": 204, "volume": 10},
            {"time": 1785628800, "open": 204, "high": 208, "low": 203, "close": 207, "volume": 12},
        ],
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline_with_diagnostics",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generic crypto path must not run")),
    )

    frame = market_data._load_strategy_frame_uncached(
        "Crypto",
        "RAAPL/USDT",
        "1d",
        start,
        end,
        market_type="spot",
        exchange_id="bitget",
        instrument_id="rAAPLUSDT",
    )

    assert list(frame["close"]) == [204, 207]
    assert calls[0][0][0] == "rAAPLUSDT"


def test_gate_hk_stock_shared_cache_uses_trading_calendar_coverage(monkeypatch):
    monkeypatch.setattr(
        "app.services.market.product_catalog.get_catalog_product",
        lambda **_: {"api_family": "stock", "underlying_market": "HKStock", "underlying_symbol": "00700"},
    )
    calls = []
    start = datetime(2026, 8, 3, tzinfo=timezone.utc)
    end = datetime(2026, 8, 7, 23, tzinfo=timezone.utc)
    index = pd.date_range("2026-08-03 16:00", "2026-08-07 16:00", freq="1D")
    frame = pd.DataFrame({"open": 500, "high": 505, "low": 495, "close": 502, "volume": 10}, index=index)
    monkeypatch.setattr(
        market_data,
        "_load_strategy_frame_uncached",
        lambda *_args, **_kwargs: calls.append(True) or frame,
    )

    first = market_data.load_strategy_frame(
        "Crypto", "00700/HKD", "1d", start, end,
        market_type="spot", exchange_id="gate", instrument_id="00700", api_family="stock",
    )
    second = market_data.load_strategy_frame(
        "Crypto", "00700/HKD", "1d", start, end,
        market_type="spot", exchange_id="gate", instrument_id="00700", api_family="stock",
    )

    assert len(first) == 5
    assert second.equals(first)
    assert calls == [True]


def test_explicit_binance_swap_identity_survives_strategy_v2_dataflow(monkeypatch):
    program = compile_strategy_v2('''
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@binance:swap"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
''')
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    captured = {}
    rows = [{
        "time": int(start.timestamp()),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 10,
    }]

    def get_kline_with_diagnostics(**kwargs):
        captured.update(kwargs)
        return rows, None

    monkeypatch.setattr(market_data, "_resolve_catalog_product", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline_with_diagnostics", get_kline_with_diagnostics)
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: pytest.fail("explicit Binance request used the unscoped fallback router"),
    )

    service = StrategyV2BacktestService(frame_fetcher=market_data.load_strategy_frame)
    candidates, _ = service.resolve_candidates(
        user_id=7,
        manifest=program.manifest,
        start_date=start,
        end_date=start,
    )
    frames, skipped = service.fetch_frames(candidates, "1d", start, start)

    instrument = program.manifest.universe.instruments[0]
    assert instrument.exchange_id == "binance"
    assert instrument.market_type == "swap"
    assert captured["exchange_id"] == "binance"
    assert captured["market_type"] == "swap"
    assert captured["symbol"] == "BTC/USDT"
    assert "Crypto:BTC/USDT@binance:swap" in frames
    assert frames["Crypto:BTC/USDT@binance:swap"].iloc[0]["close"] == pytest.approx(100)
    assert skipped == []


def test_explicit_exchange_with_empty_diagnostics_fails_without_unscoped_fallback(monkeypatch):
    monkeypatch.setattr(market_data, "_resolve_catalog_product", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_args: None)
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline_with_diagnostics",
        lambda **_kwargs: ([], None),
    )
    monkeypatch.setattr(
        market_data.DataSourceFactory,
        "get_kline",
        lambda **_kwargs: pytest.fail("explicit venue request used unscoped fallback"),
    )
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(MarketDataUnavailableError):
        market_data._load_strategy_frame_uncached(
            "Crypto",
            "BTC/USDT",
            "1h",
            start,
            start + timedelta(hours=1),
            exchange_id="binance",
            market_type="swap",
        )
