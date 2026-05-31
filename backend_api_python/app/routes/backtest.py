"""
Backtest API routes
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from datetime import datetime
import calendar
import traceback
import json
import time

from app.services.backtest import BacktestService
from app.data_sources.factory import DataSourceFactory
from app.utils.logger import get_logger
from app.utils.db import get_db_connection
from app.utils.auth import login_required
from app.utils.safe_exec import validate_code_safety

logger = get_logger(__name__)

backtest_blp = Blueprint('backtest', __name__)
backtest_service = BacktestService()


def _add_months(dt: datetime, months: int) -> datetime:
    """Add calendar months while keeping the day within the target month."""
    month_index = (dt.month - 1) + int(months or 0)
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _backtest_range_limit(timeframe: str, start_date: datetime) -> tuple[datetime, str]:
    """Return the max allowed end date and human-readable label for a timeframe."""
    tf = str(timeframe or '').strip()
    if tf == '1m':
        return _add_months(start_date, 1), '1 month'
    if tf == '5m':
        return _add_months(start_date, 6), '6 months'
    if tf in ['15m', '30m']:
        return _add_months(start_date, 12), '1 year'
    return _add_months(start_date, 36), '3 years'


@backtest_blp.route('/backtest/precision-info', methods=['GET'])
def get_precision_info():
    """
    Backtest precision hints for the UI.

    Query params:
        market: Market type
        startDate: Start date (YYYY-MM-DD)
        endDate: End date (YYYY-MM-DD)

    Returns recommended execution timeframe and estimated bar count.
    """
    try:
        # Use request.args for GET params
        market = request.args.get('market', 'crypto')
        start_date_str = request.args.get('startDate', '')
        end_date_str = request.args.get('endDate', '')
        
        if not start_date_str or not end_date_str:
            return jsonify({'code': 0, 'msg': 'startDate and endDate are required'}), 400
        
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        exec_tf, precision_info = backtest_service.get_execution_timeframe(start_date, end_date, market)
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': precision_info
        })
    except Exception as e:
        logger.error(f"Get precision info failed: {e}")
        return jsonify({'code': 0, 'msg': str(e)}), 400


@backtest_blp.route('/backtest', methods=['POST'])
@login_required
def run_backtest():
    """
    Run indicator backtest for the current user.
    
    Params:
        indicatorId: Indicator ID (optional)
        indicatorCode: Indicator Python code
        symbol: Symbol
        market: Market type
        timeframe: Timeframe
        startDate: Start date (YYYY-MM-DD)
        endDate: End date (YYYY-MM-DD)
        initialCapital: Initial capital (default 10000)
        commission: Commission rate (default 0.001)
        enableMtf: Enable multi-timeframe backtest (deprecated; use strictMode)
        strictMode: Strict mode (default true). Off uses aggressive same-bar / 1m path for crypto.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'code': 0,
                'msg': 'Request body is required',
                'data': None
            }), 400
        
        # Extract params - use current user's ID
        user_id = g.user_id
        indicator_code = data.get('indicatorCode', '')
        indicator_id = data.get('indicatorId')
        symbol = (data.get('symbol') or '').strip()
        market = (data.get('market') or '').strip()
        # 数据源仅由请求里的市场分类决定（与前端自选一致）；此处只做大小写/别名规范化
        market = DataSourceFactory.normalize_market(market)
        timeframe = data.get('timeframe', '1D')
        start_date_str = data.get('startDate', '')
        end_date_str = data.get('endDate', '')
        from app.services.backtest_execution import (
            default_slippage_if_missing,
            parse_strict_mode,
            merge_strict_mode_into_strategy_config,
        )

        strict_mode = parse_strict_mode(
            data.get('strictMode', data.get('strict_mode')),
            default=True,
        )
        initial_capital = float(data.get('initialCapital', 10000))
        commission = float(data.get('commission', 0.001))
        slippage = default_slippage_if_missing(data.get('slippage'))
        leverage = int(data.get('leverage', 1))
        trade_direction = data.get('tradeDirection', 'long')  # long, short, both
        strategy_config = merge_strict_mode_into_strategy_config(
            data.get('strategyConfig') or {},
            strict_mode,
        )
        # persist toggle: skip DB write when False for faster iteration
        persist = data.get('persist', True)
        if isinstance(persist, str):
            persist = persist.lower() in ['true', '1', 'yes']
        
        # (Debug) log received params if needed
        
        # If frontend only provides indicatorId, load code from local DB.
        if (not indicator_code or not str(indicator_code).strip()) and indicator_id:
            try:
                iid = int(indicator_id)
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute("SELECT code FROM qd_indicator_codes WHERE id = ?", (iid,))
                    row = cur.fetchone()
                    cur.close()
                if row and row.get('code'):
                    indicator_code = row.get('code')
            except Exception:
                pass

        # 参数验证
        if not all([indicator_code, symbol, market, timeframe, start_date_str, end_date_str]):
            return jsonify({
                'code': 0,
                'msg': 'Missing required parameters',
                'data': None
            }), 400

        is_safe_code, unsafe_reason = validate_code_safety(indicator_code or '')
        if not is_safe_code:
            return jsonify({
                'code': 0,
                'msg': f'Unsafe indicator code: {unsafe_reason}',
                'data': None
            }), 400
        
        # 转换日期
        # 开始日期：当天的 00:00:00
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        # 结束日期：当天的 23:59:59，确保包含整天的数据
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        
        # 验证时间范围限制
        days_diff = (end_date - start_date).days
        max_end_date, max_range_text = _backtest_range_limit(timeframe, start_date)
        max_end_date = max_end_date.replace(hour=23, minute=59, second=59)
        
        if end_date > max_end_date:
            max_days = (max_end_date - start_date).days
            return jsonify({
                'code': 0,
                'msg': f'Backtest range exceeds limit: timeframe {timeframe} supports up to {max_range_text} ({max_days} days), but you selected {days_diff} days',
                'data': None
            }), 400

        # Explicit audit log so we can always trace exactly which window the user asked for.
        logger.info(
            f"[BacktestRequest] user={user_id} indicator={indicator_id} {market}:{symbol} "
            f"tf={timeframe} range=[{start_date_str} ~ {end_date_str}] ({days_diff}d) "
            f"capital={initial_capital} leverage={leverage} direction={trade_direction} "
            f"strict_mode={strict_mode}"
        )

        result = backtest_service.run_aligned(
            strict_mode=strict_mode,
            indicator_code=indicator_code,
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission=commission,
            slippage=slippage,
            leverage=leverage,
            trade_direction=trade_direction,
            strategy_config=strategy_config,
        )

        ea = dict(result.get('executionAssumptions') or {})
        ea['commission'] = round(float(commission), 6)
        ea['slippage'] = round(float(slippage), 6)
        ea['strictMode'] = bool(strict_mode)
        result['executionAssumptions'] = ea

        run_id = None
        if persist:
            run_id = backtest_service.persist_run(
                user_id=user_id,
                indicator_id=int(indicator_id) if indicator_id is not None else None,
                run_type='indicator',
                market=market,
                symbol=symbol,
                timeframe=timeframe,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                initial_capital=initial_capital,
                commission=commission,
                slippage=slippage,
                leverage=leverage,
                trade_direction=trade_direction,
                strategy_config=strategy_config,
                config_snapshot={
                    'indicatorId': int(indicator_id) if indicator_id is not None else None,
                    'executionConfig': {
                        'strictMode': bool(strict_mode),
                    },
                },
                status='success',
                error_message='',
                result=result,
                code=indicator_code,
            )
        
        return jsonify({
            'code': 1,
            'msg': 'Backtest succeeded',
            'data': {
                'runId': run_id,
                'result': result
            }
        })
        
    except ValueError as e:
        logger.warning(f"Invalid backtest parameters: {str(e)}")
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': None
        }), 400
    except Exception as e:
        logger.error(f"Backtest failed: {str(e)}")
        logger.error(traceback.format_exc())
        try:
            data = data if isinstance(data, dict) else {}
            user_id = g.user_id
            indicator_id = data.get('indicatorId')
            backtest_service.persist_run(
                user_id=user_id,
                indicator_id=int(indicator_id) if indicator_id is not None else None,
                run_type='indicator',
                market=str(data.get('market', '') or ''),
                symbol=str(data.get('symbol', '') or ''),
                timeframe=str(data.get('timeframe', '') or ''),
                start_date_str=str(data.get('startDate', '') or ''),
                end_date_str=str(data.get('endDate', '') or ''),
                initial_capital=float(data.get('initialCapital', 0) or 0),
                commission=float(data.get('commission', 0) or 0),
                slippage=float(data.get('slippage', 0) or 0),
                leverage=int(data.get('leverage', 1) or 1),
                trade_direction=str(data.get('tradeDirection', 'long') or 'long'),
                strategy_config=data.get('strategyConfig') or {},
                config_snapshot={'indicatorId': int(indicator_id) if indicator_id is not None else None},
                status='failed',
                error_message=str(e),
                result=None,
                code=str(data.get('indicatorCode', '') or ''),
            )
        except Exception:
            pass
        return jsonify({
            'code': 0,
            'msg': f'Backtest failed: {str(e)}',
            'data': None
        }), 500


