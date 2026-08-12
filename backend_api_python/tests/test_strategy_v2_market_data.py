from datetime import datetime, timedelta, timezone

import pytest

from app.services.strategy_v2 import market_data


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
        datetime(2026, 1, 2),
        market_type="spot",
    )

    assert len(frame) == 2
    assert frame.index.tz is None
    assert captured["timeframe"] == "4H"
    assert captured["limit"] < 250
    assert captured["after_time"] == int(datetime(2025, 12, 31, 20, tzinfo=timezone.utc).timestamp())
    assert captured["before_time"] == int(datetime(2026, 1, 2, 4, tzinfo=timezone.utc).timestamp())


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


def test_futu_config_and_strict_mode_are_forwarded_without_secrets_in_cache(monkeypatch):
    captured = {}
    cache_keys = []
    config = {
        "exchange_id": "futu",
        "futu_host": "host.docker.internal",
        "futu_port": 11111,
        "trade_market": "HK",
        "unlock_password": "must-not-enter-cache-key",
    }

    def get_kline(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", get_kline)
    monkeypatch.setattr(market_data._cache, "get", lambda key: cache_keys.append(key))

    market_data.load_strategy_frame(
        "HKStock",
        "00700.HK",
        "1d",
        datetime(2026, 8, 1),
        datetime(2026, 8, 2),
        market_type="spot",
        exchange_id="futu",
        exchange_config=config,
        strict_data_source=True,
    )

    assert captured["exchange_config"] is config
    assert captured["allow_futu_fallback"] is False
    assert captured["strict_data_source"] is True
    assert "host.docker.internal" in cache_keys[0]
    assert "must-not-enter-cache-key" not in cache_keys[0]


def test_strict_market_data_failure_is_not_silenced(monkeypatch):
    monkeypatch.setattr(market_data._cache, "get", lambda _key: None)

    def get_kline(**_kwargs):
        raise RuntimeError("FUTU_OPEND_UNREACHABLE")

    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", get_kline)

    with pytest.raises(RuntimeError, match="executionMarketDataUnavailable"):
        market_data.load_strategy_frame(
            "HKStock",
            "00700.HK",
            "1d",
            datetime(2026, 8, 1),
            datetime(2026, 8, 2),
            market_type="spot",
            exchange_id="futu",
            exchange_config={"futu_host": "127.0.0.1"},
            strict_data_source=True,
        )
