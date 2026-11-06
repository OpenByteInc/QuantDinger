"""
Market API routes (local-only).
Provides watchlist, market metadata, symbol search, and pricing helpers for the frontend.
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
import traceback

from app.utils.logger import get_logger
from app.utils.config_loader import load_addon_config
from app.utils.auth import login_required
from app.services.market.quotes import get_price_map, get_single_price
from app.services.market.assets import (
    AssetResolutionError,
    get_asset_capabilities,
    search_market_assets,
)
from app.services.market.symbol_search import (
    get_crypto_symbol_capabilities,
    get_hot_symbols as search_hot_symbols,
    search_market_symbols,
)
from app.services.market.watchlist import (
    add_watchlist_item,
    get_user_watchlist_pairs,
    list_watchlist,
    normalize_symbol,
    remove_watchlist_item,
    validate_watchlist_pair,
)
from app.services.market_context import default_crypto_exchange_id
from app.utils.request_guard import RequestGuardError, cache_key, guarded_cached
from app.utils.market_visibility import filter_market_items

logger = get_logger(__name__)

market_blp = Blueprint('market', __name__)

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
                # Unified frontend model list (OpenRouter-style ids)
                'openai/gpt-5.4': 'OpenAI: GPT-5.4',
                'x-ai/grok-code-fast-1': 'xAI: Grok Code Fast 1',
                'x-ai/grok-4-fast': 'xAI: Grok 4 Fast',
                'x-ai/grok-4.1-fast': 'xAI: Grok 4.1 Fast',
                'google/gemini-2.5-flash': 'Google: Gemini 2.5 Flash',
                'google/gemini-2.0-flash-001': 'Google: Gemini 2.0 Flash',
                'google/gemini-3-pro-preview': 'Google: Gemini 3 Pro Preview',
                'google/gemini-2.5-flash-lite': 'Google: Gemini 2.5 Flash Lite',
                'google/gemini-2.5-pro': 'Google: Gemini 2.5 Pro',
                'openai/gpt-4o': 'OpenAI: GPT-4o',
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
    rules, primarily the ``ENABLED_MARKETS`` whitelist, with
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
            'copyright': '(c) 2025-2026 QuantDinger'
    }
    return jsonify({'code': 1, 'msg': 'success', 'data': data})

@market_blp.route('/symbols/search', methods=['GET'])
def search_symbols():
    """
    Lightweight symbol search.
    Crypto search is local-only; exchange catalogs are refreshed by the background sync task.
    """
    try:
        market = (request.args.get('market') or '').strip()
        keyword = (request.args.get('keyword') or '').strip().upper()
        limit = int(request.args.get('limit') or 20)
        exchange_id = (request.args.get('exchange_id') or request.args.get('exchangeId') or '').strip()
        market_type = (request.args.get('market_type') or request.args.get('marketType') or '').strip()
        if not market or not keyword:
            return jsonify({'code': 1, 'msg': 'success', 'data': []})

        out = search_market_symbols(
            market=market,
            keyword=keyword,
            limit=limit,
            exchange_id=exchange_id,
            market_type=market_type,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': out})
    except Exception as e:
        logger.error(f"search_symbols failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@market_blp.route('/symbols/hot', methods=['GET'])
def get_hot_symbols():
    """Return a small curated hot list per market (local-only)."""
    try:
        market = (request.args.get('market') or '').strip()
        limit = int(request.args.get('limit') or 10)
        hot = search_hot_symbols(market=market, limit=limit)
        return jsonify({'code': 1, 'msg': 'success', 'data': hot})
    except Exception as e:
        logger.error(f"get_hot_symbols failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@market_blp.route('/symbols/capabilities', methods=['GET'])
def get_symbol_capabilities():
    """Return exact product availability for one logical asset."""
    try:
        asset_id = int(request.args.get('asset_id') or request.args.get('assetId') or 0)
        market = (request.args.get('market') or '').strip()
        symbol = normalize_symbol(request.args.get('symbol', ''))
        exchange_id = (request.args.get('exchange_id') or request.args.get('exchangeId') or '').strip()
        if asset_id > 0:
            return jsonify({
                'code': 1,
                'msg': 'success',
                'data': get_asset_capabilities(asset_id, exchange_id=exchange_id),
            })
        if market != 'Crypto' or not symbol:
            return jsonify({'code': 1, 'msg': 'success', 'data': {
                'symbol': symbol,
                'exchange_id': exchange_id,
                'market_types': ['spot'] if market and symbol else [],
                'contexts': [],
            }})
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': get_crypto_symbol_capabilities(symbol, exchange_id=exchange_id),
        })
    except Exception as e:
        logger.error(f"get_symbol_capabilities failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': 'internal_server_error', 'data': None}), 500


@market_blp.route('/assets/search', methods=['GET'])
def search_assets():
    """Search the logical asset catalog used by user watchlists."""
    try:
        market = (request.args.get('market') or '').strip()
        keyword = (request.args.get('keyword') or '').strip()
        limit = int(request.args.get('limit') or 20)
        if not market or not keyword:
            return jsonify({'code': 1, 'msg': 'success', 'data': []})
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': search_market_assets(market=market, keyword=keyword, limit=limit),
        })
    except Exception as e:
        logger.error(f"search_assets failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': 'internal_server_error', 'data': []}), 500


@market_blp.route('/assets/capabilities', methods=['GET'])
def asset_capabilities():
    """Return venue and product availability for one logical asset."""
    try:
        asset_id = int(request.args.get('asset_id') or request.args.get('assetId') or 0)
        exchange_id = (request.args.get('exchange_id') or request.args.get('exchangeId') or '').strip()
        if asset_id <= 0:
            return jsonify({'code': 0, 'msg': 'market.assetIdRequired', 'data': None}), 400
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': get_asset_capabilities(asset_id, exchange_id=exchange_id),
        })
    except AssetResolutionError as exc:
        logger.warning("asset_capabilities asset resolution failed: %s", exc)
        return jsonify({'code': 0, 'msg': 'market.assetNotFound', 'data': None}), 404
    except Exception as e:
        logger.error(f"asset_capabilities failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': 'internal_server_error', 'data': None}), 500

@market_blp.route('/watchlist/get', methods=['GET'])
@login_required
def get_watchlist():
    """Get watchlist for the current user."""
    try:
        _ensure_watchlist_table()
        rows = list_watchlist(g.user_id)
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
        data = request.get_json() or {}
        ok, message = add_watchlist_item(
            g.user_id,
            asset_id=int(data.get('asset_id') or data.get('assetId') or 0),
        )
        if not ok:
            return jsonify({'code': 0, 'msg': message, 'data': None}), 400
        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"add_watchlist failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500

@market_blp.route('/watchlist/remove', methods=['POST'])
@login_required
def remove_watchlist():
    """Remove a symbol from watchlist for the current user."""
    try:
        data = request.get_json() or {}
        asset_id = int(data.get('asset_id') or data.get('assetId') or 0)
        if asset_id <= 0:
            return jsonify({'code': 0, 'msg': 'market.assetIdRequired', 'data': None}), 400
        remove_watchlist_item(
            g.user_id,
            asset_id=asset_id,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"remove_watchlist failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500

@market_blp.route('/watchlist/prices', methods=['GET'])
@login_required
def get_watchlist_prices():
    """Get realtime prices for the current user's watchlist."""
    try:
        user_id = g.user_id
        cache_id = cache_key("watchlist_prices", user_id, default_crypto_exchange_id())

        def _compute():
            watchlist = get_user_watchlist_pairs(user_id)
            if not watchlist:
                return []
            results = get_price_map(watchlist, timeout_sec=10)
            success_count = sum(1 for r in results if r.get('price', 0) > 0)
            logger.debug("Watchlist prices: %s/%s successful", success_count, len(results))
            return results

        results = guarded_cached(
            cache_id,
            _compute,
            ttl_sec=5,
            stale_ttl_sec=120,
            timeout_sec=11,
            namespace="watchlist_prices",
            max_concurrent=8,
            cache_if=lambda rows: not rows or all(float(row.get('price') or 0) > 0 for row in rows),
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': results})
    except RequestGuardError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), e.status_code
    except Exception as e:
        logger.error(f"Batch watchlist price fetch failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': f'Failed: {str(e)}', 'data': []}), 500

@market_blp.route('/price', methods=['GET'])
def get_price():
    """
    Get realtime price for a single symbol.

    Query params:
        market: Market type
        symbol: Symbol or ticker
    """
    try:
        market = (request.args.get('market', '') or '').strip()
        symbol = normalize_symbol(request.args.get('symbol', ''))
        exchange_id = (request.args.get('exchange_id') or request.args.get('exchangeId') or '').strip()
        market_type = (request.args.get('market_type') or request.args.get('marketType') or '').strip()
        instrument_id = (request.args.get('instrument_id') or request.args.get('instrumentId') or '').strip()
        
        if not market or not symbol:
            return jsonify({
                'code': 0,
                'msg': 'Missing market or symbol parameter(s)',
                'data': None
            }), 400

        validation_err = validate_watchlist_pair(market, symbol)
        if validation_err:
            return jsonify({'code': 0, 'msg': validation_err, 'data': None}), 400
        
        result = guarded_cached(
            cache_key("market_price", market, exchange_id, market_type, instrument_id, symbol),
            lambda: get_single_price(market, symbol, exchange_id, market_type, instrument_id),
            ttl_sec=5,
            stale_ttl_sec=120,
            timeout_sec=6,
            namespace="market_price",
            max_concurrent=12,
        )
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': result
        })
    except RequestGuardError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), e.status_code
        
    except Exception as e:
        logger.error(f"Failed to fetch price: {str(e)}")
        return jsonify({
            'code': 0,
            'msg': f'Failed: {str(e)}',
            'data': None
        }), 500


# openapi-compat: legacy import name
market_bp = market_blp



