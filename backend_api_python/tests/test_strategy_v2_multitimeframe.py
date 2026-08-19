from datetime import datetime

import pandas as pd
import pytest

from app.services.strategy_v2 import (
    StrategyV2BacktestRunner,
    StrategyV2ContractError,
    StrategyV2LiveSession,
    compile_strategy_v2,
)
from app.services.strategy_v2.data import MultiAssetDataPortal, StrategyDataError
from app.services.strategy_v2.service import StrategyV2BacktestService


SYMBOL = "Crypto:BTC/USDT@binance:swap"


def _frame(index: pd.DatetimeIndex, start: float = 100.0) -> pd.DataFrame:
    prices = [start + item for item in range(len(index))]
    return pd.DataFrame(
        {
            "open": prices,
            "high": [item + 1.0 for item in prices],
            "low": [item - 1.0 for item in prices],
            "close": prices,
            "volume": [1_000.0] * len(index),
        },
        index=index,
    )


def _frequency_frames() -> dict[str, dict[str, pd.DataFrame]]:
    return {
        "1h": {
            SYMBOL: _frame(
                pd.date_range("2026-01-02 00:00", periods=6, freq="1h")
            )
        },
        "4h": {
            SYMBOL: _frame(
                pd.DatetimeIndex(
                    [
                        "2026-01-01 20:00",
                        "2026-01-02 00:00",
                        "2026-01-02 04:00",
                    ]
                )
            )
        },
        "1d": {
            SYMBOL: _frame(
                pd.DatetimeIndex(["2026-01-01 00:00", "2026-01-02 00:00"])
            )
        },
    }


def test_manifest_selects_fastest_subscription_as_driving_frequency():
    program = compile_strategy_v2(
        f'''\
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1d")
    context.subscribe(frequency="4h")
    context.subscribe(frequency="1h")

def handle_data(context, data):
    pass
'''
    )

    assert program.manifest.primary_frequency == "1d"
    assert program.manifest.driving_frequency == "1h"
    assert program.manifest.frequencies == ("1d", "4h", "1h")
    assert program.manifest.metadata()["drivingFrequency"] == "1h"


def test_five_fifteen_thirty_minute_and_weekly_subscriptions_work_together():
    program = compile_strategy_v2(
        f'''\
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1w")
    context.subscribe(frequency="30m")
    context.subscribe(frequency="15m")
    context.subscribe(frequency="5m")

def handle_data(context, data):
    weekly = get_history(10, "1w", "close", "{SYMBOL}")
    thirty = get_history(10, "30m", "close", "{SYMBOL}")
    fifteen = get_history(10, "15m", "close", "{SYMBOL}")
    five = get_history(10, "5m", "close", "{SYMBOL}")
    if min(len(weekly), len(thirty), len(fifteen), len(five)) < 10:
        return
'''
    )

    assert program.manifest.frequencies == ("1w", "30m", "15m", "5m")
    assert program.manifest.driving_frequency == "5m"


def test_higher_timeframe_bar_is_hidden_until_its_close():
    bundles = _frequency_frames()
    portal = MultiAssetDataPortal(
        bundles["1h"],
        frequency_frames=bundles,
        driving_frequency="1h",
    )

    portal.set_clock(pd.Timestamp("2026-01-02 02:00"), include_current=True)
    before_close = portal.visible_frame(SYMBOL, frequency="4h")
    portal.set_clock(pd.Timestamp("2026-01-02 03:00"), include_current=True)
    after_close = portal.visible_frame(SYMBOL, frequency="4h")

    assert list(before_close.index) == [pd.Timestamp("2026-01-01 20:00")]
    assert list(after_close.index) == [
        pd.Timestamp("2026-01-01 20:00"),
        pd.Timestamp("2026-01-02 00:00"),
    ]
    with pytest.raises(StrategyDataError, match="frequencyNotSubscribed:15m"):
        portal.visible_frame(SYMBOL, frequency="15m")


def test_backtest_routes_history_by_frequency_without_lookahead():
    bundles = _frequency_frames()
    code = f'''\
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1d")
    context.subscribe(frequency="4h")
    context.subscribe(frequency="1h")

def handle_data(context, data):
    one_hour = get_history(20, "1h", "close", "{SYMBOL}")
    four_hour = get_history(20, "4h", "close", "{SYMBOL}")
    daily = get_history(20, "1d", "close", "{SYMBOL}")
    log.info(f"visible={{context.current_dt.hour}}:{{len(one_hour)}}:{{len(four_hour)}}:{{len(daily)}}")
'''
    result = StrategyV2BacktestRunner(
        code=code,
        frames=bundles["1h"],
        frequency_frames=bundles,
        initial_capital=10_000,
    ).run()

    assert "[info] visible=2:3:1:1" in result["logs"]
    assert "[info] visible=3:4:2:1" in result["logs"]
    assert result["sampleCount"] == 6


