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


def _ohlc(n, start=100.0):
    closes = [start + (i % 7) * 0.8 - (i % 3) * 1.3 + i * 0.05 for i in range(n)]
    return pd.DataFrame(
        {
            "high": [c + 0.4 + (i % 5) * 0.3 for i, c in enumerate(closes)],
            "low": [c - 0.5 - (i % 4) * 0.25 for i, c in enumerate(closes)],
            "close": closes,
        }
    )


def test_compute_atr_matches_hand_written_wilder_reference():
    df = _ohlc(60)
    atr = grid_runtime._compute_atr(df, 14)
    assert atr == pytest.approx(_wilder_atr_reference(df, 14), rel=1e-12)

    # Not the simple moving average of the last 14 true ranges.
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    assert atr != pytest.approx(tr.tail(14).mean(), rel=1e-6)


def test_compute_atr_warmup_boundaries():
    period = 14
    assert grid_runtime._compute_atr(_ohlc(period - 1), period) == 0.0
    exact = _ohlc(period)
    # Exactly `period` bars: the seed, the plain mean of the first `period` true ranges.
    assert grid_runtime._compute_atr(exact, period) == pytest.approx(_wilder_atr_reference(exact, period), rel=1e-12)


def test_compute_atr_uses_the_full_frame_for_long_histories():
    period = 14
    df = _ohlc(period * 10 + 37)
    full = _wilder_atr_reference(df, period)
    assert grid_runtime._compute_atr(df, period) == pytest.approx(full, rel=1e-12)
    # A truncated window would give a different value, so the whole frame is used.
    assert full != pytest.approx(_wilder_atr_reference(df.tail(period * 10).reset_index(drop=True), period), rel=1e-12)


def test_compute_atr_skips_missing_and_non_finite_bars():
    df = _ohlc(40)
    dirty = df.astype(object)  # lets a bad string sit in a numeric column
    dirty.loc[5, "high"] = float("nan")
    dirty.loc[11, "low"] = float("inf")
    dirty.loc[17, "close"] = None
    dirty.loc[23, "close"] = "bad"
    clean = df.drop(index=[5, 11, 17, 23]).reset_index(drop=True)
    assert grid_runtime._compute_atr(dirty, 14) == pytest.approx(_wilder_atr_reference(clean, 14), rel=1e-12)


def test_compute_atr_too_few_valid_bars_after_cleaning_returns_zero():
    df = _ohlc(16)
    df.loc[0:5, "close"] = float("nan")  # 10 valid bars left, period 14
    assert grid_runtime._compute_atr(df, 14) == 0.0


def test_compute_atr_missing_columns_is_logged_and_returns_zero(monkeypatch):
    warnings = []
    monkeypatch.setattr(grid_runtime.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a))
    df = _ohlc(30).drop(columns=["low"])
    assert grid_runtime._compute_atr(df, 14) == 0.0
    assert warnings and "low" in warnings[0]


def test_compute_atr_empty_or_none_returns_zero():
    assert grid_runtime._compute_atr(None, 14) == 0.0
    assert grid_runtime._compute_atr(pd.DataFrame(columns=["high", "low", "close"]), 14) == 0.0


def test_adaptive_bounds_fall_back_to_range_when_atr_unavailable():
    # Fewer bars than the ATR period: ATR is 0.0, so the range / min-width fallback sets the bounds.
    params = {"upperPrice": 0.0, "lowerPrice": 0.0, "adaptiveBounds": True}
    changed = update_adaptive_bounds(params, 100.0, _bars([100.0, 100.5, 99.8]), force=True)
    assert changed
    assert params["lowerPrice"] < 100.0 < params["upperPrice"]
    assert params["upperPrice"] - params["lowerPrice"] >= 100.0 * 0.02 - 1e-9


def test_fixed_bound_grid_is_unchanged():
    params = {"upperPrice": 110.0, "lowerPrice": 90.0, "adaptiveBounds": False}
    before = dict(params)
    assert update_adaptive_bounds(params, 95.0, _ohlc(60), force=True) is False
    assert params == before



def test_adaptive_bounds_min_width_fallback_when_bars_lack_ohlc():
    # No high/low columns: ATR is unavailable and the range fallback cannot read
    # them either, so the bounds fall back to the minimum width around price.
    params = {"upperPrice": 0.0, "lowerPrice": 0.0, "adaptiveBounds": True}
    bars = pd.DataFrame({"close": [100.0] * 30})
    assert update_adaptive_bounds(params, 100.0, bars, force=True)
    # ATR falls back to price * minWidth (2.0); half-width = max(1.0, 2.0 * atrMult 2.0) = 4.0
    assert params["lowerPrice"] == pytest.approx(96.0)
    assert params["upperPrice"] == pytest.approx(104.0)
