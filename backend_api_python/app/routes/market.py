"""
Market API routes (local-only).
Provides watchlist, market metadata, symbol search, and pricing helpers for the frontend.
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
import os
import re
import traceback
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Optional

from app.services.kline import KlineService
from app.utils.logger import get_logger
from app.utils.cache import CacheManager
from app.utils.db import get_db_connection
from app.utils.config_loader import load_addon_config
from app.utils.auth import login_required
from app.data.market_symbols_seed import (
    get_hot_symbols as seed_get_hot_symbols,
    search_symbols as seed_search_symbols,
    get_symbol_name as seed_get_symbol_name
)
from app.services.symbol_name import resolve_symbol_name, persist_seed_name, normalize_crypto_symbol
from app.utils.market_visibility import is_market_visible, filter_market_items

logger = get_logger(__name__)

# Allowed market values. Kept in sync with the strategy / data-source layer so
# we never persist a watchlist row whose market the rest of the system can't
# understand. Frontend's market-tab list must stay a subset of this set.
VALID_MARKETS = frozenset({
    'Crypto', 'USStock', 'CNStock', 'HKStock', 'Forex', 'Futures', 'MOEX',
})

# A-share ticker format: exactly 6 digits (e.g. 000159, 600519, 300750).
_RE_CN_A_SHARE = re.compile(r'^\d{6}$')


def _validate_watchlist_pair(market: str, symbol: str) -> Optional[str]:
    """Validate a (market, symbol) pair before persisting it.

    Returns an error message string if the combination is implausible,
    otherwise ``None``. This is intentionally conservative: it rejects the
    cases that have actually caused noisy upstream API calls (A-share codes
    saved as Crypto, ``.HK`` tickers saved as US stocks, etc.) and leaves the
    long tail of less-distinguishable symbols (US tickers, FX pairs) to the
    downstream data-source adapters.
    """
    if market not in VALID_MARKETS:
        return f"Unsupported market '{market}'. Must be one of: {', '.join(sorted(VALID_MARKETS))}"
    if not symbol:
        return "Empty symbol"

    # Pure 6-digit codes are CN A-share tickers; nothing else uses this exact
    # shape. Routing one of these to Crypto/USStock makes binance / yfinance
    # spam fail-not-found and bleeds rate limits on TwelveData via fallbacks.
    if _RE_CN_A_SHARE.match(symbol) and market != 'CNStock':
        return (
            f"Symbol '{symbol}' looks like a Chinese A-share code; "
            f"market must be CNStock, not {market}"
        )
    # Explicit Hong Kong suffix must be HKStock.
    if symbol.endswith('.HK') and market != 'HKStock':
        return (
            f"Symbol '{symbol}' looks like a Hong Kong stock; "
            f"market must be HKStock, not {market}"
        )
    # Crypto symbols are canonicalised to BASE/QUOTE upstream of this check
    # (see add_watchlist / remove_watchlist). If we still land here without a
    # ``/`` it means normalise_crypto_symbol produced something unusable —
    # better to refuse than to silently persist a malformed pair.
    if market == 'Crypto' and '/' not in symbol:
        return (
            f"Crypto symbol '{symbol}' must be a BASE/QUOTE pair (e.g. BTC/USDT). "
            f"If you meant the bare base, retry — the server now expands it for you."
        )
    return None


# Markets where we trust the name-resolution chain enough to reject any symbol
# whose name cannot be resolved. For equities the seed table + yfinance /
# Finnhub / Tencent quote cover the universe well, so "no name" reliably means
# "doesn't exist in this market". For Crypto/Forex/Futures/MOEX the resolver
# returns the symbol itself as a fallback (no real existence signal), so we
# skip the existence check there and rely on format validation alone.
_NAME_REQUIRED_MARKETS = frozenset({'USStock', 'CNStock', 'HKStock'})

# Bounded executor for name lookups so a slow upstream (yfinance .info,
# Tencent quote, Finnhub) can never park the /watchlist/add endpoint
# indefinitely. Daemon threads so we don't block shutdown.
_name_resolve_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix='watchlist-name-resolve',
)


def _resolve_symbol_name_bounded(market: str, symbol: str, timeout_sec: float = 4.0) -> Optional[str]:
    """Call ``resolve_symbol_name`` with a hard wall-clock cap.

    Returns the resolved name on success, ``None`` on timeout or any internal
    failure. The background thread continues running even after we give up
    waiting (yfinance/HTTP clients aren't cancellable), but it's a daemon
    thread so it won't keep the process alive.
    """
    try:
        future = _name_resolve_executor.submit(resolve_symbol_name, market, symbol)
        return future.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        logger.info(
            "Symbol name resolve timed out after %.1fs for %s:%s",
            timeout_sec, market, symbol,
        )
        return None
    except Exception as e:
        logger.debug("Symbol name resolve raised for %s:%s: %s", market, symbol, e)
        return None


# Note: the symbol-name self-learning cache used to live here as
# ``_maybe_cache_symbol_name``. It now lives in ``app.services.symbol_name``
# as ``persist_seed_name`` and is invoked automatically by
# ``resolve_symbol_name`` whenever an external source returns a real name.
# Routes only need to call it explicitly when they have a name that didn't
# come from ``resolve_symbol_name`` (e.g. a frontend-supplied ``name_in``
# picked from search results).

market_blp = Blueprint('market', __name__)
kline_service = KlineService()
cache = CacheManager()

# Thread pool for parallel price fetching.
# Each task may grab a DB connection, so keep this well below DB_POOL_MAX.
# Tunable via MARKET_EXECUTOR_WORKERS env.
def _market_executor_workers() -> int:
    try:
        v = int(os.getenv("MARKET_EXECUTOR_WORKERS", "6"))
        return v if v > 0 else 6
    except Exception:
        return 6

executor = ThreadPoolExecutor(max_workers=_market_executor_workers())

def _now_ts() -> int:
    return int(time.time())

def _normalize_symbol(symbol: str) -> str:
    return (symbol or '').strip().upper()

def _ensure_watchlist_table():
    # Table is created by db schema init; this is only a sanity hook.
    return True

@market_blp.route('/config', methods=['GET'])
def get_public_config():
    """
    Public config for frontend (local mode).
    Mirrors the old PHP `/addons/quantdinger/index/getConfig` shape.
    """
    try:
        cfg = load_addon_config()
        models = (cfg.get('ai', {}) or {}).get('models')
        if not isinstance(models, dict) or not models:
            # Fallback defaults (offline friendly)
            models = {
                # Keep some legacy defaults
                'openai/gpt-4o': 'GPT-4o',

                # Unified frontend model list (OpenRouter-style ids)
                'x-ai/grok-code-fast-1': 'xAI: Grok Code Fast 1',
                'x-ai/grok-4-fast': 'xAI: Grok 4 Fast',
                'x-ai/grok-4.1-fast': 'xAI: Grok 4.1 Fast',
                'google/gemini-2.5-flash': 'Google: Gemini 2.5 Flash',
                'google/gemini-2.0-flash-001': 'Google: Gemini 2.0 Flash',
                'google/gemini-3-pro-preview': 'Google: Gemini 3 Pro Preview',
                'google/gemini-2.5-flash-lite': 'Google: Gemini 2.5 Flash Lite',
                'google/gemini-2.5-pro': 'Google: Gemini 2.5 Pro',
                'openai/gpt-4o-mini': 'OpenAI: GPT-4o-mini',
                'openai/gpt-5-mini': 'OpenAI: GPT-5 Mini',
                'openai/gpt-4.1-mini': 'OpenAI: GPT-4.1 Mini',
                'deepseek/deepseek-v3.2': 'DeepSeek: DeepSeek V3.2',
                'minimax/minimax-m2': 'MiniMax: MiniMax M2',
                'anthropic/claude-sonnet-4': 'Anthropic: Claude Sonnet 4',
                'anthropic/claude-sonnet-4.5': 'Anthropic: Claude Sonnet 4.5',
                'anthropic/claude-opus-4.5': 'Anthropic: Claude Opus 4.5',
                'anthropic/claude-haiku-4.5': 'Anthropic: Claude Haiku 4.5',
                'z-ai/glm-4.6': 'Z.AI: GLM 4.6',
            }
        return jsonify({'code': 1, 'msg': 'success', 'data': {'models': models, 'qdt_cost': {}}})
    except Exception as e:
        logger.error(f"get_public_config failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500

@market_blp.route('/types', methods=['GET'])
def get_market_types():
    """Return supported market types for the add-watchlist modal.

    Visibility is controlled by the centralised :mod:`app.utils.market_visibility`
    rules — primarily the ``ENABLED_MARKETS`` whitelist, with
    ``SHOW_CN_STOCK`` / ``SHOW_HK_STOCK`` kept for back-compat. The radar
    (``/api/global-market/opportunities``) and the Agent API
    (``/api/agent/v1/markets``) read from the same helper so all three
    user-facing market lists stay in lock-step.
    """
    # Keep a stable UX order; CN/HK near US; MOEX last (niche vs crypto/FX/futures).
    desired_order = ['USStock', 'CNStock', 'HKStock', 'Crypto', 'Forex', 'Futures', 'MOEX']
    order_rank = {v: i for i, v in enumerate(desired_order)}

    def _normalize_item(x):
        # Expected: {value: 'USStock', i18nKey: '...'}
        if isinstance(x, dict):
            v = (x.get('value') or '').strip()
            if not v:
                return None
            return {
                'value': v,
                'i18nKey': x.get('i18nKey') or f'dashboard.analysis.market.{v}'
            }
        if isinstance(x, str):
            v = x.strip()
            if not v:
                return None
            return {'value': v, 'i18nKey': f'dashboard.analysis.market.{v}'}
        return None

    def _sort_items(items):
        # Keep unknown market types after known ones, stable by original order.
        out = []
        for it in items or []:
            norm = _normalize_item(it)
            if norm:
                out.append(norm)
        out.sort(key=lambda it: (order_rank.get(it['value'], 10_000)))
        return out

    cfg = load_addon_config()
    data = (cfg.get('market', {}) or {}).get('types')

    # Normalize & force desired order (even if config overrides the list order).
    if isinstance(data, list) and data:
        data = _sort_items(data)
    else:
        data = _sort_items(desired_order)

    data = filter_market_items(data, key='value')
    return jsonify({'code': 1, 'msg': 'success', 'data': data})


@market_blp.route('/menuFooterConfig', methods=['GET'])
def get_menu_footer_config():
    """
    Compatibility stub for old PHP `getMenuFooterConfig`.
    Frontend can also hardcode this locally; this endpoint remains for completeness.
    """
    data = {
        'contact': {
            'support_url': 'https://github.com/',
            'feature_request_url': 'https://github.com/',
            'email': 'support@example.com',
            'live_chat_url': 'https://github.com/'
        },
        'social_accounts': [
            {'name': 'GitHub', 'icon': 'github', 'url': 'https://github.com/'},
            {'name': 'X', 'icon': 'x', 'url': 'https://x.com/'}
        ],
        'legal': {
            'user_agreement': '',
            'privacy_policy': ''
        },
        'copyright': '© 2025-2026 QuantDinger'
    }
    return jsonify({'code': 1, 'msg': 'success', 'data': data})

@market_blp.route('/symbols/search', methods=['GET'])
def search_symbols():
    """
    Lightweight symbol search.
    DB seed first; for Crypto, falls back to exchange market list when DB yields few results.
    """
    try:
        market = (request.args.get('market') or '').strip()
        keyword = (request.args.get('keyword') or '').strip().upper()
        limit = int(request.args.get('limit') or 20)

        if not market or not keyword:
            return jsonify({'code': 1, 'msg': 'success', 'data': []})

        out = seed_search_symbols(market=market, keyword=keyword, limit=limit)

        if market == 'Crypto' and len(out) < 3:
            extra = _search_crypto_exchange(keyword, limit - len(out), {r['symbol'] for r in out})
            out.extend(extra)

        return jsonify({'code': 1, 'msg': 'success', 'data': out})
    except Exception as e:
        logger.error(f"search_symbols failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


_crypto_markets_cache: dict = {"data": None, "ts": 0}


def _search_crypto_exchange(keyword: str, limit: int, existing: set) -> list:
    """
    Dynamically search exchange (via CCXT) for crypto pairs matching keyword.
    Caches the full market list for 4 hours to avoid repeated API calls.
    """
    if limit <= 0:
        return []
    try:
        import ccxt  # type: ignore
        from app.config.data_sources import CCXTConfig

        now = time.time()
        if _crypto_markets_cache["data"] and now - _crypto_markets_cache["ts"] < 14400:
            markets = _crypto_markets_cache["data"]
        else:
            exchange_cls = getattr(ccxt, CCXTConfig.DEFAULT_EXCHANGE, None) or ccxt.gate
            ex = exchange_cls()
            ex.load_markets()
            markets = []
            for sym, info in ex.markets.items():
                if not info.get("active"):
                    continue
                quote = info.get("quote", "")
                if quote != "USDT":
                    continue
                markets.append({
                    "symbol": sym,
                    "base": info.get("base", ""),
                    "name": info.get("base", sym),
                })
            _crypto_markets_cache["data"] = markets
            _crypto_markets_cache["ts"] = now
            logger.info("Cached %d USDT crypto pairs from %s", len(markets), CCXTConfig.DEFAULT_EXCHANGE)

        kw = keyword.upper().replace("/USDT", "").replace("/", "")
        results = []
        for m in markets:
            sym = m["symbol"]
            if sym in existing:
                continue
            base_up = m["base"].upper()
            if kw in base_up or kw in sym.upper():
                results.append({"market": "Crypto", "symbol": sym, "name": m["name"]})
                if len(results) >= limit:
                    break
        return results
    except Exception as e:
        logger.debug("_search_crypto_exchange failed: %s", e)
        return []

@market_blp.route('/symbols/hot', methods=['GET'])
def get_hot_symbols():
    """Return a small curated hot list per market (local-only)."""
    try:
        market = (request.args.get('market') or '').strip()
        limit = int(request.args.get('limit') or 10)
        hot = seed_get_hot_symbols(market=market, limit=limit)
        return jsonify({'code': 1, 'msg': 'success', 'data': hot})
    except Exception as e:
        logger.error(f"get_hot_symbols failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500

@market_blp.route('/watchlist/get', methods=['GET'])
@login_required
def get_watchlist():
    """Get watchlist for the current user."""
    try:
        user_id = g.user_id
        _ensure_watchlist_table()
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT id, market, symbol, name FROM qd_watchlist WHERE user_id = ? ORDER BY id DESC",
                (user_id,)
            )
            rows = cur.fetchall() or []

            # Backfill display names for legacy rows (name empty or equals symbol).
            # This keeps UI consistent without requiring users to re-add items.
            for row in rows:
                try:
                    market = row.get('market')
                    symbol = row.get('symbol')
                    current_name = (row.get('name') or '').strip()
                    if not market or not symbol:
                        continue
                    if current_name and current_name != symbol:
                        continue
                    resolved = resolve_symbol_name(market, symbol) or seed_get_symbol_name(market, symbol)
                    if resolved and resolved != current_name:
                        row['name'] = resolved
                        cur.execute(
                            "UPDATE qd_watchlist SET name = ?, updated_at = NOW() WHERE user_id = ? AND market = ? AND symbol = ?",
                            (resolved, user_id, market, symbol)
                        )
                        # resolve_symbol_name already seeded qd_market_symbols
                        # for us when the name came from an external source, so
                        # nothing more to do here.
                except Exception:
                    continue
            db.commit()
            cur.close()
        return jsonify({'code': 1, 'msg': 'success', 'data': rows})
    except Exception as e:
        logger.error(f"get_watchlist failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500

@market_blp.route('/watchlist/add', methods=['POST'])
@login_required
def add_watchlist():
    """Add a symbol to watchlist for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        market = (data.get('market') or '').strip()
        symbol = _normalize_symbol(data.get('symbol'))
        name_in = (data.get('name') or '').strip()
        if not market or not symbol:
            return jsonify({'code': 0, 'msg': 'Missing market or symbol', 'data': None}), 400

        # Canonicalise Crypto to BASE/QUOTE *before* validation and storage so
        # the DB never holds a mix of "BTC" and "BTC/USDT" rows for the same
        # underlying pair. Other markets pass through unchanged.
        if market == 'Crypto':
            symbol = normalize_crypto_symbol(symbol)

        # Reject implausible (market, symbol) pairs at the edge. Without this,
        # the frontend's "search returned nothing → reuse current tab as market"
        # fallback (see ai-analysis/index.vue searchSymbolsInModal) silently
        # persisted things like {market='Crypto', symbol='000159'}, which then
        # caused recurring binance / yfinance / TwelveData "symbol not found"
        # noise and burned through paid API quota every polling tick.
        validation_err = _validate_watchlist_pair(market, symbol)
        if validation_err:
            logger.info(
                "Rejecting watchlist add for user %s: %s",
                user_id, validation_err,
            )
            return jsonify({'code': 0, 'msg': validation_err, 'data': None}), 400

        # Existence check: try to resolve a display name for the symbol. For
        # equity markets we treat "no name" as "does not exist in this market"
        # and refuse to persist the row — this is the strongest defense against
        # users still managing to bypass the format check (e.g. by typing a
        # CN A-share that doesn't actually exist, like "999999"). The resolver
        # itself is bounded by a wall-clock timeout so a slow upstream can't
        # park this endpoint.
        resolved = _resolve_symbol_name_bounded(market, symbol)
        if not resolved:
            # seed table is the offline fallback; cheap and synchronous.
            resolved = seed_get_symbol_name(market, symbol)

        if not resolved and market in _NAME_REQUIRED_MARKETS:
            err = (
                f"Symbol '{symbol}' not found on {market}. "
                f"Please verify the ticker and market, or pick from search results."
            )
            logger.info("Rejecting watchlist add for user %s: %s", user_id, err)
            return jsonify({'code': 0, 'msg': err, 'data': None}), 400

        # Prefer frontend-provided name (already shown to the user in the
        # search result list); fall back to the resolved name; finally the
        # symbol itself for markets where resolution returns the ticker.
        name = name_in or resolved or symbol

        # Self-learning seed: if the user picked a name from the search-result
        # list (``name_in``), that value never flows through resolve_symbol_name
        # so it wouldn't be auto-cached. Persist it explicitly here. When the
        # name actually came from the resolver, this is a no-op (the resolver
        # already wrote it) — the ON CONFLICT branch short-circuits.
        persist_seed_name(market, symbol, name)

        with get_db_connection() as db:
            cur = db.cursor()
            # Insert or update (PostgreSQL UPSERT)
            cur.execute(
                """
                INSERT INTO qd_watchlist (user_id, market, symbol, name, created_at, updated_at) 
                VALUES (?, ?, ?, ?, NOW(), NOW())
                ON CONFLICT(user_id, market, symbol) DO UPDATE SET
                    name = excluded.name,
                    updated_at = NOW()
                """,
                (user_id, market, symbol, name)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"add_watchlist failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500

@market_blp.route('/watchlist/remove', methods=['POST'])
@login_required
def remove_watchlist():
    """Remove a symbol from watchlist for the current user.

    The watchlist table has ``UNIQUE(user_id, market, symbol)`` but a long-
    standing bug here only matched on ``(user_id, symbol)``, so a removal could
    in principle wipe rows across markets (e.g. a manually-added ``BTC`` row
    in CNStock vs Crypto). We now require ``market`` in the request and use
    it in the ``WHERE`` clause. For Crypto we additionally canonicalise the
    symbol so the legacy clients still sending ``"BTC"`` resolve to the
    canonical ``"BTC/USDT"`` row written by the new ``add_watchlist`` logic.
    """
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        market = (data.get('market') or '').strip()
        raw_symbol = _normalize_symbol(data.get('symbol'))
        if not raw_symbol:
            return jsonify({'code': 0, 'msg': 'Missing symbol', 'data': None}), 400

        # For Crypto, the new write path canonicalises everything to BASE/QUOTE
        # before INSERT, so for any row created after stage A the target shape
        # is the normalised one. But legacy rows can still be stored as the
        # bare base ("BTC") — the frontend reads that raw value back from the
        # DB and sends it here on delete. Without a fallback we'd canonicalise
        # to "BTC/USDT" and silently fail to delete the legacy "BTC" row,
        # leaving the user clicking a no-op delete button.
        canonical_symbol = (
            normalize_crypto_symbol(raw_symbol) if market == 'Crypto' else raw_symbol
        )

        with get_db_connection() as db:
            cur = db.cursor()
            deleted = 0
            if market:
                cur.execute(
                    "DELETE FROM qd_watchlist WHERE user_id = ? AND market = ? AND symbol = ?",
                    (user_id, market, canonical_symbol)
                )
                deleted = cur.rowcount or 0

                # Legacy fallback: stage A canonicalised the input to BASE/QUOTE,
                # but the row we want to delete might predate that change and
                # still be stored as the user-supplied raw form ("BTC", "BTCUSDT").
                # Try again with the raw input when the canonical delete found
                # nothing. Order matters: canonical first so a clean DB never
                # touches the legacy branch.
                if deleted == 0 and canonical_symbol != raw_symbol:
                    cur.execute(
                        "DELETE FROM qd_watchlist WHERE user_id = ? AND market = ? AND symbol = ?",
                        (user_id, market, raw_symbol)
                    )
                    if (cur.rowcount or 0) > 0:
                        logger.info(
                            "remove_watchlist: deleted legacy row user=%s market=%s "
                            "symbol=%s (canonical=%s)",
                            user_id, market, raw_symbol, canonical_symbol,
                        )
            else:
                # Back-compat fallback for legacy clients that never sent
                # ``market`` (pre-A-stage frontend). Logged so we can spot and
                # eventually remove this branch once the deployed frontend is
                # known to always include market.
                logger.info(
                    "remove_watchlist called without market (user=%s, symbol=%s); "
                    "falling back to symbol-only delete for back-compat",
                    user_id, raw_symbol,
                )
                cur.execute(
                    "DELETE FROM qd_watchlist WHERE user_id = ? AND symbol = ?",
                    (user_id, raw_symbol)
                )
            db.commit()
            cur.close()
        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"remove_watchlist failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


def get_single_price(market: str, symbol: str) -> dict:
    """获取单个标的的价格数据"""
    try:
        # 使用 get_realtime_price 获取实时价格（内部已有30秒缓存）
        # 相比原先的 '1D' K线逻辑，这能更及时地反映 Crypto 等 24h 市场的变化
        price_data = kline_service.get_realtime_price(market, symbol)
        
        return {
            'market': market,
            'symbol': symbol,
            'price': price_data.get('price', 0),
            'change': price_data.get('change', 0),
            'changePercent': price_data.get('changePercent', 0)
        }
    except Exception as e:
        logger.error(f"Failed to fetch price {market}:{symbol} - {str(e)}")
        return {
            'market': market,
            'symbol': symbol,
            'price': 0,
            'change': 0,
            'changePercent': 0
        }


@market_blp.route('/watchlist/prices', methods=['GET'])
@login_required
def get_watchlist_prices():
    """
    Get realtime prices for the current user's watchlist.

    Historical contract accepted a `watchlist=[{market,symbol},...]` query
    parameter, which (combined with no auth) let any caller fan out arbitrary
    market/symbol combinations to upstream data providers. We now ignore that
    parameter and read the authenticated user's watchlist straight from the
    DB. The query param is still parsed for back-compat logging only.
    """
    try:
        user_id = g.user_id

        # Back-compat: log if a legacy client still sends the query param so we
        # can spot stale frontends in deployment. The value is otherwise ignored.
        legacy_param = request.args.get('watchlist')
        if legacy_param:
            try:
                legacy_count = len(json.loads(legacy_param) or [])
            except Exception:
                legacy_count = -1
            logger.debug(
                "watchlist/prices: ignoring legacy client-supplied list (len=%s, user=%s)",
                legacy_count, user_id,
            )

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT market, symbol FROM qd_watchlist WHERE user_id = ?",
                (user_id,),
            )
            rows = cur.fetchall() or []
            cur.close()

        watchlist = [
            {'market': (r.get('market') or '').strip(), 'symbol': (r.get('symbol') or '').strip()}
            for r in rows
        ]

        if not watchlist:
            return jsonify({'code': 1, 'msg': 'success', 'data': []})

        results = []
        futures = {}
        for item in watchlist:
            market = item.get('market', '')
            symbol = item.get('symbol', '')

            if market and symbol:
                future = executor.submit(get_single_price, market, symbol)
                futures[future] = (market, symbol)
        
        # 收集结果（带超时保护）
        completed_futures = set()
        try:
            for future in as_completed(futures, timeout=30):
                completed_futures.add(future)
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    market, symbol = futures[future]
                    logger.warning(f"Price fetch failed: {market}:{symbol} - {str(e)}")
                    results.append({
                        'market': market,
                        'symbol': symbol,
                        'price': 0,
                        'change': 0,
                        'changePercent': 0
                    })
        except TimeoutError:
            # 超时时，为未完成的任务添加默认结果
            for future, (market, symbol) in futures.items():
                if future not in completed_futures:
                    logger.warning(f"Price fetch timed out: {market}:{symbol}")
                    results.append({
                        'market': market,
                        'symbol': symbol,
                        'price': 0,
                        'change': 0,
                        'changePercent': 0,
                        'error': 'timeout'
                    })
        
        success_count = sum(1 for r in results if r.get('price', 0) > 0)
        logger.info(f"Watchlist prices: {success_count}/{len(results)} successful")
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': results
        })
        
    except Exception as e:
        logger.error(f"Batch watchlist price fetch failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': f'Failed: {str(e)}',
            'data': []
        }), 500


@market_blp.route('/price', methods=['GET'])
def get_price():
    """
    Get realtime price for a single symbol.

    Query params:
        market: Market type
        symbol: Symbol or ticker
    """
    try:
        market = request.args.get('market', '')
        symbol = request.args.get('symbol', '')
        
        if not market or not symbol:
            return jsonify({
                'code': 0,
                'msg': 'Missing market or symbol parameter(s)',
                'data': None
            }), 400
        
        result = get_single_price(market, symbol)
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Failed to fetch price: {str(e)}")
        return jsonify({
            'code': 0,
            'msg': f'Failed: {str(e)}',
            'data': None
        }), 500


# openapi-compat: legacy import name
market_bp = market_blp
