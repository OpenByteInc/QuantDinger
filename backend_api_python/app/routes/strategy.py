"""
Trading Strategy API Routes
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from datetime import datetime, timezone
from typing import Optional
import json
import re
import traceback
import time

from app.services.strategy import StrategyService
from app.services.strategy_compiler import StrategyCompiler
from app.services.backtest import BacktestService
from app.services.strategy_snapshot import StrategySnapshotResolver
from app import get_trading_executor
from app.utils.logger import get_logger
from app.utils.db import get_db_connection

try:
    from psycopg2.errors import UndefinedTable as PgUndefinedTable
except Exception:  # pragma: no cover
    PgUndefinedTable = None  # type: ignore
from app.utils.auth import login_required
from app.data_sources import DataSourceFactory
from app.utils.pnl import (
    calc_margin_notional,
    calc_notional_value,
    calc_pnl_percent,
    calc_unrealized_pnl,
    is_derivatives_market,
)

logger = get_logger(__name__)

strategy_blp = Blueprint('strategy', __name__)


def _normalize_trade_row_for_api(trade: dict, *, leverage: float = 1.0, market_type: str = "spot") -> dict:
    """Ensure numeric fields are JSON-friendly floats (PostgreSQL DECIMAL → float)."""
    try:
        from decimal import Decimal
    except Exception:  # pragma: no cover
        Decimal = ()  # type: ignore
    out = dict(trade)
    for k in ("price", "amount", "value", "commission", "profit"):
        v = out.get(k)
        if isinstance(v, Decimal):
            out[k] = float(v)
    try:
        price = float(out.get("price") or 0.0)
        amount = float(out.get("amount") or 0.0)
        value = float(out.get("value") or 0.0)
        if value <= 0 and price > 0 and amount > 0:
            value = calc_notional_value(price, amount)
            out["value"] = value
        out["notional_value"] = value
        out["margin_value"] = calc_margin_notional(value, leverage, market_type)
        profit = out.get("profit")
        if profit is not None:
            margin = float(out.get("margin_value") or 0.0)
            if margin > 0:
                out["profit_pct_on_margin"] = round(float(profit) / margin * 100.0, 4)
            else:
                out["profit_pct_on_margin"] = 0.0
            if value > 0:
                out["profit_pct_on_notional"] = round(float(profit) / value * 100.0, 4)
            else:
                out["profit_pct_on_notional"] = 0.0
    except Exception:
        pass
    return out


def _analyze_strategy_code_quality(code: str) -> list[dict]:
    hints = []
    raw = (code or "").strip()
    if not raw:
        return [{"severity": "error", "code": "EMPTY_CODE", "params": {}}]

    has_on_init = bool(re.search(r"^\s*def\s+on_init\s*\(", raw, re.MULTILINE))
    has_on_bar = bool(re.search(r"^\s*def\s+on_bar\s*\(", raw, re.MULTILINE))
    has_ctx_param = bool(re.search(r"\bctx\.param\s*\(", raw))
    has_order_intent = bool(re.search(r"\bctx\.(buy|sell|close_position)\s*\(", raw))

    if not has_on_init:
        hints.append({"severity": "warn", "code": "MISSING_ON_INIT", "params": {}})
    if not has_on_bar:
        hints.append({"severity": "error", "code": "MISSING_ON_BAR", "params": {}})
    if not has_ctx_param:
        hints.append({"severity": "info", "code": "NO_CTX_PARAM_DEFAULTS", "params": {}})
    if not has_order_intent:
        hints.append({"severity": "info", "code": "NO_ORDER_INTENT", "params": {}})
    return hints


def _validate_strategy_code_internal(code: str) -> dict:
    from app.services.strategy_script_runtime import compile_strategy_script_handlers

    raw = (code or "").strip()
    hints = _analyze_strategy_code_quality(raw)
    if not raw:
        return {
            "success": False,
            "message": "Code is empty",
            "error_type": "EmptyCode",
            "details": None,
            "hints": hints,
        }

    try:
        compile(raw, '<strategy>', 'exec')
    except SyntaxError as se:
        return {
            "success": False,
            "message": f"Syntax error at line {se.lineno}: {se.msg}",
            "error_type": "SyntaxError",
            "details": str(se),
            "hints": hints,
        }

    required_funcs = ['on_bar', 'on_init']
    found = [f for f in required_funcs if f'def {f}' in raw]
    missing = [f for f in required_funcs if f not in found]
    if missing:
        return {
            "success": False,
            "message": f"Missing required functions: {', '.join(missing)}",
            "error_type": "MissingFunctions",
            "details": None,
            "hints": hints,
        }

    try:
        compile_strategy_script_handlers(raw)
    except Exception as e:
        return {
            "success": False,
            "message": f"Runtime Error: {e}",
            "error_type": "RuntimeError",
            "details": str(e),
            "hints": hints,
        }

    return {
        "success": True,
        "message": "Code verification passed",
        "error_type": None,
        "details": None,
        "hints": hints,
    }


def _strategy_debug_summary(validation: dict | None = None) -> dict:
    validation = validation or {}
    hints = validation.get("hints") or []
    return {
        "success": bool(validation.get("success")),
        "message": validation.get("message"),
        "error_type": validation.get("error_type"),
        "hint_codes": [h.get("code") for h in hints if h.get("code")],
        "hint_count": len(hints),
    }


def _request_lang(default: str = "zh-CN") -> str:
    raw = (
        request.headers.get("X-App-Lang")
        or request.headers.get("Accept-Language")
        or default
    )
    lang = str(raw or default).split(",", 1)[0].strip()
    return lang or default


def _is_zh_lang(lang: str | None) -> bool:
    return str(lang or "zh-CN").strip().lower().startswith("zh")


def _strategy_ai_text(key: str, lang: str = "zh-CN") -> str:
    is_zh = _is_zh_lang(lang)
    texts = {
        "prompt_empty": "提示词不能为空" if is_zh else "Prompt cannot be empty",
        "no_llm_key": "未配置 LLM API Key" if is_zh else "No LLM API key configured",
        "insufficient_credits": "积分不足，请充值后重试" if is_zh else "Insufficient credits. Please top up and try again.",
        "invalid_json_params": "AI 未返回有效的 JSON 参数" if is_zh else "AI did not return valid JSON parameters",
        "ai_empty_result": "AI 生成结果为空" if is_zh else "AI generation returned empty result",
        "success": "success",
    }
    return texts.get(key, key)


def _strategy_hint_to_text(hint_code: str, params: dict | None = None, lang: str = "zh-CN") -> str:
    _ = params or {}
    is_zh = _is_zh_lang(lang)
    if hint_code == 'MISSING_ON_INIT':
        return "缺少 on_init(ctx) 函数。" if is_zh else "Missing on_init(ctx) function."
    if hint_code == 'MISSING_ON_BAR':
        return "缺少 on_bar(ctx, bar) 函数。" if is_zh else "Missing on_bar(ctx, bar) function."
    if hint_code == 'NO_CTX_PARAM_DEFAULTS':
        return "没有通过 ctx.param(...) 声明参数默认值。" if is_zh else "No parameter defaults were declared via ctx.param(...)."
    if hint_code == 'NO_ORDER_INTENT':
        return "没有检测到 ctx.buy / ctx.sell / ctx.close_position 等交易动作。" if is_zh else "No order intent like ctx.buy / ctx.sell / ctx.close_position was detected."
    if hint_code == 'EMPTY_CODE':
        return "策略代码为空。" if is_zh else "Strategy code is empty."
    return f"检测到策略提示：{hint_code}" if is_zh else f"Strategy hint detected: {hint_code}"


def _strategy_human_summary(
    initial_validation: dict,
    final_validation: dict,
    auto_fix_applied: bool,
    auto_fix_succeeded: bool,
    returned_candidate: str,
    lang: str = "zh-CN",
) -> dict:
    is_zh = _is_zh_lang(lang)
    initial_hints = initial_validation.get('hints') or []
    final_hints = final_validation.get('hints') or []
    initial_codes = {h.get('code') for h in initial_hints if h.get('code')}
    final_codes = {h.get('code') for h in final_hints if h.get('code')}
    fixed_codes = sorted(initial_codes - final_codes)
    remaining_codes = sorted(final_codes)

    fixed_messages = [
        _strategy_hint_to_text(h.get('code'), h.get('params'), lang=lang)
        for h in initial_hints
        if h.get('code') in fixed_codes
    ]
    remaining_messages = [
        _strategy_hint_to_text(h.get('code'), h.get('params'), lang=lang)
        for h in final_hints
        if h.get('code') in remaining_codes
    ]

    if auto_fix_applied and auto_fix_succeeded:
        title = "AI 已自动修复并返回更稳定的策略代码" if is_zh else "AI auto-fixed the strategy code and returned a more stable version"
    elif auto_fix_applied:
        title = "AI 尝试自动修复策略代码，但仍保留部分问题" if is_zh else "AI attempted to auto-fix the strategy code, but some issues still remain"
    else:
        title = "AI 已生成策略代码，并通过当前质检流程" if is_zh else "AI generated strategy code and it passed the current QA flow"

    returned_text = (
        "当前返回的是自动修复后的代码。"
        if returned_candidate == 'repaired' and is_zh else
        "The returned code is the auto-fixed version."
        if returned_candidate == 'repaired' else
        "当前返回的是首次生成的代码。"
        if is_zh else
        "The returned code is the initially generated version."
    )
    return {
        "title": title,
        "returned_text": returned_text,
        "fixed_messages": fixed_messages,
        "remaining_messages": remaining_messages,
    }


# Local mode: avoid heavy initialization during module import.
# Instantiate services lazily on first use to keep startup clean.
_strategy_service = None
_backtest_service = None

def get_strategy_service() -> StrategyService:
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service


def get_backtest_service() -> BacktestService:
    global _backtest_service
    if _backtest_service is None:
        _backtest_service = BacktestService()
    return _backtest_service


@strategy_blp.route('/strategies', methods=['GET'])
@login_required
def list_strategies():
    """
    List strategies for the current user.
    """
    try:
        user_id = g.user_id
        items = get_strategy_service().list_strategies(user_id=user_id)
        return jsonify({'code': 1, 'msg': 'success', 'data': {'strategies': items}})
    except Exception as e:
        logger.error(f"list_strategies failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'strategies': []}}), 500


@strategy_blp.route('/strategies/detail', methods=['GET'])
@login_required
def get_strategy_detail():
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404
        return jsonify({'code': 1, 'msg': 'success', 'data': st})
    except Exception as e:
        logger.error(f"get_strategy_detail failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/backtest', methods=['POST'])
@login_required
def run_strategy_backtest():
    try:
        payload = request.get_json() or {}
        user_id = g.user_id
        strategy_id = int(payload.get('strategyId') or 0)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'strategyId is required', 'data': None}), 400

        start_date_str = str(payload.get('startDate') or '').strip()
        end_date_str = str(payload.get('endDate') or '').strip()
        if not start_date_str or not end_date_str:
            return jsonify({'code': 0, 'msg': 'startDate and endDate are required', 'data': None}), 400

        strategy = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not strategy:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        resolver = StrategySnapshotResolver(user_id=user_id)
        snapshot = resolver.resolve(strategy, payload.get('overrideConfig') or {})
        snapshot['user_id'] = user_id

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)

        days_diff = (end_date - start_date).days
        timeframe = snapshot.get('timeframe') or '1D'
        if timeframe == '1m':
            max_days = 30
            max_range_text = '1 month'
        elif timeframe == '5m':
            max_days = 180
            max_range_text = '6 months'
        elif timeframe in ['15m', '30m']:
            max_days = 365
            max_range_text = '1 year'
        else:
            max_days = 1095
            max_range_text = '3 years'
        if days_diff > max_days:
            return jsonify({
                'code': 0,
                'msg': f'Backtest range exceeds limit: timeframe {timeframe} supports up to {max_range_text} ({max_days} days), but you selected {days_diff} days',
                'data': None
            }), 400

        svc = get_backtest_service()
        result = svc.run_strategy_snapshot(snapshot, start_date=start_date, end_date=end_date)
        ea = dict(result.get('executionAssumptions') or {})
        ea['commission'] = round(float(snapshot.get('commission') or 0), 6)
        ea['slippage'] = round(float(snapshot.get('slippage') or 0), 6)
        ea['strictMode'] = bool(snapshot.get('strict_mode', True))
        result['executionAssumptions'] = ea
        run_id = svc.persist_run(
            user_id=user_id,
            indicator_id=snapshot.get('indicator_id'),
            strategy_id=snapshot.get('strategy_id'),
            strategy_name=snapshot.get('strategy_name') or '',
            run_type=snapshot.get('run_type') or 'strategy_indicator',
            market=snapshot.get('market') or '',
            symbol=snapshot.get('symbol') or '',
            timeframe=snapshot.get('timeframe') or '',
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            initial_capital=float(snapshot.get('initial_capital') or 0),
            commission=float(snapshot.get('commission') or 0),
            slippage=float(snapshot.get('slippage') or 0),
            leverage=int(snapshot.get('leverage') or 1),
            trade_direction=str(snapshot.get('trade_direction') or 'long'),
            strategy_config=snapshot.get('strategy_config') or {},
            config_snapshot=snapshot.get('config_snapshot') or {},
            status='success',
            error_message='',
            result=result,
            code=snapshot.get('code') or '',
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': {'runId': run_id, 'result': result}})
    except ValueError as e:
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 400
    except Exception as e:
        logger.error(f"run_strategy_backtest failed: {str(e)}")
        logger.error(traceback.format_exc())
        try:
            payload = payload if isinstance(payload, dict) else {}
            strategy_id = int(payload.get('strategyId') or 0)
            strategy = get_strategy_service().get_strategy(strategy_id, user_id=g.user_id) if strategy_id else None
            if strategy:
                resolver = StrategySnapshotResolver(user_id=g.user_id)
                snapshot = resolver.resolve(strategy, payload.get('overrideConfig') or {})
                snapshot['user_id'] = g.user_id
                get_backtest_service().persist_run(
                    user_id=g.user_id,
                    indicator_id=snapshot.get('indicator_id'),
                    strategy_id=snapshot.get('strategy_id'),
                    strategy_name=snapshot.get('strategy_name') or '',
                    run_type=snapshot.get('run_type') or 'strategy_indicator',
                    market=snapshot.get('market') or '',
                    symbol=snapshot.get('symbol') or '',
                    timeframe=snapshot.get('timeframe') or '',
                    start_date_str=str(payload.get('startDate') or ''),
                    end_date_str=str(payload.get('endDate') or ''),
                    initial_capital=float(snapshot.get('initial_capital') or 0),
                    commission=float(snapshot.get('commission') or 0),
                    slippage=float(snapshot.get('slippage') or 0),
                    leverage=int(snapshot.get('leverage') or 1),
                    trade_direction=str(snapshot.get('trade_direction') or 'long'),
                    strategy_config=snapshot.get('strategy_config') or {},
                    config_snapshot=snapshot.get('config_snapshot') or {},
                    status='failed',
                    error_message=str(e),
                    result=None,
                    code=snapshot.get('code') or '',
                )
        except Exception:
            pass
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/backtest/history', methods=['GET'])
@login_required
def get_strategy_backtest_history():
    try:
        user_id = g.user_id
        strategy_id = int(request.args.get('strategyId') or request.args.get('id') or 0)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'strategyId is required', 'data': None}), 400
        limit = max(1, min(int(request.args.get('limit') or 50), 200))
        offset = max(0, int(request.args.get('offset') or 0))
        symbol = (request.args.get('symbol') or '').strip()
        market = (request.args.get('market') or '').strip()
        timeframe = (request.args.get('timeframe') or '').strip()
        rows = get_backtest_service().list_runs(
            user_id=user_id,
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
            symbol=symbol,
            market=market,
            timeframe=timeframe,
        )
        rows = [r for r in rows if str(r.get('run_type') or '').startswith('strategy_')]
        return jsonify({'code': 1, 'msg': 'success', 'data': rows})
    except Exception as e:
        logger.error(f"get_strategy_backtest_history failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/backtest/get', methods=['GET'])
@login_required
def get_strategy_backtest_run():
    try:
        user_id = g.user_id
        run_id = int(request.args.get('runId') or 0)
        if not run_id:
            return jsonify({'code': 0, 'msg': 'runId is required', 'data': None}), 400
        row = get_backtest_service().get_run(user_id=user_id, run_id=run_id)
        if not row or not str(row.get('run_type') or '').startswith('strategy_'):
            return jsonify({'code': 0, 'msg': 'run not found', 'data': None}), 404
        return jsonify({'code': 1, 'msg': 'success', 'data': row})
    except Exception as e:
        logger.error(f"get_strategy_backtest_run failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/create', methods=['POST'])
@login_required
def create_strategy():
    try:
        user_id = g.user_id
        payload = request.get_json() or {}
        # Use current user's ID
        payload['user_id'] = user_id
        payload['strategy_type'] = payload.get('strategy_type') or 'IndicatorStrategy'
        new_id = get_strategy_service().create_strategy(payload)
        return jsonify({'code': 1, 'msg': 'success', 'data': {'id': new_id}})
    except Exception as e:
        logger.error(f"create_strategy failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/batch-create', methods=['POST'])
@login_required
def batch_create_strategies():
    """
    Batch create strategies (multiple symbols)
    
    Request body:
        strategy_name: Base strategy name
        symbols: Array of symbols, e.g. ["Crypto:BTC/USDT", "Crypto:ETH/USDT"]
        ... other strategy config
    """
    try:
        user_id = g.user_id
        payload = request.get_json() or {}
        payload['user_id'] = user_id
        payload['strategy_type'] = payload.get('strategy_type') or 'IndicatorStrategy'
        
        result = get_strategy_service().batch_create_strategies(payload)
        
        if result['success']:
            return jsonify({
                'code': 1,
                'msg': f"Successfully created {result['total_created']} strategies",
                'data': result
            })
        else:
            return jsonify({
                'code': 0,
                'msg': 'Batch creation failed',
                'data': result
            })
    except Exception as e:
        logger.error(f"batch_create_strategies failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/batch-start', methods=['POST'])
@login_required
def batch_start_strategies():
    """
    Batch start strategies
    
    Request body:
        strategy_ids: Array of strategy IDs
        or
        strategy_group_id: Strategy group ID
    """
    try:
        user_id = g.user_id
        payload = request.get_json() or {}
        strategy_ids = payload.get('strategy_ids') or []
        strategy_group_id = payload.get('strategy_group_id')
        
        # If strategy_group_id provided, get all strategies in the group
        if strategy_group_id and not strategy_ids:
            strategy_ids = get_strategy_service().get_strategies_by_group(strategy_group_id, user_id=user_id)
        
        if not strategy_ids:
            return jsonify({'code': 0, 'msg': 'Please provide strategy IDs', 'data': None}), 400
        
        # Update database status first
        result = get_strategy_service().batch_start_strategies(strategy_ids, user_id=user_id)
        
        # Then start executor
        executor = get_trading_executor()
        for sid in result.get('success_ids', []):
            try:
                executor.start_strategy(sid)
            except Exception as e:
                logger.error(f"Failed to start executor for strategy {sid}: {e}")
        
        return jsonify({
            'code': 1 if result['success'] else 0,
            'msg': f"Successfully started {len(result.get('success_ids', []))} strategies",
            'data': result
        })
    except Exception as e:
        logger.error(f"batch_start_strategies failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/batch-stop', methods=['POST'])
@login_required
def batch_stop_strategies():
    """
    Batch stop strategies
    
    Request body:
        strategy_ids: Array of strategy IDs
        or
        strategy_group_id: Strategy group ID
    """
    try:
        user_id = g.user_id
        payload = request.get_json() or {}
        strategy_ids = payload.get('strategy_ids') or []
        strategy_group_id = payload.get('strategy_group_id')
        
        if strategy_group_id and not strategy_ids:
            strategy_ids = get_strategy_service().get_strategies_by_group(strategy_group_id, user_id=user_id)
        
        if not strategy_ids:
            return jsonify({'code': 0, 'msg': 'Please provide strategy IDs', 'data': None}), 400
        
        # Stop executor first
        executor = get_trading_executor()
        for sid in strategy_ids:
            try:
                executor.stop_strategy(sid)
            except Exception as e:
                logger.error(f"Failed to stop executor for strategy {sid}: {e}")
        
        # Then update database status
        result = get_strategy_service().batch_stop_strategies(strategy_ids, user_id=user_id)
        
        return jsonify({
            'code': 1 if result['success'] else 0,
            'msg': f"Successfully stopped {len(result.get('success_ids', []))} strategies",
            'data': result
        })
    except Exception as e:
        logger.error(f"batch_stop_strategies failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/batch-delete', methods=['DELETE'])
@login_required
def batch_delete_strategies():
    """
    Batch delete strategies
    
    Request body:
        strategy_ids: Array of strategy IDs
        or
        strategy_group_id: Strategy group ID
    """
    try:
        user_id = g.user_id
        payload = request.get_json() or {}
        strategy_ids = payload.get('strategy_ids') or []
        strategy_group_id = payload.get('strategy_group_id')
        
        if strategy_group_id and not strategy_ids:
            strategy_ids = get_strategy_service().get_strategies_by_group(strategy_group_id, user_id=user_id)
        
        if not strategy_ids:
            return jsonify({'code': 0, 'msg': 'Please provide strategy IDs', 'data': None}), 400
        
        # Stop executor first
        executor = get_trading_executor()
        for sid in strategy_ids:
            try:
                executor.stop_strategy(sid)
            except Exception as e:
                pass  # Ignore stop errors
        
        # Then delete
        result = get_strategy_service().batch_delete_strategies(strategy_ids, user_id=user_id)
        
        return jsonify({
            'code': 1 if result['success'] else 0,
            'msg': f"Successfully deleted {len(result.get('success_ids', []))} strategies",
            'data': result
        })
    except Exception as e:
        logger.error(f"batch_delete_strategies failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/update', methods=['PUT'])
@login_required
def update_strategy():
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400
        payload = request.get_json() or {}
        ok = get_strategy_service().update_strategy(strategy_id, payload, user_id=user_id)
        if not ok:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404
        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"update_strategy failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/delete', methods=['DELETE'])
@login_required
def delete_strategy():
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400
        ok = get_strategy_service().delete_strategy(strategy_id, user_id=user_id)
        return jsonify({'code': 1 if ok else 0, 'msg': 'success' if ok else 'failed', 'data': None})
    except Exception as e:
        logger.error(f"delete_strategy failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/trades', methods=['GET'])
@login_required
def get_trades():
    """Get trade records for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': {'trades': [], 'items': []}}), 400
        
        # Verify strategy belongs to user
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': {'trades': [], 'items': []}}), 404

        trading_config = st.get("trading_config") if isinstance(st.get("trading_config"), dict) else {}
        try:
            leverage = float(trading_config.get("leverage") or st.get("leverage") or 1.0)
        except Exception:
            leverage = 1.0
        if leverage <= 0:
            leverage = 1.0
        market_type = str(trading_config.get("market_type") or st.get("market_type") or "swap").strip().lower()
        if is_derivatives_market(market_type):
            market_type = "swap"
        from app.services.live_trading.records import ensure_strategy_trades_close_reason_column
        ensure_strategy_trades_close_reason_column()

        bot_type = str(trading_config.get("bot_type") or "").strip().lower()
        lang = str(request.args.get("lang") or request.headers.get("Accept-Language") or "zh")[:2].lower()
        if not lang.startswith("zh"):
            lang = "en"
        else:
            lang = "zh"
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, symbol, type, price, amount, value,
                       commission, commission_ccy, profit, close_reason,
                       matched_entry_price, grid_matched_profit, created_at
                FROM qd_strategy_trades
                WHERE strategy_id = ?
                ORDER BY id DESC
                """,
                (strategy_id,)
            )
            rows = cur.fetchall() or []
            cur.close()
        
        # Convert created_at to Unix seconds (UTC instant).
        # qd_strategy_trades.created_at is TIMESTAMP WITHOUT TIME ZONE; with PostgreSQL session UTC
        # the stored wall clock is UTC. Naive datetime must not use .timestamp() alone — that would
        # interpret it in the Python process local TZ and shift the instant (e.g. +8h on CN laptops).
        from datetime import datetime as _dt, timezone as _tz
        processed_rows = []
        for row in rows:
            trade = dict(row)
            created_at = trade.get('created_at')
            if created_at:
                if hasattr(created_at, 'timestamp'):
                    dt = created_at
                    if getattr(dt, 'tzinfo', None) is None:
                        dt = dt.replace(tzinfo=_tz.utc)
                    trade['created_at'] = int(dt.timestamp())
                elif isinstance(created_at, str):
                    try:
                        dt = _dt.fromisoformat(created_at.replace('Z', '+00:00'))
                        if getattr(dt, 'tzinfo', None) is None:
                            dt = dt.replace(tzinfo=_tz.utc)
                        trade['created_at'] = int(dt.timestamp())
                    except Exception:
                        pass
            from app.utils.trade_close_reason import enrich_trade_row
            trade = enrich_trade_row(trade, bot_type=bot_type, lang=lang)
            processed_rows.append(_normalize_trade_row_for_api(trade, leverage=leverage, market_type=market_type))
        
        # Frontend expects data.trades; keep data.items for compatibility with list-style components.
        return jsonify({'code': 1, 'msg': 'success', 'data': {'trades': processed_rows, 'items': processed_rows}})
    except Exception as e:
        logger.error(f"get_trades failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'trades': [], 'items': []}}), 500


@strategy_blp.route('/strategies/dry-run-deviation', methods=['GET'])
@login_required
def get_dry_run_deviation():
    """Quantify how far live fills drifted from backtest signal closes.

    For every recorded trade the service rebuilds the prior closed bar (the
    one a backtest would have used as the decision price) and computes per-
    trade slippage / latency, plus a summary verdict.
    """
    try:
        user_id = int(g.user_id)
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400
        limit = max(20, min(int(request.args.get('limit') or 200), 1000))

        from app.services.dry_run_deviation import DryRunDeviationService
        svc = DryRunDeviationService()
        report = svc.build_report(
            strategy_id=strategy_id,
            user_id=user_id,
            limit=limit,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': report})
    except Exception as exc:
        logger.error("get_dry_run_deviation failed: %s", exc)
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(exc), 'data': None}), 500


@strategy_blp.route('/strategies/positions', methods=['GET'])
@login_required
def get_positions():
    """Get position records for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': {'positions': [], 'items': []}}), 400
        
        # Verify strategy belongs to user
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': {'positions': [], 'items': []}}), 404

        trading_config = st.get("trading_config") if isinstance(st.get("trading_config"), dict) else {}
        try:
            leverage = float(trading_config.get("leverage") or st.get("leverage") or 1.0)
        except Exception:
            leverage = 1.0
        if leverage <= 0:
            leverage = 1.0
        market_type = str(trading_config.get("market_type") or st.get("market_type") or "swap").strip().lower()
        if is_derivatives_market(market_type):
            market_type = "swap"

        exchange_config = st.get("exchange_config") if isinstance(st.get("exchange_config"), dict) else {}
        from app.data_sources.crypto import resolve_crypto_venue

        price_exchange_id, price_market_type = resolve_crypto_venue(
            exchange_config=exchange_config,
            trading_config=trading_config,
            market_type=market_type,
        )
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                       unrealized_pnl, pnl_percent, equity, updated_at
                FROM qd_strategy_positions
                WHERE strategy_id = ?
                ORDER BY id DESC
                """,
                (strategy_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        if not rows:
            try:
                from app.services.live_trading.records import rebuild_positions_from_trades

                if rebuild_positions_from_trades(strategy_id):
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            SELECT id, strategy_id, symbol, side, size, entry_price, current_price, highest_price,
                                   unrealized_pnl, pnl_percent, equity, updated_at
                            FROM qd_strategy_positions
                            WHERE strategy_id = ?
                            ORDER BY id DESC
                            """,
                            (strategy_id,),
                        )
                        rows = cur.fetchall() or []
                        cur.close()
            except Exception as e:
                logger.warning("rebuild_positions_from_trades failed for strategy %s: %s", strategy_id, e)

        # Sync current price and PnL on read (frontend polls every few seconds).
        now = int(time.time())
        # Fetch prices once per symbol to reduce API calls.
        sym_to_price: dict[str, float] = {}
        for r in rows:
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            if sym in sym_to_price:
                continue
            try:
                t = DataSourceFactory.get_ticker(
                    "Crypto",
                    sym,
                    exchange_id=price_exchange_id,
                    market_type=price_market_type,
                ) or {}
                px = float(t.get("last") or t.get("close") or 0.0)
                if px > 0:
                    sym_to_price[sym] = px
            except Exception:
                continue

        # Apply to rows and persist best-effort
        out = []
        with get_db_connection() as db:
            cur = db.cursor()
            for r in rows:
                sym = (r.get("symbol") or "").strip()
                side = (r.get("side") or "").strip().lower()
                entry = float(r.get("entry_price") or 0.0)
                size = float(r.get("size") or 0.0)
                cp = float(sym_to_price.get(sym) or r.get("current_price") or 0.0)
                pnl = calc_unrealized_pnl(side, entry, cp, size)
                pct = calc_pnl_percent(entry, size, pnl, leverage=leverage, market_type=market_type)
                notional = calc_notional_value(entry, size)

                rr = dict(r)
                # 确保 entry_price 有值（如果数据库中是 NULL，使用计算出的 entry 值）
                if not rr.get("entry_price") or float(rr.get("entry_price") or 0.0) <= 0:
                    rr["entry_price"] = float(entry or 0.0)
                else:
                    rr["entry_price"] = float(rr.get("entry_price") or 0.0)
                rr["current_price"] = float(cp or 0.0)
                rr["unrealized_pnl"] = float(pnl)
                rr["pnl_percent"] = float(pct)
                rr["notional_value"] = float(notional)
                rr["margin_value"] = calc_margin_notional(notional, leverage, market_type)
                rr["updated_at"] = now
                out.append(rr)

                try:
                    cur.execute(
                        """
                        UPDATE qd_strategy_positions
                        SET current_price = ?, unrealized_pnl = ?, pnl_percent = ?, updated_at = NOW()
                        WHERE id = ?
                        """,
                        (float(cp or 0.0), float(pnl), float(pct), int(rr.get("id"))),
                    )
                except Exception:
                    pass
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': {'positions': out, 'items': out}})
    except Exception as e:
        logger.error(f"get_positions failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'positions': [], 'items': []}}), 500


@strategy_blp.route('/strategies/grid-resting-orders', methods=['GET'])
@login_required
def get_grid_resting_orders():
    """List resting grid limit orders tracked for a live grid bot."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': {'orders': [], 'items': []}}), 400

        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': {'orders': [], 'items': []}}), 404

        bot_type = str(st.get('bot_type') or (st.get('trading_config') or {}).get('bot_type') or '').lower()
        if bot_type != 'grid':
            return jsonify({'code': 0, 'msg': 'Not a grid strategy', 'data': {'orders': [], 'items': []}}), 400

        status = request.args.get('status', '')
        limit = request.args.get('limit', default=200, type=int)

        from app.services.grid.resting_orders_repo import GridRestingOrderRepository

        repo = GridRestingOrderRepository()
        rows = repo.list_for_strategy(strategy_id, status=status, limit=limit or 200)
        out = []
        for o in rows:
            out.append({
                'id': o.id,
                'strategy_id': o.strategy_id,
                'symbol': o.symbol,
                'cell_index': o.cell_index,
                'purpose': o.purpose,
                'side': o.side,
                'pos_side': o.pos_side,
                'reduce_only': o.reduce_only,
                'price': o.price,
                'quantity': o.quantity,
                'quote_amount': o.quote_amount,
                'client_order_id': o.client_order_id,
                'exchange_order_id': o.exchange_order_id,
                'status': o.status,
                'filled_quantity': o.filled_quantity,
                'avg_fill_price': o.avg_fill_price,
                'extra': o.extra or {},
            })
        return jsonify({'code': 1, 'msg': 'success', 'data': {'orders': out, 'items': out}})
    except Exception as e:
        logger.error("get_grid_resting_orders failed: %s", e)
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'orders': [], 'items': []}}), 500


def _build_strategy_equity_curve(user_id: int, strategy_id: int):
    st = get_strategy_service().get_strategy(strategy_id, user_id=user_id) or {}
    if not st:
        return None, 'Strategy not found'

    initial = float(st.get('initial_capital') or (st.get('trading_config') or {}).get('initial_capital') or 0)
    if initial <= 0:
        initial = 1000.0

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT created_at, profit, commission
            FROM qd_strategy_trades
            WHERE strategy_id = ?
            ORDER BY created_at ASC
            """,
            (strategy_id,)
        )
        rows = cur.fetchall() or []
        cur.execute(
            """
            SELECT COALESCE(SUM(unrealized_pnl), 0) AS u
            FROM qd_strategy_positions
            WHERE strategy_id = ?
            """,
            (strategy_id,),
        )
        prow = cur.fetchone() or {}
        cur.close()

    equity = initial
    curve = []
    for r in rows:
        try:
            # Net equity step: gross trade P&L minus exchange-synced fee.
            equity += float(r.get('profit') or 0) - float(r.get('commission') or 0)
        except Exception:
            pass
        created_at = r.get('created_at')
        if created_at and hasattr(created_at, 'timestamp'):
            ts = int(created_at.timestamp())
        elif created_at:
            ts = int(created_at)
        else:
            ts = int(time.time())
        curve.append({'time': ts, 'equity': round(equity, 2)})

    try:
        unreal = float(prow.get('u') or prow.get('U') or 0)
    except Exception:
        unreal = 0.0
    live_equity = float(equity) + unreal
    now_ts = int(time.time())
    if abs(unreal) > 1e-12 or not curve:
        curve.append({'time': now_ts, 'equity': round(live_equity, 2)})

    return curve, None


