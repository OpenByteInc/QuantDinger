from datetime import datetime, timezone

import pandas as pd
import pytest

from app.services.market_schedule import (
    equity_daily_bar_cutoff,
    equity_daily_execution_session,
    is_equity_market_open,
)
from app.services.strategy_runtime.timeframes import daily_equity_execution_policy, equity_daily_frames_ready
from app.services.strategy_v2 import market_data


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("now, expected", [
    ("2026-09-18T00:00:02", "2026-09-17"),
    ("2026-09-17T19:59:59", "2026-09-16"),
    ("2026-09-17T20:14:59", "2026-09-16"),
    ("2026-09-17T20:15:00", "2026-09-17"),
    ("2026-11-27T18:15:00", "2026-11-27"),
    ("2026-12-25T22:00:00", "2026-12-24"),
])
def test_daily_cutoff_uses_completed_session(now, expected):
    cutoff = equity_daily_bar_cutoff("USStock", utc(now))
    assert cutoff == pd.Timestamp(expected) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


@pytest.mark.parametrize("now, expected", [
    ("2026-09-18T00:00:02", None),
    ("2026-09-18T13:34:59", None),
    ("2026-09-18T13:35:00", "2026-09-17"),
    ("2026-09-18T19:59:59", "2026-09-17"),
    ("2026-09-18T20:00:00", None),
    ("2026-09-19T14:00:00", None),
    ("2026-09-08T13:35:00", "2026-09-04"),
    ("2026-11-27T14:34:59", None),
    ("2026-11-27T14:35:00", "2026-11-25"),
    ("2026-11-27T18:00:00", None),
    ("2026-12-25T15:00:00", None),
])
def test_execution_window_dst_holidays_and_early_close(now, expected):
    session = equity_daily_execution_session("USStock", now=utc(now))
    assert (session.date().isoformat() if session else None) == expected


@pytest.mark.parametrize("now, expected", [
    ("2026-09-18T13:29:59", False),
    ("2026-09-18T13:30:00", True),
    ("2026-09-18T19:59:59", True),
    ("2026-09-18T20:00:00", False),
    ("2026-09-19T15:00:00", False),
    ("2026-11-27T17:59:59", True),
    ("2026-11-27T18:00:00", False),
    ("2026-12-25T15:00:00", False),
])
def test_us_regular_market_session_gate(now, expected):
    assert is_equity_market_open("USStock", utc(now)) is expected


def test_crypto_cutoff_and_custom_schedules_are_unchanged():
    now = utc("2026-09-18T00:00:02")
    assert market_data._market_bar_cutoff("Crypto", "1d", now=now) == pd.Timestamp("2026-09-17")
    us = [{"market": "USStock", "key": "USStock:NVDA"}]
    assert daily_equity_execution_policy("1d", us, execution_mode="live")
    assert not daily_equity_execution_policy("1d", us, execution_mode="live", schedules=[object()])
    assert not daily_equity_execution_policy("1m", us, execution_mode="live")
    assert not daily_equity_execution_policy("1d", us, execution_mode="signal")
    assert not daily_equity_execution_policy("1d", [{"market": "Crypto"}], execution_mode="live")


def test_all_constituents_must_have_previous_session_data():
    candidates = [{"key": "USStock:NVDA"}, {"key": "USStock:AAPL"}]
    frames = {
        "USStock:NVDA": pd.DataFrame({"close": [100]}, index=pd.to_datetime(["2026-09-17T13:30"])),
        "USStock:AAPL": pd.DataFrame({"close": [200]}, index=pd.to_datetime(["2026-09-16T04:00"])),
    }
    session = utc("2026-09-17")
    assert not equity_daily_frames_ready(frames, candidates, session, "USStock")
    frames["USStock:AAPL"].index = pd.to_datetime(["2026-09-17T04:00"])
    assert equity_daily_frames_ready(frames, candidates, session, "USStock")


