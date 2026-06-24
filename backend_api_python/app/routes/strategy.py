"""
Trading Strategy API Routes
"""
from flask import g, jsonify, request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import json
import re
import traceback
import time

from app.services.strategy_compiler import StrategyCompiler
from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app import get_trading_executor
from app.utils.logger import get_logger
from app.utils.db import get_db_connection

from app.utils.auth import login_required

logger = get_logger(__name__)

# Register split strategy route modules on the shared blueprint.
from app.routes import strategy_account_routes  # noqa: E402,F401
from app.routes import strategy_backtest_routes  # noqa: E402,F401
from app.routes import strategy_deviation_routes  # noqa: E402,F401
from app.routes import strategy_grid_routes  # noqa: E402,F401
from app.routes import strategy_ledger_routes  # noqa: E402,F401
from app.routes import strategy_logs_routes  # noqa: E402,F401
from app.routes import strategy_notifications  # noqa: E402,F401
from app.routes import strategy_positions_routes  # noqa: E402,F401
from app.routes import strategy_review_routes  # noqa: E402,F401
from app.routes import script_source_routes  # noqa: E402,F401


def _strategy_live_lock_key(strategy: Dict[str, Any], user_id: int) -> Optional[Tuple[Any, ...]]:
    """Return the account/symbol key that cannot run twice for live strategies."""
    execution_mode = str(strategy.get("execution_mode") or "signal").strip().lower()
    if execution_mode != "live":
        return None

    trading_config = strategy.get("trading_config") if isinstance(strategy.get("trading_config"), dict) else {}
    exchange_config = strategy.get("exchange_config") if isinstance(strategy.get("exchange_config"), dict) else {}

    try:
        from app.services.exchange_execution import resolve_exchange_config
        from app.services.live_trading.leg_context import credential_id_from_exchange_config
        from app.services.live_trading.records import normalize_strategy_symbol

        resolved_exchange = resolve_exchange_config(exchange_config, user_id=int(user_id or strategy.get("user_id") or 1))
        exchange_id = str(
            resolved_exchange.get("exchange_id")
            or exchange_config.get("exchange_id")
            or ""
        ).strip().lower()
        if not exchange_id:
            return None

        credential_id = int(credential_id_from_exchange_config(resolved_exchange) or credential_id_from_exchange_config(exchange_config) or 0)
        credential_key: Any = credential_id if credential_id > 0 else f"inline:{exchange_id}"

        market_type = str(
            trading_config.get("market_type")
            or strategy.get("market_type")
            or resolved_exchange.get("market_type")
            or "swap"
        ).strip().lower()
        if market_type in ("futures", "future", "perp", "perpetual"):
            market_type = "swap"

        symbol = (
            strategy.get("symbol")
            or trading_config.get("symbol")
            or ""
        )
        symbol = normalize_strategy_symbol(str(symbol or "").strip()).upper()
        if not symbol:
            return None

        return (int(user_id or strategy.get("user_id") or 0), credential_key, exchange_id, market_type, symbol)
    except Exception as e:
        logger.warning("strategy live lock key failed for strategy %s: %s", strategy.get("id"), e)
        return None


