"""
Fast Analysis API Routes

New high-performance analysis endpoints that replace the slow multi-agent system.
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint

from app.utils.auth import login_required
from app.utils.logger import get_logger
from app.services.fast_analysis_tasks import (
    acquire_inflight,
    build_inflight_key,
    release_inflight,
    start_async_analysis_task,
    try_refund_credits,
)
from app.services.fast_analysis import get_fast_analysis_service
from app.services.analysis_memory import get_analysis_memory
from app.services.billing_service import get_billing_service
from app.services.market.instrument_products import PRODUCT_CRYPTO
from app.services.market.product_catalog import get_catalog_product

logger = get_logger(__name__)

fast_analysis_blp = Blueprint('fast_analysis', __name__)


def _resolve_analysis_instrument(
    *, market: str, symbol: str, exchange_id: str = '', market_type: str = '',
    instrument_id: str = '',
) -> tuple[str, str]:
    if str(market or '').strip() != 'Crypto' or not str(exchange_id or '').strip():
        return market, symbol
    try:
        product = get_catalog_product(
            market='Crypto',
            symbol=symbol,
            exchange_id=exchange_id,
            market_type=market_type or 'spot',
            instrument_id=instrument_id,
        )
    except Exception:
        return market, symbol
    if not product or str(product.get('product_type') or PRODUCT_CRYPTO) == PRODUCT_CRYPTO:
        return market, symbol
    underlying_market = str(product.get('underlying_market') or '').strip()
    underlying_symbol = str(product.get('underlying_symbol') or '').strip()
    if underlying_market and underlying_symbol:
        return underlying_market, underlying_symbol
    return market, symbol


def _professional_response_payload(result, credits_charged=0, remaining_credits=None):
    """Expose the versioned professional contract without legacy report fields."""
    result = result if isinstance(result, dict) else {}
    return {
        'schema_version': 'professional_analysis_envelope_v1',
        'report': result.get('professional_report'),
        'runtime': {
            'memory_id': result.get('memory_id'),
            'analysis_time_ms': result.get('analysis_time_ms'),
            'llm_time_ms': result.get('llm_time_ms'),
            'data_collection_time_ms': result.get('data_collection_time_ms'),
        },
        'billing': {
            'credits_charged': credits_charged,
            'remaining_credits': remaining_credits,
        },
    }


@fast_analysis_blp.route('/analyze', methods=['POST'])
@login_required
def analyze():
    """
    Fast AI analysis for any symbol.

    Request body:
        market (required): Crypto, USStock, Forex, etc.
        symbol (required): e.g. BTC/USDT, AAPL
        language (optional, default en-US): Response language
        model (optional): LLM model id, e.g. openai/gpt-5.4
        timeframe (optional, default 1D): Analysis timeframe
        async_submit (optional): Submit as background task
        response_contract (optional): professional_report_v1 returns only the
            versioned report artifact plus runtime and billing metadata.
    """
    try:
        data = request.get_json() or {}
        
        market = (data.get('market') or '').strip()
        symbol = (data.get('symbol') or '').strip()
        exchange_id = (data.get('exchange_id') or data.get('exchangeId') or '').strip().lower()
        market_type = (data.get('market_type') or data.get('marketType') or 'spot').strip().lower()
        instrument_id = (data.get('instrument_id') or data.get('instrumentId') or '').strip()
        language = data.get('language', 'en-US')
        model = data.get('model')
        timeframe = data.get('timeframe', '1D')
        research_mode = str(data.get('research_mode') or 'research').strip().lower()
        async_submit = bool(data.get('async_submit', False))
        response_contract = str(data.get('response_contract') or 'legacy').strip().lower()
        
        if not market or not symbol:
            return jsonify({
                'code': 0,
                'msg': 'market and symbol are required',
                'data': None
            }), 400
        market, symbol = _resolve_analysis_instrument(
            market=market,
            symbol=symbol,
            exchange_id=exchange_id,
            market_type=market_type,
            instrument_id=instrument_id,
        )
        if response_contract not in {'legacy', 'professional_report_v1'}:
            return jsonify({
                'code': 0,
                'msg': 'response_contract must be legacy or professional_report_v1',
                'data': None,
            }), 400
        
        # Get current user's ID to associate analysis with user
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({'code': 0, 'msg': 'Unauthorized', 'data': None}), 401

        inflight_key = build_inflight_key(user_id, market, symbol, timeframe)
        # The client timeout is five minutes; keep the de-duplication lease
        # alive slightly longer so a slow professional report cannot be charged
        # and submitted twice while the first run is still active.
        if not acquire_inflight(inflight_key, ttl_sec=330):
            return jsonify({
                'code': 0,
                'msg': 'Analysis already in progress for this symbol/timeframe. Please wait.',
                'data': {'in_progress': True}
            }), 429

        # Billing / credits (best-effort)
        credits_charged = 0
        remaining_credits = None
        billing_consumed = False
        billing = None
        try:
            billing = get_billing_service()
            if billing.is_billing_enabled():
                credits_charged = int(billing.get_feature_cost('ai_analysis') or 0)
                if credits_charged > 0:
                    ok, msg = billing.check_and_consume(
                        user_id=int(user_id),
                        feature='ai_analysis',
                        reference_id=f"fast_analysis_{market}:{symbol}:{timeframe}"
                    )
                    if not ok:
                        # Standardize insufficient credits message
                        if str(msg or "").startswith('insufficient_credits'):
                            # Format: insufficient_credits:<current>:<cost>
                            parts = str(msg).split(':')
                            cur = float(parts[1]) if len(parts) >= 2 else 0.0
                            req = float(parts[2]) if len(parts) >= 3 else float(credits_charged)
                            return jsonify({
                                'code': 0,
                                'msg': 'Insufficient credits',
                                'data': {
                                    'required': req,
                                    'current': cur,
                                    'shortage': max(0.0, req - cur),
                                }
                            }), 400
                        return jsonify({'code': 0, 'msg': f'Failed to deduct credits: {msg}', 'data': None}), 500
                    billing_consumed = True
                    # Query remaining credits after successful consumption
                    try:
                        remaining_credits = float(billing.get_user_credits(int(user_id)))
                    except Exception:
                        remaining_credits = None
        except Exception as e:
            # Billing failure should not crash analysis by default, but should be visible in logs.
            logger.warning(f"Billing check failed (skipped): {e}", exc_info=True)
        
        service = get_fast_analysis_service()

        # Async submit mode: record "processing" immediately and return task id.
        if async_submit:
            memory = get_analysis_memory()
            pending_id = memory.create_pending_task(
                market=market,
                symbol=symbol,
                language=language,
                model=model or "",
                timeframe=timeframe,
                user_id=user_id
            )
            if not pending_id:
                return jsonify({'code': 0, 'msg': 'Failed to create analysis task', 'data': None}), 500

            start_async_analysis_task(
                int(pending_id), market, symbol, language, model, timeframe,
                int(user_id), inflight_key, int(credits_charged or 0), research_mode,
            )
            # worker owns inflight release
            inflight_key = None

            return jsonify({
                'code': 1,
                'msg': 'submitted',
                'data': {
                    'task_id': int(pending_id),
                    'memory_id': int(pending_id),
                    'status': 'processing',
                    'market': market,
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'credits_charged': credits_charged,
                    'remaining_credits': remaining_credits,
                }
            })

        result = service.analyze(
            market=market,
            symbol=symbol,
            language=language,
            model=model,
            timeframe=timeframe,
            user_id=user_id,
            research_mode=research_mode,
        )

        if response_contract == 'professional_report_v1':
            professional_report = result.get('professional_report') if isinstance(result, dict) else None
            if not isinstance(professional_report, dict) or professional_report.get('schema_version') not in {'professional_report_v1', '1.0'}:
                result = {**(result or {}), 'error': 'professional_report_v1 generation failed'}
        
        if result.get('error'):
            # Best-effort refund if we already charged but analysis failed.
            if billing_consumed and billing and credits_charged > 0:
                try:
                    try_refund_credits(
                        user_id=int(user_id),
                        amount=int(credits_charged),
                        remark=f'Auto refund: fast-analysis failed ({market}:{symbol}:{timeframe})'
                    )
                    remaining_credits = float(billing.get_user_credits(int(user_id)))
                except Exception as re:
                    logger.error(f"Auto refund failed: {re}", exc_info=True)
            return jsonify({
                'code': 0,
                'msg': result['error'],
                'data': result
            }), 500
        
        # memory_id is already set in service.analyze() -> _store_analysis_memory()
        # No need to store again here (would create duplicates)
        
        response_data = (
            _professional_response_payload(result, credits_charged, remaining_credits)
            if response_contract == 'professional_report_v1'
            else {
                **(result or {}),
                'market': market,
                'symbol': symbol,
                'timeframe': timeframe,
                'credits_charged': credits_charged,
                'remaining_credits': remaining_credits,
            }
        )
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': response_data,
        })
        
    except Exception as e:
        # Best-effort refund on unexpected error after charge.
        try:
            if 'billing_consumed' in locals() and billing_consumed and 'billing' in locals() and billing and credits_charged > 0 and 'user_id' in locals() and user_id:
                try_refund_credits(
                    user_id=int(user_id),
                    amount=int(credits_charged),
                    remark=f'Auto refund: fast-analysis exception ({market}:{symbol}:{timeframe})',
                    reference_id=(
                        f'fast-analysis-refund:{int(pending_id)}'
                        if 'pending_id' in locals() and pending_id else ''
                    ),
                )
        except Exception:
            pass
        try:
            if 'pending_id' in locals() and pending_id:
                get_analysis_memory().fail_pending_task(int(pending_id), str(e))
        except Exception:
            pass
        logger.error(f"Fast analysis API failed: {e}", exc_info=True)
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500
    finally:
        try:
            if 'inflight_key' in locals() and inflight_key:
                release_inflight(inflight_key)
        except Exception:
            pass


@fast_analysis_blp.route('/data-sources', methods=['GET'])
@login_required
def get_professional_report_data_sources():
    """List community and professional report providers without secrets."""
    from app.professional_report.providers import list_providers, provider_configuration_status

    market = request.args.get('market', '').strip() or None
    capability = request.args.get('capability', '').strip() or None
    tier = request.args.get('tier', '').strip().lower() or None
    try:
        providers = list_providers(market=market, capability=capability, tier=tier)
    except ValueError as exc:
        return jsonify({'code': 0, 'msg': str(exc), 'data': None}), 400

    items = []
    for provider in providers:
        status = provider_configuration_status(provider)
        items.append({
            'key': provider.key,
            'name': provider.name,
            'tier': provider.tier,
            'markets': sorted(provider.markets),
            'capabilities': sorted(provider.capabilities),
            'configured': bool(status['configured']),
            'keyless': provider.keyless,
            'cost_level': provider.cost_level,
            'required_env_keys': list(provider.api_env_keys),
            'missing_env_keys': list(status['missing_env_keys']),
            'license_warning': provider.license_warning,
            'integration_status': provider.integration_status,
        })
    return jsonify({
        'code': 1,
        'msg': 'success',
        'data': {
            'items': items,
            'tiers': ['community', 'professional'],
            'default_tier': 'community',
        },
    })


@fast_analysis_blp.route('/history', methods=['GET'])
@login_required
def get_history():
    """
    Get analysis history for a symbol.
    
    GET /api/fast-analysis/history?market=Crypto&symbol=BTC/USDT&days=7&limit=10
    """
    try:
        market = request.args.get('market', '').strip()
        symbol = request.args.get('symbol', '').strip()
        days = int(request.args.get('days', 7))
        limit = min(int(request.args.get('limit', 10)), 50)
        
        if not market or not symbol:
            return jsonify({
                'code': 0,
                'msg': 'market and symbol are required',
                'data': None
            }), 400
        
        memory = get_analysis_memory()
        history = memory.get_recent(
            market, symbol, days, limit, user_id=getattr(g, 'user_id', None)
        )
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'items': history,
                'total': len(history)
            }
        })
        
    except Exception as e:
        logger.error(f"Get history failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500


@fast_analysis_blp.route('/history/all', methods=['GET'])
@login_required
def get_all_history():
    """
    Get all analysis history with pagination.
    
    GET /api/fast-analysis/history/all?page=1&pagesize=20
    """
    try:
        page = int(request.args.get('page', 1))
        pagesize = min(int(request.args.get('pagesize', 20)), 50)
        
        # Get current user's ID to filter history
        user_id = getattr(g, 'user_id', None)
        
        memory = get_analysis_memory()
        result = memory.get_all_history(user_id=user_id, page=page, page_size=pagesize)
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'list': result['items'],
                'total': result['total'],
                'page': result['page'],
                'pagesize': result['page_size']
            }
        })
        
    except Exception as e:
        logger.error(f"Get all history failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500


@fast_analysis_blp.route('/history/<int:memory_id>', methods=['DELETE'])
@login_required
def delete_history(memory_id: int):
    """
    Delete a history record.
    
    DELETE /api/fast-analysis/history/123
    """
    try:
        # Get current user's ID to ensure they can only delete their own records
        user_id = getattr(g, 'user_id', None)
        
        memory = get_analysis_memory()
        success = memory.delete_history(memory_id, user_id=user_id)
        
        if success:
            return jsonify({
                'code': 1,
                'msg': 'Deleted successfully',
                'data': None
            })
        else:
            return jsonify({
                'code': 0,
                'msg': 'Record not found or no permission',
                'data': None
            }), 404
        
    except Exception as e:
        logger.error(f"Delete history failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500


@fast_analysis_blp.route('/feedback', methods=['POST'])
@login_required
def submit_feedback():
    """
    Submit user feedback on an analysis.

    Request body:
        memory_id (required): Analysis history ID
        feedback (required): helpful, not_helpful, accurate, or inaccurate
    """
    try:
        data = request.get_json() or {}
        
        memory_id = int(data.get('memory_id', 0))
        feedback = (data.get('feedback') or '').strip()
        
        if not memory_id or not feedback:
            return jsonify({
                'code': 0,
                'msg': 'memory_id and feedback are required',
                'data': None
            }), 400
        
        valid_feedback = ['helpful', 'not_helpful', 'accurate', 'inaccurate']
        if feedback not in valid_feedback:
            return jsonify({
                'code': 0,
                'msg': f'feedback must be one of: {valid_feedback}',
                'data': None
            }), 400
        
        memory = get_analysis_memory()
        success = memory.record_feedback(
            memory_id, feedback, user_id=getattr(g, 'user_id', None)
        )
        
        return jsonify({
            'code': 1 if success else 0,
            'msg': 'success' if success else 'failed',
            'data': None
        })
        
    except Exception as e:
        logger.error(f"Submit feedback failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500


@fast_analysis_blp.route('/performance', methods=['GET'])
@login_required
def get_performance():
    """
    Get AI analysis performance statistics.
    
    GET /api/fast-analysis/performance?market=Crypto&symbol=BTC/USDT&days=30
    """
    try:
        market = request.args.get('market', '').strip() or None
        symbol = request.args.get('symbol', '').strip() or None
        days = int(request.args.get('days', 30))
        
        memory = get_analysis_memory()
        stats = memory.get_performance_stats(
            user_id=int(g.user_id),
            market=market,
            symbol=symbol,
            days=days,
        )
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': stats
        })
        
    except Exception as e:
        logger.error(f"Get performance failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500


@fast_analysis_blp.route('/similar-patterns', methods=['GET'])
@login_required
def get_similar_patterns():
    """
    Get similar historical patterns for current market conditions.
    
    GET /api/fast-analysis/similar-patterns?market=Crypto&symbol=BTC/USDT
    """
    try:
        market = request.args.get('market', '').strip()
        symbol = request.args.get('symbol', '').strip()
        
        if not market or not symbol:
            return jsonify({
                'code': 0,
                'msg': 'market and symbol are required',
                'data': None
            }), 400
        
        # Get current indicators
        service = get_fast_analysis_service()
        data = service._collect_market_data(market, symbol)
        indicators = data.get('indicators', {})
        
        # Find similar patterns
        memory = get_analysis_memory()
        patterns = memory.get_similar_patterns(
            market, symbol, indicators, user_id=getattr(g, 'user_id', None)
        )
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'patterns': patterns,
                'current_indicators': {
                    'rsi': indicators.get('rsi', {}).get('value'),
                    'macd_signal': indicators.get('macd', {}).get('signal'),
                    'trend': indicators.get('moving_averages', {}).get('trend'),
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Get similar patterns failed: {e}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 500

# openapi-compat: legacy import name
fast_analysis_bp = fast_analysis_blp
