"""
Unified data provider layer for global market data.

Shared cache and utility helpers live here; domain-specific fetchers are
organised into sub-modules (crypto, forex, ...).

The cache layer delegates to :class:`app.utils.cache.CacheManager`, which
transparently uses **Redis** when configured (``CACHE_ENABLED=true``) or
falls back to a thread-safe in-memory dict.

Beyond plain get/set this module also exposes three production-grade
helpers used by the global-market routes:

* :func:`cached_or_compute` – cache-first wrapper with single-flight
  request coalescing and optional stale-while-revalidate (SWR).
* :func:`set_cached` / :func:`get_cached` – kept for backward compatibility
  with existing call sites; new code should prefer
  :func:`cached_or_compute`.
* :func:`clear_cache` – flushes every ``dp:*`` key (used by `/refresh`).

The single-flight + SWR pattern matters because the upstream sources
(yfinance, CoinGecko, TradingEconomics, etc.) are slow and rate-limited.
Without coalescing, a TTL-expiry moment can stampede 10+ concurrent
requests onto the same upstream endpoint. With SWR, the user that triggers
the refresh still gets an instant response from the previous cache value.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Cache TTL table (single source of truth)
#
# Each key maps to its *hard* TTL in seconds — the window during which the
# value is considered fresh and is returned without any refresh attempt.
# Route code should NOT pass a TTL override unless it has a very specific
# reason; instead, edit this table.
# ---------------------------------------------------------------------------

CACHE_TTL = {
    # Heatmap (sector / crypto / forex / commodity tiles) — volatile but
    # mostly directional; 2 min keeps it visually fresh and is friendly to
    # yfinance / CoinGecko rate limits.
    "crypto_heatmap": 300,
    "market_heatmap": 120,
    # Quote-style aggregates — same cadence as heatmap.
    "forex_pairs": 120,
    "stock_indices": 120,
    "market_overview": 120,
    "commodities": 120,
    # News & calendar — slow-moving, can be cached aggressively.
    "market_news": 180,
    "economic_calendar": 3600,
    "economic_calendar_v2": 3600,
    "economic_calendar_v3": 3600,
    # Macro sentiment (Fear&Greed, VIX, DXY...) — daily-ish cadence so 6h
    # is fine. SWR lets us return the previous payload while we refresh.
    "market_sentiment": 21600,
    # Opportunity scanner — heavy compute, 1h is the right cadence.
    "trading_opportunities": 3600,
}

_DEFAULT_TTL = 60

# Soft-TTL multiplier for stale-while-revalidate. Within
# `hard_ttl .. hard_ttl * SWR_MULTIPLIER`, the cache will return the old
# value AND trigger a background refresh. Outside that window the request
# blocks until the refresh completes.
SWR_MULTIPLIER = 5


# ---------------------------------------------------------------------------
# Single-flight + SWR plumbing
# ---------------------------------------------------------------------------

# Per-key locks ensure that within a single Flask worker process, only one
# thread actually computes a given cache key at a time. Other threads block
# on the lock, then read the freshly-populated cache.
_inflight_locks: dict[str, threading.Lock] = {}
_inflight_master = threading.Lock()

# Tracks which keys have a background refresh currently in flight (so we
# don't fire multiple background refreshes for the same key when many SWR
# reads land in the same second).
_bg_refreshing: set[str] = set()
_bg_refreshing_lock = threading.Lock()

# Shared executor for background refreshes. Keep it small — these tasks are
# I/O bound and we don't want them to overwhelm the upstream APIs we're
# specifically trying to protect from stampedes.
_swr_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="dp-swr"
)


def _get_lock(key: str) -> threading.Lock:
    """Return (or lazily create) the per-key compute lock."""
    lock = _inflight_locks.get(key)
    if lock is not None:
        return lock
    with _inflight_master:
        lock = _inflight_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _inflight_locks[key] = lock
        return lock


def _cm():
    """Lazy singleton accessor to avoid import-time side effects."""
    from app.utils.cache import CacheManager
    return CacheManager()


def _wrap_envelope(value: Any, hard_ttl: int) -> dict:
    """Wrap a cached payload with freshness metadata.

    We use a JSON envelope so the SWR layer can tell "is this still fresh?"
    without needing TTL introspection on the underlying backend (which
    differs for Redis vs MemoryCache).
    """
    now = time.time()
    return {
        "__v": 1,
        "value": value,
        "stored_at": now,
        "hard_until": now + max(1, int(hard_ttl)),
    }


def _unwrap_envelope(envelope: Any) -> tuple[Optional[Any], bool]:
    """Return (value, is_fresh) from a stored envelope.

    Backward compatibility: legacy entries written before this envelope
    existed (raw payloads) are treated as fresh — we never want to break a
    page on a deploy boundary.
    """
    if not isinstance(envelope, dict) or envelope.get("__v") != 1:
        if envelope is None:
            return None, False
        return envelope, True
    value = envelope.get("value")
    hard_until = float(envelope.get("hard_until") or 0)
    return value, time.time() < hard_until


# ---------------------------------------------------------------------------
# Public API — backward compatible
# ---------------------------------------------------------------------------


def get_cached(key: str, ttl: int | None = None) -> Optional[Any]:
    """Return cached data if not expired.

    *ttl* is accepted for backward-compat with the legacy signature but is
    only used as a hint — actual expiry is governed by the envelope stored
    at write-time. Returns ``None`` for cache miss or hard-expired entries.
    """
    raw = _cm().get(f"dp:{key}")
    value, is_fresh = _unwrap_envelope(raw)
    if value is None:
        return None
    if not is_fresh:
        # Don't surface hard-expired values via the legacy getter; callers
        # that want SWR semantics should use `cached_or_compute`.
        return None
    return value


def set_cached(key: str, data: Any, ttl: int | None = None):
    """Write a cache entry, wrapping it in a freshness envelope.

    The backend Redis/MemoryCache layer also enforces its own expiry; we
    set it to ``hard_ttl * SWR_MULTIPLIER`` so SWR reads can still see the
    stale value after the soft expiry.
    """
    effective_ttl = ttl or CACHE_TTL.get(key, _DEFAULT_TTL)
    envelope = _wrap_envelope(data, effective_ttl)
    backend_ttl = effective_ttl * SWR_MULTIPLIER
    _cm().set(f"dp:{key}", envelope, ttl=backend_ttl)


def cached_or_compute(
    key: str,
    compute: Callable[[], Any],
    *,
    ttl: int | None = None,
    force: bool = False,
    allow_stale: bool = True,
) -> Any:
    """Cache-first wrapper with single-flight + stale-while-revalidate.

    Behaviour:
        1. ``force=True``                → bypass cache, compute, store, return.
        2. Cache HIT (fresh)             → return cached value.
        3. Cache HIT (stale, within SWR) → return stale value AND trigger a
           background refresh (only one in-flight per key). The current
           caller is not blocked.
        4. Cache MISS or stale past SWR  → acquire per-key lock; the first
           thread computes and stores; subsequent threads read the result.

    The compute function must be self-contained (no shared mutable state
    that would race with background callers) and must return a
    JSON-serialisable value.
    """
    effective_ttl = ttl or CACHE_TTL.get(key, _DEFAULT_TTL)

    if force:
        return _compute_and_store(key, compute, effective_ttl)

    raw = _cm().get(f"dp:{key}")
    value, is_fresh = _unwrap_envelope(raw)

    if value is not None and is_fresh:
        return value

    # Stale-while-revalidate: surface the old payload immediately and
    # refresh in the background. The user perceives "instant" load.
    if value is not None and allow_stale:
        _schedule_background_refresh(key, compute, effective_ttl)
        return value

    # No usable value at all — serialise concurrent computes via lock.
    lock = _get_lock(key)
    with lock:
        # Double-check after acquiring the lock: another thread may have
        # populated the cache while we waited.
        raw = _cm().get(f"dp:{key}")
        value, is_fresh = _unwrap_envelope(raw)
        if value is not None and is_fresh:
            return value
        return _compute_and_store(key, compute, effective_ttl)


def _compute_and_store(key: str, compute: Callable[[], Any], ttl: int) -> Any:
    """Run ``compute`` and persist the result. Never raises — on compute
    failure we return ``None`` and leave the existing (stale) entry alone
    so the next call still benefits from SWR."""
    try:
        result = compute()
    except Exception as e:
        logger.error("Cache compute failed for key %s: %s", key, e, exc_info=True)
        return None
    try:
        envelope = _wrap_envelope(result, ttl)
        _cm().set(f"dp:{key}", envelope, ttl=ttl * SWR_MULTIPLIER)
    except Exception as e:
        logger.error("Cache write failed for key %s: %s", key, e)
    return result


def _schedule_background_refresh(
    key: str, compute: Callable[[], Any], ttl: int
):
    """Fire a one-shot background refresh for ``key`` if none is in flight.

    The background task uses the per-key lock so it won't stampede the
    upstream, and de-duplicates against ``_bg_refreshing`` so a burst of
    SWR reads only schedules one job.
    """
    with _bg_refreshing_lock:
        if key in _bg_refreshing:
            return
        _bg_refreshing.add(key)

    def _runner():
        try:
            lock = _get_lock(key)
            # If the lock is already held, another caller is doing a
            # synchronous compute — let them finish and skip.
            if not lock.acquire(blocking=False):
                return
            try:
                _compute_and_store(key, compute, ttl)
            finally:
                lock.release()
        finally:
            with _bg_refreshing_lock:
                _bg_refreshing.discard(key)

    try:
        _swr_executor.submit(_runner)
    except Exception as e:
        logger.warning("SWR scheduling failed for %s: %s", key, e)
        with _bg_refreshing_lock:
            _bg_refreshing.discard(key)


def invalidate(key: str) -> None:
    """Drop a single cache entry. No-op if the key doesn't exist or the
    backend rejects the delete (we never want a route-side eviction call
    to crash the request)."""
    try:
        _cm().delete(f"dp:{key}")
    except Exception as e:
        logger.debug("Cache invalidate(%s) failed: %s", key, e)


def clear_cache():
    """Clear all cached data (used by /refresh endpoint).

    For Redis this deletes ``dp:*`` keys; for the in-memory backend it
    clears the whole dict (acceptable — only market data lives here).
    """
    cm = _cm()
    if hasattr(cm, "_client") and hasattr(cm._client, "clear"):
        cm._client.clear()
    else:
        try:
            import redis as _redis
            if isinstance(cm._client, _redis.Redis):
                for key in cm._client.scan_iter("dp:*"):
                    cm._client.delete(key)
        except Exception:
            pass


def safe_float(v: Any, default: float = 0.0) -> float:
    """Safely convert *v* to float; return *default* on failure."""
    try:
        return float(v)
    except Exception:
        return default
