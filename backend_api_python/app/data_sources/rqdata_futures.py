"""Ricequant RQData adapter for Chinese domestic futures research bars."""
from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.data_sources.base import TIMEFRAME_SECONDS
from app.data_sources.cn_futures_symbols import to_rqdata_order_book_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_INIT_ERROR: Optional[str] = None

_RQ_FREQUENCY = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1H": "60m",
    "1D": "1d",
    "1W": "1w",
}

_TF_ALIASES = {
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
    "1w": "1W",
    "60m": "1H",
    "240m": "4H",
    "1hour": "1H",
    "1day": "1D",
    "1week": "1W",
}


def normalize_cn_futures_timeframe(timeframe: str) -> str:
    raw = (timeframe or "1D").strip()
    if not raw:
        return "1D"
    if raw in _RQ_FREQUENCY or raw == "4H":
        return raw
    return _TF_ALIASES.get(raw.lower(), raw)


def cn_futures_source_mode() -> str:
    return (os.getenv("FUTURES_CN_PROVIDER") or "rqdata").strip().lower() or "rqdata"


def rqdata_configured() -> bool:
    return bool(
        (os.getenv("RQDATAC_LICENSE") or "").strip()
        or (os.getenv("RQDATAC_URI") or "").strip()
        or ((os.getenv("RQDATAC_USERNAME") or "").strip() and (os.getenv("RQDATAC_PASSWORD") or "").strip())
    )


def _import_rqdatac():
    try:
        import rqdatac  # type: ignore
        return rqdatac
    except Exception as exc:
        raise RuntimeError("rqdatac is not installed") from exc


def ensure_rqdata_initialized() -> bool:
    """Initialize RQData once per process. Returns False when unavailable."""
    global _INITIALIZED, _INIT_ERROR
    if _INITIALIZED:
        return True
    with _INIT_LOCK:
        if _INITIALIZED:
            return True
        try:
            rqdatac = _import_rqdatac()
            uri = (os.getenv("RQDATAC_URI") or "").strip()
            license_key = (os.getenv("RQDATAC_LICENSE") or "").strip()
            username = (os.getenv("RQDATAC_USERNAME") or "").strip()
            password = (os.getenv("RQDATAC_PASSWORD") or "").strip()
            if uri:
                rqdatac.init(uri=uri)
            elif license_key:
                try:
                    rqdatac.init("license", license_key)
                except TypeError:
                    rqdatac.init(username="license", password=license_key)
            elif username and password:
                rqdatac.init(username, password)
            else:
                rqdatac.init()
            _INITIALIZED = True
            _INIT_ERROR = None
            logger.info("RQData initialized")
            return True
        except Exception as exc:
            _INIT_ERROR = str(exc)
            logger.warning("RQData init failed: %s", exc)
            return False


def _lookback_start(end_dt: datetime, timeframe: str, fetch_limit: int) -> datetime:
    """Calendar start for session-based CN futures (nights, weekends, holidays)."""
    tf = normalize_cn_futures_timeframe(timeframe)
    limit = max(int(fetch_limit), 1)
    tf_seconds = TIMEFRAME_SECONDS.get(tf, 86400)
    if tf == "1W":
        days = limit * 8 + 14
    elif tf == "1D":
        days = int(limit * 1.8) + 14
    else:
        # ~4 liquid hours/day is a conservative floor for commodity sessions.
        days = int(math.ceil(limit * tf_seconds / (4 * 3600))) + 14
    days = max(10, min(int(days), 2500))
    return end_dt - timedelta(days=days)


def _numeric(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _bar_timestamp(value) -> Optional[int]:
    if value is None:
        return None
    try:
        import pandas as pd

        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Shanghai")
        return int(ts.timestamp())
    except Exception:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone(timedelta(hours=8)))
            return int(value.timestamp())
        return None


def _resample_ohlcv(bars: List[Dict[str, Any]], bucket_seconds: int) -> List[Dict[str, Any]]:
    if not bars or bucket_seconds <= 0:
        return bars
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for bar in bars:
        key = int(bar["time"]) // bucket_seconds * bucket_seconds
        grouped.setdefault(key, []).append(bar)
    out = []
    for key in sorted(grouped):
        chunk = grouped[key]
        out.append({
            "time": key,
            "open": float(chunk[0]["open"]),
            "high": max(float(b["high"]) for b in chunk),
            "low": min(float(b["low"]) for b in chunk),
            "close": float(chunk[-1]["close"]),
            "volume": float(sum(float(b["volume"]) for b in chunk)),
        })
    return out


def _dataframe_to_klines(df) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    work = df.reset_index()
    time_col = None
    for candidate in ("datetime", "date", "index", work.columns[0]):
        if candidate in work.columns:
            time_col = candidate
            break
    klines = []
    for _, row in work.iterrows():
        ts = _bar_timestamp(row.get(time_col))
        if ts is None:
            continue
        klines.append({
            "time": ts,
            "open": _numeric(row.get("open")),
            "high": _numeric(row.get("high")),
            "low": _numeric(row.get("low")),
            "close": _numeric(row.get("close")),
            "volume": _numeric(row.get("volume")),
        })
    klines.sort(key=lambda x: x["time"])
    return klines


def get_cn_futures_kline_rqdata(
    symbol: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not ensure_rqdata_initialized():
        return []
    rqdatac = _import_rqdatac()
    order_book_id = to_rqdata_order_book_id(symbol)
    timeframe = normalize_cn_futures_timeframe(timeframe)
    want_4h = timeframe == "4H"
    frequency = _RQ_FREQUENCY.get("1H" if want_4h else timeframe)
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
            adjust_type="none",
            expect_df=True,
        )
    except Exception as exc:
        logger.warning("RQData get_price failed %s (%s): %s", symbol, order_book_id, exc)
        return []
    klines = _dataframe_to_klines(df)
    if want_4h:
        klines = _resample_ohlcv(klines, TIMEFRAME_SECONDS["4H"])
    if before_time:
        klines = [k for k in klines if k["time"] <= int(before_time)]
    if limit and len(klines) > limit:
        klines = klines[-limit:]
    if klines:
        logger.debug("RQData futures kline %s -> %s %s: %d bars", symbol, order_book_id, timeframe, len(klines))
    return klines


def get_cn_futures_ticker_rqdata(symbol: str) -> Dict[str, Any]:
    bars = get_cn_futures_kline_rqdata(symbol, "1m", 8)
    if len(bars) < 1:
        bars = get_cn_futures_kline_rqdata(symbol, "1D", 3)
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
        "previousClose": round(prev, 4),
        "source": "rqdata",
    }