def _find_live_strategy_conflict(strategy: Dict[str, Any], user_id: int) -> Optional[Dict[str, Any]]:
    """Find another running live strategy using the same account + market + symbol."""
    key = _strategy_live_lock_key(strategy, user_id)
    if not key:
        return None
    sid = int(strategy.get("id") or 0)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id
            FROM qd_strategies_trading
            WHERE user_id = ? AND status = 'running' AND execution_mode = 'live' AND id <> ?
            """,
            (int(user_id), sid),
        )
        rows = cur.fetchall() or []
        cur.close()

    service = get_strategy_service()
    for row in rows:
        other_id = int(row.get("id") or 0)
        other = service.get_strategy(other_id, user_id=user_id)
        if not other:
            continue
        if _strategy_live_lock_key(other, user_id) == key:
            return {
                "strategy_id": other_id,
                "strategy_name": other.get("strategy_name") or other.get("name") or str(other_id),
                "symbol": key[-1],
                "market_type": key[-2],
                "exchange_id": key[-3],
            }
    return None


def _live_conflict_message(conflict: Dict[str, Any]) -> str:
    return (
        "Live strategy conflict: another running strategy already uses the same "
        f"API key/exchange/market/symbol ({conflict.get('exchange_id')} "
        f"{conflict.get('market_type')} {conflict.get('symbol')}). "
        f"Please stop strategy {conflict.get('strategy_id')} "
        f"({conflict.get('strategy_name')}) first."
    )


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
    zh_texts = {
        "prompt_empty": "提示词不能为空",
        "no_llm_key": "未配置 LLM API Key",
        "insufficient_credits": "积分不足，请充值后重试",
        "invalid_json_params": "AI 未返回有效的 JSON 参数",
        "ai_empty_result": "AI 生成结果为空",
        "success": "success",
    }
    en_texts = {
        "prompt_empty": "Prompt cannot be empty",
        "no_llm_key": "No LLM API key configured",
        "insufficient_credits": "Insufficient credits. Please top up and try again.",
        "invalid_json_params": "AI did not return valid JSON parameters",
        "ai_empty_result": "AI generation returned empty result",
        "success": "success",
    }
    return (zh_texts if is_zh else en_texts).get(key, key)


def _strategy_hint_to_text(hint_code: str, params: dict | None = None, lang: str = "zh-CN") -> str:
    _ = params or {}
    is_zh = _is_zh_lang(lang)
    zh_texts = {
        "MISSING_ON_INIT": "缺少 on_init(ctx) 函数。",
        "MISSING_ON_BAR": "缺少 on_bar(ctx, bar) 函数。",
        "NO_CTX_PARAM_DEFAULTS": "没有通过 ctx.param(...) 声明参数默认值。",
        "NO_ORDER_INTENT": "没有检测到 ctx.buy / ctx.sell / ctx.close_position 等交易动作。",
        "EMPTY_CODE": "策略代码为空。",
    }
    en_texts = {
        "MISSING_ON_INIT": "Missing on_init(ctx) function.",
        "MISSING_ON_BAR": "Missing on_bar(ctx, bar) function.",
        "NO_CTX_PARAM_DEFAULTS": "No parameter defaults were declared via ctx.param(...).",
        "NO_ORDER_INTENT": "No order intent like ctx.buy / ctx.sell / ctx.close_position was detected.",
        "EMPTY_CODE": "Strategy code is empty.",
    }
    if is_zh:
        return zh_texts.get(hint_code, f"检测到策略提示：{hint_code}")
    return en_texts.get(hint_code, f"Strategy hint detected: {hint_code}")


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

    if returned_candidate == 'repaired':
        returned_text = "当前返回的是自动修复后的代码。" if is_zh else "The returned code is the auto-fixed version."
    else:
        returned_text = "当前返回的是首次生成的代码。" if is_zh else "The returned code is the initially generated version."

    return {
        "title": title,
        "returned_text": returned_text,
        "fixed_messages": fixed_messages,
        "remaining_messages": remaining_messages,
    }

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

        seen_live_keys: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        batch_conflicts: List[Dict[str, Any]] = []
        for sid in strategy_ids:
            st = get_strategy_service().get_strategy(int(sid), user_id=user_id)
            if not st:
                continue
            existing_conflict = _find_live_strategy_conflict(st, user_id)
            if existing_conflict:
                batch_conflicts.append({
                    'strategy_id': int(sid),
                    'conflict': existing_conflict,
                    'message': _live_conflict_message(existing_conflict),
                })
                continue
            key = _strategy_live_lock_key(st, user_id)
            if key and key in seen_live_keys:
                other = seen_live_keys[key]
                conflict = {
                    'strategy_id': other.get('id'),
                    'strategy_name': other.get('strategy_name') or other.get('name') or str(other.get('id')),
                    'symbol': key[-1],
                    'market_type': key[-2],
                    'exchange_id': key[-3],
                }
                batch_conflicts.append({
                    'strategy_id': int(sid),
                    'conflict': conflict,
                    'message': _live_conflict_message(conflict),
                })
            elif key:
                seen_live_keys[key] = st

        if batch_conflicts:
            return jsonify({
                'code': 0,
                'msg': 'Live strategy conflict',
                'data': {'conflicts': batch_conflicts},
            }), 409
        
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

        conflict = _find_live_strategy_conflict(st, user_id)
        if conflict:
            msg = _live_conflict_message(conflict)
            return jsonify({
                'code': 0,
                'msg': msg,
                'data': {'conflict': conflict},
            }), 409

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
            msg = f"Strategy exited immediately after startup: {hint}"
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
        
        # Log request keys for debugging without logging sensitive values.
        logger.debug(f"Connection test request keys: {list(data.keys())}")
        
        # Read exchange configuration.
        exchange_config = data.get('exchange_config', data)
        
        # Local deployment: no encryption/decryption; accept dict or JSON string.
        if isinstance(exchange_config, str):
            try:
                import json
                exchange_config = json.loads(exchange_config)
            except Exception:
                pass
        
        # Validate exchange_config is a dictionary.
        if not isinstance(exchange_config, dict):
            logger.error(f"Invalid exchange_config type: {type(exchange_config)}, data: {str(exchange_config)[:200]}")
            # Frontend expects HTTP 200 with {code:0} for business failures.
            return jsonify({'code': 0, 'msg': 'Invalid exchange config format; please check your payload', 'data': None})

        # Demo/testnet toggles and base_url are often sent on the JSON root while keys live under exchange_config.
        if isinstance(data, dict) and "exchange_config" in data:
            from app.services.live_trading.factory import merge_root_exchange_config_overlay

            exchange_config = merge_root_exchange_config_overlay(root=data, exchange_config=exchange_config)

        # Resolve credential_id to full config (merges credential keys with any overrides).
        # This allows the frontend to send just {credential_id: 5} without raw api_key/secret_key.
        from app.services.exchange_execution import resolve_exchange_config
        from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

        user_id = g.user_id if hasattr(g, 'user_id') else 1
        resolved = resolve_exchange_config(exchange_config, user_id=user_id)

        # Validate required fields after credential merge.
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

            # Detailed diagnostics for connection tests.
            logger.info(f"Testing connection: exchange_id={resolved.get('exchange_id')}")
            if api_key:
                logger.info(f"API Key: {api_key[:5]}... (len={len(api_key)})")
            if secret_key:
                logger.info(f"Secret Key: {secret_key[:5]}... (len={len(secret_key)})")

            # Check for accidental leading or trailing whitespace.
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
        if validation.get('success'):
            strategy_id = int(payload.get('strategyId') or payload.get('strategy_id') or 0)
            if strategy_id:
                try:
                    get_strategy_service().patch_trading_config(
                        strategy_id,
                        {
                            'lifecycle_verified': True,
                            'script_verified': True,
                            'lifecycle_verified_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                        },
                        user_id=g.user_id,
                    )
                except Exception as _lc_err:
                    logger.warning(f"lifecycle_verified patch skipped: {_lc_err}")
        return jsonify(validation)
    except Exception as e:
        logger.error(f"verify_strategy_code failed: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@strategy_blp.route('/strategies/publish-template', methods=['POST'])
@login_required
def publish_strategy_template():
    """Publish script strategy code to marketplace as script_template asset."""
    try:
        payload = request.get_json() or {}
        source_id = int(payload.get('sourceId') or payload.get('source_id') or payload.get('scriptSourceId') or 0)
        source = None
        if source_id:
            from app.services.script_source import get_script_source_service
            source = get_script_source_service().get_source(source_id, user_id=g.user_id)
            if not source:
                return jsonify({'code': 0, 'msg': 'Script source not found', 'data': None}), 404

        strategy_id = int(payload.get('strategyId') or payload.get('strategy_id') or 0)
        if not strategy_id and not source:
            return jsonify({'code': 0, 'msg': 'strategyId is required', 'data': None}), 400

        strategy = None
        if strategy_id:
            strategy = get_strategy_service().get_strategy(strategy_id, user_id=g.user_id)
            if not strategy:
                return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        code = ((source or {}).get('code') or (strategy or {}).get('strategy_code') or '').strip()
        if not code:
            return jsonify({'code': 0, 'msg': 'Strategy has no script code', 'data': None}), 400

        validation = _validate_strategy_code_internal(code)
        if not validation.get('success'):
            return jsonify({
                'code': 0,
                'msg': validation.get('message') or 'Code verification failed',
                'data': validation,
            }), 400

        name = (payload.get('name') or (source or {}).get('name') or (strategy or {}).get('strategy_name') or '').strip()
        description = (payload.get('description') or (source or {}).get('description') or '').strip()
        pricing_type = (payload.get('pricingType') or payload.get('pricing_type') or 'free').strip() or 'free'
        try:
            price = float(payload.get('price') or 0)
        except Exception:
            price = 0.0
        existing_indicator_id = int(payload.get('indicatorId') or payload.get('indicator_id') or 0)

        user_role = getattr(g, 'user_role', 'user')
        is_admin = user_role == 'admin'

        from app.services.community_service import get_community_service
        ok, msg, data = get_community_service().publish_script_template_from_strategy(
            user_id=g.user_id,
            strategy_id=strategy_id,
            code=code,
            name=name,
            description=description,
            pricing_type=pricing_type,
            price=price,
            is_admin=is_admin,
            existing_indicator_id=existing_indicator_id,
        )
        if data is not None and source_id:
            data['source_id'] = source_id
        if not ok:
            return jsonify({'code': 0, 'msg': msg, 'data': data}), 400
        return jsonify({'code': 1, 'msg': 'success', 'data': data})
    except Exception as e:
        logger.error(f"publish_strategy_template failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/publish-bot-preset', methods=['POST'])
@login_required
def publish_bot_preset():
    """Publish a bot strategy configuration to marketplace as bot_preset asset."""
    try:
        payload = request.get_json() or {}
        strategy_id = int(payload.get('strategyId') or payload.get('strategy_id') or 0)
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'strategyId is required', 'data': None}), 400

        strategy = get_strategy_service().get_strategy(strategy_id, user_id=g.user_id)
        if not strategy:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        strategy_mode = str(strategy.get('strategy_mode') or '').strip().lower()
        if strategy_mode != 'bot':
            return jsonify({'code': 0, 'msg': 'Only bot strategies can be published as presets', 'data': None}), 400

        name = (payload.get('name') or strategy.get('strategy_name') or '').strip()
        description = (payload.get('description') or '').strip()
        pricing_type = (payload.get('pricingType') or payload.get('pricing_type') or 'free').strip() or 'free'
        try:
            price = float(payload.get('price') or 0)
        except Exception:
            price = 0.0
        existing_indicator_id = int(payload.get('indicatorId') or payload.get('indicator_id') or 0)

        user_role = getattr(g, 'user_role', 'user')
        is_admin = user_role == 'admin'

        from app.services.community_service import get_community_service
        ok, msg, data = get_community_service().publish_bot_preset_from_strategy(
            user_id=g.user_id,
            strategy_id=strategy_id,
            name=name,
            description=description,
            pricing_type=pricing_type,
            price=price,
            is_admin=is_admin,
            existing_indicator_id=existing_indicator_id,
            strategy=strategy,
        )
        if not ok:
            return jsonify({'code': 0, 'msg': msg, 'data': data}), 400
        return jsonify({'code': 1, 'msg': 'success', 'data': data})
    except Exception as e:
        logger.error(f"publish_bot_preset failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


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
            # Detect (market, symbol) from prompt and fetch real K-lines.
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

                        # Forex/USStock often have no volume data; omit the
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
                "Available bot types and their parameter schemas. Use these exact frontend keys:\n"
                "1. grid - Grid Trading: {upperPrice, lowerPrice, gridCount: int(5-100), gridMode: 'arithmetic'|'geometric', "
                "gridDirection: 'long'|'short'|'neutral', initialPositionPct: number(0-100), "
                "boundaryAction: 'pause'|'stop_loss'|'hold', adaptiveBounds: boolean, adaptiveAtrMult: number(0.5-5), "
                "waterfallProtection: boolean, waterfallDropPct: decimal ratio(0.005-0.20; example 0.03 means 3%)}\n"
                "2. martingale - Martingale: {multiplier: number(1.1-3.0), maxLayers: int(2-10), "
                "priceDropPct: number(1-20), takeProfitPct: number(0.2-50), stopLossPct: number(1-50), "
                "direction: 'long'|'short', trailingTpEnabled: boolean, trailingTpCallbackPct: number(0.05-50), "
                "waterfallProtection: boolean, waterfallDropPct: decimal ratio(0.005-0.20; example 0.04 means 4%)}\n"
                "3. trend - Trend Following: {maPeriod: int(5-200), maType: 'SMA'|'EMA', confirmBars: int(1-5), "
                "positionPct: number(10-100), direction: 'long'|'short'|'both', trailingTpEnabled: boolean, "
                "trailingTpActivationPct: number(0.2-100), trailingTpCallbackPct: number(0.05-50)}\n"
                "4. dca - DCA (Dollar-Cost Averaging): {frequency: 'every_bar'|'hourly'|'4h'|'daily'|'weekly'|'biweekly'|'monthly', "
                "dipBuyEnabled: boolean, dipThreshold: number(1-30)}\n\n"
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
                "- stopLossPct: number(0-100), stored as a 0-100 UI percent\n"
                "- takeProfitPct: number(0-1000), stored as a 0-100 UI percent\n"
                "- maxPosition: number\n\n"
                "Percent convention: fields ending in Pct are 0-100 UI percentages, except waterfallDropPct, which is a 0-1 decimal ratio.\n"
                "CRITICAL: If real-time market data is provided, you MUST use it to set realistic and accurate parameters.\n"
                "For example, for grid trading, the upperPrice and lowerPrice MUST be derived from the actual price range in the market data.\n"
                "IMPORTANT: Do NOT set initialCapital in baseConfig - leave it as 0 or omit it. The user will enter their own investment amount.\n"
                "Also do NOT set amountPerGrid, initialAmount(for martingale), amountEach, or totalBudget(for DCA) - these will be auto-calculated from the user's capital.\n\n"
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

            params = result.get('strategyParams') if isinstance(result.get('strategyParams'), dict) else {}
            risk_cfg = result.get('riskConfig') if isinstance(result.get('riskConfig'), dict) else {}

            def _num(v, default, min_v=None, max_v=None):
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    n = float(default)
                if min_v is not None:
                    n = max(float(min_v), n)
                if max_v is not None:
                    n = min(float(max_v), n)
                return n

            def _int(v, default, min_v=None, max_v=None):
                return int(round(_num(v, default, min_v, max_v)))

            def _bool(v, default=False):
                if isinstance(v, bool):
                    return v
                if isinstance(v, str):
                    return v.strip().lower() in ('1', 'true', 'yes', 'on')
                return default if v is None else bool(v)

            def _ratio(v, default):
                n = _num(v, default, 0.001, 100)
                return n / 100 if n > 1 else n

            bot_type = result.get('botType')
            market_type = base_cfg.get('marketType') or 'spot'
            force_long = market_type == 'spot' or base_cfg.get('marketCategory') in ('USStock', 'Forex')

            if bot_type == 'grid':
                params.pop('amountPerGrid', None)
                params.update({
                    'upperPrice': _num(params.get('upperPrice'), 0, 0),
                    'lowerPrice': _num(params.get('lowerPrice'), 0, 0),
                    'gridCount': _int(params.get('gridCount'), 10, 5, 100),
                    'gridMode': params.get('gridMode') if params.get('gridMode') in ('arithmetic', 'geometric') else 'arithmetic',
                    'gridDirection': 'long' if force_long else (params.get('gridDirection') if params.get('gridDirection') in ('long', 'short', 'neutral') else 'neutral'),
                    'initialPositionPct': _num(params.get('initialPositionPct'), 0, 0, 100),
                    'boundaryAction': params.get('boundaryAction') if params.get('boundaryAction') in ('pause', 'stop_loss', 'hold') else 'pause',
                    'adaptiveBounds': _bool(params.get('adaptiveBounds'), True),
                    'adaptiveAtrMult': _num(params.get('adaptiveAtrMult'), 2, 0.5, 5),
                    'waterfallProtection': _bool(params.get('waterfallProtection'), True),
                    'waterfallDropPct': _ratio(params.get('waterfallDropPct'), 0.03),
                })
            elif bot_type == 'martingale':
                params.pop('initialAmount', None)
                params.update({
                    'multiplier': _num(params.get('multiplier'), 2, 1.1, 3),
                    'maxLayers': _int(params.get('maxLayers'), 5, 2, 10),
                    'priceDropPct': _num(params.get('priceDropPct'), 3, 1, 20),
                    'takeProfitPct': _num(params.get('takeProfitPct') or risk_cfg.get('takeProfitPct'), 2, 0.2, 50),
                    'stopLossPct': _num(params.get('stopLossPct') or risk_cfg.get('stopLossPct'), 12, 1, 50),
                    'direction': 'long' if force_long else (params.get('direction') if params.get('direction') in ('long', 'short') else 'long'),
                    'trailingTpEnabled': _bool(params.get('trailingTpEnabled'), False),
                    'trailingTpCallbackPct': _num(params.get('trailingTpCallbackPct'), 0.8, 0.05, 50),
                    'waterfallProtection': _bool(params.get('waterfallProtection'), True),
                    'waterfallDropPct': _ratio(params.get('waterfallDropPct'), 0.04),
                })
            elif bot_type == 'trend':
                params.update({
                    'maPeriod': _int(params.get('maPeriod'), 20, 5, 200),
                    'maType': params.get('maType') if params.get('maType') in ('SMA', 'EMA') else 'EMA',
                    'confirmBars': _int(params.get('confirmBars'), 2, 1, 5),
                    'positionPct': _num(params.get('positionPct'), 50, 10, 100),
                    'direction': 'long' if force_long else (params.get('direction') if params.get('direction') in ('long', 'short', 'both') else 'both'),
                    'trailingTpEnabled': _bool(params.get('trailingTpEnabled'), False),
                    'trailingTpActivationPct': _num(params.get('trailingTpActivationPct'), 5, 0.2, 100),
                    'trailingTpCallbackPct': _num(params.get('trailingTpCallbackPct'), 1, 0.05, 50),
                })
            elif bot_type == 'dca':
                params.pop('amountEach', None)
                params.pop('totalBudget', None)
                freq = str(params.get('frequency') or '').strip().lower()
                allowed = {'every_bar', 'hourly', '4h', 'daily', 'weekly', 'biweekly', 'monthly'}
                params.update({
                    'frequency': freq if freq in allowed else 'daily',
                    'dipBuyEnabled': _bool(params.get('dipBuyEnabled'), False),
                    'dipThreshold': _num(params.get('dipThreshold'), 5, 1, 30),
                })

            risk_cfg['stopLossPct'] = _num(risk_cfg.get('stopLossPct'), 10, 0, 100)
            risk_cfg['takeProfitPct'] = _num(risk_cfg.get('takeProfitPct'), 20, 0, 1000)
            risk_cfg['maxPosition'] = _num(risk_cfg.get('maxPosition'), 0, 0)
            result['strategyParams'] = params
            result['riskConfig'] = risk_cfg

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
Do not use markdown fences, do not add explanations before or after the JSON.

Percent parameter convention (IMPORTANT):
- Template UI stores percent-type fields on a 0-100 scale (80 = 80%, 2.5 = 2.5%).
- Generated Python code uses 0-1 ratios in ctx.param(...); the platform converts UI values automatically.
- When returning JSON for adjust_params, always use the 0-100 scale for keys ending in _pct or typed as percent
  (e.g. position_pct: 80, hard_stop_pct: 2.5). Never return 0.8 when the user means 80%.
"""

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
  - Preferred actions: ctx.open_long/open_short(amount, price), ctx.add_long/add_short(amount, price), ctx.close_long/close_short(amount=None, price=None), ctx.close_position(); ctx.buy/sell are legacy helpers
  - ctx.position supports both numeric checks and dict-style fields:
    - if not ctx.position / if ctx.position > 0 / if ctx.position < 0
    - ctx.position['side'], ctx.position['size'], ctx.position['entry_price']
  - ctx.balance, ctx.equity
  - ctx.bars(n) to get last N bars, ctx.log(message) to log
