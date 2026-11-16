"""Adaptive grid price bounds and cascade protection helpers."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import math

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULTS: Dict[str, Any] = {
    "adaptiveBounds": True,
    "adaptiveLookback": 48,
    "adaptiveAtrPeriod": 14,
    "adaptiveAtrMult": 2.0,
    "adaptiveMinWidthPct": 0.02,
    "adaptiveMaxShiftPct": 0.08,
    "adaptiveEdgePct": 0.12,
    "waterfallProtection": True,
    "waterfallDropPct": 0.03,
    "waterfallWindowBars": 6,
    "waterfallWindowSec": 300,
    "waterfallCooldownBars": 12,
    "waterfallCooldownSec": 900,
    "waterfallCloseOnTrigger": False,
}


def _truthy(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _apply_defaults(params: Dict[str, Any]) -> None:
    for k, dv in _DEFAULTS.items():
        if k not in params or params[k] is None or params[k] == "":
            params[k] = dv


_ATR_COLUMNS = ("high", "low", "close")


def _compute_atr(bars_df: Optional[pd.DataFrame], period: int) -> float:
    """Wilder ATR over the whole frame; 0.0 until ``period`` valid bars exist.

    True range of the first valid bar is ``high - low``; later bars use
    ``max(high - low, |high - prev_close|, |low - prev_close|)``. The ATR is
    seeded with the mean of the first ``period`` true ranges and then updated
    with Wilder's RMA: ``atr = (atr * (period - 1) + tr) / period``.

    Rows whose high, low or close is missing or not finite are skipped, so the
    previous close always comes from the last valid bar. A frame without the
    required columns, or with fewer than ``period`` valid bars, returns 0.0 so
    the caller's range / min-width fallback takes over.
    """
    if bars_df is None or bars_df.empty or period < 1:
        return 0.0
    missing = [c for c in _ATR_COLUMNS if c not in bars_df.columns]
    if missing:
        logger.warning("grid ATR unavailable: bars are missing columns %s", missing)
        return 0.0

    cols = [pd.to_numeric(bars_df[c], errors="coerce").tolist() for c in _ATR_COLUMNS]
    rows = [
        (h, lo, c)
        for h, lo, c in zip(*cols)
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (h, lo, c))
    ]
    skipped = len(bars_df) - len(rows)
    if skipped:
        logger.debug("grid ATR: skipped %d bars with missing or non-finite OHLC", skipped)
    if len(rows) < period:
        return 0.0

    true_ranges = []
    prev_close = None
    for high, low, close in rows:
        if prev_close is None:
            true_ranges.append(high - low)
        else:
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close

    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr) if math.isfinite(atr) else 0.0


def _blend_bounds(
    old_lo: float,
    old_hi: float,
    new_lo: float,
    new_hi: float,
    max_shift_pct: float,
) -> tuple[float, float]:
    if old_lo <= 0 or old_hi <= 0 or old_hi <= old_lo:
        return new_lo, new_hi
    mid_old = (old_lo + old_hi) * 0.5
    mid_new = (new_lo + new_hi) * 0.5
    half_old = (old_hi - old_lo) * 0.5
    half_new = (new_hi - new_lo) * 0.5
    if mid_old > 0:
        shift = abs(mid_new - mid_old) / mid_old
        if shift > max_shift_pct:
            mid_new = mid_old + (mid_new - mid_old) * (max_shift_pct / max(shift, 1e-9))
    half = half_new * 0.5 + half_old * 0.5
    return mid_new - half, mid_new + half


def update_adaptive_bounds(
    params: Dict[str, Any],
    price: float,
    bars_df: Optional[pd.DataFrame],
    *,
    force: bool = False,
) -> bool:
    """Adjust upperPrice/lowerPrice around price using ATR/range. Returns True if changed."""
    if not _truthy(params.get("adaptiveBounds"), True):
        return False
    if price <= 0:
        return False

    upper = _float(params.get("upperPrice") or params.get("upper_price"), 0.0)
    lower = _float(params.get("lowerPrice") or params.get("lower_price"), 0.0)
    lookback = max(10, _int(params.get("adaptiveLookback"), 48))
    atr_period = max(2, _int(params.get("adaptiveAtrPeriod"), 14))
    atr_mult = max(0.5, _float(params.get("adaptiveAtrMult"), 2.0))
    min_width_pct = max(0.005, _float(params.get("adaptiveMinWidthPct"), 0.02))
    max_shift_pct = max(0.01, _float(params.get("adaptiveMaxShiftPct"), 0.08))
    edge_pct = max(0.05, min(0.45, _float(params.get("adaptiveEdgePct"), 0.12)))

    atr = _compute_atr(bars_df, atr_period)
    if atr <= 0 and bars_df is not None and len(bars_df) > 0:
        try:
            tail = bars_df.tail(lookback)
            hi = float(pd.to_numeric(tail["high"], errors="coerce").max())
            lo = float(pd.to_numeric(tail["low"], errors="coerce").min())
            atr = max((hi - lo) / max(lookback, 1), price * min_width_pct * 0.5)
        except Exception:
            atr = price * min_width_pct

    half = max(price * min_width_pct * 0.5, atr * atr_mult)
    target_lo = price - half
    target_hi = price + half

    need_recenter = force
    if not need_recenter and upper > lower > 0:
        width = upper - lower
        pos = (price - lower) / width if width > 0 else 0.5
        if pos < edge_pct or pos > (1.0 - edge_pct):
            need_recenter = True
        if price < lower or price > upper:
            need_recenter = True
    else:
        need_recenter = True

    if not need_recenter:
        return False

    if upper > lower > 0:
        new_lo, new_hi = _blend_bounds(lower, upper, target_lo, target_hi, max_shift_pct)
    else:
        new_lo, new_hi = target_lo, target_hi

    if new_hi <= new_lo:
        return False

    params["lowerPrice"] = round(new_lo, 8)
    params["upperPrice"] = round(new_hi, 8)
    params["adaptive_last_center"] = round(price, 8)
    return True


def update_waterfall_state(
    params: Dict[str, Any],
    price: float,
    high: float,
    *,
    is_closed_bar: bool,
    now_ts: Optional[int] = None,
) -> bool:
    """
    Track cascade moves; set waterfall_pause until cooldown elapses.
    Returns True if waterfall just triggered this call.
    """
    if not _truthy(params.get("waterfallProtection"), True):
        params["waterfall_pause"] = False
        return False
    if price <= 0:
        return False

    now = int(now_ts or time.time())
    until_ts = _int(params.get("waterfall_until_ts"), 0)
    if until_ts > now:
        params["waterfall_pause"] = True
        return False
    if until_ts > 0 and until_ts <= now:
        params["waterfall_pause"] = False
        params["waterfall_until_ts"] = 0
        params["waterfall_peak_price"] = 0.0
        params.pop("waterfall_triggered_ts", None)

    drop_pct = max(0.005, _float(params.get("waterfallDropPct"), 0.03))
    peak = _float(params.get("waterfall_peak_price"), 0.0)
    ref_high = max(high, price, peak)

    if is_closed_bar:
        window = max(2, _int(params.get("waterfallWindowBars"), 6))
        params["waterfall_peak_price"] = max(peak, ref_high)
        peak = _float(params.get("waterfall_peak_price"), ref_high)
        params["waterfall_bar_counter"] = _int(params.get("waterfall_bar_counter"), 0) + 1
        if _int(params.get("waterfall_bar_counter"), 0) >= window:
            params["waterfall_bar_counter"] = 0
            params["waterfall_peak_price"] = ref_high
            peak = ref_high
    else:
        window_sec = max(30, _int(params.get("waterfallWindowSec"), 300))
        last_reset = _int(params.get("waterfall_peak_reset_ts"), 0)
        if last_reset <= 0 or (now - last_reset) >= window_sec:
            params["waterfall_peak_reset_ts"] = now
            params["waterfall_peak_price"] = ref_high
            peak = ref_high
        else:
            params["waterfall_peak_price"] = max(peak, ref_high)
            peak = _float(params.get("waterfall_peak_price"), ref_high)

    bar_index = _int(params.get("waterfall_bar_index"), 0)
    triggered = False
    if peak > 0 and price <= peak * (1.0 - drop_pct):
        triggered = True
        if is_closed_bar:
            cooldown = max(1, _int(params.get("waterfallCooldownBars"), 12))
            params["waterfall_until_bar"] = bar_index + cooldown
            params["waterfall_pause"] = True
        else:
            cooldown_sec = max(60, _int(params.get("waterfallCooldownSec"), 900))
            params["waterfall_until_ts"] = now + cooldown_sec
            params["waterfall_pause"] = True
        params["waterfall_triggered_ts"] = now
        logger.info(
            "Grid waterfall triggered: drop>=%.2f%% peak=%.6f price=%.6f pause_until=%s",
            drop_pct * 100,
            peak,
            price,
            params.get("waterfall_until_ts") or params.get("waterfall_until_bar"),
        )

    if is_closed_bar:
        bar_index += 1
        params["waterfall_bar_index"] = bar_index
        until_bar = _int(params.get("waterfall_until_bar"), 0)
        if until_bar > 0 and bar_index >= until_bar:
            params["waterfall_pause"] = False
            params["waterfall_until_bar"] = 0
            params["waterfall_peak_price"] = 0.0

    return triggered


def prepare_grid_runtime(
    params: Dict[str, Any],
    *,
    price: float,
    high: float,
    low: float,
    bars_df: Optional[pd.DataFrame] = None,
    is_closed_bar: bool = False,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Merge defaults and update adaptive bounds + waterfall state in-place."""
    return prepare_bot_market_guards(
        "grid",
        params,
        price=price,
        high=high,
        low=low,
        bars_df=bars_df,
        is_closed_bar=is_closed_bar,
        now_ts=now_ts,
    )


