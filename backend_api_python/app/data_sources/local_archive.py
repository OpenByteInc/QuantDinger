"""Host-side K-line archive under QUANTDINGER_DATE_DIR (default: repo DATE/).

Layout:
    DATE/stocks/CN/{SYMBOL}/{TIMEFRAME}.csv
    DATE/futures/CN/{SYMBOL}/{TIMEFRAME}.csv

CSV columns: time,open,high,low,close,volume  (time = unix seconds)
"""
from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

CSV_FIELDS = ("time", "open", "high", "low", "close", "volume")
_ARCHIVED_MARKETS = frozenset({"CNStock", "USStock", "HKStock", "Futures"})


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def date_root() -> Path:
    raw = (os.getenv("QUANTDINGER_DATE_DIR") or "").strip()
    if raw:
        return Path(raw)
    # backend_api_python/app/data_sources/local_archive.py → repo root
    return Path(__file__).resolve().parents[3] / "DATE"


def archive_enabled_read() -> bool:
    return _env_flag("QUANTDINGER_DATE_READ", True)


def archive_enabled_write() -> bool:
    return _env_flag("QUANTDINGER_DATE_WRITE", True)


def market_supported(market: str) -> bool:
    return (market or "") in _ARCHIVED_MARKETS


def should_read(market: str, symbol: str) -> bool:
    return archive_enabled_read() and archive_kind(market, symbol) is not None


def should_write(market: str, symbol: str) -> bool:
    return archive_enabled_write() and archive_kind(market, symbol) is not None


def archive_kind(market: str, symbol: str) -> Optional[Tuple[str, str]]:
    m = market or ""
    if m == "CNStock":
        return "stocks", "CN"
    if m == "USStock":
        return "stocks", "US"
    if m == "HKStock":
        return "stocks", "HK"
    if m == "Futures":
        from app.data_sources.cn_futures_symbols import is_cn_futures_symbol

        return "futures", "CN" if is_cn_futures_symbol(symbol) else "US"
    return None


def canonical_symbol(market: str, symbol: str) -> str:
    raw = (symbol or "").strip()
    if market == "CNStock":
        from app.data_sources.tencent import normalize_cn_code

        raw = normalize_cn_code(raw) or raw
    cleaned = raw.upper().replace("/", "-").replace("\\", "-").replace("..", "")
    return cleaned or "UNKNOWN"


def canonical_timeframe(timeframe: str) -> str:
    tf = (timeframe or "1D").strip().upper().replace("/", "-")
    return tf or "1D"


def kline_path(market: str, symbol: str, timeframe: str) -> Optional[Path]:
    kind = archive_kind(market, symbol)
    if kind is None:
        return None
    asset, region = kind
    return (
        date_root()
        / asset
        / region
        / canonical_symbol(market, symbol)
        / f"{canonical_timeframe(timeframe)}.csv"
    )


def filter_klines(
    klines: List[Dict[str, Any]],
    limit: int,
    before_time: Optional[int] = None,
    after_time: Optional[int] = None,
    truncate: bool = True,
) -> List[Dict[str, Any]]:
    rows = sorted(klines, key=lambda x: int(x["time"]))
    if before_time:
        rows = [k for k in rows if int(k["time"]) < int(before_time)]
    if after_time is not None:
        rows = [k for k in rows if int(k["time"]) >= int(after_time)]
    if truncate and limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def local_covers(
    rows: List[Dict[str, Any]],
    *,
    limit: int,
    before_time: Optional[int],
    after_time: Optional[int],
    timeframe: str,
) -> bool:
    if not rows:
        return False
    window = filter_klines(
        rows,
        limit=10**9,
        before_time=before_time,
        after_time=after_time,
        truncate=False,
    )
    if not window:
        return False
    if after_time is not None:
        slack = 5 * 86400
        left_ok = int(window[0]["time"]) <= int(after_time) + slack
        return left_ok and len(window) >= min(int(limit or 1), 20)
    if len(window) < min(int(limit or 1), 30):
        return False
    last = int(window[-1]["time"])
    now = int(time.time())
    tf = canonical_timeframe(timeframe)
    max_age = 10 * 86400 if tf in ("1D", "D", "1W", "W", "1M") else 2 * 86400
    return (now - last) <= max_age


def _as_bar(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        ts = int(float(row["time"]))
        return {
            "time": ts,
            "open": float(row.get("open") or 0),
            "high": float(row.get("high") or 0),
            "low": float(row.get("low") or 0),
            "close": float(row.get("close") or 0),
            "volume": float(row.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def read_klines(market: str, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
    path = kline_path(market, symbol, timeframe)
    if path is None or not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            bar = _as_bar(raw)
            if bar:
                out.append(bar)
    out.sort(key=lambda x: x["time"])
    return out


def merge_write(
    market: str,
    symbol: str,
    timeframe: str,
    incoming: Iterable[Dict[str, Any]],
) -> Path:
    path = kline_path(market, symbol, timeframe)
    if path is None:
        raise ValueError(f"market {market} is not archived")
    by_time: Dict[int, Dict[str, Any]] = {}
    if path.is_file():
        for bar in read_klines(market, symbol, timeframe):
            by_time[int(bar["time"])] = bar
    added = 0
    for raw in incoming:
        bar = _as_bar(raw)
        if not bar:
            continue
        ts = int(bar["time"])
        if ts not in by_time:
            added += 1
        by_time[ts] = bar
    rows = [by_time[k] for k in sorted(by_time)]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for bar in rows:
            writer.writerow({k: bar[k] for k in CSV_FIELDS})
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    logger.debug(
        "DATE archive wrote %s bars (+%s new) → %s",
        len(rows),
        added,
        path,
    )
    return path
