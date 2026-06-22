"""Backtest accepts four-way df columns with chart output['signals'] (no buy/sell)."""

from __future__ import annotations

import pandas as pd

from app.services.backtest import BacktestService
from app.services.builtin_indicators import _builtin_specs


def _sample_df(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    close = pd.Series(range(100, 100 + n), dtype=float, index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


FOUR_WAY_WITH_CHART_SIGNALS = """
my_indicator_name = "T"
my_indicator_description = "D"
# signal_form: four_way
# exit_owner: indicator
df = df.copy()
n = len(df)
df['open_long'] = False
df['close_long'] = False
df['open_short'] = False
df['close_short'] = False
df.at[5, 'open_long'] = True
df.at[10, 'close_long'] = True
output = {
    'name': 'T',
    'plots': [],
    'signals': [
        {'type': 'buy', 'text': 'L', 'color': '#0f0', 'data': [None] * n},
    ],
}
"""


def test_execute_indicator_four_way_with_output_signals_no_buy_sell():
    svc = BacktestService()
    df = _sample_df()
    out = svc._execute_indicator(FOUR_WAY_WITH_CHART_SIGNALS, df, backtest_params={})
    assert isinstance(out, dict)
    assert "open_long" in out
    assert bool(out["open_long"].iloc[5])
    assert bool(out["close_long"].iloc[10])


def test_builtin_indicator_sample_executes_with_four_way_contract():
    svc = BacktestService()
    df = _sample_df(120)
    code = _builtin_specs()[0]["code"]
    out = svc._execute_indicator(code, df, backtest_params={})
    assert isinstance(out, dict)
    for col in ("open_long", "close_long", "open_short", "close_short"):
        assert col in out
        assert len(out[col]) == len(df)
