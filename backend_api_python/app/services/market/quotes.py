"""Quote fetching and cache helpers for watchlist pricing."""

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

from app.services.kline import KlineService
from app.services.market_context import (
    default_crypto_exchange_id,
    normalize_exchange_id,
    normalize_market_type,
)
from app.services.market.symbol_search import find_market_symbol
from app.services.market.assets import AssetResolutionError, resolve_research_instrument
from app.utils.cache import CacheManager
from app.utils.logger import get_logger
from app.utils.request_guard import RequestGuardError, cache_key, guarded_cached

logger = get_logger(__name__)

kline_service = KlineService()
_market_cache = CacheManager()

QUOTE_CACHE_TTL_SEC = int(os.getenv("WATCHLIST_QUOTE_CACHE_TTL_SEC", "5"))
QUOTE_STALE_TTL_SEC = int(os.getenv("WATCHLIST_QUOTE_STALE_TTL_SEC", "600"))
QUOTE_FETCH_TIMEOUT_SEC = float(os.getenv("WATCHLIST_QUOTE_FETCH_TIMEOUT_SEC", "8"))
QUOTE_CACHE_VERSION = os.getenv("WATCHLIST_QUOTE_CACHE_VERSION", "v2").strip() or "v2"
CRYPTO_SWAP_FIRST_BASES = frozenset(
    value.strip().upper()
    for value in os.getenv("WATCHLIST_CRYPTO_SWAP_FIRST_BASES", "XAU,XAG").split(",")
    if value.strip()
)


def _executor_workers() -> int:
    try:
        value = int(os.getenv("MARKET_EXECUTOR_WORKERS", "6"))
        return value if value > 0 else 6
    except Exception:
        return 6


executor = ThreadPoolExecutor(max_workers=_executor_workers())


def _quote_source_context(
    market: str,
    exchange_id: str = "",
    market_type: str = "",
) -> tuple[str, str]:
    if str(market or "").strip() != "Crypto":
        return "", ""
    resolved_exchange_id = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
    resolved_market_type = normalize_market_type(market_type, market="Crypto")
    return resolved_exchange_id, resolved_market_type


def quote_cache_key(
    market: str,
    symbol: str,
    *,
    exchange_id: str = "",
    market_type: str = "",
    instrument_id: str = "",
    stale: bool = False,
) -> str:
    prefix = "watchlist_quote_stale" if stale else "watchlist_quote"
    return f"{prefix}:{QUOTE_CACHE_VERSION}:{market}:{exchange_id}:{market_type}:{instrument_id}:{symbol}".upper()


def empty_price(
    market: str,
    symbol: str,
    *,
    exchange_id: str = "",
    market_type: str = "",
    error: str = "",
) -> dict:
    out = {
        "market": market,
        "symbol": symbol,
        "exchange_id": exchange_id,
        "market_type": market_type,
        "price": 0,
        "change": 0,
        "changePercent": 0,
    }
    if error:
        out["error"] = error
    return out


def normalize_price_payload(
    market: str,
    symbol: str,
    price_data: dict,
    *,
    exchange_id: str = "",
    market_type: str = "",
    cached: bool = False,
    stale: bool = False,
    source_exchange_id: str = "",
    source_market_type: str = "",
) -> dict:
    out = {
        "market": market,
        "symbol": symbol,
        "exchange_id": exchange_id,
        "market_type": market_type,
        "price": price_data.get("price", 0),
        "change": price_data.get("change", 0),
        "changePercent": price_data.get("changePercent", 0),
    }
    if cached:
        out["cached"] = True
    if stale:
        out["stale"] = True
    if price_data.get("source"):
        out["source"] = price_data.get("source")
    if source_exchange_id:
        out["source_exchange_id"] = source_exchange_id
    if source_market_type:
        out["source_market_type"] = source_market_type
    return out


def _has_price(value: object) -> bool:
    return isinstance(value, dict) and float(value.get("price") or 0) > 0


def _source_candidates(
    market: str,
    symbol: str,
    exchange_id: str,
    market_type: str,
) -> list[tuple[str, str]]:
    primary = _quote_source_context(market, exchange_id, market_type)
    candidates = [primary]
    if str(market or "").strip() == "Crypto" and primary[1] == "spot":
        base = str(symbol or "").strip().upper().split("/", 1)[0]
        if base in CRYPTO_SWAP_FIRST_BASES:
            try:
                swap_match = find_market_symbol(
                    "Crypto",
                    symbol,
                    exchange_id=primary[0],
                    market_type="swap",
                )
                if swap_match:
                    return [(primary[0], "swap")]
            except Exception as exc:
                logger.debug(
                    "Quote catalog lookup failed for %s@%s: %s",
                    symbol,
                    primary[0],
                    exc,
                )
        candidates.append((primary[0], "swap"))
    return candidates


def _decorate_price_source(
    price_data: dict,
    source_exchange_id: str,
    source_market_type: str,
) -> dict:
    decorated = dict(price_data)
    decorated["_source_exchange_id"] = source_exchange_id
    decorated["_source_market_type"] = source_market_type
    return decorated


