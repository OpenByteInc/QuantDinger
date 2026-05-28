"""Indicator math vs CN terminal conventions."""

from app.utils.technical_indicators import compute_kdj_cn, compute_rsi_wilder


def test_kdj_seeds_k_and_d_at_fifty():
    # Flat OHLC → RSV=50 every bar; with seed 50, K and D should stay 50.
    n = 12
    high = [10.0] * n
    low = [10.0] * n
    close = [10.0] * n
    k, d, j = compute_kdj_cn(high, low, close, period=9, k_smooth=3, d_smooth=3)
    assert k[8] == 50.0
    assert d[8] == 50.0
    assert j[8] == 50.0
    assert k[11] == 50.0


def test_kdj_first_valid_bar_not_equal_to_raw_rsv_when_rsv_high():
    high = [float(i + 10) for i in range(15)]
    low = [float(i) for i in range(15)]
    close = [float(i + 5) for i in range(15)]
    k, d, _j = compute_kdj_cn(high, low, close, period=9, k_smooth=3, d_smooth=3)
    # First K must be 2/3*50 + 1/3*RSV, not RSV alone.
    assert k[8] is not None
    assert k[8] != 100.0
    assert 50.0 < k[8] < 100.0


def test_rsi_wilder_first_index():
    closes = [100, 101, 102, 101, 100, 99, 98, 99, 100, 101, 102, 103, 104, 105, 106]
    rsi = compute_rsi_wilder(closes, 14)
    assert rsi[13] is None
    assert rsi[14] is not None
    assert 0 <= rsi[14] <= 100
