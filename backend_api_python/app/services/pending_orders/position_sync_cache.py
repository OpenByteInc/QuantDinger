"""Shared cache helpers for exchange position synchronization."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from app.utils.security_fingerprint import opaque_fingerprint

PositionSnapshot = Tuple[
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, str]],
]

_snapshot_cache: Dict[str, Tuple[float, PositionSnapshot]] = {}
_backoff_until: Dict[str, float] = {}
_lock = threading.Lock()


def position_sync_cache_key(
    user_id: int,
    exchange_id: str,
    market_type: str,
    exchange_config: Dict[str, Any],
) -> str:
    """Build a cache key scoped to user, exchange, market, and credential."""
    cred_id = exchange_config.get("credential_id") or exchange_config.get("credentials_id")
    if cred_id:
        return f"u{int(user_id)}:{exchange_id}:{market_type}:cred:{int(cred_id)}"
    fingerprint = opaque_fingerprint(
        exchange_config.get("api_key") or exchange_config.get("apiKey") or "",
        namespace="position-sync-credential",
    )
    return f"u{int(user_id)}:{exchange_id}:{market_type}:inline:{fingerprint}"


def position_sync_cache_ttl_sec() -> float:
    try:
        custom = float(os.getenv("POSITION_SYNC_CACHE_TTL_SEC", "0"))
        if custom > 0:
            return custom
    except Exception:
        pass
    try:
        interval = float(os.getenv("POSITION_SYNC_INTERVAL_SEC", "30"))
        return max(30.0, interval)
    except Exception:
        return 60.0


def get_position_sync_snapshot(cache_key: str) -> Optional[PositionSnapshot]:
    now = time.time()
    with _lock:
        entry = _snapshot_cache.get(cache_key)
        if not entry:
            return None
        expires, snapshot = entry
        if now >= expires:
            _snapshot_cache.pop(cache_key, None)
            return None
        return snapshot


def set_position_sync_snapshot(
    cache_key: str,
    exch_size: Dict[str, Dict[str, float]],
    exch_entry_price: Dict[str, Dict[str, float]],
    inst_id_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    snapshot: PositionSnapshot = (exch_size, exch_entry_price, inst_id_map or {})
    with _lock:
        _snapshot_cache[cache_key] = (time.time() + position_sync_cache_ttl_sec(), snapshot)


def invalidate_position_sync_snapshot(cache_key: str) -> None:
    """Drop a cached exchange position snapshot."""
    with _lock:
        _snapshot_cache.pop(str(cache_key or ""), None)


def invalidate_position_sync_snapshot_for_exchange(
    *,
    user_id: int,
    exchange_id: str,
    market_type: str,
    exchange_config: Dict[str, Any],
) -> None:
    invalidate_position_sync_snapshot(
        position_sync_cache_key(
            int(user_id or 1),
            str(exchange_id or "").strip().lower(),
            str(market_type or "swap").strip().lower(),
            exchange_config if isinstance(exchange_config, dict) else {},
        )
    )


def exchange_sync_backoff_sec() -> float:
    try:
        return max(60.0, float(os.getenv("EXCHANGE_SYNC_BACKOFF_SEC", "900")))
    except Exception:
        return 900.0


def is_exchange_sync_backoff(cache_key: str) -> bool:
    with _lock:
        until = float(_backoff_until.get(cache_key) or 0.0)
    return time.time() < until


def set_exchange_sync_backoff(cache_key: str, seconds: Optional[float] = None) -> None:
    sec = float(seconds if seconds is not None else exchange_sync_backoff_sec())
    with _lock:
        _backoff_until[cache_key] = time.time() + sec


def is_exchange_rate_limit_error(message: str) -> bool:
    text = (message or "").lower()
    return any(
        token in text
        for token in (
            "418",
            "-1003",
            "too many requests",
            "rate limit",
            "banned until",
        )
    )
