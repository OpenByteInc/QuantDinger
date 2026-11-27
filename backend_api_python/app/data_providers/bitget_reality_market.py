"""Public Bitget Reality candles through the UTA v3 market API."""

from __future__ import annotations

from typing import Any, Optional
import time

import requests

from app.config.data_sources import CryptoPublicConfig


BITGET_MARKET_CANDLES_URL = "https://api.bitget.com/api/v3/market/candles"
BITGET_HISTORY_CANDLES_URL = "https://api.bitget.com/api/v3/market/history-candles"
_TIMEFRAME_MAP = {
    "1M": "1m",
    "5M": "5m",
    "15M": "15m",
    "1H": "1H",
    "4H": "4H",
    "1D": "1D",
}


def get_bitget_reality_klines(
    symbol: str,
    timeframe: str,
    limit: int = 300,
    *,
    before_time: Optional[int] = None,
    after_time: Optional[int] = None,
) -> list[dict[str, Any]]:
    interval = _TIMEFRAME_MAP.get(str(timeframe or "").strip().upper())
    if not interval:
        raise ValueError(f"Unsupported Bitget Reality timeframe: {timeframe}")
    native_symbol = _native_symbol(symbol)
    proxy = str(CryptoPublicConfig.PROXY or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    requested_limit = max(1, int(limit or 300))
    start_ms = int(after_time) * 1000 if after_time else 0
    cursor_end_ms = int(before_time) * 1000 if before_time else int(time.time() * 1000)
    rows_by_time: dict[int, dict[str, Any]] = {}
    max_pages = min(500, max(1, (requested_limit + 99) // 100))
    for _ in range(max_pages):
        history = cursor_end_ms < int(time.time() * 1000) - 89 * 86400 * 1000
        per_page = min(requested_limit - len(rows_by_time), 100 if history else 1000)
        if per_page <= 0:
            break
        params: dict[str, Any] = {
            "category": "SPOT",
            "symbol": native_symbol,
            "interval": interval,
            "limit": per_page,
            "endTime": cursor_end_ms,
            "type": "market",
        }
        page_start_ms = max(start_ms, cursor_end_ms - 89 * 86400 * 1000)
        if page_start_ms:
            params["startTime"] = page_start_ms
        page = _request_page(
            BITGET_HISTORY_CANDLES_URL if history else BITGET_MARKET_CANDLES_URL,
            params=params,
            proxies=proxies,
        )
        if not page:
            break
        oldest_ms = cursor_end_ms
        for item in page:
            row = _parse_row(item)
            if not row:
                continue
            rows_by_time[row["time"]] = row
            oldest_ms = min(oldest_ms, row["time"] * 1000)
        if oldest_ms >= cursor_end_ms or (start_ms and oldest_ms <= start_ms):
            break
        cursor_end_ms = oldest_ms - 1
    rows = sorted(rows_by_time.values(), key=lambda row: row["time"])
    if not rows:
        raise ValueError("Bitget Reality returned no usable candlesticks")
    return rows[-requested_limit:]


def _native_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper().split(":", 1)[0]
    native = value.replace("/", "").replace("-", "").replace("_", "")
    if not native:
        raise ValueError("Bitget Reality symbol is required")
    return native


def _request_page(url: str, *, params: dict[str, Any], proxies: Any) -> list[Any]:
    from app.utils.exchange_request_guard import guarded_exchange_request

    response = guarded_exchange_request(requests.get,
        url,
        params=params,
        timeout=(6.0, 15.0),
        proxies=proxies,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("code") or "") not in {"", "0", "00000"}:
        raise ValueError(f"Bitget Reality candles failed: {payload}")
    data = payload.get("data")
    return data if isinstance(data, list) else data.get("list", []) if isinstance(data, dict) else []


def _parse_row(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, (list, tuple)) or len(item) < 6:
        return None
    try:
        timestamp = int(float(item[0]))
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return {
            "time": timestamp,
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        }
    except (TypeError, ValueError):
        return None
