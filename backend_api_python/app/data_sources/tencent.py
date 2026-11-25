"""
Tencent market data helpers (no API key).

Provides:
- Quote: https://qt.gtimg.cn/q=sh600519 / sz000001 / hk00700
- Kline: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=CODE,PERIOD,,,COUNT,ADJ

This is used as a stable alternative when Yahoo/yfinance gets rate-limited.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.data_sources.rate_limiter import get_request_headers, retry_with_backoff, get_tencent_limiter
from app.data_sources.quote_time import quote_timestamp
from app.utils.logger import get_logger
from app.utils.resource_guard import assert_fd_available

logger = get_logger(__name__)


def normalize_cn_code(symbol: str) -> str:
    """
    Normalize A-share symbol to Tencent code: sh600519 / sz000001.
    Accepts:
    - 600519 / 600519.SH / 600519.SS
    - 000001 / 000001.SZ
    """
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if s.endswith(".SH"):
        s = s[:-3]
        return f"SH{s}"
    if s.endswith(".SS"):
        s = s[:-3]
        return f"SH{s}"
    if s.endswith(".SZ"):
        s = s[:-3]
        return f"SZ{s}"

    if s.isdigit() and len(s) == 6:
        return ("SH" + s) if s.startswith("6") else ("SZ" + s)

    return s


def normalize_hk_code(symbol: str) -> str:
    """
    Normalize HK stock symbol to Tencent code: hk00700 (5 digits).
    Accepts:
    - 700 / 0700 / 00700.HK / 0700.HK
    """
    s = (symbol or "").strip().upper()
    if not s:
        return s
    if s.endswith(".HK"):
        s = s[:-3]
    if s.isdigit():
        return "HK" + s.zfill(5)
    # If user already passed HKxxxxx
    if s.startswith("HK") and s[2:].isdigit():
        return "HK" + s[2:].zfill(5)
    return s


def _lower_code(code: str) -> str:
    return (code or "").strip().lower()


@retry_with_backoff(max_attempts=3, base_delay=1.2, max_delay=8.0, exceptions=(Exception,))
def fetch_quote(code: str, timeout: int = 8) -> Optional[List[str]]:
    """
    Returns the raw '~' split array from qt.gtimg.cn, or None.
    """
    c = _lower_code(code)
    if not c:
        return None
    assert_fd_available("Tencent quote")

    limiter = get_tencent_limiter()
    limiter.wait()
    url = f"https://qt.gtimg.cn/q={c}"
    with requests.get(url, headers=get_request_headers(referer="https://qt.gtimg.cn/"), timeout=timeout) as resp:
        resp.raise_for_status()
        # Tencent quote is often GBK encoded
        try:
            resp.encoding = "gbk"
        except Exception:
            pass
        text = (resp.text or "").strip()
    if not text or "~" not in text:
        return None

    # Format: v_sh600519="1~NAME~CODE~LAST~PREV~OPEN~..."
    try:
        start = text.index('="') + 2
        end = text.rindex('"')
        payload = text[start:end]
    except Exception:
        return None

    parts = payload.split("~")
    return parts if len(parts) > 5 else None


def parse_quote_to_ticker(parts: List[str]) -> Dict[str, Any]:
    """
    Best-effort conversion to a unified ticker dict.
    """
    def _f(i: int, default: float = 0.0) -> float:
        try:
            v = parts[i]
            if v is None or v == "":
                return default
            return float(v)
        except Exception:
            return default

    name = (parts[1] or "").strip() if len(parts) > 1 else ""
    symbol = (parts[2] or "").strip() if len(parts) > 2 else ""
    last_ = _f(3, 0.0)
    prev = _f(4, 0.0)
    open_ = _f(5, 0.0)

    change = round(last_ - prev, 4) if prev else 0.0
    change_pct = round(change / prev * 100, 2) if prev else 0.0

    # Indices are not fully consistent across markets; keep conservative.
    high = _f(33, last_) if len(parts) > 33 else last_
    low = _f(34, last_) if len(parts) > 34 else last_
    try:
        observed = datetime.strptime(parts[30], "%Y%m%d%H%M%S")
    except (IndexError, TypeError, ValueError):
        observed = None

    return {
        "symbol": symbol,
        "name": name,
        "last": last_,
        "quote_time": quote_timestamp(observed, timezone="Asia/Shanghai"),
        "change": change,
        "changePercent": change_pct,
        "high": high,
        "low": low,
        "open": open_ or last_,
        "previousClose": prev,
        "raw": parts,
    }


def parse_tencent_kline_time(ds: str) -> Optional[int]:
    """Parse Tencent fqkline first column to Unix seconds (local parse, matches prior chart behavior)."""
    raw = str(ds or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return int(datetime.strptime(raw, fmt).timestamp())
        except ValueError:
            continue
    try:
        ts = int(float(raw))
        if ts > 10**12:
            ts = int(ts / 1000)
        return ts
    except Exception:
        return None


def tencent_kline_rows_to_dicts(rows: List[Any]) -> List[Dict[str, Any]]:
    """Convert raw fqkline rows to chart dicts; ignores corporate-action tail objects on HK rows."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        ts = parse_tencent_kline_time(r[0])
        if ts is None:
            continue
        try:
            o, c, h, low, vol = float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "time": ts,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": round(vol, 2),
            }
        )
    return out


@retry_with_backoff(max_attempts=3, base_delay=1.2, max_delay=8.0, exceptions=(Exception,))
def fetch_kline(code: str, period: str, count: int = 300, adj: str = "qfq", timeout: int = 10) -> List[List[str]]:
    """
    Fetch kline arrays from Tencent.

    period examples:
    - day, week, month (supported by Tencent fqkline)

    Note: Minute periods (m1/m5/…) return **bad params** on this endpoint; use AkShare in ``asia_stock_kline``.
    """
    c = _lower_code(code)
    if not c:
        return []
    assert_fd_available("Tencent K-line")

    limiter = get_tencent_limiter()
    limiter.wait()

    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{c},{period},,,{int(count)},{adj}"}
    with requests.get(url, headers=get_request_headers(referer="https://gu.qq.com/"), params=params, timeout=timeout) as resp:
        resp.raise_for_status()
        data = resp.json() if resp.text else {}
    if not isinstance(data, dict) or int(data.get("code", 0)) != 0:
        return []
    root = (data.get("data") or {}).get(c)
    if not isinstance(root, dict):
        return []

    # Data key variants:
    # - A-share: qfqday / qfqweek / qfqm1 ...
    # - HK: day / week / m1 ...
    candidates = []
    if adj:
        candidates.append(f"{adj}{period}")
    candidates.append(period)

    for key in candidates:
        arr = root.get(key)
        if isinstance(arr, list) and arr:
            return arr

    # Fallback: search any key that endswith period and is a list
    for k, v in root.items():
        if isinstance(v, list) and v and str(k).lower().endswith(str(period).lower()):
            return v
    return []

