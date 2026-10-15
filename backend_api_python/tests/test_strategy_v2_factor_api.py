import numpy as np
import pandas as pd
import pytest

from app.services.strategy_v2 import StrategyV2BacktestRunner


def test_v2_strategy_can_compute_builtin_factor_without_future_data():
    close = np.linspace(100.0, 130.0, 40)
    frame = pd.DataFrame({
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(40, 1000),
    }, index=pd.date_range("2026-01-01", periods=40, freq="D"))
    code = """
def initialize(context):
    context.set_universe(["USStock:AAPL"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    if len(get_history(100, security_list="AAPL")) >= 20:
        context.log("sma=%.2f" % factor("sma", "AAPL", period=20))
"""
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"USStock:AAPL": frame},
        initial_capital=10000,
    ).run()

    assert result["logs"]
    assert result["logs"][0].startswith("sma=")


@pytest.mark.parametrize("use_talib", [False, True])
@pytest.mark.parametrize("parameter", ["period", "timeperiod"])
def test_sma_crossover_uses_requested_periods_and_next_open(monkeypatch, use_talib, parameter):
    if use_talib:
        pytest.importorskip("talib")
    monkeypatch.setattr(
        "app.services.strategy_v2.runtime.is_talib_available", lambda: use_talib
    )
    close = np.concatenate([
        np.linspace(150.0, 100.0, 80),
        np.linspace(100.0, 180.0, 80),
        np.linspace(180.0, 100.0, 80),
    ])
    frame = pd.DataFrame({
        "open": close + 0.25,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": np.full(len(close), 1000),
    }, index=pd.date_range("2026-01-01", periods=len(close), freq="D"))
    code = '''
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.set_warmup(61)

def handle_data(context, data):
    fast = indicator("sma", g.symbol, PARAMETER=20).dropna()
    slow = indicator("sma", g.symbol, PARAMETER=60).dropna()
    if len(fast) < 2 or len(slow) < 2:
        return
    if fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]:
        order_target_percent(g.symbol, 0.95, reason="ma_cross_entry")
    elif fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]:
        order_target_percent(g.symbol, 0.0, reason="ma_cross_exit")
'''.replace("PARAMETER", parameter)
    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=10000,
        commission=0,
        slippage=0,
    ).run()
    fast = frame["close"].rolling(20).mean()
    slow = frame["close"].rolling(60).mean()
    crosses = ((fast.shift(1) <= slow.shift(1)) & (fast > slow)) | (
        (fast.shift(1) >= slow.shift(1)) & (fast < slow)
    )
    signal_indices = np.flatnonzero(crosses.to_numpy())
    executions = result["executions"]

    assert len(signal_indices) == len(executions) == 2
    assert [execution["side"] for execution in executions] == ["buy", "sell"]
    for execution, index in zip(executions, signal_indices):
        assert pd.Timestamp(execution["signal_time"]) == frame.index[index].tz_localize("UTC")
        assert pd.Timestamp(execution["time"]) == frame.index[index + 1].tz_localize("UTC")
        assert execution["price"] == pytest.approx(frame["open"].iloc[index + 1])


def test_builtin_supertrend_remains_series_and_explicit_talib_is_dataframe():
    pytest.importorskip("talib")
    close = np.linspace(100.0, 140.0, 70) + np.sin(np.arange(70)) * 3
    frame = pd.DataFrame({
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.full(len(close), 1000),
    }, index=pd.date_range("2026-01-01", periods=len(close), freq="D"))
    code = '''
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d")
    context.set_warmup(30)

def handle_data(context, data):
    native = indicator("supertrend", g.symbol, period=10, multiplier=3.0, output="direction")
    library = indicator("talib:SUPERTREND", g.symbol, timeperiod=10, multiplier=3.0)
    context.log("native=%s talib_outputs=%s" % (native.name, len(library.columns)))
'''

    result = StrategyV2BacktestRunner(
        code=code,
        frames={"Crypto:BTC/USDT@spot": frame},
        initial_capital=10000,
    ).run()

    assert result["logs"]
    assert set(result["logs"]) == {"native=supertrend talib_outputs=2"}