@strategy_blp.route('/strategies/equityCurve', methods=['GET'])
@login_required
def get_equity_curve():
    """Get equity curve for the current user's strategy."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': []}), 400

        curve, error = _build_strategy_equity_curve(user_id, strategy_id)
        if error:
            return jsonify({'code': 0, 'msg': error, 'data': []}), 404

        return jsonify({'code': 1, 'msg': 'success', 'data': curve})
    except Exception as e:
        logger.error(f"get_equity_curve failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500





@strategy_blp.route('/strategies/stop', methods=['POST'])
@login_required
def stop_strategy():
    """
    Stop a strategy for the current user.
    
    Params:
        id: Strategy ID
    """
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        
        if not strategy_id:
            return jsonify({
                'code': 0,
                'msg': 'Missing strategy id parameter',
                'data': None
            }), 400
        
        # Verify strategy belongs to user
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404
        
        # Get strategy type
        strategy_type = get_strategy_service().get_strategy_type(strategy_id)
        
        # Local backend: AI strategy executor was removed. Only indicator strategies are supported.
        if strategy_type == 'PromptBasedStrategy':
            return jsonify({'code': 0, 'msg': 'AI strategy has been removed; local edition does not support starting/stopping AI strategies', 'data': None}), 400

        # Indicator strategy
        get_trading_executor().stop_strategy(strategy_id)
        
        # Update strategy status
        get_strategy_service().update_strategy_status(strategy_id, 'stopped', user_id=user_id)
        
        return jsonify({
            'code': 1,
            'msg': 'Stopped successfully',
            'data': None
        })
        
    except Exception as e:
        logger.error(f"Failed to stop strategy: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': f'Failed to stop strategy: {str(e)}',
            'data': None
        }), 500


@strategy_blp.route('/strategies/start', methods=['POST'])
@login_required
def start_strategy():
    """
    Start a strategy for the current user.
    
    Params:
        id: Strategy ID
    """
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        
        if not strategy_id:
            return jsonify({
                'code': 0,
                'msg': 'Missing strategy id parameter',
                'data': None
            }), 400
        
        # Verify strategy belongs to user
        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404
        
        # Get strategy type
        strategy_type = get_strategy_service().get_strategy_type(strategy_id)

        # IndicatorStrategy and ScriptStrategy are executed by TradingExecutor.
        if strategy_type == 'PromptBasedStrategy':
            return jsonify({
                'code': 0,
                'msg': 'AI strategy has been removed; local edition does not support starting AI strategies',
                'data': None
            }), 400
        get_strategy_service().update_strategy_status(strategy_id, 'running', user_id=user_id)

        executor = get_trading_executor()
        success = executor.start_strategy(strategy_id)

        if not success:
            # If start failed, restore status
            get_strategy_service().update_strategy_status(strategy_id, 'stopped', user_id=user_id)
            detail = getattr(executor, "_last_start_failure", "") or ""
            msg = "Failed to start strategy executor"
            if detail:
                msg = f"{msg}: {detail}"
            return jsonify({'code': 0, 'msg': msg, 'data': {'detail': detail} if detail else None}), 500

        alive, hint = executor.wait_strategy_running(strategy_id, timeout=3.0)
        if not alive:
            get_strategy_service().update_strategy_status(strategy_id, 'stopped', user_id=user_id)
            msg = f"策略启动后立即退出: {hint}"
            return jsonify({
                'code': 0,
                'msg': msg,
                'data': {'detail': hint, 'status': 'stopped'},
            }), 500
        
        return jsonify({
            'code': 1,
            'msg': 'Started successfully',
            'data': None
        })
        
    except Exception as e:
        logger.error(f"Failed to start strategy: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': f'Failed to start strategy: {str(e)}',
            'data': None
        }), 500


@strategy_blp.route('/strategies/test-connection', methods=['POST'])
@login_required
def test_connection():
    """
    Test exchange connection.
    
    Request body:
        exchange_config: Exchange configuration (may contain credential_id or inline keys)
    """
    try:
        data = request.get_json() or {}
        
        # 记录请求数据（用于调试，但不记录敏感信息）
        logger.debug(f"Connection test request keys: {list(data.keys())}")
        
        # 获取交易所配置
        exchange_config = data.get('exchange_config', data)
        
        # Local deployment: no encryption/decryption; accept dict or JSON string.
        if isinstance(exchange_config, str):
            try:
                import json
                exchange_config = json.loads(exchange_config)
            except Exception:
                pass
        
        # 验证 exchange_config 是否为字典
        if not isinstance(exchange_config, dict):
            logger.error(f"Invalid exchange_config type: {type(exchange_config)}, data: {str(exchange_config)[:200]}")
            # Frontend expects HTTP 200 with {code:0} for business failures.
            return jsonify({'code': 0, 'msg': 'Invalid exchange config format; please check your payload', 'data': None})

        # Demo/testnet toggles and base_url are often sent on the JSON root while keys live under exchange_config.
        if isinstance(data, dict) and "exchange_config" in data:
            from app.services.live_trading.factory import merge_root_exchange_config_overlay

            exchange_config = merge_root_exchange_config_overlay(root=data, exchange_config=exchange_config)

        # Resolve credential_id → full config (merges credential keys with any overrides).
        # This allows the frontend to send just {credential_id: 5} without raw api_key/secret_key.
        from app.services.exchange_execution import resolve_exchange_config
        from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

        user_id = g.user_id if hasattr(g, 'user_id') else 1
        resolved = resolve_exchange_config(exchange_config, user_id=user_id)

        # 验证必要字段 (check resolved config after credential merge)
        ex_id = (resolved.get('exchange_id') or '').strip().lower()
        if not ex_id:
            return jsonify({'code': 0, 'msg': 'Please select an exchange', 'data': None})

        if ex_id in ('ibkr', 'mt5'):
            if not local_desktop_brokers_allowed():
                return jsonify({'code': 0, 'msg': desktop_broker_cloud_reject_message(), 'data': None})
            logger.info("Testing connection: exchange_id=%s (local desktop broker, skipping API key check)", ex_id)
        else:
            api_key = resolved.get('api_key', '')
            secret_key = resolved.get('secret_key', '')

            # 详细日志排查
            logger.info(f"Testing connection: exchange_id={resolved.get('exchange_id')}")
            if api_key:
                logger.info(f"API Key: {api_key[:5]}... (len={len(api_key)})")
            if secret_key:
                logger.info(f"Secret Key: {secret_key[:5]}... (len={len(secret_key)})")

            # 检查是否有特殊字符
            if api_key and api_key.strip() != api_key:
                logger.warning("API key contains leading/trailing whitespace")
            if secret_key and secret_key.strip() != secret_key:
                logger.warning("Secret key contains leading/trailing whitespace")

            if not api_key or not secret_key:
                return jsonify({'code': 0, 'msg': 'Please provide API key and secret key', 'data': None})

        # Pass the resolved config (with actual keys) to the service
        result = get_strategy_service().test_exchange_connection(resolved, user_id=user_id)
        
        if result['success']:
            return jsonify({'code': 1, 'msg': result.get('message') or 'Connection successful', 'data': result.get('data')})
        # Always return HTTP 200 for business-level failures.
        return jsonify({'code': 0, 'msg': result.get('message') or 'Connection failed', 'data': result.get('data')})
        
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': f'Connection test failed: {str(e)}',
            'data': None
        }), 500


@strategy_blp.route('/strategies/notifications', methods=['GET'])
@login_required
def get_strategy_notifications():
    """
    Strategy signal notifications for the current user.

    Query:
      - id: strategy id (optional)
      - limit: default 50, max 200
      - since_id: return rows with id > since_id (optional)
    """
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        limit = request.args.get('limit', type=int) or 50
        limit = max(1, min(200, int(limit)))
        since_id = request.args.get('since_id', type=int) or 0

        # Get user's strategy IDs for filtering notifications
        user_strategy_ids = []
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT id FROM qd_strategies_trading WHERE user_id = ?", (user_id,))
            rows = cur.fetchall() or []
            user_strategy_ids = [r.get('id') for r in rows if r.get('id')]
            cur.close()
        
        where = []
        args = []
        
        # Filter by user's strategies
        if strategy_id:
            if strategy_id in user_strategy_ids:
                where.append("strategy_id = ?")
                args.append(int(strategy_id))
            else:
                return jsonify({'code': 1, 'msg': 'success', 'data': {'items': []}})
        else:
            if user_strategy_ids:
                placeholders = ",".join(["?"] * len(user_strategy_ids))
                where.append(f"(strategy_id IN ({placeholders}) OR (strategy_id IS NULL AND user_id = ?))")
                args.extend(user_strategy_ids)
                args.append(user_id)
            else:
                # Only portfolio monitor notifications (strategy_id is NULL)
                where.append("strategy_id IS NULL AND user_id = ?")
                args.append(user_id)
        
        if since_id:
            where.append("id > ?")
            args.append(int(since_id))
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT *
                FROM qd_strategy_notifications
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(args + [int(limit)]),
            )
            rows = cur.fetchall() or []
            cur.close()

        # Convert created_at to UTC timestamp (seconds) for frontend
        from datetime import timezone as _dt_tz
        processed_rows = []
        for row in rows:
            item = dict(row)
            created_at = item.get('created_at')
            if created_at:
                if hasattr(created_at, 'timestamp'):
                    # 无时区 datetime：连接已 SET TIME ZONE UTC，按 UTC 解释再转 Unix，避免服务端本地 TZ 误判
                    if getattr(created_at, 'tzinfo', None) is None:
                        created_at = created_at.replace(tzinfo=_dt_tz.utc)
                    item['created_at'] = int(created_at.timestamp())
                elif isinstance(created_at, str):
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        item['created_at'] = int(dt.timestamp())
                    except Exception:
                        pass
            processed_rows.append(item)

        return jsonify({'code': 1, 'msg': 'success', 'data': {'items': processed_rows}})
    except Exception as e:
        logger.error(f"get_strategy_notifications failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'items': []}}), 500


@strategy_blp.route('/strategies/notifications/unread-count', methods=['GET'])
@login_required
def get_unread_notification_count():
    """
    Get unread notification count for the current user.
    Used by frontend header badge (cap at 99+ on UI).
    """
    try:
        user_id = g.user_id

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("SELECT id FROM qd_strategies_trading WHERE user_id = ?", (user_id,))
            rows = cur.fetchall() or []
            user_strategy_ids = [r.get('id') for r in rows if r.get('id')]
            cur.close()

        where = ["is_read = 0"]
        args = []

        if user_strategy_ids:
            placeholders = ",".join(["?"] * len(user_strategy_ids))
            where.append(f"(strategy_id IN ({placeholders}) OR (strategy_id IS NULL AND user_id = ?))")
            args.extend(user_strategy_ids)
            args.append(user_id)
        else:
            where.append("strategy_id IS NULL AND user_id = ?")
            args.append(user_id)

        where_sql = "WHERE " + " AND ".join(where)

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"SELECT COUNT(1) AS cnt FROM qd_strategy_notifications {where_sql}",
                tuple(args),
            )
            cnt = int((cur.fetchone() or {}).get("cnt") or 0)
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': {'unread': cnt}})
    except Exception as e:
        logger.error(f"get_unread_notification_count failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'unread': 0}}), 500


@strategy_blp.route('/strategies/notifications/read', methods=['POST'])
@login_required
def mark_notification_read():
    """Mark a single notification as read for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json(force=True, silent=True) or {}
        notification_id = data.get('id')
        if not notification_id:
            return jsonify({'code': 0, 'msg': 'Missing id'}), 400

        # Update notifications for user's strategies OR portfolio monitor notifications
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_strategy_notifications SET is_read = 1 
                WHERE id = ? AND (
                    strategy_id IN (SELECT id FROM qd_strategies_trading WHERE user_id = ?)
                    OR (strategy_id IS NULL AND user_id = ?)
                )
                """,
                (int(notification_id), user_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success'})
    except Exception as e:
        logger.error(f"mark_notification_read failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500


@strategy_blp.route('/strategies/notifications/read-all', methods=['POST'])
@login_required
def mark_all_notifications_read():
    """Mark all notifications as read for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_strategy_notifications SET is_read = 1 
                WHERE strategy_id IN (SELECT id FROM qd_strategies_trading WHERE user_id = ?)
                   OR (strategy_id IS NULL AND user_id = ?)
                """,
                (user_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success'})
    except Exception as e:
        logger.error(f"mark_all_notifications_read failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500


@strategy_blp.route('/strategies/notifications/clear', methods=['DELETE'])
@login_required
def clear_notifications():
    """Clear all notifications for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                DELETE FROM qd_strategy_notifications 
                WHERE strategy_id IN (SELECT id FROM qd_strategies_trading WHERE user_id = ?)
                   OR (strategy_id IS NULL AND user_id = ?)
                """,
                (user_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success'})
    except Exception as e:
        logger.error(f"clear_notifications failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500


# ===== Script Strategy Endpoints =====

@strategy_blp.route('/strategies/verify-code', methods=['POST'])
@login_required
def verify_strategy_code():
    """Verify script strategy code syntax and safety."""
    try:
        payload = request.get_json() or {}
        code = payload.get('code', '')
        if not code.strip():
            return jsonify({'success': False, 'message': 'Code is empty'})

        validation = _validate_strategy_code_internal(code)
        return jsonify(validation)
    except Exception as e:
        logger.error(f"verify_strategy_code failed: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@strategy_blp.route('/strategies/ai-generate', methods=['POST'])
@login_required
def ai_generate_strategy():
    """Generate strategy code or suggest template parameter updates using AI."""
    try:
        payload = request.get_json() or {}
        lang = _request_lang()
        prompt = payload.get('prompt', '')
        if not prompt.strip():
            return jsonify({'code': '', 'msg': _strategy_ai_text('prompt_empty', lang), 'params': None})

        intent = (payload.get('intent') or 'generate_code').strip()
        from app.services.llm import LLMService
        llm = LLMService()
        api_key = llm.get_api_key()
        if not api_key:
            return jsonify({'code': '', 'msg': _strategy_ai_text('no_llm_key', lang), 'params': None})

        from app.services.billing_service import get_billing_service
        billing = get_billing_service()
        user_id = g.user_id
        ok, billing_msg = billing.check_and_consume(
            user_id=user_id,
            feature='ai_code_gen',
            reference_id=f"ai_strategy_{intent}_{user_id}_{int(time.time())}"
        )
        if not ok:
            msg = f'积分不足: {billing_msg}' if _is_zh_lang(lang) and billing_msg else _strategy_ai_text('insufficient_credits', lang)
            return jsonify({'code': '', 'msg': msg, 'params': None})

        if intent == 'bot_recommend':
            # ── Detect (market, symbol) from prompt and fetch real K-lines ──
            # Symbol detection is delegated to ai_bot_symbol_detect so the
            # logic is unit-testable and trivial to extend with new tickers.
            # The route only knows: (a) what market it ended up being, and
            # (b) what kline timeframe to ask for that market.
            market_data_section = ""
            detected_market: str = ""
            detected_symbol: Optional[str] = None
            try:
                from app.services.ai_bot_symbol_detect import detect_market_and_symbol
                from app.services.broker_market_policy import (
                    BOT_TYPE_MARKETS, allowed_bot_types,
                )
                hit = detect_market_and_symbol(prompt)
                if hit:
                    detected_market, detected_symbol = hit

                if detected_symbol:
                    from app.services.kline import KlineService
                    ks = KlineService()
                    # Forex / USStock data sources may not return 4h candles;
                    # try the broker's preferred frame first then degrade.
                    candidate_frames = (
                        ('4h', '1d', '1h')
                        if detected_market == 'Crypto'
                        else ('1d', '4h', '1h')
                    )
                    klines = []
                    tf_label = ''
                    for tf in candidate_frames:
                        try:
                            klines = ks.get_kline(
                                market=detected_market,
                                symbol=detected_symbol,
                                timeframe=tf,
                                limit=50 if tf in ('4h', '1h') else 30,
                            ) or []
                        except Exception as kl_err:
                            logger.warning(
                                "[AI Bot] kline fetch failed market=%s sym=%s tf=%s: %s",
                                detected_market, detected_symbol, tf, kl_err,
                            )
                            klines = []
                        if klines and len(klines) >= 5:
                            tf_label = tf
                            break

                    if klines and len(klines) >= 5:
                        closes = [float(k.get('close', 0)) for k in klines if k.get('close')]
                        highs = [float(k.get('high', 0)) for k in klines if k.get('high')]
                        lows = [float(k.get('low', 0)) for k in klines if k.get('low')]
                        volumes = [float(k.get('volume', 0)) for k in klines if k.get('volume')]
                        current_price = closes[-1] if closes else 0
                        high_recent = max(highs) if highs else 0
                        low_recent = min(lows) if lows else 0
                        avg_price = sum(closes) / len(closes) if closes else 0
                        avg_volume = sum(volumes) / len(volumes) if volumes else 0
                        price_change_pct = ((closes[-1] - closes[0]) / closes[0] * 100) if closes[0] else 0

                        sma5 = sum(closes[-5:]) / min(5, len(closes[-5:])) if len(closes) >= 5 else avg_price
                        sma20 = sum(closes[-20:]) / min(20, len(closes[-20:])) if len(closes) >= 20 else avg_price
                        volatility = ((high_recent - low_recent) / avg_price * 100) if avg_price else 0

                        # Forex/USStock often have no volume data — omit the
                        # noisy 'Avg Volume: 0.00' line in that case so the
                        # LLM doesn't read into a meaningless number.
                        vol_line = (
                            f"Avg Volume: {avg_volume:.2f}\n"
                            if avg_volume > 0 else ""
                        )

                        market_data_section = (
                            f"\n\n=== REAL-TIME MARKET DATA for {detected_symbol} "
                            f"(market={detected_market}, last {len(klines)} candles, {tf_label} timeframe) ===\n"
                            f"Current Price: {current_price}\n"
                            f"Period High: {high_recent}\n"
                            f"Period Low: {low_recent}\n"
                            f"Price Change: {price_change_pct:+.2f}%\n"
                            f"Average Price: {avg_price:.4f}\n"
                            f"SMA(5): {sma5:.4f}\n"
                            f"SMA(20): {sma20:.4f}\n"
                            f"Trend: {'Bullish (SMA5 > SMA20)' if sma5 > sma20 else 'Bearish (SMA5 < SMA20)'}\n"
                            f"Volatility (range/avg): {volatility:.2f}%\n"
                            f"{vol_line}"
                            f"Recent 10 closes: {[round(c, 4) for c in closes[-10:]]}\n"
                            f"=== END MARKET DATA ===\n\n"
                            f"IMPORTANT: Use the REAL market data above to set realistic parameters. "
                            f"For grid bots, set upperPrice/lowerPrice based on the actual Period High/Low and current volatility. "
                            f"For trend bots, consider the current trend direction. "
                            f"For DCA bots, consider the price level and change percentage."
                        )
                        logger.info(
                            "[AI Bot] Fetched market data for %s/%s: price=%s, range=[%s, %s], change=%+.2f%%",
                            detected_market, detected_symbol,
                            current_price, low_recent, high_recent, price_change_pct,
                        )
                    elif detected_symbol:
                        # Symbol was identified but no data came back. Tell
                        # the LLM that explicitly so it can recommend a more
                        # conservative bot type or fall back to user inputs.
                        market_data_section = (
                            f"\n\nNOTE: Symbol {detected_symbol} ({detected_market}) was identified "
                            f"but no recent K-line data was available. Recommend conservative "
                            f"defaults and tell the user to manually verify the upper/lower bounds.\n"
                        )
                        logger.warning(
                            "[AI Bot] No klines returned for market=%s sym=%s; falling back to no-data prompt",
                            detected_market, detected_symbol,
                        )
            except Exception as mkt_err:
                logger.warning(f"[AI Bot] Failed to fetch market data: {mkt_err}")

            # Tell the LLM which bot types are usable for this market so it
            # doesn't recommend e.g. grid on USStock (overnight gap risk).
            if detected_market:
                _allowed_bots_for_market = sorted(allowed_bot_types(detected_market)) or ['grid', 'martingale', 'trend', 'dca']
            else:
                _allowed_bots_for_market = ['grid', 'martingale', 'trend', 'dca']
            _market_constraint_line = (
                f"\nIMPORTANT MARKET CONSTRAINT: The detected market is "
                f"'{detected_market or 'Crypto'}'. Allowed bot types for this market are: "
                f"{_allowed_bots_for_market}. Do NOT recommend a botType outside this list.\n"
                if detected_market else ""
            )
            # Quote-currency hint for capital field labelling (USD for
            # forex/stock, USDT for crypto). Pure cosmetics for the LLM's
            # 'reason' string.
            _quote_label = 'USD' if detected_market in ('USStock', 'Forex') else 'USDT'

            system_prompt = (
                "You are an expert quantitative trading advisor. The user wants to create an automated trading bot.\n"
                "Based on their description AND the real-time market data provided, recommend one of the four bot types and provide optimal parameters.\n\n"
                "Available bot types and their parameter schemas:\n"
                "1. grid - Grid Trading: {upperPrice, lowerPrice, gridCount: int(5-100), amountPerGrid, gridMode: 'arithmetic'|'geometric', "
                "gridDirection: 'long'|'short'|'neutral', initialPositionPct: 0-100 (initial market position %), "
                "boundaryAction: 'pause'|'stop_loss'|'hold', adaptiveBounds: true, adaptiveAtrMult: 2.0, waterfallProtection: true, waterfallDropPct: 0.03; "
                "martingale: waterfallProtection: true, waterfallDropPct: 0.04}\n"
                "2. martingale - Martingale: {initialAmount: number, multiplier: number(1.1-3.0), maxLayers: int(2-10), priceDropPct: number(1-20), takeProfitPct: number(1-50)}\n"
                "3. trend - Trend Following: {maPeriod: int(5-200), maType: 'SMA'|'EMA', confirmBars: int(1-5), positionPct: number(10-100), direction: 'long'|'short'|'both'}\n"
                "4. dca - DCA (Dollar-Cost Averaging): {amountEach: number, frequency: 'every_bar'|'hourly'|'4h'|'daily'|'weekly'|'biweekly'|'monthly', totalBudget: number, dipBuyEnabled: bool, dipThreshold: number(1-30)}\n\n"
                f"Bot type x market matrix (single source of truth from broker_market_policy): {dict(BOT_TYPE_MARKETS)}\n"
                f"{_market_constraint_line}"
                "Also suggest base config:\n"
                f"- marketCategory: 'Crypto'|'USStock'|'Forex' (must match the detected market: '{detected_market or 'Crypto'}')\n"
                f"- symbol: string (e.g. 'BTC/USDT' for Crypto, 'XAU/USD' or 'EUR/USD' for Forex, 'TSLA' for USStock)\n"
                "- timeframe: '1m'|'5m'|'15m'|'1h'|'4h'|'1d'\n"
                "- marketType: 'swap'|'spot' (USStock and Forex are always 'spot')\n"
                "- leverage: int(1-125, only for swap; ignored on spot/USStock/Forex)\n"
                f"- initialCapital: number (in {_quote_label})\n\n"
                "Risk config:\n"
                "- stopLossPct: number(0-100)\n"
                "- takeProfitPct: number(0-1000)\n"
                "- maxPosition: number\n\n"
                "CRITICAL: If real-time market data is provided, you MUST use it to set realistic and accurate parameters.\n"
                "For example, for grid trading, the upperPrice and lowerPrice MUST be derived from the actual price range in the market data.\n"
                "IMPORTANT: Do NOT set initialCapital in baseConfig - leave it as 0 or omit it. The user will enter their own investment amount.\n"
                "Also do NOT set amountPerGrid, initialAmount(for martingale), or totalBudget(for DCA) - these will be auto-calculated from the user's capital.\n\n"
                "Return ONLY a single JSON object with this structure:\n"
                "{\n"
                '  "botType": "grid"|"martingale"|"trend"|"dca",\n'
                '  "botName": "descriptive name",\n'
                '  "reason": "brief explanation in user\'s language, mention the market analysis",\n'
                '  "baseConfig": {marketCategory, symbol, timeframe, marketType, leverage, initialCapital},\n'
                '  "strategyParams": {... type-specific params ...},\n'
                '  "riskConfig": {stopLossPct, takeProfitPct, maxPosition}\n'
                "}\n"
                "Do not use markdown fences. Respond with valid JSON only."
            )

            user_content = f"User request:\n{prompt.strip()}{market_data_section}"

            content = llm.call_llm_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                model=llm.get_code_generation_model(),
                temperature=0.4,
                use_json_mode=False
            )

            raw = (content or '').strip()
            if raw.startswith('```'):
                raw = re.sub(r'^```[a-zA-Z]*', '', raw).strip()
                if raw.endswith('```'):
                    raw = raw[:-3].strip()
            result = None
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'\{[\s\S]*\}', raw)
                if m:
                    try:
                        result = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        result = None
            if not isinstance(result, dict) or 'botType' not in result:
                return jsonify({'code': '', 'params': None, 'bot_recommend': None,
                                'msg': 'AI did not return valid bot recommendation'})
            valid_types = ('grid', 'martingale', 'trend', 'dca')
            if result.get('botType') not in valid_types:
                result['botType'] = 'grid'

            # Cross-check the LLM's botType against the per-market matrix and
            # downgrade if it picked something incompatible (e.g. 'grid' for
            # a USStock symbol where overnight gaps blow up grid bots, or
            # 'martingale' for Forex where margin rules differ). 'dca' is
            # the universal safe fallback because it works on every market.
            if detected_market:
                _allowed_for_mkt = allowed_bot_types(detected_market)
                if _allowed_for_mkt and result.get('botType') not in _allowed_for_mkt:
                    fallback = 'dca' if 'dca' in _allowed_for_mkt else sorted(_allowed_for_mkt)[0]
                    logger.info(
                        "[AI Bot] Downgrading botType=%s -> %s for market=%s (incompatible)",
                        result.get('botType'), fallback, detected_market,
                    )
                    result['botType'] = fallback

            # Force baseConfig.marketCategory to the detected market so the
            # frontend wizard's applyAiPreset lights up the correct market
            # radio + credential filter immediately. If detection failed we
            # leave whatever the LLM said (or default to Crypto).
            base_cfg = result.get('baseConfig') if isinstance(result.get('baseConfig'), dict) else {}
            if detected_market:
                base_cfg['marketCategory'] = detected_market
            elif not base_cfg.get('marketCategory'):
                base_cfg['marketCategory'] = 'Crypto'
            # USStock and Forex on QuantDinger are always spot; lock it so
            # the LLM can't accidentally suggest 'swap' which would later
            # fail broker_market_policy validation.
            if base_cfg.get('marketCategory') in ('USStock', 'Forex'):
                base_cfg['marketType'] = 'spot'
                base_cfg['leverage'] = 1
            # If we successfully detected the symbol from prompt, prefer the
            # canonical form over whatever the LLM echoed back (e.g. it
            # might say 'XAU' instead of the data-source-friendly 'XAU/USD').
            if detected_symbol:
                base_cfg['symbol'] = detected_symbol
            result['baseConfig'] = base_cfg

            if result.get('botType') == 'dca':
                params = result.get('strategyParams') if isinstance(result.get('strategyParams'), dict) else {}
                freq = str(params.get('frequency') or '').strip().lower()
                allowed = {'every_bar', 'hourly', '4h', 'daily', 'weekly', 'biweekly', 'monthly'}
                if freq and freq not in allowed:
                    params['frequency'] = 'daily'
                    result['strategyParams'] = params
            return jsonify({'code': '', 'params': None, 'bot_recommend': result, 'msg': 'success'})

        if intent == 'adjust_params':
            template_key = payload.get('template_key') or ''
            current_params = payload.get('params') or {}
            code_snapshot = (payload.get('code') or '')[:8000]
            system_prompt = """You tune quantitative strategy template parameters from the user's request.
