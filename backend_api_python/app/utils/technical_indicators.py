"""
Technical indicator math aligned with mainstream CN terminals (同花顺 / 东方财富).

- KDJ(9,3,3): RSV on rolling HH/LL; K and D seeded at 50, then smoothed with 1/3 weight.
- RSI: Wilder smoothing (first average = SMA of first N changes).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np


def compute_kdj_cn(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 9,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """KDJ with K/D initial value 50 (A-share terminal convention)."""
    n = len(close)
    k_out: List[Optional[float]] = [None] * n
    d_out: List[Optional[float]] = [None] * n
    j_out: List[Optional[float]] = [None] * n
    if n < period or period < 1:
        return k_out, d_out, j_out

    k_prev = 50.0
    d_prev = 50.0
    for i in range(period - 1, n):
        window_high = max(float(high[j]) for j in range(i - period + 1, i + 1))
        window_low = min(float(low[j]) for j in range(i - period + 1, i + 1))
        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (float(close[i]) - window_low) / (window_high - window_low) * 100.0

        k_prev = (k_prev * (k_smooth - 1) + rsv) / k_smooth
        d_prev = (d_prev * (d_smooth - 1) + k_prev) / d_smooth
        j_val = 3.0 * k_prev - 2.0 * d_prev

        k_out[i] = round(k_prev, 4)
        d_out[i] = round(d_prev, 4)
        j_out[i] = round(j_val, 4)

    return k_out, d_out, j_out


def compute_rsi_wilder(closes: Sequence[float], period: int = 14) -> List[Optional[float]]:
    """Wilder RSI; first valid value at index ``period``."""
    n = len(closes)
    out: List[Optional[float]] = [None] * n
    if n < period + 1 or period < 1:
        return out

    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, n):
        chg = float(closes[i]) - float(closes[i - 1])
        gains.append(chg if chg > 0 else 0.0)
        losses.append(-chg if chg < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 4)


def kdj_codegen(period: int, k_smooth: int, d_smooth: int, col_prefix: str) -> str:
    """Pandas code fragment for strategy compiler (CN KDJ)."""
    return f"""
# KDJ ({period},{k_smooth},{d_smooth}) — K/D seed 50 (CN terminal style)
_low_min = df['low'].rolling(window={period}).min()
_high_max = df['high'].rolling(window={period}).max()
_hl = (_high_max - _low_min).replace(0, np.nan)
_rsv = ((df['close'] - _low_min) / _hl * 100).fillna(50.0)
_k_list, _d_list = [], []
_k_prev, _d_prev = 50.0, 50.0
for _rv in _rsv:
    if pd.isna(_rv):
        _k_list.append(np.nan)
        _d_list.append(np.nan)
        continue
    _k_prev = (_k_prev * ({k_smooth} - 1) + float(_rv)) / {k_smooth}
    _d_prev = (_d_prev * ({d_smooth} - 1) + _k_prev) / {d_smooth}
    _k_list.append(_k_prev)
    _d_list.append(_d_prev)
df['{col_prefix}_k'] = pd.Series(_k_list, index=df.index)
df['{col_prefix}_d'] = pd.Series(_d_list, index=df.index)
df['{col_prefix}_j'] = 3 * df['{col_prefix}_k'] - 2 * df['{col_prefix}_d']
"""


def rsi_wilder_codegen(period: int, col_name: str) -> str:
    """Pandas code fragment for Wilder RSI (matches chart / market_data_collector)."""
    return f"""
# RSI ({period}) — Wilder smoothing
_cl = df['close'].astype(float).tolist()
_rsi_out = [np.nan] * len(_cl)
if len(_cl) > {period}:
    _g = [max(_cl[_i] - _cl[_i - 1], 0.0) for _i in range(1, len(_cl))]
    _l = [max(_cl[_i - 1] - _cl[_i], 0.0) for _i in range(1, len(_cl))]
    _ag = sum(_g[:{period}]) / {period}
    _al = sum(_l[:{period}]) / {period}
    _rsi_out[{period}] = 100.0 if _al == 0 else 100 - (100 / (1 + _ag / _al))
    for _ii in range({period}, len(_g)):
        _ag = (_ag * ({period} - 1) + _g[_ii]) / {period}
        _al = (_al * ({period} - 1) + _l[_ii]) / {period}
        _rsi_out[_ii + 1] = 100.0 if _al == 0 else 100 - (100 / (1 + _ag / _al))
df['{col_name}'] = pd.Series(_rsi_out, index=df.index)
"""