@backtest_blp.route('/backtest/history', methods=['GET'])
@login_required
def get_backtest_history():
    """
    Get backtest run history for the current user.

    Params (Query String):
        limit: Page size (default 50, max 200)
        offset: Offset (default 0)
        indicatorId: Optional indicator id filter
        symbol: Optional symbol filter
        market: Optional market filter
        timeframe: Optional timeframe filter
    """
    try:
        # Use current user's ID
        user_id = g.user_id
        limit = int(request.args.get('limit') or 50)
        offset = int(request.args.get('offset') or 0)
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        indicator_id = request.args.get('indicatorId')
        strategy_id = request.args.get('strategyId')
        run_type = (request.args.get('runType') or '').strip()
        symbol = (request.args.get('symbol') or '').strip()
        market = (request.args.get('market') or '').strip()
        timeframe = (request.args.get('timeframe') or '').strip()
        rows = backtest_service.list_runs(
            user_id=user_id,
            limit=limit,
            offset=offset,
            indicator_id=int(indicator_id) if indicator_id is not None and str(indicator_id).strip() != "" else None,
            strategy_id=int(strategy_id) if strategy_id is not None and str(strategy_id).strip() != "" else None,
            run_type=run_type or None,
            symbol=symbol,
            market=market,
            timeframe=timeframe,
        )

        return jsonify({'code': 1, 'msg': 'OK', 'data': rows})
    except Exception as e:
        logger.error(f"get_backtest_history failed: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@backtest_blp.route('/backtest/get', methods=['GET'])
@login_required
def get_backtest_run():
    """
    Get a backtest run detail by run id for the current user.

    Params (Query String):
        runId: Backtest run id (required)
    """
    try:
        user_id = g.user_id
        run_id = int(request.args.get('runId') or 0)
        if not run_id:
            return jsonify({'code': 0, 'msg': 'runId is required', 'data': None}), 400

        row = backtest_service.get_run(user_id=user_id, run_id=run_id)
        if not row:
            return jsonify({'code': 0, 'msg': 'run not found', 'data': None}), 404

        return jsonify({'code': 1, 'msg': 'OK', 'data': row})
    except Exception as e:
        logger.error(f"get_backtest_run failed: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# openapi-compat: legacy import name
backtest_bp = backtest_blp
