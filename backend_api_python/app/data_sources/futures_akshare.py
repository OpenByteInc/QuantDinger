"""Legacy Chinese futures adapter (AkShare / Sina).

This is the pre-RQData implementation kept as a fallback copy. US and crypto
futures stay in ``futures.py``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.data_sources.base import TIMEFRAME_SECONDS
from app.data_sources.cn_futures_symbols import cn_futures_sina_market
from app.utils.logger import get_logger

logger = get_logger(__name__)

_CN_MINUTE_PERIOD_MAP = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1H": "60"}


def cn_to_timestamp(value) -> Optional[int]:
    """Parse a date/datetime-ish value into an epoch second (robust across dtypes)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.timestamp())
    if hasattr(value, "timestamp"):
        try:
            return int(value.timestamp())
        except Exception:
            pass
    if hasattr(value, "timetuple") and not isinstance(value, str):
        try:
            return int(datetime.combine(value, datetime.min.time()).timestamp())
        except Exception:
            pass
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    return None


def get_cn_futures_ticker_akshare(symbol: str) -> Dict[str, Any]:
    """国内期货实时行情 via AkShare (新浪)."""
    sym = (symbol or "").strip().upper()
    try:
        import akshare as ak  # type: ignore

        df = ak.futures_zh_spot(symbol=sym, market=cn_futures_sina_market(sym))
        if df is None or df.empty:
            return {"symbol": sym, "last": 0.0}
        row = df.iloc[0].to_dict()
        last = float(row.get("current_price") or 0)
        prev = float(row.get("last_settle_price") or row.get("last_close") or row.get("open") or 0)
        change = last - prev if prev else 0.0
        change_pct = (change / prev * 100) if prev else 0.0
        return {
            "symbol": sym,
            "last": round(last, 4),
            "change": round(change, 4),
            "changePercent": round(change_pct, 2),
            "previousClose": round(prev, 4),
        }
    except Exception as e:
        logger.debug("CN futures ticker (AkShare) failed %s: %s", sym, e)
        return {"symbol": sym, "last": 0.0}


def get_cn_futures_kline_akshare(
    symbol: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """国内期货K线 via AkShare (新浪日线 / 分钟线)."""
    sym = (symbol or "").strip().upper()
    try:
        import akshare as ak  # type: ignore

        from app.data_sources.rqdata_futures import normalize_cn_futures_timeframe, _resample_ohlcv

        timeframe = normalize_cn_futures_timeframe(timeframe)
        want_4h = timeframe == "4H"
        minute_period = _CN_MINUTE_PERIOD_MAP.get("1H" if want_4h else timeframe)
        if minute_period:
            df = ak.futures_zh_minute_sina(symbol=sym, period=minute_period)
            date_col = "datetime"
        else:
            df = ak.futures_zh_daily_sina(symbol=sym)
            date_col = "date"
        if df is None or df.empty:
            return []
        klines = []
        for _, row in df.iterrows():
            ts = cn_to_timestamp(row.get(date_col))
            if ts is None:
                continue
            klines.append({
                "time": ts,
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "volume": float(row.get("volume") or 0),
            })
        klines.sort(key=lambda x: x["time"])
        if want_4h:
            klines = _resample_ohlcv(klines, TIMEFRAME_SECONDS["4H"])
        if before_time:
            klines = [k for k in klines if k["time"] <= int(before_time)]
        if limit and len(klines) > limit:
            klines = klines[-limit:]
        return klines
    except Exception as e:
        logger.error("CN futures kline (AkShare) failed %s: %s", sym, e)
        return []
