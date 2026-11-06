"""
A-share / H-share chart K-lines — multi-tier fallback.

Priority order (when TWELVE_DATA_API_KEY is configured):
  ALL timeframes → Twelve Data (paid, globally stable) → Tencent daily/weekly → yfinance → AkShare

Without API key:
  Daily / Weekly → Tencent fqkline (fast, no key) → yfinance → AkShare
  Minute / Hour → yfinance → AkShare (Eastmoney, fragile overseas)

Tencent ``fqkline`` only reliably supports day/week/month.
yfinance supports CN (.SS/.SZ) and HK (.HK) at all common intervals.
Twelve Data (https://twelvedata.com) supports XSHG/XSHE/XHKG at all intervals.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional

import pandas as pd
import requests

from app.utils.logger import get_logger
from app.utils.resource_guard import ResourceExhaustedError, assert_fd_available, record_exception

logger = get_logger(__name__)

_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


@contextmanager
def _bypass_proxy() -> Generator[None, None, None]:
    """Temporarily clear proxy env vars for AkShare calls to Chinese domestic sites."""
    saved = {}
    for key in _PROXY_KEYS:
        val = os.environ.pop(key, None)
        if val is not None:
            saved[key] = val
    try:
        yield
    finally:
        for key, val in saved.items():
            os.environ[key] = val

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SEC = 1.5
_BACKOFF_CAP_SEC = 12.0

_TRANSIENT_ERR_MARKERS = (
    "remote end closed connection",
    "connection aborted",
    "connection reset",
    "timed out",
    "timeout",
    "max retries exceeded",
    "temporarily unavailable",
    "broken pipe",
    "eof occurred",
    "remote disconnected",
    "chunkedencodingerror",
    "incompleteread",
    "rate",
    "too many requests",
    "429",
)


def _is_transient(exc: BaseException) -> bool:
    return any(m in str(exc).lower() for m in _TRANSIENT_ERR_MARKERS)


_CHART_TF_ALIASES = {
    "1w": "1W",
    "1d": "1D",
    "1h": "1H",
    "4h": "4H",
    "d": "1D",
    "day": "1D",
    "w": "1W",
    "week": "1W",
    "wk": "1W",
    "60m": "1H",
    "240m": "4H",
    "1day": "1D",
    "1week": "1W",
}


def normalize_chart_timeframe(timeframe: str) -> str:
    t = (timeframe or "1D").strip()
    if not t:
        return "1D"
    key = t.lower()
    if key in _CHART_TF_ALIASES:
        return _CHART_TF_ALIASES[key]
    return t


# ---------------------------------------------------------------------------
# AkShare code converters
# ---------------------------------------------------------------------------

def ak_a_code_from_tencent(tencent_code: str) -> str:
    c = (tencent_code or "").strip().lower()
    if len(c) >= 8 and c[:2] in ("sh", "sz"):
        return c[2:]
    return c


def ak_hk_code_from_tencent(tencent_code: str) -> str:
    c = (tencent_code or "").strip().upper().replace(".HK", "")
    if c.startswith("HK"):
        num = c[2:]
    else:
        num = c
    if num.isdigit():
        return num.zfill(5)
    return num


# ---------------------------------------------------------------------------
# Twelve Data (paid, globally reliable — https://twelvedata.com)
# ---------------------------------------------------------------------------

def _get_twelve_data_api_key() -> str:
    try:
        from app.utils.config_loader import load_addon_config
        key = load_addon_config().get("twelve_data", {}).get("api_key", "")
        if key:
            return key
    except Exception:
        pass
    return (os.getenv("TWELVE_DATA_API_KEY") or "").strip()


_TD_INTERVAL_MAP = {
    "1m": "1min",
    "3m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1H": "1h",
    "4H": "1h",
    "1D": "1day",
    "1W": "1week",
}

_TD_DAILY_LIMIT_LOCK = threading.Lock()
_TD_DAILY_LIMIT_UNTIL = 0.0


def _twelvedata_daily_limit_active() -> bool:
    with _TD_DAILY_LIMIT_LOCK:
        return time.time() < _TD_DAILY_LIMIT_UNTIL


def _mark_twelvedata_daily_limited(symbol: str, exchange: str, message: str) -> None:
    global _TD_DAILY_LIMIT_UNTIL
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    until = datetime(
        tomorrow.year,
        tomorrow.month,
        tomorrow.day,
        0,
        5,
        tzinfo=timezone.utc,
    ).timestamp()
    first = False
    with _TD_DAILY_LIMIT_LOCK:
        if time.time() >= _TD_DAILY_LIMIT_UNTIL:
            first = True
        _TD_DAILY_LIMIT_UNTIL = max(_TD_DAILY_LIMIT_UNTIL, until)
    if first:
        logger.warning(
            "TwelveData daily API credits exhausted for %s/%s; "
            "skipping TwelveData requests until next UTC day. Last message: %s",
            symbol,
            exchange,
            message,
        )


def _td_symbol_and_exchange(tencent_code: str, is_hk: bool) -> tuple[str, str]:
    """Convert Tencent code to Twelve Data (symbol, exchange).

    Twelve Data time_series requires exchange *name* (SSE / SZSE / HKEX),
    not the MIC code (XSHG / XSHE / XHKG).
    """
    c = (tencent_code or "").strip().upper()
    if is_hk:
        num = c.replace("HK", "")
        if num.isdigit():
            num = str(int(num)).zfill(4)
        return num, "HKEX"
    digits = c.lstrip("SHSZ")
    if c.startswith("SH") or digits.startswith("6"):
        return digits, "SSE"
    return digits, "SZSE"


def fetch_twelvedata_klines(
    *,
    is_hk: bool,
    tencent_code: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int],
) -> List[Dict[str, Any]]:
    """Fetch K-lines from Twelve Data REST API. Requires TWELVE_DATA_API_KEY."""
    try:
        assert_fd_available("TwelveData K-line")
    except ResourceExhaustedError as e:
        logger.warning(str(e))
        return []

    api_key = _get_twelve_data_api_key()
    if not api_key:
        return []

    interval = _TD_INTERVAL_MAP.get(timeframe)
    if not interval:
        return []
    if _twelvedata_daily_limit_active():
        return []

    symbol, exchange = _td_symbol_and_exchange(tencent_code, is_hk)
    merge_factor = _MERGE_FACTOR_MAP.get(timeframe, 1)
    if is_hk and timeframe in {"1H", "4H"}:
        interval, merge_factor = "30min", 2 if timeframe == "1H" else 8
    params: Dict[str, Any] = {
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "outputsize": min(int(limit) * merge_factor, 5000),
        "apikey": api_key,
        "format": "JSON",
        "dp": "4",
    }
    if before_time:
        end_dt = datetime.fromtimestamp(int(before_time))
        if is_hk:
            end_dt = pd.Timestamp(int(before_time), unit="s", tz="UTC").tz_convert("Asia/Hong_Kong")
        params["end_date"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    url = "https://api.twelvedata.com/time_series"

    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.get(url, params=params, timeout=20)
            data = resp.json()
            break
        except Exception as e:
            try:
                record_exception(e, scope="TwelveData K-line")
            except ResourceExhaustedError as guarded:
                logger.warning(str(guarded))
                return []
            if attempt + 1 < _MAX_ATTEMPTS and _is_transient(e):
                delay = min(_BACKOFF_CAP_SEC, _BACKOFF_BASE_SEC * (2 ** attempt))
                logger.debug(
                    "TwelveData transient error %s/%s tf=%s (attempt %s/%s): %s",
                    symbol, exchange, timeframe, attempt + 1, _MAX_ATTEMPTS, e,
                )
                time.sleep(delay)
                continue
            logger.warning("TwelveData request failed %s/%s tf=%s: %s", symbol, exchange, timeframe, e)
            return []
    else:
        return []

    if data.get("status") != "ok" or "values" not in data:
        code = data.get("code", "")
        msg = data.get("message", str(data))
        msg_l = str(msg).lower()
        if "api credits" in msg_l or "for the day" in msg_l:
            _mark_twelvedata_daily_limited(symbol, exchange, msg)
        elif code == 429 or "minute limit" in msg_l:
            logger.warning("TwelveData rate limit for %s/%s: %s", symbol, exchange, msg)
        elif "Pro" in msg or "Venture" in msg or "upgrading" in msg:
            logger.debug("TwelveData plan limit %s/%s tf=%s: %s", symbol, exchange, timeframe, msg)
        else:
            logger.warning("TwelveData error %s/%s tf=%s: %s", symbol, exchange, timeframe, msg)
        return []

    out: List[Dict[str, Any]] = []
    for v in data["values"]:
        try:
            dt_str = v.get("datetime", "")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed_time = datetime.strptime(dt_str, fmt)
                    if is_hk:
                        parsed_time = pd.Timestamp(parsed_time).tz_localize("Asia/Hong_Kong")
                    ts = int(parsed_time.timestamp())
                    break
                except ValueError:
                    continue
            else:
                continue
            o = float(v["open"])
            h = float(v["high"])
            low = float(v["low"])
            c = float(v["close"])
            vol = float(v.get("volume") or 0)
            if o == 0 and c == 0:
                continue
            out.append({
                "time": ts,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": round(vol, 2),
            })
        except Exception:
            continue

    out.sort(key=lambda x: x["time"])
    if merge_factor > 1 and out:
        out = _merge_session_bars(out, timeframe, is_hk, source_seconds=1800 if is_hk and timeframe in {"1H", "4H"} else None)
    logger.debug("TwelveData returned %d bars for %s/%s tf=%s", len(out), symbol, exchange, timeframe)
    return out


# ---------------------------------------------------------------------------
# yfinance helpers (globally accessible — Yahoo CDN)
# ---------------------------------------------------------------------------

def yf_symbol_from_tencent(tencent_code: str, is_hk: bool) -> str:
    """Convert Tencent-style code (SH600519 / SZ000001 / HK00700) to yfinance ticker."""
    c = (tencent_code or "").strip().upper()
    if is_hk:
        num = c.replace("HK", "")
        if num.isdigit():
            return str(int(num)).zfill(4) + ".HK"
        return num + ".HK"
    if c.startswith("SH"):
        return c[2:] + ".SS"
    if c.startswith("SZ"):
        return c[2:] + ".SZ"
    digits = c.lstrip("SHSZ")
    if digits.startswith("6"):
        return digits + ".SS"
    return digits + ".SZ"


_YF_INTERVAL_MAP = {
    "1m": "1m",
    "3m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1H": "1h",
    "4H": "1h",
    "1D": "1d",
    "1W": "1wk",
}

_MERGE_FACTOR_MAP = {
    "3m": 3,
    "4H": 4,
}

_YF_DAYS_MAP = {
    "1m": lambda lim: min(7, max(2, (lim // 240) + 2)),
    "5m": lambda lim: min(60, max(3, (lim // 48) + 3)),
    "15m": lambda lim: min(60, max(3, (lim // 16) + 3)),
    "30m": lambda lim: min(60, max(5, (lim // 8) + 5)),
    "1H": lambda lim: min(730, max(8, (lim // 4) + 8)),
    "4H": lambda lim: min(730, max(20, lim + 10)),
    "1D": lambda lim: min(3650, lim + 10),
    "1W": lambda lim: min(3650, lim * 7 + 30),
}


def _bars_from_yfinance_df(df: Any) -> List[Dict[str, Any]]:
    """Convert a yfinance DataFrame (with DatetimeIndex or Date/Datetime column) to bar dicts."""
    if df is None or getattr(df, "empty", True):
        return []
    df = df.reset_index()
    time_col = None
    for candidate in ("Datetime", "Date", "index"):
        if candidate in df.columns:
            time_col = candidate
            break
    if time_col is None:
        return []
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            tv = row[time_col]
            if hasattr(tv, "timestamp"):
                ts = int(tv.timestamp())
            else:
                continue
            o, h, low, c, v = (
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row["Volume"]),
            )
            if o == 0 and c == 0:
                continue
            out.append({
                "time": ts,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": round(v, 2),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["time"])
    return out


def fetch_yfinance_klines(
    *,
    is_hk: bool,
    tencent_code: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int],
    after_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Fetch K-lines via yfinance for CN/HK stocks. Globally accessible, no API key needed."""
    try:
        assert_fd_available("yfinance K-line")
    except ResourceExhaustedError as e:
        logger.warning(str(e))
        return []

    try:
        import yfinance as yf
    except ImportError:
        logger.debug("yfinance not installed; skipping yfinance K-lines")
        return []

    interval = _YF_INTERVAL_MAP.get(timeframe)
    if not interval:
        return []

    yf_sym = yf_symbol_from_tencent(tencent_code, is_hk)
    merge_factor = _MERGE_FACTOR_MAP.get(timeframe, 1)
    effective_limit = limit * merge_factor
    days_func = _YF_DAYS_MAP.get(timeframe, lambda x: x + 10)
    days = days_func(effective_limit)

    end = datetime.fromtimestamp(int(before_time)) if before_time else datetime.now()
    start = (
        datetime.fromtimestamp(int(after_time))
        if after_time is not None
        else end - timedelta(days=days)
    )

    df: Any = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            ticker = yf.Ticker(yf_sym)
            df = ticker.history(
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                interval=interval,
            )
            break
        except Exception as e:
            try:
                record_exception(e, scope="yfinance K-line")
            except ResourceExhaustedError as guarded:
                logger.warning(str(guarded))
                return []
            if attempt + 1 < _MAX_ATTEMPTS and _is_transient(e):
                delay = min(_BACKOFF_CAP_SEC, _BACKOFF_BASE_SEC * (2 ** attempt))
                logger.debug(
                    "yfinance transient error %s tf=%s (attempt %s/%s), retry in %.1fs: %s",
                    yf_sym, timeframe, attempt + 1, _MAX_ATTEMPTS, delay, e,
                )
                time.sleep(delay)
                continue
            logger.warning("yfinance K-line failed %s tf=%s: %s", yf_sym, timeframe, e)
            return []

    bars = _bars_from_yfinance_df(df)
    if merge_factor > 1 and bars:
        bars = _merge_session_bars(bars, timeframe, is_hk)
    logger.debug("yfinance returned %d bars for %s tf=%s", len(bars), yf_sym, timeframe)
    return bars


