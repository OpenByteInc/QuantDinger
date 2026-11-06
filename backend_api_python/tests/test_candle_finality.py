"""Regression coverage for venue boundaries and exchange-session finality."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.data_sources.candle_calendar import candle_open, completed_candle_open
from app.data_sources.native_crypto import NativeCryptoPublicClient
from app.services.market_schedule import filter_closed_equity_klines, equity_intraday_bar_open
from app.services.strategy_v2.market_data import _market_bar_cutoff


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("frequency,step", [("1m", 60), ("5m", 300), ("1h", 3600), ("4h", 14400), ("1d", 86400), ("1w", 604800)])
def test_gate_futures_reads_the_entire_final_candle(frequency, step):
    client = NativeCryptoPublicClient("gate", "swap")
    target = completed_candle_open(utc("2026-01-10T12:00:00").timestamp(), frequency, "gate", "swap")
    def get(_url, params):
        assert params["to"] == target + step - 1
        return [{"t": target, "o": 10, "h": 12, "l": 9, "c": 11, "v": 42}]
    client._get = get
    rows = client.fetch_ohlcv("BTC/USDT", frequency, since=(target - 4 * step) * 1000, limit=5, until=(target + step) * 1000)
    assert rows[-1][4:] == [11, 42]


@pytest.mark.parametrize("market", ["spot", "swap"])
def test_bitget_daily_end_uses_native_session_boundary(market):
    client = NativeCryptoPublicClient("bitget", market)
    target = int(utc("2026-01-05T16:00:00").timestamp() * 1000)
    def get(_url, params):
        assert params["endTime"] == target + 86400000
        return {"data": [[target, 10, 12, 9, 11, 42]]}
    client._get = get
    assert client.fetch_ohlcv("BTC/USDT", "1d", since=target - 4 * 86400000, limit=5)[-1][0] == target


@pytest.mark.parametrize("venue,market", [("okx", "spot"), ("okx", "swap"), ("gate", "spot")])
def test_explicit_unconfirmed_candles_do_not_reach_strategy(venue, market):
    client = NativeCryptoPublicClient(venue, market)
    target = int(utc("2026-01-01T12:00:00").timestamp() * 1000)
    raw = {"data": [[target, 10, 12, 9, 11, 42, 1, 1, "0"]]}
    if venue == "gate":
        raw = [[target // 1000, 1, 11, 12, 9, 10, 42, "false"]]
    client._get = lambda *_args: raw
    assert client.fetch_ohlcv("BTC/USDT", "1m", since=target, limit=1)
    assert client.fetch_ohlcv("BTC/USDT", "1m", since=target, limit=1, closed_only=True) == []


@pytest.mark.parametrize("venue,market,expected", [
    ("okx", "swap", "2026-11-05T16:00:00"), ("bitget", "spot", "2026-11-05T16:00:00"),
    ("htx", "swap", "2026-11-05T16:00:00"), ("binance", "spot", "2026-11-05T00:00:00"),
    ("bybit", "swap", "2026-11-05T00:00:00"), ("gate", "swap", "2026-11-05T00:00:00"),
])
def test_daily_strategy_cutoff_matches_venue(venue, market, expected):
    result = _market_bar_cutoff("Crypto", "1d", now=utc("2026-11-06T16:00:05"), exchange_id=venue, market_type=market)
    assert result == pd.Timestamp(expected)


@pytest.mark.parametrize("venue,market,expected", [
    ("binance", "spot", "2026-10-26T00:00:00"), ("bybit", "swap", "2026-10-26T00:00:00"),
    ("gate", "spot", "2026-10-26T00:00:00"), ("gate", "swap", "2026-10-22T00:00:00"),
    ("okx", "swap", "2026-10-25T16:00:00"), ("htx", "spot", "2026-10-25T16:00:00"),
    ("bitget", "swap", "2026-10-25T16:00:00"),
])
def test_weekly_boundary_is_not_always_epoch_thursday(venue, market, expected):
    result = completed_candle_open(utc("2026-11-02T00:00:05").timestamp(), "1w", venue, market)
    assert result == int(utc(expected).timestamp())


@pytest.mark.parametrize("market,label,before,after", [
    ("USStock", "2025-07-07T04:00:00", "2025-07-07T20:10:00", "2025-07-07T20:16:00"),
    ("USStock", "2025-11-03T05:00:00", "2025-11-03T21:10:00", "2025-11-03T21:16:00"),
    ("USStock", "2025-07-03T04:00:00", "2025-07-03T17:10:00", "2025-07-03T17:16:00"),
    ("HKStock", "2025-07-06T16:00:00", "2025-07-07T08:10:00", "2025-07-07T08:16:00"),
])
def test_daily_equity_close_uses_session_not_24_hours(market, label, before, after):
    rows = [{"time": int(utc(label).timestamp()), "close": 10}]
    assert filter_closed_equity_klines(rows, "1d", market, utc(before)) == []
    assert filter_closed_equity_klines(rows, "1d", market, utc(after)) == rows


def test_weekly_equity_waits_for_shortened_last_session():
    rows = [{"time": int(utc("2025-06-30T04:00:00").timestamp()), "close": 10}]
    assert filter_closed_equity_klines(rows, "1w", "USStock", utc("2025-07-03T17:10:00")) == []
    assert filter_closed_equity_klines(rows, "1w", "USStock", utc("2025-07-03T17:16:00")) == rows


@pytest.mark.parametrize("market,now,expected", [
    ("HKStock", "2025-07-07T04:05:00", "2025-07-07T03:30:00"),
    ("HKStock", "2025-07-07T04:55:00", "2025-07-07T03:30:00"),
    ("USStock", "2025-07-03T17:05:00", "2025-07-03T16:30:00"),
    ("USStock", "2025-07-04T14:05:00", "2025-07-03T16:30:00"),
])
def test_intraday_completion_handles_breaks_holidays_and_short_sessions(market, now, expected):
    assert equity_intraday_bar_open(market, 3600, utc(now)) == pd.Timestamp(expected)


def frame(label):
    return pd.DataFrame({"open": [10.], "high": [12.], "low": [9.], "close": [11.], "volume": [42.]}, index=[pd.Timestamp(label)])


def test_runtime_waits_for_confirmed_tail_and_boundary_settling():
    from app.services.strategy_runtime.candle_readiness import runtime_frames_ready
    members = [{"key": "btc", "market": "Crypto", "exchange_id": "gate", "market_type": "swap"}]
    bundle = {"1m": {"btc": frame("2026-01-01T11:59:00")}}
    assert not runtime_frames_ready(bundle, members, utc("2026-01-01T12:01:05"))
    bundle["1m"]["btc"] = frame("2026-01-01T12:00:00")
    assert not runtime_frames_ready(bundle, members, utc("2026-01-01T12:01:01"))
    assert runtime_frames_ready(bundle, members, utc("2026-01-01T12:01:05"))
    assert not runtime_frames_ready({}, members, utc("2026-01-01T12:01:05"))


def test_secondary_timeframe_must_also_be_current():
    from app.services.strategy_runtime.candle_readiness import runtime_frames_ready
    members = [{"key": "btc", "market": "Crypto", "exchange_id": "okx"}]
    bundle = {"1m": {"btc": frame("2026-01-01T16:00:00")}, "1d": {"btc": frame("2025-12-30T16:00:00")}}
    assert not runtime_frames_ready(bundle, members, utc("2026-01-01T16:01:05"))
    bundle["1d"]["btc"] = frame("2025-12-31T16:00:00")
    assert runtime_frames_ready(bundle, members, utc("2026-01-01T16:01:05"))


def test_equity_weekend_is_current_but_stock_perpetual_is_not():
    from app.services.strategy_runtime.candle_readiness import frame_is_current
    weekend = utc("2025-07-05T15:00:05")
    last_session = frame("2025-07-03T16:59:00")
    assert frame_is_current(last_session, {"market": "USStock"}, "1m", weekend)
    assert frame_is_current(last_session, {"market": "Crypto", "api_family": "stock", "underlying_market": "USStock"}, "1m", weekend)
    assert not frame_is_current(last_session, {"market": "Crypto", "api_family": "futures", "underlying_market": "USStock", "market_type": "swap"}, "1m", weekend)


@pytest.mark.parametrize("venue,frequency", [("okx", "1d"), ("bitget", "1d"), ("htx", "1w"), ("gate", "1w")])
def test_event_token_and_recovery_close_share_native_boundary(venue, frequency):
    from app.events.bar_recovery import BarRecoveryRoute
    from app.services.strategy_runtime.timeframes import completed_bar_token, completed_bar_close
    now = utc("2026-11-06T16:00:05")
    token = completed_bar_token(frequency, now, venue=venue, market_type="swap")
    close = completed_bar_close(frequency, token, venue=venue, market_type="swap")
    step = 604800 if frequency == "1w" else 86400
    assert close.timestamp() == completed_candle_open(now.timestamp(), frequency, venue, "swap") + step
    route = BarRecoveryRoute.parse(f"bar:{venue}:crypto:swap:btc/usdt:{frequency}")
    assert datetime.fromisoformat(route.event(token).payload["closed_at"].replace("Z", "+00:00")) == close


def test_gate_historical_asof_inside_bar_cannot_confirm_truncated_candle():
    client = NativeCryptoPublicClient("gate", "swap")
    target = int(utc("2026-01-01T12:00:00").timestamp())
    client._get = lambda *_: [{"t": target, "o": 10, "h": 10, "l": 10, "c": 10, "v": 0}]
    assert client.fetch_ohlcv("BTC/USDT", "1m", since=target * 1000, until=(target + 20) * 1000, closed_only=True) == []


def bars(labels):
    return [dict(time=int(utc(label).timestamp()), open=10, high=12, low=9, close=11, volume=42) for label in labels]


def test_hk_four_hour_resample_preserves_grid_and_does_not_join_next_day():
    from app.services.market_schedule import resample_equity_klines
    rows = bars(["2025-07-07T01:30:00", "2025-07-07T02:30:00", "2025-07-07T03:30:00", "2025-07-07T04:30:00", "2025-07-07T05:30:00", "2025-07-07T06:30:00", "2025-07-07T07:30:00", "2025-07-08T01:30:00"])
    result = resample_equity_klines(rows, "HKStock", 3600, 14400)
    assert [row["time"] for row in result] == [rows[0]["time"], rows[4]["time"]]
    assert [row["volume"] for row in result] == [168, 126]
    assert resample_equity_klines(rows[:1] + rows[2:3], "HKStock", 3600, 14400) == []


def test_hk_lunch_overlap_is_not_dropped_or_prematurely_finalized():
    row = bars(["2025-07-07T04:30:00"])
    assert filter_closed_equity_klines(row, "1h", "HKStock", utc("2025-07-07T05:20:00")) == []
    assert filter_closed_equity_klines(row, "1h", "HKStock", utc("2025-07-07T05:31:00")) == row
    assert equity_intraday_bar_open("HKStock", 3600, utc("2025-07-07T05:31:00")) == pd.Timestamp("2025-07-07T04:30:00")
    four_hour = bars(["2025-07-07T01:30:00"])
    assert filter_closed_equity_klines(four_hour, "4h", "HKStock", utc("2025-07-07T04:10:00")) == []
    assert equity_intraday_bar_open("HKStock", 14400, utc("2025-07-07T04:10:00")) < pd.Timestamp("2025-07-07")
    assert filter_closed_equity_klines(four_hour, "4h", "HKStock", utc("2025-07-07T05:31:00")) == four_hour


def test_hk_half_hour_fallback_matches_native_hour_grid():
    from app.data_sources.asia_stock_kline import _merge_session_bars
    rows = bars(["2025-07-07T01:30:00", "2025-07-07T02:00:00", "2025-07-07T02:30:00", "2025-07-07T03:00:00", "2025-07-07T03:30:00", "2025-07-07T05:00:00", "2025-07-07T05:30:00", "2025-07-07T06:00:00", "2025-07-07T06:30:00", "2025-07-07T07:00:00", "2025-07-07T07:30:00"])
    result = _merge_session_bars(rows, "1H", True, source_seconds=1800)
    assert [row["time"] for row in result] == [int(utc(f"2025-07-07T{hour:02d}:30:00").timestamp()) for hour in range(1, 8)]
    assert sum(row["volume"] for row in result) == sum(row["volume"] for row in rows)


def test_hk_akshare_close_labels_are_normalized_to_utc_open():
    from app.data_sources.asia_stock_kline import _bars_from_ak_min_df
    source = pd.DataFrame([["2025-07-07 10:00:00", 10, 11, 12, 9, 42]], columns=["time", "open", "close", "high", "low", "volume"])
    rows = _bars_from_ak_min_df(source, is_hk=True, period_seconds=1800)
    assert rows == bars(["2025-07-07T01:30:00"])


def test_twelvedata_hk_uses_local_timezone_and_half_hour_constituents(monkeypatch):
    from types import SimpleNamespace
    from app.data_sources import asia_stock_kline
    monkeypatch.setattr(asia_stock_kline, "_get_twelve_data_api_key", lambda: "test-only")
    monkeypatch.setattr(asia_stock_kline, "_twelvedata_daily_limit_active", lambda: False)
    captured = {}

    def get(_url, **kwargs):
        captured.update(kwargs["params"])
        return SimpleNamespace(json=lambda: {"status": "ok", "values": [
            dict(datetime=f"2025-07-07 {label}", open="10", high="12", low="9", close="11", volume="42")
            for label in ["09:30:00", "10:00:00"]
        ]})

    monkeypatch.setattr(asia_stock_kline.requests, "get", get)
    result = asia_stock_kline.fetch_twelvedata_klines(
        is_hk=True, tencent_code="HK00700", timeframe="1H", limit=10,
        before_time=int(utc("2025-07-07T02:30:00").timestamp()),
    )
    assert captured["interval"] == "30min"
    assert captured["end_date"] == "2025-07-07 10:30:00"
    assert result == [dict(bars(["2025-07-07T01:30:00"])[0], volume=84)]


def test_us_weekly_resample_uses_holiday_week_not_five_rows():
    from app.services.market_schedule import resample_equity_klines
    rows = bars(["2025-06-30T00:00:00", "2025-07-01T00:00:00", "2025-07-02T00:00:00", "2025-07-03T00:00:00", "2025-07-07T00:00:00"])
    result = resample_equity_klines(rows, "USStock", 86400, 604800)
    assert len(result) == 1
    assert result[0]["time"] == int(utc("2025-06-30T04:00:00").timestamp())
    assert result[0]["volume"] == 168
    assert resample_equity_klines(rows[1:], "USStock", 86400, 604800) == []
