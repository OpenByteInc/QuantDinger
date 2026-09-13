"""Ricequant RQData adapters for A-share and HK equity K-lines."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.data_sources.base import TIMEFRAME_SECONDS
from app.data_sources.rqdata_futures import (
    _dataframe_to_klines,
    _import_rqdatac,
    _lookback_start,
    _resample_ohlcv,
    ensure_rqdata_initialized,
    normalize_cn_futures_timeframe,
)
from app.data_sources.tencent import normalize_cn_code, normalize_hk_code
from app.utils.logger import get_logger

logger = get_logger(__name__)


def cn_stock_source_mode() -> str:
    return (os.getenv("CN_STOCK_PROVIDER") or "rqdata").strip().lower() or "rqdata"


def hk_stock_source_mode() -> str:
    return (os.getenv("HK_STOCK_PROVIDER") or "rqdata").strip().lower() or "rqdata"


def is_cn_index_code(tencent_code: str) -> bool:
    code = (tencent_code or "").strip().upper()
    if code.startswith("SH") and code[2:].isdigit() and code[2:].startswith("000"):
        return True
    if code.startswith("SZ") and code[2:].isdigit() and code[2:].startswith("399"):
        return True
    return False


def to_rqdata_cn_equity_id(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".XSHG") or raw.endswith(".XSHE"):
        return raw
    code = normalize_cn_code(symbol)
    digits = code[2:] if len(code) > 2 else raw
    if code.startswith("SH") and digits.isdigit():
        return f"{digits}.XSHG"
    if code.startswith("SZ") and digits.isdigit():
        return f"{digits}.XSHE"
    return raw


def to_rqdata_hk_equity_id(symbol: str) -> str:
    raw = (symbol or "").strip().upper()
    if raw.endswith(".XHKG"):
        return raw
    code = normalize_hk_code(symbol)
    digits = code[2:] if code.startswith("HK") else raw
    if digits.isdigit():
        return f"{digits.zfill(5)}.XHKG"
    return raw


def _get_price_klines(
    order_book_id: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None,
    *,
    adjust_type: str = "pre",
) -> List[Dict[str, Any]]:
    if not ensure_rqdata_initialized():
        return []
    rqdatac = _import_rqdatac()
    timeframe = normalize_cn_futures_timeframe(timeframe)
    want_4h = timeframe == "4H"
    frequency = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1H": "60m",
        "1D": "1d",
        "1W": "1w",
    }.get("1H" if want_4h else timeframe)
    if not frequency:
        return []
    end_dt = datetime.now()
    if before_time:
        end_dt = datetime.fromtimestamp(int(before_time))
    fetch_limit = max(int(limit) * 4, int(limit)) if want_4h else max(int(limit), 1)
    start_dt = _lookback_start(end_dt, "1H" if want_4h else timeframe, fetch_limit)
    try:
        df = rqdatac.get_price(
            order_book_id,
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_dt.strftime("%Y-%m-%d"),
            frequency=frequency,
            fields=["open", "high", "low", "close", "volume"],
            adjust_type=adjust_type,
            expect_df=True,
        )
    except Exception as exc:
        logger.warning("RQData get_price failed %s: %s", order_book_id, exc)
        return []
    klines = _dataframe_to_klines(df)
    if want_4h:
        klines = _resample_ohlcv(klines, TIMEFRAME_SECONDS["4H"])
    if before_time:
        klines = [k for k in klines if k["time"] <= int(before_time)]
    if limit and len(klines) > limit:
        klines = klines[-limit:]
    return klines


def get_cn_stock_kline_rqdata(
    symbol: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    order_book_id = to_rqdata_cn_equity_id(symbol)
    tencent_code = normalize_cn_code(symbol)
    adjust = "none" if is_cn_index_code(tencent_code) else "pre"
    bars = _get_price_klines(
        order_book_id, timeframe, limit, before_time, adjust_type=adjust
    )
    if bars:
        logger.debug("RQData CN equity %s -> %s %s: %d bars", symbol, order_book_id, timeframe, len(bars))
    return bars


def get_hk_stock_kline_rqdata(
    symbol: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    order_book_id = to_rqdata_hk_equity_id(symbol)
    bars = _get_price_klines(order_book_id, timeframe, limit, before_time, adjust_type="pre")
    if bars:
        logger.debug("RQData HK equity %s -> %s %s: %d bars", symbol, order_book_id, timeframe, len(bars))
    return bars


def _ticker_from_bars(symbol: str, bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not bars:
        return {"symbol": symbol, "last": 0.0}
    last = float(bars[-1]["close"])
    prev = float(bars[-2]["close"]) if len(bars) > 1 else last
    change = last - prev if prev else 0.0
    change_pct = (change / prev * 100) if prev else 0.0
    return {
        "symbol": symbol,
        "last": round(last, 4),
        "change": round(change, 4),
        "changePercent": round(change_pct, 2),
        "open": round(float(bars[-1]["open"]), 4),
        "high": round(float(bars[-1]["high"]), 4),
        "low": round(float(bars[-1]["low"]), 4),
        "previousClose": round(prev, 4),
        "source": "rqdata",
    }


def get_cn_stock_ticker_rqdata(symbol: str) -> Dict[str, Any]:
    bars = get_cn_stock_kline_rqdata(symbol, "1D", 3)
    return _ticker_from_bars(symbol, bars)


def get_hk_stock_ticker_rqdata(symbol: str) -> Dict[str, Any]:
    bars = get_hk_stock_kline_rqdata(symbol, "1D", 3)
    return _ticker_from_bars(symbol, bars)