def test_service_loads_and_reports_every_declared_frequency():
    calls: list[str] = []
    bundles = _frequency_frames()

    def fetch(_market, _symbol, frequency, *_args, **_kwargs):
        calls.append(frequency)
        return bundles[frequency][SYMBOL]

    code = f'''\
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1d")
    context.subscribe(frequency="4h")
    context.subscribe(frequency="1h")

def handle_data(context, data):
    pass
'''
    _, result = StrategyV2BacktestService(frame_fetcher=fetch).run(
        user_id=1,
        code=code,
        start_date=datetime(2026, 1, 2),
        end_date=datetime(2026, 1, 2, 5),
        initial_capital=10_000,
        persist=False,
    )

    assert set(calls) == {"1h", "4h", "1d"}
    assert result["dataProvenance"]["frequencies"] == ["1d", "4h", "1h"]
    assert set(result["dataProvenance"]["timeframes"]) == {"1d", "4h", "1h"}
    assert result["executionAssumptions"]["drivingFrequency"] == "1h"


def test_live_session_uses_the_same_multitimeframe_visibility_policy():
    bundles = _frequency_frames()
    code = f'''\
def initialize(context):
    context.set_universe(["{SYMBOL}"])
    context.subscribe(frequency="1h")
    context.subscribe(frequency="4h")
    context.subscribe(frequency="1d")

def handle_data(context, data):
    one_hour = get_history(20, "1h", "close", "{SYMBOL}")
    four_hour = get_history(20, "4h", "close", "{SYMBOL}")
    daily = get_history(20, "1d", "close", "{SYMBOL}")
    log.info(f"live={{len(one_hour)}}:{{len(four_hour)}}:{{len(daily)}}")
'''
    session = StrategyV2LiveSession(
        code=code,
        frames=bundles["1h"],
        frequency_frames=bundles,
        initial_capital=10_000,
    )

    _orders, messages, timestamp = session.process(
        bundles["1h"],
        frequency_frames=bundles,
    )

    assert timestamp == pd.Timestamp("2026-01-02 05:00")
    assert messages == ["[info] live=6:2:1"]


def test_loader_drops_instruments_with_an_incomplete_timeframe_bundle():
    index = pd.date_range("2026-01-01", periods=5, freq="1h")

    def fetch(_market, symbol, frequency, *_args, **_kwargs):
        if symbol == "ETH/USDT" and frequency == "4h":
            return pd.DataFrame()
        return _frame(index)

    service = StrategyV2BacktestService(frame_fetcher=fetch)
    candidates = [
        {
            "key": "Crypto:BTC/USDT@binance:swap",
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "market_type": "swap",
            "exchange_id": "binance",
        },
        {
            "key": "Crypto:ETH/USDT@binance:swap",
            "market": "Crypto",
            "symbol": "ETH/USDT",
            "market_type": "swap",
            "exchange_id": "binance",
        },
    ]
    bundles, skipped = service.fetch_frequency_frames(
        candidates,
        ("1h", "4h"),
        {"1h": datetime(2026, 1, 1), "4h": datetime(2026, 1, 1)},
        datetime(2026, 1, 2),
    )

    assert set(bundles["1h"]) == {"Crypto:BTC/USDT@binance:swap"}
    assert set(bundles["4h"]) == {"Crypto:BTC/USDT@binance:swap"}
    assert skipped == [
        {
            "symbol": "Crypto:ETH/USDT@binance:swap",
            "frequency": "4h",
            "reason": "strategyV2.noMarketData",
        }
    ]


def test_contract_rejects_unsupported_monthly_subscription():
    with pytest.raises(StrategyV2ContractError, match="frequencyUnsupported:1mo"):
        compile_strategy_v2(
            '''\
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="monthly")

def handle_data(context, data):
    pass
'''
        )


def test_contract_accepts_frequency_keyword_on_data_history():
    program = compile_strategy_v2(
        '''\
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1h")
    context.subscribe(frequency="4h")

def handle_data(context, data):
    bars = data.history("USStock:AAPL", count=10, fields=["close"], frequency="4h")
    if len(bars) < 10:
        return
'''
    )

    assert program.manifest.driving_frequency == "1h"


def test_contract_rejects_a_literal_read_from_an_unsubscribed_frequency():
    with pytest.raises(
        StrategyV2ContractError,
        match="frequencyNotSubscribed:4h",
    ):
        compile_strategy_v2(
            '''\
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1h")

def handle_data(context, data):
    bars = get_history(10, "4h", "close", "USStock:AAPL")
    if len(bars) < 10:
        return
'''
        )
