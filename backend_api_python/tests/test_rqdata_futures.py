from datetime import datetime
from app.data_sources.cn_futures_symbols import (
    is_cn_futures_symbol,
    to_rqdata_order_book_id,
)
from app.data_sources.rqdata_futures import (
    _lookback_start,
    _resample_ohlcv,
    normalize_cn_futures_timeframe,
)


def test_cn_futures_symbol_accepts_sina_and_ricequant_ids():
    assert is_cn_futures_symbol("IF2609")
    assert is_cn_futures_symbol("RB0")
    assert is_cn_futures_symbol("SC2611")
    assert is_cn_futures_symbol("IF2609.CFE")
    assert is_cn_futures_symbol("IF88")
    assert not is_cn_futures_symbol("ES")
    assert not is_cn_futures_symbol("BTC/USDT")


def test_rqdata_order_book_id_mapping():
    assert to_rqdata_order_book_id("IF2609") == "IF2609"
    assert to_rqdata_order_book_id("RB0") == "RB88"
    assert to_rqdata_order_book_id("SC2611") == "SC2611"
    assert to_rqdata_order_book_id("M2505") == "M2505"
    assert to_rqdata_order_book_id("IF2609.CFE") == "IF2609"
    assert to_rqdata_order_book_id("CF609") == "CF2609"


def test_suggest_cn_continuous_symbols():
    from app.data_sources.cn_futures_symbols import suggest_cn_continuous_symbols

    assert "RB0" in suggest_cn_continuous_symbols("RB")
    assert "SI0" in suggest_cn_continuous_symbols("SI")
    assert "ZC0" in suggest_cn_continuous_symbols("ZC")
    assert "IF0" in suggest_cn_continuous_symbols("IF0")


def test_resample_ohlcv_4h_buckets():
    bars = [
        {"time": 0, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 10},
        {"time": 3600, "open": 1.5, "high": 3, "low": 1.4, "close": 2, "volume": 20},
        {"time": 14400, "open": 2, "high": 2.2, "low": 1.9, "close": 2.1, "volume": 5},
    ]
    out = _resample_ohlcv(bars, 14400)
    assert len(out) == 2
    assert out[0]["open"] == 1
    assert out[0]["high"] == 3
    assert out[0]["low"] == 1
    assert out[0]["close"] == 2
    assert out[0]["volume"] == 30


def test_normalize_cn_futures_timeframe_aliases():
    assert normalize_cn_futures_timeframe("1h") == "1H"
    assert normalize_cn_futures_timeframe("4h") == "4H"
    assert normalize_cn_futures_timeframe("1d") == "1D"
    assert normalize_cn_futures_timeframe("60m") == "1H"


def test_lookback_covers_session_gaps_for_hourly_charts():
    end = datetime(2026, 9, 12, 12, 0, 0)
    start = _lookback_start(end, "1H", 300)
    assert (end - start).days >= 80
    daily_start = _lookback_start(end, "1D", 5)
    assert 10 <= (end - daily_start).days <= 40
