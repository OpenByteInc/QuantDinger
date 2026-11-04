"""Symbol search helpers for the market API."""

import json
import re
import time
from typing import Iterable

from app.data.market_symbols_seed import (
    get_hot_symbols as seed_get_hot_symbols,
    search_symbols as seed_search_symbols,
)
from app.services.symbol_name import persist_seed_name
from app.services.market.instrument_products import classify_instrument_product
from app.services.market_context import (
    canonical_crypto_symbol,
    default_crypto_exchange_id,
    normalize_exchange_id,
    normalize_market_type,
    SUPPORTED_CRYPTO_EXCHANGE_IDS,
)
from app.utils.cache import CacheManager
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

SYMBOL_SEARCH_CACHE_TTL_SEC = 21600
_market_cache = CacheManager()
_crypto_markets_cache: dict[str, dict] = {}
CRYPTO_EXCHANGE_FALLBACK_ORDER = ("binance", "bitget", "bybit", "okx", "gate", "htx")


def dedupe_symbol_results(items: Iterable[dict], limit: int) -> list:
    """Normalize, dedupe, and limit symbol search results."""
    out = []
    seen = set()
    for item in items or []:
        market = (item.get("market") or "").strip()
        symbol = (item.get("symbol") or "").strip().upper()
        name = (item.get("name") or "").strip()
        if not market or not symbol:
            continue
        exchange_id = normalize_exchange_id(item.get("exchange_id") or item.get("exchange"))
        market_type = normalize_market_type(item.get("market_type"), market=market)
        instrument_id = str(item.get("instrument_id") or item.get("instrumentId") or "").strip()
        settle_currency = str(item.get("settle_currency") or item.get("settleCurrency") or "").strip().upper()
        asset_class = str(item.get("asset_class") or "crypto").strip().lower()
        key = (market, symbol, exchange_id, market_type, instrument_id)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "market": market,
            "symbol": symbol,
            "name": name,
            "exchange_id": exchange_id,
            "market_type": market_type,
            "instrument_id": instrument_id,
            "settle_currency": settle_currency,
            "asset_class": asset_class,
            "product_type": str(item.get("product_type") or "crypto").strip().lower(),
            "api_family": str(item.get("api_family") or market_type or "spot").strip().lower(),
            "underlying_market": str(item.get("underlying_market") or "").strip(),
            "underlying_symbol": str(item.get("underlying_symbol") or "").strip().upper(),
            "product_meta": _json_object(item.get("product_meta")),
        })
        if len(out) >= limit:
            break
    return out


def search_market_symbols(
    market: str,
    keyword: str,
    limit: int = 20,
    *,
    exchange_id: str = "",
    market_type: str = "",
) -> list:
    """Search the local catalog, using external fallbacks only for equities."""
    market = (market or "").strip()
    keyword = (keyword or "").strip().upper()
    limit = max(1, int(limit or 20))
    if not market or not keyword:
        return []

    if market == "Crypto":
        exchange_id = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
        if exchange_id not in SUPPORTED_CRYPTO_EXCHANGE_IDS:
            return []
        requested_market_type = str(market_type or "").strip()
        if requested_market_type:
            normalized_market_type = normalize_market_type(requested_market_type, market=market)
            out = _search_cached_crypto_symbols(keyword, limit, exchange_id, normalized_market_type)
        else:
            result_groups = [
                _search_cached_crypto_symbols(
                    keyword,
                    limit,
                    exchange_id,
                    candidate_market_type,
                )
                for candidate_market_type in ("spot", "swap")
            ]
            out = [
                group[index]
                for index in range(max((len(group) for group in result_groups), default=0))
                for group in result_groups
                if index < len(group)
            ]
        return dedupe_symbol_results(out, limit)

    out = dedupe_symbol_results(
        seed_search_symbols(market=market, keyword=keyword, limit=limit),
        limit,
    )
    if out:
        return out

    existing = {r["symbol"] for r in out}
    if market in {"USStock", "CNStock", "HKStock"}:
        out.extend(_search_external_symbols(market, keyword, limit - len(out), existing))

    return dedupe_symbol_results(out, limit)


