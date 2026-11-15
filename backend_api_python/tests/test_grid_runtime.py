"""Grid bot adaptive bounds and waterfall protection."""
import pandas as pd
import pytest

from app.services.bot_scripts import grid_runtime
from app.services.bot_scripts.grid_runtime import (
    filter_grid_signals_under_waterfall,
    prepare_grid_runtime,
    update_adaptive_bounds,
    update_waterfall_state,
)


def _bars(closes):
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
        }
    )


def test_adaptive_bounds_recenter_when_price_near_edge():
    params = {"upperPrice": 110.0, "lowerPrice": 90.0, "adaptiveBounds": True}
    changed = update_adaptive_bounds(params, 91.0, _bars([100.0] * 30), force=False)
    assert changed
    assert params["lowerPrice"] < 91.0 < params["upperPrice"]


def test_waterfall_triggers_pause_on_tick():
    params = {"waterfallProtection": True, "waterfallDropPct": 0.05, "waterfall_peak_price": 100.0}
    triggered = update_waterfall_state(
        params, price=94.0, high=94.0, is_closed_bar=False, now_ts=1_000_000,
    )
    assert triggered
    assert params.get("waterfall_pause") is True
    assert params.get("waterfall_until_ts", 0) > 1_000_000


def test_filter_blocks_entries_during_pause():
    params = {"waterfall_pause": True}
    sigs = [
        {"type": "open_long", "position_size": 0.1},
        {"type": "close_long", "position_size": 0},
    ]
    out = filter_grid_signals_under_waterfall(sigs, params)
    assert len(out) == 1
    assert out[0]["type"] == "close_long"


def test_prepare_grid_runtime_merges_defaults():
    params = {"upperPrice": 0, "lowerPrice": 0}
    prepare_grid_runtime(
        params,
        price=100.0,
        high=101.0,
        low=99.0,
        bars_df=_bars([98 + i * 0.1 for i in range(40)]),
        is_closed_bar=True,
    )
    assert params.get("adaptiveBounds") is True
    assert params.get("upperPrice", 0) > params.get("lowerPrice", 0)


def _wilder_atr_reference(df, period):
    """Wilder (1978): first TR is high - low, seed with the mean of the first N TRs, then RMA."""
    trs = []
    prev_close = None
    for high, low, close in zip(df["high"], df["low"], df["close"]):
        if prev_close is None:
            trs.append(high - low)
        else:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


@pytest.mark.skipif(grid_runtime._fp is None, reason="fin-primitives not installed")
def test_compute_atr_uses_wilder_smoothing():
    closes = [100.0 + (i % 7) * 0.8 - (i % 3) * 1.3 + i * 0.05 for i in range(60)]
    df = pd.DataFrame(
        {
            "high": [c + 0.4 + (i % 5) * 0.3 for i, c in enumerate(closes)],
            "low": [c - 0.5 - (i % 4) * 0.25 for i, c in enumerate(closes)],
            "close": closes,
        }
    )
    atr = grid_runtime._compute_atr(df, 14)
    assert atr == pytest.approx(_wilder_atr_reference(df, 14), rel=1e-9)

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    assert atr != pytest.approx(tr.tail(14).mean(), rel=1e-6)


@pytest.mark.skipif(grid_runtime._fp is None, reason="fin-primitives not installed")
def test_compute_atr_waits_for_warmup():
    assert grid_runtime._compute_atr(_bars([100.0, 101.0, 99.5]), 14) == 0.0
    assert grid_runtime._compute_atr(_bars([100.0 + i * 0.1 for i in range(14)]), 14) > 0.0