def test_daily_loader_includes_yesterdays_session_and_excludes_unfinished_today(monkeypatch):
    market_data.clear_shared_strategy_frame_cache()
    monkeypatch.setattr(market_data, "_market_bar_cutoff", lambda *_, **kwargs: equity_daily_bar_cutoff("USStock", utc("2026-09-18T14:00")))
    monkeypatch.setattr(market_data._cache, "get", lambda *_: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_: None)
    times = ["2026-09-16T13:30", "2026-09-17T13:30", "2026-09-18T13:30"]
    rows = [dict(time=int(pd.Timestamp(t).timestamp()), open=100, high=102, low=99, close=101) for t in times]
    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", lambda **_: rows)
    frame = market_data.load_strategy_frame("USStock", "NVDA", "1d", utc("2026-09-16"), utc("2026-09-18T14:00"))
    assert list(frame.index) == list(pd.to_datetime(times[:2]))
    market_data.clear_shared_strategy_frame_cache()


def test_daily_signal_survives_restart_without_duplicate_order():
    from app.services.strategy_v2 import StrategyV2LiveSession

    code = '''
PERSIST_RUNTIME_STATE = True
def initialize(context):
    context.set_universe(["USStock:NVDA"])
    context.subscribe(frequency="1d")
def handle_data(context, data):
    order_target_percent("USStock:NVDA", 0.5,
                         client_order_id=str(context.current_dt) + ":entry")
'''
    frame = pd.DataFrame(
        {"open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0], "volume": [1000.0]},
        index=pd.to_datetime(["2026-09-17T13:30"]),
    )
    frames = {"USStock:NVDA": frame}
    session = StrategyV2LiveSession(code=code, frames=frames, initial_capital=1000)
    orders, _, _ = session.process(frames, schedule_time=utc("2026-09-18T13:35"))
    assert len(orders) == 1
    restarted = StrategyV2LiveSession(code=code, frames=frames, initial_capital=1000)
    restarted.restore_session_snapshot(session.session_snapshot())
    assert restarted.process(frames, schedule_time=utc("2026-09-18T14:00"))[0] == []


def test_daily_entry_sizes_from_current_quote_without_changing_signal_candle():
    from app.services.strategy_v2 import OrderIntent
    from app.services.trading_executor import TradingExecutor

    executor = TradingExecutor.__new__(TradingExecutor)
    executor._get_current_positions = lambda *_: []
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return True

    executor._execute_signal = execute
    member = {"key": "USStock:NVDA", "symbol": "NVDA", "market": "USStock", "market_type": "spot"}
    frame = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-09-17T13:30"]))
    assert executor._execute_strategy_v2_intent(
        strategy_id=1, strategy_name="NVDA", intent=OrderIntent(symbol=member["key"], kind="target_percent", value=0.5),
        frames={member["key"]: frame}, candidates=[member], initial_capital=1000, leverage=1,
        execution_mode="live", notification_config={}, trading_config={}, exchange_config={},
        signal_ts=1, strategy_run_id=1, current_price_override=125,
    )
    assert captured["script_base_qty"] == 4
    assert frame["close"].iloc[-1] == 100


def test_delayed_daily_provider_is_retried_in_shared_cache(monkeypatch):
    market_data.clear_shared_strategy_frame_cache()
    monkeypatch.setattr(market_data, "_market_bar_cutoff", lambda *_, **kwargs: equity_daily_bar_cutoff("USStock", utc("2026-09-18T14:00")))
    calls = []

    def fetch(*args, **kwargs):
        calls.append(1)
        day = "2026-09-16T13:30" if len(calls) == 1 else "2026-09-17T13:30"
        return pd.DataFrame({"close": [100]}, index=pd.to_datetime([day]))

    monkeypatch.setattr(market_data, "_load_strategy_frame_uncached", fetch)
    args = ("USStock", "NVDA", "1d", utc("2026-09-16"), utc("2026-09-18T14:00"))
    market_data.load_strategy_frame(*args)
    second = market_data.load_strategy_frame(*args)
    assert len(calls) == 2
    assert second.index[-1] == pd.Timestamp("2026-09-17T13:30")
    market_data.clear_shared_strategy_frame_cache()


@pytest.mark.parametrize("now, expected", [
    ("2026-09-18T01:34:59", None),
    ("2026-09-18T01:35:00", "2026-09-17"),
    ("2026-09-18T04:00:00", None),
    ("2026-09-18T04:59:59", None),
    ("2026-09-18T05:00:00", "2026-09-17"),
    ("2026-09-18T08:00:00", None),
    ("2026-09-19T02:00:00", None),
    ("2026-12-24T04:00:00", None),
    ("2026-12-25T02:00:00", None),
    ("2026-12-28T01:35:00", "2026-12-24"),
])
def test_hk_execution_lunch_break_half_day_and_holidays(now, expected):
    session = equity_daily_execution_session("HKStock", now=utc(now))
    assert (session.date().isoformat() if session else None) == expected


@pytest.mark.parametrize("now, expected", [
    ("2026-09-18T01:29:59", False),
    ("2026-09-18T01:30:00", True),
    ("2026-09-18T03:59:59", True),
    ("2026-09-18T04:00:00", False),
    ("2026-09-18T04:59:59", False),
    ("2026-09-18T05:00:00", True),
    ("2026-09-18T08:00:00", False),
    ("2026-09-19T02:00:00", False),
    ("2026-12-25T02:00:00", False),
])
def test_hk_regular_market_session_gate(now, expected):
    assert is_equity_market_open("HKStock", utc(now)) is expected


def test_non_equity_market_session_gate_is_continuous():
    assert is_equity_market_open("Crypto", utc("2026-09-19T02:00:00")) is True


def test_hk_daily_candle_date_is_hong_kong_date_not_utc_date():
    cutoff = equity_daily_bar_cutoff("HKStock", utc("2026-09-18T01:35"))
    assert cutoff == pd.Timestamp("2026-09-17T15:59:59.999999")
    candidates = [{"key": "HKStock:00700"}]
    frame = pd.DataFrame({"close": [500]}, index=pd.to_datetime(["2026-09-16T16:00"]))
    assert equity_daily_frames_ready({"HKStock:00700": frame}, candidates, utc("2026-09-17"), "HKStock")
    assert equity_daily_bar_cutoff("HKStock", utc("2026-12-24T04:15")).date().isoformat() == "2026-12-24"


@pytest.mark.parametrize("market", ["USStock", "HKStock"])
def test_gate_stock_uses_underlying_daily_data_without_enforcing_broker_hours(market):
    from app.services.market_schedule import equity_data_market

    member = {"market": "Crypto", "api_family": "stock", "product_type": "direct_equity", "underlying_market": market}
    assert equity_data_market("Crypto", member) == market
    policy = daily_equity_execution_policy("1d", [member], execution_mode="live")
    assert policy == (market, False)
    signal = equity_daily_execution_session(*policy, now=utc("2026-09-18T23:00"))
    assert signal.date().isoformat() == "2026-09-18"


@pytest.mark.parametrize("family, product_type", [
    ("spot", "tokenized_equity"), ("swap", "stock_perpetual"), ("reality", "tokenized_equity"),
])
def test_token_equity_candles_are_not_replaced_with_underlying_stock_calendar(family, product_type):
    from app.services.market_schedule import equity_data_market

    member = {"market": "Crypto", "api_family": family, "product_type": product_type, "underlying_market": "USStock"}
    assert equity_data_market("Crypto", member) == "Crypto"
    assert daily_equity_execution_policy("1d", [member], execution_mode="live") is None
    assert market_data._market_bar_cutoff("Crypto", "1d", now=utc("2026-09-19T00:00:02")) == pd.Timestamp("2026-09-18")


def test_hk_loader_excludes_intraday_candle_labelled_at_local_midnight(monkeypatch):
    market_data.clear_shared_strategy_frame_cache()
    monkeypatch.setattr(market_data, "_market_bar_cutoff", lambda *_, **kwargs: equity_daily_bar_cutoff("HKStock", utc("2026-09-18T01:35")))
    monkeypatch.setattr(market_data._cache, "get", lambda *_: None)
    monkeypatch.setattr(market_data._cache, "put", lambda *_: None)
    times = ["2026-09-16T16:00", "2026-09-17T16:00"]
    rows = [dict(time=int(pd.Timestamp(t).timestamp()), open=500, high=502, low=499, close=501) for t in times]
    monkeypatch.setattr(market_data.DataSourceFactory, "get_kline", lambda **_: rows)
    frame = market_data.load_strategy_frame("HKStock", "00700", "1d", utc("2026-09-16"), utc("2026-09-18T01:35"))
    assert list(frame.index) == [pd.Timestamp(times[0])]
    market_data.clear_shared_strategy_frame_cache()