Return ONLY the Python code, no explanations.

Quality rules:
- Always define both on_init(ctx) and on_bar(ctx, bar)
- Prefer reading defaults via ctx.param(...)
- Use open_long/open_short for first entries, add_long/add_short only for intentional scale-ins, and close_long/close_short/close_position for exits
- Entry logic must be event-based: use cross_up = prev_fast <= prev_slow and fast > slow, breakout = prev_close <= level and close > level. Do NOT enter on persistent states like `if not ctx.position and fast > slow:`.
- Scale-ins must have layer count, price distance/cooldown, and max layers; call ctx.add_long/add_short, not ctx.buy/ctx.sell.
- Generated code must compile cleanly
- Avoid markdown fences or explanatory text

Percent / ratio convention:
- ctx.param defaults for *_pct fields must use 0-1 ratios (0.8 = 80%, 0.025 = 2.5%).
- When sizing with ctx.equity * some_pct, keep some_pct as a 0-1 ratio.
- Template UI may show 0-100; only the Python default literals should be ratios.
- If user says "80% position", use ctx.param('position_pct', 0.8) and qty = ctx.equity * ctx.position_pct / price.
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
                "- Prefer ctx.param(...) for defaults; use explicit open/add/close actions.\n"
                "- Entry conditions must be edge/crossing events; scale-ins must call add_long/add_short deliberately.\n"
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


# openapi-compat: legacy import name
strategy_bp = strategy_blp
