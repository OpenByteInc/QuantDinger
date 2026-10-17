"""
K-line (OHLCV) API routes.
"""
from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from datetime import datetime
import traceback

from app.services.kline import KlineService
from app.utils.logger import get_logger
from app.services.market.watchlist import validate_watchlist_pair
from app.services.market_context import (
    MarketContext,
    SUPPORTED_CRYPTO_EXCHANGE_IDS,
    parse_crypto_identifier,
)
from app.utils.request_guard import RequestGuardError, cache_key, guarded_cached

logger = get_logger(__name__)

kline_blp = Blueprint('kline', __name__)
kline_service = KlineService()


def _latest_kline_ttl(timeframe: str) -> int:
    tf = (timeframe or '').strip()
    return {
        '1m': 3,
        '3m': 4,
        '5m': 5,
        '15m': 8,
        '30m': 10,
        '1H': 10,
        '4H': 15,
        '1D': 30,
        '1W': 60,
        '1h': 10,
        '4h': 15,
        '1d': 30,
        '1w': 60,
    }.get(tf, 10)


def _guard_policy(timeframe: str, limit: int, before_time):
    if before_time:
        return {
            'ttl_sec': 300,
            'stale_ttl_sec': 1800,
            'timeout_sec': 25,
            'max_concurrent': 6,
        }
    ttl = _latest_kline_ttl(timeframe)
    if limit <= 10:
        return {
            'ttl_sec': ttl,
            'stale_ttl_sec': max(20, ttl * 4),
            'timeout_sec': 8,
            'max_concurrent': 10,
        }
    return {
        'ttl_sec': ttl,
        'stale_ttl_sec': max(60, ttl * 6),
        'timeout_sec': 25,
        'max_concurrent': 10,
    }


@kline_blp.route('/kline', methods=['GET'])
def get_kline():
    """
    Fetch OHLCV k-line bars.

    Query params:
        market: Market type (Crypto, USStock, Forex, Futures)
        symbol: Symbol or ticker
        timeframe: Bar size (1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W)
        limit: Number of bars (default 300)
        before_time: Return bars before this Unix timestamp (optional)
    """
    try:
        market = (request.args.get('market', 'USStock') or '').strip()
        symbol = (request.args.get('symbol', '') or '').strip()
        timeframe = (request.args.get('timeframe', '1D') or '').strip()
        limit = int(request.args.get('limit', 300))
        limit = max(1, min(1000, limit))
        before_time = request.args.get('before_time') or request.args.get('beforeTime')
        exchange_id = (request.args.get('exchange_id') or request.args.get('exchangeId') or '').strip() or None
        market_type = (request.args.get('market_type') or request.args.get('marketType') or '').strip() or None
        instrument_id = (request.args.get('instrument_id') or request.args.get('instrumentId') or '').strip()
        if not symbol:
            return jsonify({
                'code': 0,
                'msg': 'Missing symbol parameter',
                'data': None
            }), 400

        try:
            context = MarketContext.from_mapping({
                'market': market,
                'symbol': symbol,
                'exchange_id': exchange_id,
                'market_type': market_type,
                'instrument_id': instrument_id,
                'timeframe': timeframe,
            })
        except ValueError as e:
            return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400

        service_symbol = context.symbol if market == 'Crypto' else symbol
        service_exchange_id = context.exchange_id if market == 'Crypto' else exchange_id
        service_market_type = context.market_type if market == 'Crypto' else market_type
        if market == 'Crypto':
            if context.exchange_id not in SUPPORTED_CRYPTO_EXCHANGE_IDS:
                return jsonify({'code': 0, 'msg': 'Unsupported crypto exchange', 'data': None}), 400

            try:
                parsed_symbol, suffix_market_type, suffix_exchange_id = parse_crypto_identifier(symbol)
            except ValueError as e:
                return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
            if (
                parsed_symbol == 'BTC/USDT'
                and suffix_market_type == 'swap'
                and not suffix_exchange_id
                and context.exchange_id != 'binance'
            ):
                return jsonify({
                    'code': 0,
                    'msg': 'Crypto:BTC/USDT@swap is mapped to Binance USDⓈ-M; use an explicit venue-qualified symbol for another exchange',
                    'data': None,
                }), 400
        
        if before_time:
            before_time = int(before_time)
        
        validation_err = validate_watchlist_pair(market, service_symbol)
        if validation_err:
            return jsonify({'code': 0, 'msg': validation_err, 'data': None}), 400
        
        logger.info(f"Requesting K-lines: {market}:{service_symbol}, timeframe={timeframe}, limit={limit}")
        
        policy = _guard_policy(timeframe, limit, before_time)
        klines = guarded_cached(
            cache_key(
                "indicator_kline", market, service_symbol, timeframe, limit, before_time or "",
                service_exchange_id or "", service_market_type or "", instrument_id,
            ),
            lambda: kline_service.get_kline(
                market=market,
                symbol=service_symbol,
                timeframe=timeframe,
                limit=limit,
                before_time=before_time,
                exchange_id=service_exchange_id,
                market_type=service_market_type,
                instrument_id=instrument_id,
            ),
            ttl_sec=policy['ttl_sec'],
            stale_ttl_sec=policy['stale_ttl_sec'],
            timeout_sec=policy['timeout_sec'],
            namespace="indicator_kline",
            max_concurrent=policy['max_concurrent'],
        )
        
        if not klines:
            msg = 'No data found'
            if market == 'Forex' and timeframe == '1m':
                msg = 'Forex 1-minute data requires Tiingo paid subscription'
            elif market == 'Forex' and timeframe in ('1W', '1M'):
                msg = 'No weekly/monthly data available for this period'
            return jsonify({
                'code': 0,
                'msg': msg,
                'data': [],
                'hint': 'tiingo_subscription' if (market == 'Forex' and timeframe == '1m') else None
            })
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': klines,
            'meta': {
                'market_context': context.as_dict(),
                'bar_count': len(klines),
            },
        })
        
    except RequestGuardError as e:
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), e.status_code
    except Exception as e:
        logger.error(f"Failed to fetch K-lines: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': f'Failed to fetch kline data: {str(e)}',
            'data': None
        }), 500


# openapi-compat: legacy import name
kline_bp = kline_blp