def find_market_symbol(
    market: str,
    symbol: str,
    *,
    exchange_id: str = "",
    market_type: str = "",
) -> dict | None:
    """Return an exact symbol match from local seed or supported external sources."""
    market = (market or "").strip()
    symbol = (symbol or "").strip().upper()
    if not market or not symbol:
        return None

    if market == "Crypto":
        exchange_id = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
        if exchange_id not in SUPPORTED_CRYPTO_EXCHANGE_IDS:
            return None
        market_type = normalize_market_type(market_type, market=market)
        rows = _search_cached_crypto_symbols(symbol, 20, exchange_id, market_type)
    elif market in {"USStock", "CNStock", "HKStock"}:
        local = dedupe_symbol_results(
            seed_search_symbols(market=market, keyword=symbol, limit=10),
            10,
        )
        exact = _first_exact_match(local, market, symbol)
        if exact:
            return exact
        rows = _search_external_symbols(market, symbol, 10, set())
    else:
        rows = dedupe_symbol_results(
            seed_search_symbols(market=market, keyword=symbol, limit=10),
            10,
        )

    return _first_exact_match(rows, market, symbol)


def get_crypto_symbol_capabilities(symbol: str, *, exchange_id: str = "") -> dict:
    """Return exact spot/swap catalog contexts for one exchange symbol."""
    canonical_symbol = canonical_crypto_symbol(symbol)
    exchange_id = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
    if not canonical_symbol or exchange_id not in SUPPORTED_CRYPTO_EXCHANGE_IDS:
        return {
            "symbol": canonical_symbol,
            "exchange_id": exchange_id,
            "market_types": [],
            "contexts": [],
        }

    contexts = []
    for market_type in ("spot", "swap"):
        rows = dedupe_symbol_results(
            _search_cached_crypto_symbols(canonical_symbol, 10, exchange_id, market_type),
            10,
        )
        exact = _first_exact_match(rows, "Crypto", canonical_symbol)
        if exact:
            contexts.append(exact)
    return {
        "symbol": canonical_symbol,
        "exchange_id": exchange_id,
        "market_types": [item["market_type"] for item in contexts],
        "contexts": contexts,
    }


def find_available_crypto_symbol(
    symbol: str,
    *,
    preferred_exchange_id: str = "",
    preferred_market_type: str = "spot",
) -> dict | None:
    """Find a crypto pair on a concrete supported exchange and market type."""
    preferred_exchange_id = normalize_exchange_id(preferred_exchange_id) or default_crypto_exchange_id()
    preferred_market_type = normalize_market_type(preferred_market_type, market="Crypto")
    candidates = [(preferred_exchange_id, preferred_market_type)]
    candidates.append((preferred_exchange_id, "swap" if preferred_market_type == "spot" else "spot"))
    for exchange_id in CRYPTO_EXCHANGE_FALLBACK_ORDER:
        for market_type in ("spot", "swap"):
            candidate = (exchange_id, market_type)
            if candidate not in candidates:
                candidates.append(candidate)

    for exchange_id, market_type in candidates:
        matched = find_market_symbol(
            "Crypto",
            symbol,
            exchange_id=exchange_id,
            market_type=market_type,
        )
        if matched:
            return matched
    return None


def get_hot_symbols(market: str, limit: int = 10) -> list:
    """Return curated hot symbols backed by a concrete market-data identity."""
    market = (market or "").strip()
    limit = max(1, int(limit or 10))
    curated = seed_get_hot_symbols(market=market, limit=limit)
    if market != "Crypto":
        return curated

    available = []
    for item in curated:
        symbol = canonical_crypto_symbol(item.get("symbol"))
        matched = find_available_crypto_symbol(
            symbol,
            preferred_exchange_id=default_crypto_exchange_id(),
            preferred_market_type="spot",
        )
        if not matched:
            continue
        matched["name"] = (item.get("name") or matched.get("name") or symbol).strip()
        available.append(matched)
    return dedupe_symbol_results(available, limit)


def _first_exact_match(items: Iterable[dict], market: str, symbol: str) -> dict | None:
    want_market = (market or "").strip()
    want_symbol = (symbol or "").strip().upper()
    for item in items or []:
        item_market = (item.get("market") or "").strip()
        item_symbol = (item.get("symbol") or "").strip().upper()
        if item_market == want_market and item_symbol == want_symbol:
            return dict(item)
    return None