def _actual_price_source(
    price_data: dict,
    default_exchange_id: str,
    default_market_type: str,
) -> tuple[str, str]:
    return (
        str(price_data.get("_source_exchange_id") or default_exchange_id),
        str(price_data.get("_source_market_type") or default_market_type),
    )


def _store_price(
    market: str,
    symbol: str,
    price_data: dict,
    *,
    cache_exchange_id: str,
    cache_market_type: str,
    instrument_id: str = "",
    source_exchange_id: str,
    source_market_type: str,
) -> dict:
    decorated = _decorate_price_source(
        price_data,
        source_exchange_id,
        source_market_type,
    )
    _market_cache.set(
        quote_cache_key(
            market,
            symbol,
            exchange_id=cache_exchange_id,
            market_type=cache_market_type,
            instrument_id=instrument_id,
        ),
        decorated,
        QUOTE_CACHE_TTL_SEC,
    )
    _market_cache.set(
        quote_cache_key(
            market,
            symbol,
            exchange_id=cache_exchange_id,
            market_type=cache_market_type,
            instrument_id=instrument_id,
            stale=True,
        ),
        decorated,
        QUOTE_STALE_TTL_SEC,
    )
    return decorated


def _fetch_price_data(
    market: str,
    symbol: str,
    source_exchange_id: str,
    source_market_type: str,
    instrument_id: str = "",
) -> dict:
    return guarded_cached(
        cache_key(
            "single_quote_fetch",
            market,
            source_exchange_id,
            source_market_type,
            instrument_id,
            symbol,
        ),
        lambda: kline_service.get_realtime_price(
            market,
            symbol,
            exchange_id=source_exchange_id or None,
            market_type=source_market_type or None,
            instrument_id=instrument_id or None,
        ),
        ttl_sec=QUOTE_CACHE_TTL_SEC,
        stale_ttl_sec=QUOTE_STALE_TTL_SEC,
        timeout_sec=QUOTE_FETCH_TIMEOUT_SEC,
        namespace="single_quote_fetch",
        max_concurrent=_executor_workers(),
        cache_if=_has_price,
    )


def get_single_price(
    market: str,
    symbol: str,
    exchange_id: str = "",
    market_type: str = "",
    instrument_id: str = "",
    *,
    resolved_crypto_exchange_id: str = "",
) -> dict:
    """Get one quote snapshot with fresh and stale cache fallback."""
    source_exchange_hint = exchange_id
    if str(market or "").strip() == "Crypto" and not source_exchange_hint:
        source_exchange_hint = resolved_crypto_exchange_id
    candidates = _source_candidates(market, symbol, source_exchange_hint, market_type)
    primary_exchange_id, primary_market_type = candidates[0]

    for source_exchange_id, source_market_type in candidates:
        fresh_key = quote_cache_key(
            market,
            symbol,
            exchange_id=source_exchange_id,
            market_type=source_market_type,
            instrument_id=instrument_id,
        )
        cached = _market_cache.get(fresh_key)
        if _has_price(cached):
            actual_exchange_id, actual_market_type = _actual_price_source(
                cached,
                source_exchange_id,
                source_market_type,
            )
            if (source_exchange_id, source_market_type) != candidates[0]:
                _store_price(
                    market,
                    symbol,
                    cached,
                    cache_exchange_id=primary_exchange_id,
                    cache_market_type=primary_market_type,
                    instrument_id=instrument_id,
                    source_exchange_id=actual_exchange_id,
                    source_market_type=actual_market_type,
                )
            return normalize_price_payload(
                market,
                symbol,
                cached,
                exchange_id=exchange_id,
                market_type=market_type,
                cached=True,
                source_exchange_id=actual_exchange_id,
                source_market_type=actual_market_type,
            )

        try:
            price_data = _fetch_price_data(
                market,
                symbol,
                source_exchange_id,
                source_market_type,
                instrument_id,
            )
        except RequestGuardError as exc:
            logger.info(
                "Price fetch guarded for %s:%s@%s:%s - %s",
                market,
                symbol,
                source_exchange_id,
                source_market_type,
                exc,
            )
            continue
        except Exception as exc:
            logger.error(
                "Failed to fetch price %s:%s@%s:%s - %s",
                market,
                symbol,
                source_exchange_id,
                source_market_type,
                exc,
            )
            continue

        if not _has_price(price_data):
            continue

        decorated = _store_price(
            market,
            symbol,
            price_data,
            cache_exchange_id=source_exchange_id,
            cache_market_type=source_market_type,
            instrument_id=instrument_id,
            source_exchange_id=source_exchange_id,
            source_market_type=source_market_type,
        )
        if (source_exchange_id, source_market_type) != candidates[0]:
            _store_price(
                market,
                symbol,
                decorated,
                cache_exchange_id=primary_exchange_id,
                cache_market_type=primary_market_type,
                instrument_id=instrument_id,
                source_exchange_id=source_exchange_id,
                source_market_type=source_market_type,
            )
        return normalize_price_payload(
            market,
            symbol,
            decorated,
            exchange_id=exchange_id,
            market_type=market_type,
            source_exchange_id=source_exchange_id,
            source_market_type=source_market_type,
        )

    for source_exchange_id, source_market_type in candidates:
        stale = _market_cache.get(
            quote_cache_key(
                market,
                symbol,
                exchange_id=source_exchange_id,
                market_type=source_market_type,
                instrument_id=instrument_id,
                stale=True,
            )
        )
        if _has_price(stale):
            actual_exchange_id, actual_market_type = _actual_price_source(
                stale,
                source_exchange_id,
                source_market_type,
            )
            return normalize_price_payload(
                market,
                symbol,
                stale,
                exchange_id=exchange_id,
                market_type=market_type,
                cached=True,
                stale=True,
                source_exchange_id=actual_exchange_id,
                source_market_type=actual_market_type,
            )

    return empty_price(
        market,
        symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        error="unavailable",
    )


