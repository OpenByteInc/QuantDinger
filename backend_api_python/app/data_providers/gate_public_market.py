"""Small native Gate.io public-market client for recent spot candles.

This path uses the dedicated Gate endpoint for latency-sensitive,
already-normalized symbols. The caller falls back to the regular data-source
stack if Gate rejects the request or the response is unusable.
"""
from __future__ import annotations

from typing import Any

import requests

from app.config.data_sources import CryptoPublicConfig
from app.utils.exchange_request_guard import guarded_exchange_request


GATE_SPOT_CANDLES_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
_TIMEFRAME_MAP = {
    "1M": "1m",
    "5M": "5m",
    "15M": "15m",
    "30M": "30m",
    "1H": "1h",
    "4H": "4h",
    "1D": "1d",
    "1W": "7d",
}


def _currency_pair(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper().split(":", 1)[0]
    parts = normalized.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Gate spot symbol must be BASE/QUOTE: {symbol}")
    return f"{parts[0]}_{parts[1]}"


def get_gate_spot_klines(symbol: str, timeframe: str, limit: int = 120) -> list[dict[str, Any]]:
    interval = _TIMEFRAME_MAP.get(str(timeframe or "").upper())
    if not interval:
        raise ValueError(f"Unsupported Gate spot timeframe: {timeframe}")
    proxy = str(CryptoPublicConfig.PROXY or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    request_kwargs = {
        "params": {
            "currency_pair": _currency_pair(symbol),
            "interval": interval,
            "limit": max(1, min(int(limit), 1000)),
        },
        # A slightly wider connect window prevents normal cross-region TLS
        # setup jitter from needlessly falling back to the general market-data path.
        "timeout": (6.0, 12.0),
        "proxies": proxies,
    }
    response = None
    for attempt in range(2):
        try:
            response = guarded_exchange_request(requests.get, GATE_SPOT_CANDLES_URL, **request_kwargs)
            break
        except (requests.Timeout, requests.ConnectionError):
            if attempt:
                raise
    if response is None:  # pragma: no cover - defensive; loop either succeeds or raises
        raise RuntimeError("Gate request returned no response")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Gate candlesticks response must be a list")

    rows: list[dict[str, Any]] = []
    for item in payload:
        # Gate v4 spot: [timestamp, quote_volume, close, high, low, open,
        #                base_volume, is_window_closed]
        if not isinstance(item, (list, tuple)) or len(item) < 7:
            continue
        try:
            rows.append({
                "time": int(float(item[0])),
                "open": float(item[5]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[2]),
                "volume": float(item[6]),
            })
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda row: row["time"])
    if not rows:
        raise ValueError("Gate returned no usable candlesticks")
    return rows[-max(1, int(limit)):]