def _search_crypto_exchange(
    keyword: str,
    limit: int,
    existing: set,
    exchange_id: str,
    market_type: str,
) -> list:
    """Search exchange markets for active USDT crypto pairs."""
    if limit <= 0:
        return []
    try:
        from app.config.data_sources import CryptoPublicConfig
        from app.data_sources.native_crypto import create_native_crypto_client

        now = time.time()
        exchange_id = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
        market_type = normalize_market_type(market_type, market="Crypto")
        cache_key = f"{exchange_id}:{market_type}"
        cached = _crypto_markets_cache.get(cache_key) or {}
        if cached.get("data") and now - float(cached.get("ts") or 0) < 14400:
            markets = cached["data"]
        else:
            ex = create_native_crypto_client(
                exchange_id,
                market_type,
                timeout_ms=max(int(CryptoPublicConfig.TIMEOUT or 0), 30000),
                proxy=CryptoPublicConfig.PROXY,
            )
            ex.load_markets()
            markets = []
            for sym, info in ex.markets.items():
                if not info.get("active") or info.get("quote", "") != "USDT":
                    continue
                is_target_type = bool(info.get("spot")) if market_type == "spot" else bool(info.get("swap"))
                if not is_target_type:
                    continue
                canonical_symbol = canonical_crypto_symbol(sym)
                profile = classify_instrument_product(
                    info,
                    exchange_id=exchange_id,
                    market_type=market_type,
                    symbol=canonical_symbol,
                    instrument_id=str(info.get("id") or sym),
                )
                markets.append({
                    "symbol": canonical_symbol,
                    "base": info.get("base", ""),
                    "name": info.get("base", sym),
                    "exchange_id": exchange_id,
                    "market_type": market_type,
                    "instrument_id": str(info.get("id") or sym),
                    "settle_currency": str(info.get("settle") or info.get("quote") or "").upper(),
                    **profile.as_dict(),
                })
            _crypto_markets_cache[cache_key] = {"data": markets, "ts": now}
            _persist_crypto_markets(markets, exchange_id, market_type)
            logger.info(
                "Cached %d crypto instruments from %s/%s",
                len(markets),
                exchange_id,
                market_type,
            )

        kw = keyword.upper().replace("/USDT", "").replace("/", "")
        results = []
        for item in markets:
            symbol = item["symbol"]
            if symbol in existing:
                continue
            if kw in item["base"].upper() or kw in symbol.upper():
                results.append({"market": "Crypto", **item})
                if len(results) >= limit:
                    break
        return results
    except Exception as exc:
        logger.debug("Crypto exchange symbol search failed: %s", exc)
        return []


def _search_cached_crypto_symbols(
    keyword: str,
    limit: int,
    exchange_id: str,
    market_type: str,
) -> list:
    pattern = f"%{str(keyword or '').strip().upper()}%"
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT market, symbol, name, exchange AS exchange_id, market_type,
                       instrument_id, settle_currency, asset_class, product_type,
                       api_family, underlying_market, underlying_symbol, product_meta
                FROM qd_market_symbols
                WHERE market = 'Crypto' AND is_active = 1
                  AND exchange = ? AND market_type = ?
                  AND (UPPER(symbol) LIKE ? OR UPPER(name) LIKE ?)
                ORDER BY CASE WHEN UPPER(symbol) = UPPER(?) THEN 0 ELSE 1 END,
                         sort_order DESC, symbol ASC
                LIMIT ?
                """,
                (exchange_id, market_type, pattern, pattern, keyword, max(1, limit)),
            )
            rows = cur.fetchall() or []
            cur.close()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug("Cached crypto symbol search failed: %s", exc)
        return []


def _persist_crypto_markets(markets: list, exchange_id: str, market_type: str) -> None:
    if not markets:
        return
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_market_symbols SET is_active = 0
                WHERE market = 'Crypto' AND exchange = ? AND market_type = ?
                """,
                (exchange_id, market_type),
            )
            for item in markets:
                cur.execute(
                    """
                    INSERT INTO qd_market_symbols (
                        market, symbol, name, exchange, market_type, instrument_id,
                        settle_currency, currency, asset_class, product_type, api_family,
                        underlying_market, underlying_symbol, product_meta, metadata_updated_at,
                        is_active, is_hot, sort_order
                    )
                    VALUES ('Crypto', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, NOW(), 1, 0, 0)
                    ON CONFLICT (market, symbol, exchange, market_type, instrument_id) DO UPDATE
                      SET name = EXCLUDED.name,
                          settle_currency = EXCLUDED.settle_currency,
                          currency = EXCLUDED.currency,
                          asset_class = EXCLUDED.asset_class,
                          product_type = EXCLUDED.product_type,
                          api_family = EXCLUDED.api_family,
                          underlying_market = EXCLUDED.underlying_market,
                          underlying_symbol = EXCLUDED.underlying_symbol,
                          product_meta = EXCLUDED.product_meta,
                          metadata_updated_at = NOW(),
                          is_active = 1
                    """,
                    (
                        item.get("symbol") or "",
                        item.get("name") or item.get("base") or "",
                        exchange_id,
                        market_type,
                        item.get("instrument_id") or "",
                        item.get("settle_currency") or "",
                        item.get("settle_currency") or "",
                        item.get("asset_class") or "crypto",
                        item.get("product_type") or "crypto",
                        item.get("api_family") or market_type,
                        item.get("underlying_market") or "",
                        item.get("underlying_symbol") or "",
                        json.dumps(item.get("product_meta") or {}, ensure_ascii=False),
                    ),
                )
            db.commit()
            cur.close()
    except Exception as exc:
        logger.debug("Crypto market catalog persist failed for %s/%s: %s", exchange_id, market_type, exc)