# ---------------------------------------------------------------------------
# AkShare helpers (Eastmoney — unreliable from overseas, used as last resort)
# ---------------------------------------------------------------------------

def _minute_period_str(timeframe: str) -> Optional[str]:
    return {"1m": "1", "3m": "1", "5m": "5", "15m": "15", "30m": "30", "1H": "60", "4H": "60"}.get(timeframe)


def _min_bar_window(
    timeframe: str,
    limit: int,
    before_time: Optional[int],
    after_time: Optional[int] = None,
) -> tuple[str, str]:
    _ = (timeframe, limit)
    end = datetime.fromtimestamp(int(before_time)) if before_time else datetime.now()
    start = (
        datetime.fromtimestamp(int(after_time))
        if after_time is not None
        else end - timedelta(days=16)
    )
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)


def _bars_from_ak_min_df(df: Any, *, is_hk: bool = False, period_seconds: int = 0) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    cols = [str(x) for x in df.columns]
    time_c = "时间" if "时间" in cols else (cols[0] if len(cols) > 5 else None)
    if not time_c:
        return []

    def _pick(name_zh: str, idx: int) -> str:
        return name_zh if name_zh in cols else (cols[idx] if len(cols) > idx else "")

    c_open = _pick("开盘", 1)
    c_close = _pick("收盘", 2)
    c_high = _pick("最高", 3)
    c_low = _pick("最低", 4)
    c_vol = _pick("成交量", 5)
    if not all((c_open, c_close, c_high, c_low, c_vol)):
        return []

    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            t = pd.Timestamp(row[time_c])
            if is_hk:
                t = t.tz_localize("Asia/Hong_Kong") if t.tzinfo is None else t
                t -= pd.Timedelta(seconds=period_seconds)
            ts = int(t.timestamp())
            o, c, h, low, v = float(row[c_open]), float(row[c_close]), float(row[c_high]), float(row[c_low]), float(row[c_vol])
            out.append({
                "time": ts,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": round(v, 2),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["time"])
    return out


def _merge_session_bars(bars, timeframe, is_hk, *, source_seconds=None):
    if not is_hk:
        return _merge_every_n_sorted_bars(bars, _MERGE_FACTOR_MAP.get(timeframe, 1))
    from app.services.market_schedule import resample_equity_klines
    source_seconds = source_seconds or (3600 if timeframe == "4H" else 60)
    target_seconds = {"1H": 3600, "4H": 14400, "3m": 180}[timeframe]
    return resample_equity_klines(bars, "HKStock", source_seconds, target_seconds)


def _merge_every_n_sorted_bars(bars: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    if n <= 1 or len(bars) < n:
        return bars
    out: List[Dict[str, Any]] = []
    i = 0
    while i + n <= len(bars):
        chunk = bars[i : i + n]
        out.append({
            "time": chunk[0]["time"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": round(sum(b["volume"] for b in chunk), 2),
        })
        i += n
    return out


def fetch_akshare_minute_klines(
    *,
    is_hk: bool,
    tencent_code: str,
    timeframe: str,
    limit: int,
    before_time: Optional[int],
    after_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    try:
        assert_fd_available("AkShare minute K-line")
    except ResourceExhaustedError as e:
        logger.warning(str(e))
        return []

    p = _minute_period_str(timeframe)
    if is_hk and timeframe in {"1H", "4H"}:
        p = "30"
    if p is None:
        return []
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        logger.debug("akshare not installed; skipping AkShare minute K-lines")
        return []

    sym = ak_hk_code_from_tencent(tencent_code) if is_hk else ak_a_code_from_tencent(tencent_code)
    sd, ed = _min_bar_window(timeframe, limit, before_time, after_time)
    if is_hk:
        if before_time is not None:
            ed = pd.Timestamp(before_time, unit="s", tz="UTC").tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M:%S")
        if after_time is not None:
            sd = pd.Timestamp(after_time, unit="s", tz="UTC").tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M:%S")
    adj = "" if p == "1" else "qfq"

    df: Any = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with _bypass_proxy():
                if is_hk:
                    df = ak.stock_hk_hist_min_em(symbol=sym, period=p, adjust=adj, start_date=sd, end_date=ed)
                else:
                    df = ak.stock_zh_a_hist_min_em(symbol=sym, start_date=sd, end_date=ed, period=p, adjust=adj)
            break
        except Exception as e:
            try:
                record_exception(e, scope="AkShare minute K-line")
            except ResourceExhaustedError as guarded:
                logger.warning(str(guarded))
                return []
            if attempt + 1 < _MAX_ATTEMPTS and _is_transient(e):
                delay = min(_BACKOFF_CAP_SEC, _BACKOFF_BASE_SEC * (2 ** attempt))
                logger.debug(
                    "AkShare minute transient error %s tf=%s sym=%s (attempt %s/%s): %s",
                    tencent_code, timeframe, sym, attempt + 1, _MAX_ATTEMPTS, e,
                )
                time.sleep(delay)
                continue
            logger.warning("AkShare minute K-line failed %s tf=%s sym=%s: %s", tencent_code, timeframe, sym, e)
            return []

    bars = _bars_from_ak_min_df(df, is_hk=is_hk, period_seconds=int(p) * 60)
    merge_factor = _MERGE_FACTOR_MAP.get(timeframe, 1)
    if is_hk and timeframe in {"1H", "4H"}:
        merge_factor = 2 if timeframe == "1H" else 8
    if merge_factor > 1 and bars:
        bars = _merge_session_bars(bars, timeframe, is_hk, source_seconds=int(p) * 60)
    return bars


def fetch_akshare_weekly_klines(
    *,
    is_hk: bool,
    tencent_code: str,
    limit: int,
    before_time: Optional[int],
) -> List[Dict[str, Any]]:
    try:
        assert_fd_available("AkShare weekly K-line")
    except ResourceExhaustedError as e:
        logger.warning(str(e))
        return []

    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return []

    sym = ak_hk_code_from_tencent(tencent_code) if is_hk else ak_a_code_from_tencent(tencent_code)
    end = datetime.fromtimestamp(int(before_time)) if before_time else datetime.now()
    start = end - timedelta(days=max(int(limit or 300), 1) * 14 + 400)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    df: Any = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with _bypass_proxy():
                if is_hk:
                    df = ak.stock_hk_hist(symbol=sym, period="weekly", start_date=start_s, end_date=end_s, adjust="qfq")
                else:
                    df = ak.stock_zh_a_hist(symbol=sym, period="weekly", start_date=start_s, end_date=end_s, adjust="qfq")
            break
        except Exception as e:
            try:
                record_exception(e, scope="AkShare weekly K-line")
            except ResourceExhaustedError as guarded:
                logger.warning(str(guarded))
                return []
            if attempt + 1 < _MAX_ATTEMPTS and _is_transient(e):
                delay = min(_BACKOFF_CAP_SEC, _BACKOFF_BASE_SEC * (2 ** attempt))
                logger.debug(
                    "AkShare weekly transient error sym=%s (attempt %s/%s): %s",
                    sym, attempt + 1, _MAX_ATTEMPTS, e,
                )
                time.sleep(delay)
                continue
            logger.warning("AkShare weekly K-line failed sym=%s: %s", sym, e)
            return []

    if df is None or getattr(df, "empty", True) or "日期" not in df.columns:
        return []
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            t = pd.Timestamp(row["日期"])
            ts = int(t.timestamp())
            o, c, h, low = float(row["开盘"]), float(row["收盘"]), float(row["最高"]), float(row["最低"])
            v = float(row["成交量"])
            out.append({
                "time": ts,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": round(v, 2),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["time"])
    return out
