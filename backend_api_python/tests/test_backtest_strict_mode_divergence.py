"""Strict vs non-strict backtest paths must produce different fills when timing differs."""

from __future__ import annotations

import pandas as pd

from app.services.backtest import BacktestService
from app.services.backtest_execution import merge_strict_mode_into_strategy_config


def _make_signal_frames():
    """One 4H signal bar with open_long; exec on 15m."""
    sig_idx = pd.date_range('2024-01-01 00:00', periods=3, freq='4h')
    df_signal = pd.DataFrame(
        {
            'open': [100.0, 100.0, 100.0],
            'high': [101.0, 101.0, 101.0],
            'low': [99.0, 99.0, 99.0],
            'close': [100.5, 100.5, 100.5],
            'volume': [1.0, 1.0, 1.0],
        },
        index=sig_idx,
    )
    exec_idx = pd.date_range('2024-01-01 00:00', periods=20, freq='15min')
    opens = [100.0] * 16 + [120.0, 120.0, 120.0, 120.0]
    closes = [100.0] * 15 + [110.0] + [120.0] * 4
    df_exec = pd.DataFrame(
        {
            'open': opens,
            'high': [v + 1 for v in opens],
            'low': [v - 1 for v in opens],
            'close': closes,
            'volume': [1.0] * len(exec_idx),
        },
        index=exec_idx,
    )
    signals = {
        'open_long': pd.Series([True, False, False], index=sig_idx),
        'close_long': pd.Series([False, False, False], index=sig_idx),
        'open_short': pd.Series([False, False, False], index=sig_idx),
        'close_short': pd.Series([False, False, False], index=sig_idx),
    }
    return df_signal, df_exec, signals


def test_standard_path_strict_vs_same_bar_differ():
    svc = BacktestService()
    idx = pd.date_range('2024-01-01', periods=4, freq='4h')
    df = pd.DataFrame(
        {
            'open': [10.0, 10.0, 10.0, 10.0],
            'high': [11.0, 11.0, 11.0, 11.0],
            'low': [9.0, 9.0, 9.0, 9.0],
            'close': [10.5, 10.5, 10.5, 10.5],
            'volume': [1.0] * 4,
        },
        index=idx,
    )
    signals = {
        'open_long': pd.Series([False, True, False, False], index=idx),
        'close_long': pd.Series([False, False, False, False], index=idx),
        'open_short': pd.Series([False] * 4, index=idx),
        'close_short': pd.Series([False] * 4, index=idx),
    }
    cfg_strict = merge_strict_mode_into_strategy_config({}, True)
    cfg_loose = merge_strict_mode_into_strategy_config({}, False)
    _, tr_strict, _ = svc._simulate_trading_new_format(
        df, signals, 10000, 0.001, 0, 1, 'long', cfg_strict,
    )
    _, tr_loose, _ = svc._simulate_trading_new_format(
        df, signals, 10000, 0.001, 0, 1, 'long', cfg_loose,
    )
    assert tr_strict and tr_loose
    assert tr_strict[0]['time'] != tr_loose[0]['time'] or tr_strict[0]['price'] != tr_loose[0]['price']


def test_mtf_same_bar_close_fills_on_signal_bar_close_not_next_open():
    svc = BacktestService()
    df_signal, df_exec, signals = _make_signal_frames()
    cfg = merge_strict_mode_into_strategy_config({}, False)
    _, trades, _, _ = svc._simulate_trading_mtf(
        df_signal,
        df_exec,
        signals,
        initial_capital=10000,
        commission=0.0,
        slippage=0.0,
        leverage=1,
        trade_direction='long',
        strategy_config=cfg,
        signal_timeframe='4H',
        exec_timeframe='15m',
    )
    assert len(trades) >= 1
    entry = trades[0]
    assert entry['type'] == 'open_long'
    # same_bar_close on MTF: fill at close of last 15m in signal 4H bar (110), not next 4H open (120)
    assert entry['price'] == 110.0
    assert '03:45' in entry['time']


def test_mtf_next_bar_open_fills_after_signal_bar():
    svc = BacktestService()
    df_signal, df_exec, signals = _make_signal_frames()
    cfg = merge_strict_mode_into_strategy_config({}, True)
    _, trades, _, _ = svc._simulate_trading_mtf(
        df_signal,
        df_exec,
        signals,
        initial_capital=10000,
        commission=0.0,
        slippage=0.0,
        leverage=1,
        trade_direction='long',
        strategy_config=cfg,
        signal_timeframe='4H',
        exec_timeframe='15m',
    )
    assert len(trades) >= 1
    entry = trades[0]
    assert entry['type'] == 'open_long'
    assert entry['price'] == 120.0
    assert '04:00' in entry['time']