def get_price_map(watchlist: list, timeout_sec: int = 30) -> list:
    """Fetch quote snapshots for watchlist rows in parallel."""
    results = []
    futures = {}
    resolved_crypto_exchange_id = ""
    if any(str(item.get("market") or "").strip() == "Crypto" for item in watchlist):
        resolved_crypto_exchange_id = default_crypto_exchange_id()
    for item in watchlist:
        market = item.get("market", "")
        symbol = item.get("symbol", "")
        asset_id = int(item.get("asset_id") or 0)
        exchange_id = item.get("exchange_id", "")
        market_type = item.get("market_type", "")
        instrument_id = item.get("instrument_id", "")
        if market and symbol:
            if asset_id > 0:
                future = executor.submit(
                    _get_logical_asset_price,
                    asset_id,
                    market,
                    symbol,
                    resolved_crypto_exchange_id,
                )
            else:
                future = executor.submit(
                    get_single_price,
                    market,
                    symbol,
                    exchange_id,
                    market_type,
                    instrument_id,
                    resolved_crypto_exchange_id=resolved_crypto_exchange_id,
                )
            futures[future] = (market, symbol, exchange_id, market_type, instrument_id, asset_id)

    completed = set()
    try:
        for future in as_completed(futures, timeout=timeout_sec):
            completed.add(future)
            market, symbol, exchange_id, market_type, instrument_id, asset_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("Price fetch failed: %s:%s - %s", market, symbol, exc)
                fallback = _cached_or_empty(
                    market, symbol, exchange_id, market_type, "failed", instrument_id=instrument_id,
                )
                if asset_id > 0:
                    fallback["asset_id"] = asset_id
                results.append(fallback)
    except FuturesTimeoutError:
        for future, (market, symbol, exchange_id, market_type, instrument_id, asset_id) in futures.items():
            if future not in completed:
                logger.warning("Price fetch timed out: %s:%s", market, symbol)
                fallback = _cached_or_empty(
                    market, symbol, exchange_id, market_type, "timeout", instrument_id=instrument_id,
                )
                if asset_id > 0:
                    fallback["asset_id"] = asset_id
                results.append(fallback)

    return results


def _get_logical_asset_price(
    asset_id: int,
    market: str,
    symbol: str,
    preferred_exchange_id: str,
) -> dict:
    try:
        product = resolve_research_instrument(
            asset_id,
            preferred_exchange_id=preferred_exchange_id,
            preferred_market_type="spot",
        )
    except AssetResolutionError as exc:
        out = empty_price(market, symbol, error=str(exc))
        out["asset_id"] = asset_id
        return out
    result = get_single_price(
        market,
        symbol,
        product.get("exchange_id") or "",
        product.get("market_type") or "",
        product.get("instrument_id") or "",
    )
    result["asset_id"] = asset_id
    result["source_exchange_id"] = product.get("exchange_id") or result.get("source_exchange_id") or ""
    result["source_market_type"] = product.get("market_type") or result.get("source_market_type") or ""
    result["exchange_id"] = ""
    result["market_type"] = ""
    return result


def _cached_or_empty(
    market: str,
    symbol: str,
    exchange_id: str,
    market_type: str,
    error: str,
    *,
    instrument_id: str = "",
) -> dict:
    source_exchange_id, source_market_type = _quote_source_context(
        market,
        exchange_id,
        market_type,
    )
    stale = _market_cache.get(
        quote_cache_key(
            market,
            symbol,
            exchange_id=source_exchange_id,
            market_type=source_market_type,
            instrument_id=instrument_id,
            stale=True,
        )
    )
    if isinstance(stale, dict) and float(stale.get("price") or 0) > 0:
        return normalize_price_payload(
            market,
            symbol,
            stale,
            exchange_id=exchange_id,
            market_type=market_type,
            cached=True,
            stale=True,
            source_exchange_id=source_exchange_id,
            source_market_type=source_market_type,
        )
    return empty_price(
        market,
        symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        error=error,
    )