Return ONLY a single JSON object: keys are parameter names (strings), values are JSON numbers or booleans.
You may return a partial object (only keys that should change) or a full object.
Do not use markdown fences, do not add explanations before or after the JSON."""

            user_content = (
                f"Template key: {template_key}\n"
                f"Current parameters (JSON):\n{json.dumps(current_params, ensure_ascii=False)}\n\n"
                f"Strategy code excerpt (context):\n{code_snapshot}\n\n"
                f"User request:\n{prompt.strip()}\n\n"
                "Respond with JSON only."
            )

            content = llm.call_llm_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                model=llm.get_code_generation_model(),
                temperature=0.3,
                use_json_mode=False
            )

            raw = (content or '').strip()
            if raw.startswith('```'):
                raw = re.sub(r'^```[a-zA-Z]*', '', raw).strip()
                if raw.endswith('```'):
                    raw = raw[:-3].strip()
            updates = None
            try:
                updates = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'\{[\s\S]*\}', raw)
                if m:
                    try:
                        updates = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        updates = None
            if not isinstance(updates, dict):
                return jsonify({'code': '', 'params': None, 'msg': _strategy_ai_text('invalid_json_params', lang)})
            return jsonify({'code': '', 'params': updates, 'msg': _strategy_ai_text('success', lang)})

        system_prompt = """You are a quantitative trading strategy code generator.