def prepare_bot_market_guards(
    bot_type: str,
    params: Dict[str, Any],
    *,
    price: float,
    high: float,
    low: float,
    bars_df: Optional[pd.DataFrame] = None,
    is_closed_bar: bool = False,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Grid: adaptive bounds + waterfall; Martingale: waterfall only."""
    if not isinstance(params, dict):
        return {}
    bt = (bot_type or "").strip().lower()
    if bt not in ("grid", "martingale"):
        return params

    _apply_defaults(params)
    px = float(price or 0.0)
    hi = float(high or px)
    if px <= 0:
        return params

    if bt == "grid":
        changed = update_adaptive_bounds(
            params, px, bars_df, force=(is_closed_bar and _float(params.get("upperPrice"), 0) <= 0)
        )
        if changed:
            logger.debug(
                "Grid adaptive bounds: lower=%s upper=%s price=%s",
                params.get("lowerPrice"),
                params.get("upperPrice"),
                px,
            )

    wf = update_waterfall_state(params, px, hi, is_closed_bar=is_closed_bar, now_ts=now_ts)
    if wf and _truthy(params.get("waterfallCloseOnTrigger"), False):
        params["waterfall_request_close"] = True

    return params


_ENTRY_SIGNALS = frozenset({"open_long", "open_short", "add_long", "add_short"})


def filter_grid_signals_under_waterfall(
    signals: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Drop new grid entries while waterfall_pause; allow close/reduce."""
    if not signals:
        return signals
    if not _truthy((params or {}).get("waterfall_pause"), False):
        return signals
    out = []
    for s in signals:
        st = str((s or {}).get("type") or "").strip().lower()
        if st in _ENTRY_SIGNALS:
            continue
        out.append(s)
    return out


def inject_waterfall_close_signal(
    signals: List[Dict[str, Any]],
    params: Dict[str, Any],
    *,
    has_long: bool,
    has_short: bool,
    price: float,
    timestamp: int,
) -> List[Dict[str, Any]]:
    if not _truthy((params or {}).get("waterfall_request_close"), False):
        return signals
    params["waterfall_request_close"] = False
    out = list(signals or [])
    if has_long and not any((s or {}).get("type") == "close_long" for s in out):
        out.insert(
            0,
            {
                "type": "close_long",
                "trigger_price": float(price or 0),
                "position_size": 0,
                "timestamp": int(timestamp or 0),
                "reason": "grid_waterfall_close",
            },
        )
    if has_short and not any((s or {}).get("type") == "close_short" for s in out):
        out.insert(
            0,
            {
                "type": "close_short",
                "trigger_price": float(price or 0),
                "position_size": 0,
                "timestamp": int(timestamp or 0),
                "reason": "grid_waterfall_close",
            },
        )
    return out
