"""
K-line (OHLCV) API routes.
"""
from flask import jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from datetime import datetime
import traceback

from app.services.kline import KlineService
from app.utils.logger import get_logger

logger = get_logger(__name__)

kline_blp = Blueprint('kline', __name__)
kline_service = KlineService()


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
        # 强制 GET, 使用 request.args
        market = request.args.get('market', 'USStock')
        symbol = request.args.get('symbol', '')
        timeframe = request.args.get('timeframe', '1D')
        limit = int(request.args.get('limit', 300))
        before_time = request.args.get('before_time') or request.args.get('beforeTime')
        
        if before_time:
            before_time = int(before_time)
        
        if not symbol:
            return jsonify({
                'code': 0,
                'msg': 'Missing symbol parameter',
                'data': None
            }), 400
        
        logger.info(f"Requesting K-lines: {market}:{symbol}, timeframe={timeframe}, limit={limit}")
        
        klines = kline_service.get_kline(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            before_time=before_time
        )
        
        if not klines:
            # 针对特定情况给出更详细的提示
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
            'data': klines
        })
        
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