Generate Python strategy code that follows this framework:
- def on_init(ctx): Initialize strategy parameters using ctx.param(name, default)
- def on_bar(ctx, bar): Core logic called on each K-line bar
  - bar supports both bar.close and bar['close'] access, and has: open, high, low, close, volume, timestamp
  - ctx.buy(price, amount), ctx.sell(price, amount), ctx.close_position()
  - ctx.position supports both numeric checks and dict-style fields:
    - if not ctx.position / if ctx.position > 0 / if ctx.position < 0
    - ctx.position['side'], ctx.position['size'], ctx.position['entry_price']
  - ctx.balance, ctx.equity
  - ctx.bars(n) to get last N bars, ctx.log(message) to log
- def on_order_filled(ctx, order): Optional callback when order fills
- def on_stop(ctx): Optional cleanup when strategy stops

Return ONLY the Python code, no explanations.

Quality rules:
- Always define both on_init(ctx) and on_bar(ctx, bar)
- Prefer reading defaults via ctx.param(...)
- Use ctx.buy / ctx.sell / ctx.close_position for order intent
- Generated code must compile cleanly
- Avoid markdown fences or explanatory text
"""

        extra = ''
        template_key = payload.get('template_key')
        params = payload.get('params')
        code_ctx = (payload.get('code') or '').strip()
        if template_key or params is not None or code_ctx:
            extra_parts = []
            if template_key:
                extra_parts.append(f"Current template key: {template_key}")
            if isinstance(params, dict) and params:
                extra_parts.append('Current template parameters (JSON):\n' + json.dumps(params, ensure_ascii=False))
            if code_ctx:
                extra_parts.append('Current code (may be long):\n' + code_ctx[:12000])
            extra = '\n\n' + '\n\n'.join(extra_parts)

        user_prompt = prompt.strip() + extra

        content = llm.call_llm_api(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=llm.get_code_generation_model(),
            temperature=0.7,
            use_json_mode=False
        )

        content = content.strip()
        if content.startswith("```python"):
            content = content[9:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        AUTO_FIX_HINT_CODES = {
            'MISSING_ON_INIT',
            'MISSING_ON_BAR',
        }

        def _needs_auto_fix_strategy(validation: dict) -> bool:
            if not validation.get('success'):
                return True
            return any(h.get('code') in AUTO_FIX_HINT_CODES for h in (validation.get('hints') or []))

        def _format_strategy_validation_issues(validation: dict) -> str:
            issues = []
            if not validation.get('success'):
                issues.append(f"- Verification failed: {validation.get('message')}")
                if validation.get('details'):
                    issues.append(f"- Details: {validation.get('details')}")
            for hint in validation.get('hints') or []:
                code_name = hint.get('code') or 'UNKNOWN'
                params_obj = hint.get('params') or {}
                if params_obj:
                    issues.append(f"- Hint {code_name}: {json.dumps(params_obj, ensure_ascii=False)}")
                else:
                    issues.append(f"- Hint {code_name}")
            return "\n".join(issues) if issues else "- No issues provided"

        def _repair_strategy_code_via_llm(bad_code: str, validation: dict) -> str:
            repair_prompt = (
                "You produced QuantDinger strategy script code that failed automatic validation. "
                "Fix the code while preserving the user's trading idea. Return one full replacement script only.\n\n"
                f"# Original user request\n{prompt.strip()}\n\n"
                f"# Validation issues to fix\n{_format_strategy_validation_issues(validation)}\n\n"
                "# Current code\n```python\n"
                + bad_code.strip()
                + "\n```\n\n"
                "# Repair requirements\n"
                "- Must define both on_init(ctx) and on_bar(ctx, bar).\n"
                "- Must compile and run in QuantDinger strategy runtime.\n"
                "- Prefer ctx.param(...) for defaults.\n"
                "- Use ctx.buy / ctx.sell / ctx.close_position for actions.\n"
                "- Return Python only, no markdown, no explanation."
            )
            repaired_content = llm.call_llm_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": repair_prompt},
                ],
                model=llm.get_code_generation_model(),
                temperature=0.2,
                use_json_mode=False
            )
            repaired_content = (repaired_content or '').strip()
            if repaired_content.startswith("```python"):
                repaired_content = repaired_content[9:]
            elif repaired_content.startswith("```"):
                repaired_content = repaired_content[3:]
            if repaired_content.endswith("```"):
                repaired_content = repaired_content[:-3]
            return repaired_content.strip() or bad_code

        validation = _validate_strategy_code_internal(content)
        debug = {
            'auto_fix_applied': False,
            'auto_fix_succeeded': False,
            'returned_candidate': 'initial',
            'initial_validation': _strategy_debug_summary(validation),
            'final_validation': _strategy_debug_summary(validation),
        }
        debug['human_summary'] = _strategy_human_summary(validation, validation, False, False, 'initial', lang=lang)

        if _needs_auto_fix_strategy(validation):
            logger.warning("ai_generate_strategy produced code needing auto-fix: %s", _format_strategy_validation_issues(validation))
            try:
                repaired = _repair_strategy_code_via_llm(content, validation)
                repaired_validation = _validate_strategy_code_internal(repaired)
                debug = {
                    'auto_fix_applied': True,
                    'auto_fix_succeeded': repaired_validation.get('success', False),
                    'returned_candidate': 'repaired' if repaired_validation.get('success') else 'initial',
                    'initial_validation': _strategy_debug_summary(validation),
                    'final_validation': _strategy_debug_summary(repaired_validation),
                }
                debug['human_summary'] = _strategy_human_summary(
                    validation,
                    repaired_validation,
                    True,
                    repaired_validation.get('success', False),
                    'repaired' if repaired_validation.get('success') else 'initial',
                    lang=lang
                )
                logger.info("ai_generate_strategy debug=%s", json.dumps(debug, ensure_ascii=False))
                if repaired_validation.get('success'):
                    content = repaired
                else:
                    logger.warning("ai_generate_strategy auto-fix failed, keeping initial candidate")
            except Exception as repair_err:
                debug = {
                    'auto_fix_applied': True,
                    'auto_fix_succeeded': False,
                    'returned_candidate': 'initial',
                    'initial_validation': _strategy_debug_summary(validation),
                    'final_validation': _strategy_debug_summary(validation),
                    'auto_fix_error': str(repair_err),
                }
                debug['human_summary'] = _strategy_human_summary(validation, validation, True, False, 'initial', lang=lang)
                logger.error("ai_generate_strategy auto-fix failed: %s", repair_err)
        else:
            debug['human_summary'] = _strategy_human_summary(validation, validation, False, False, 'initial', lang=lang)
            logger.info("ai_generate_strategy debug=%s", json.dumps(debug, ensure_ascii=False))

        if content:
            return jsonify({'code': content, 'msg': _strategy_ai_text('success', lang), 'params': None, 'debug': debug})
        else:
            return jsonify({'code': '', 'msg': _strategy_ai_text('ai_empty_result', lang), 'params': None, 'debug': debug})
    except Exception as e:
        logger.error(f"ai_generate_strategy failed: {str(e)}")
        return jsonify({'code': '', 'msg': str(e), 'params': None, 'debug': None})


@strategy_blp.route('/strategies/performance', methods=['GET'])
@login_required
def get_strategy_performance():
    """Get strategy performance metrics (aggregated from equity curve and trades)."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Strategy ID required'})

        equity_data, error = _build_strategy_equity_curve(user_id, strategy_id)
        if error:
            return jsonify({'code': 0, 'msg': error, 'data': None}), 404

        latest_equity = float(equity_data[-1].get('equity') or 0) if equity_data else 0.0
        first_equity = float(equity_data[0].get('equity') or 0) if equity_data else latest_equity
        total_return = latest_equity - first_equity
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'equity_curve': equity_data,
                'latest_equity': round(latest_equity, 2),
                'total_return': round(total_return, 2),
                'points': len(equity_data),
            }
        })
    except Exception as e:
        logger.error(f"get_strategy_performance failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500


@strategy_blp.route('/strategies/logs', methods=['GET'])
@login_required
def get_strategy_logs():
    """Get strategy running logs."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id')
        limit = int(request.args.get('limit', 200))
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Strategy ID required'})

        st = get_strategy_service().get_strategy(int(strategy_id), user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found'}), 404

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, strategy_id, level, message, timestamp
                FROM qd_strategy_logs
                WHERE strategy_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(strategy_id), limit)
            )
            rows = cur.fetchall() or []
            cur.close()

        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            rr = dict(r)
            msg = str(rr.get('message') or '')
            if msg.startswith('tick price=') or msg.startswith('tick price '):
                continue
            ts = rr.get('timestamp')
            if ts is not None:
                from app.utils.timeutil import to_utc_iso
                iso = to_utc_iso(ts)
                rr['timestamp'] = iso if iso is not None else str(ts)
            out.append(rr)
        # Already ORDER BY id DESC — newest first for the UI log panel.
        return jsonify({'code': 1, 'msg': 'success', 'data': out})
    except Exception as e:
        if PgUndefinedTable is not None and isinstance(e, PgUndefinedTable):
            return jsonify({'code': 1, 'msg': 'success', 'data': []})
        el = str(e).lower()
        if 'qd_strategy_logs' in el and ('does not exist' in el or 'no such table' in el):
            return jsonify({'code': 1, 'msg': 'success', 'data': []})
        logger.error(f"get_strategy_logs failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500
# openapi-compat: legacy import name
strategy_bp = strategy_blp