def _classify_asset(info: dict) -> str:
    profile = classify_instrument_product(info)
    raw = info.get("info") if isinstance(info.get("info"), dict) else {}
    if (
        str(raw.get("isRwa") or "").strip().upper() == "YES"
        and not any(
            token in " ".join(str(value or "") for value in raw.values()).lower()
            for token in ("stock", "xstock", "equity")
        )
    ):
        return "rwa"
    return profile.asset_class


def _json_object(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _df_records(df) -> list:
    if df is None:
        return []
    try:
        return df.to_dict("records")
    except Exception:
        return []


def _search_cn_akshare(keyword: str, limit: int) -> list:
    if limit <= 0:
        return []
    try:
        import akshare as ak  # type: ignore

        rows = _df_records(ak.stock_info_a_code_name())
        kw = keyword.strip().upper()
        out = []
        for row in rows:
            symbol = str(row.get("code") or row.get("代码") or "").strip().upper()
            name = str(row.get("name") or row.get("名称") or "").strip()
            if not symbol or not name:
                continue
            if kw in symbol or kw in name.upper():
                out.append({"market": "CNStock", "symbol": symbol, "name": name})
                persist_seed_name("CNStock", symbol, name)
                if len(out) >= limit:
                    break
        return out
    except Exception as exc:
        logger.debug("CN AkShare symbol search failed: %s", exc)
        return []


def _search_hk_akshare(keyword: str, limit: int) -> list:
    if limit <= 0:
        return []
    try:
        import akshare as ak  # type: ignore

        rows = _df_records(ak.stock_hk_spot_em())
        kw = keyword.strip().upper()
        out = []
        for row in rows:
            raw_symbol = str(row.get("代码") or row.get("code") or row.get("symbol") or "").strip().upper()
            name = str(row.get("名称") or row.get("name") or "").strip()
            if not raw_symbol or not name:
                continue
            symbol = re.sub(r"[^0-9]", "", raw_symbol).zfill(5)
            if kw in raw_symbol or kw in symbol or kw in name.upper():
                out.append({"market": "HKStock", "symbol": symbol, "name": name})
                persist_seed_name("HKStock", symbol, name)
                if len(out) >= limit:
                    break
        return out
    except Exception as exc:
        logger.debug("HK AkShare symbol search failed: %s", exc)
        return []


def _search_us_yahoo(keyword: str, limit: int) -> list:
    if limit <= 0:
        return []
    try:
        import requests

        resp = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": keyword, "quotesCount": limit, "newsCount": 0},
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code != 200:
            return []
        out = []
        for quote in (resp.json() or {}).get("quotes") or []:
            quote_type = str(quote.get("quoteType") or "").upper()
            if quote_type not in {"EQUITY", "ETF"}:
                continue
            symbol = str(quote.get("symbol") or "").strip().upper()
            name = str(quote.get("shortname") or quote.get("longname") or quote.get("name") or "").strip()
            exchange = str(quote.get("exchange") or "").upper()
            if not symbol or not name:
                continue
            if exchange in {"HKG", "SHH", "SHZ"} or symbol.endswith((".HK", ".SS", ".SZ")):
                continue
            out.append({"market": "USStock", "symbol": symbol, "name": name})
            persist_seed_name("USStock", symbol, name)
            if len(out) >= limit:
                break
        return out
    except Exception as exc:
        logger.debug("Yahoo symbol search failed: %s", exc)
        return []


def _search_external_symbols(market: str, keyword: str, limit: int, existing: set) -> list:
    cache_key = f"symbol_search:{market}:{keyword.strip().upper()}:{limit}"
    cached = _market_cache.get(cache_key)
    if isinstance(cached, list):
        return [r for r in cached if r.get("symbol") not in existing][:limit]

    if market == "CNStock":
        rows = _search_cn_akshare(keyword, limit)
    elif market == "HKStock":
        rows = _search_hk_akshare(keyword, limit)
    elif market == "USStock":
        rows = _search_us_yahoo(keyword, limit)
    else:
        rows = []

    rows = dedupe_symbol_results(rows, limit)
    if rows:
        _market_cache.set(cache_key, rows, SYMBOL_SEARCH_CACHE_TTL_SEC)
    return [r for r in rows if r.get("symbol") not in existing][:limit]

