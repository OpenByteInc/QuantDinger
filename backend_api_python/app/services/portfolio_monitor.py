"""
Portfolio Monitor Service.
Runs scheduled AI analysis on manual positions and sends notifications.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.fast_analysis import get_fast_analysis_service
from app.services.signal_notifier import SignalNotifier
from app.services.kline import KlineService
from app.services.billing_service import get_billing_service

logger = get_logger(__name__)

DEFAULT_USER_ID = 1

_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

ALERT_MESSAGES = {
    'zh-CN': {
        'price_above': '🔔 价格突破预警: {symbol} 当前价格 ${current_price:.4f} 已突破 ${threshold:.4f}',
        'price_below': '🔔 价格跌破预警: {symbol} 当前价格 ${current_price:.4f} 已跌破 ${threshold:.4f}',
        'pnl_above': '🎉 盈利预警: {symbol} 当前盈亏 {pnl_percent:.1f}% 已达到 {threshold:.1f}% 目标',
        'pnl_below': '⚠️ 亏损预警: {symbol} 当前盈亏 {pnl_percent:.1f}% 已触及 {threshold:.1f}% 止损线',
        'alert_title': '价格/盈亏预警'
    },
    'en-US': {
        'price_above': '🔔 Price Alert: {symbol} current price ${current_price:.4f} has exceeded ${threshold:.4f}',
        'price_below': '🔔 Price Alert: {symbol} current price ${current_price:.4f} has dropped below ${threshold:.4f}',
        'pnl_above': '🎉 Profit Alert: {symbol} P&L {pnl_percent:.1f}% has reached {threshold:.1f}% target',
        'pnl_below': '⚠️ Loss Alert: {symbol} P&L {pnl_percent:.1f}% has hit {threshold:.1f}% stop-loss',
        'alert_title': 'Price/P&L Alert'
    }
}


def _get_alert_message(alert_type: str, language: str = 'en-US', **kwargs) -> str:
    """Get localized alert message."""
    lang = 'zh-CN' if language and language.startswith('zh') else 'en-US'
    templates = ALERT_MESSAGES.get(lang, ALERT_MESSAGES['en-US'])
    template = templates.get(alert_type, '')
    if template:
        return template.format(**kwargs)
    return ''


def _get_alert_title(language: str = 'en-US') -> str:
    """Get localized alert title."""
    lang = 'zh-CN' if language and language.startswith('zh') else 'en-US'
    return ALERT_MESSAGES.get(lang, ALERT_MESSAGES['en-US']).get('alert_title', 'Alert')


def _now_ts() -> int:
    return int(time.time())


def _bump_monitor_schedule(
    monitor_id: int,
    interval_minutes: int,
    last_result: Any,
    skipped: bool = False,
) -> None:
    """Advance ``next_run_at`` after a monitor cycle (real run or skip).

    Both INSERT (creation) and UPDATE paths must produce timestamps with the
    same time-zone semantics; ``qd_position_monitors`` columns are naive
    ``TIMESTAMP``, so we let PostgreSQL compute everything via ``NOW()`` and
    ``INTERVAL`` to stay consistent with the creation path in
    ``routes/portfolio.py``.

    When ``skipped`` is True we keep ``last_run_at`` and ``run_count`` as-is
    (since the monitor didn't actually consume an analysis cycle), but we
    still push ``next_run_at`` forward so the loop does not keep firing the
    same monitor every 30 seconds while the underlying condition (no credits,
    symbol removed from watchlist, etc.) persists.
    """
    try:
        interval = int(interval_minutes or 60)
        if interval <= 0:
            interval = 60
        # Cap at one week to avoid pathological intervals from bad configs.
        interval = min(interval, 60 * 24 * 7)

        result_json = json.dumps(last_result, ensure_ascii=False, default=str)

        with get_db_connection() as db:
            cur = db.cursor()
            if skipped:
                cur.execute(
                    """
                    UPDATE qd_position_monitors
                    SET next_run_at = NOW() + INTERVAL '%s minutes',
                        last_result = ?,
                        updated_at = NOW()
                    WHERE id = ?
                    """,
                    (interval, result_json, monitor_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE qd_position_monitors
                    SET last_run_at = NOW(),
                        next_run_at = NOW() + INTERVAL '%s minutes',
                        last_result = ?,
                        run_count = run_count + 1,
                        updated_at = NOW()
                    WHERE id = ?
                    """,
                    (interval, result_json, monitor_id),
                )
            db.commit()
            cur.close()
    except Exception as e:
        logger.error(f"_bump_monitor_schedule failed for monitor #{monitor_id}: {e}")


def _resolve_notification_delivery(user_id: int, notification_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    合并个人中心保存的 notification_settings 到 targets，并规范化 channels。
    前端创建监控常只传 channels（email/telegram/webhook），不传 targets；若不合并则外发渠道全部跳过且无任何送达。
    若当前 channels 均无法送达（无邮箱/Chat ID 等），则追加 browser 保证站内通知。
    """
    cfg: Dict[str, Any] = dict(notification_config) if isinstance(notification_config, dict) else {}
    raw_ch = cfg.get('channels')
    if isinstance(raw_ch, str):
        raw_ch = [raw_ch]
    elif not isinstance(raw_ch, list):
        raw_ch = []
    channels = [str(c).strip().lower() for c in raw_ch if c is not None and str(c).strip()]
    if not channels:
        channels = ['browser']

    targets: Dict[str, Any] = dict(cfg.get('targets') or {})

    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "SELECT email, notification_settings FROM qd_users WHERE id = ?",
                (user_id,),
            )
            row = cur.fetchone()
            cur.close()
        if not row:
            account_email = ""
            settings = {}
        else:
            account_email = (row.get("email") or "").strip()
            settings = _safe_json_loads(row.get("notification_settings"), {})
        if not (targets.get("email") or "").strip():
            te = (settings.get("email") or "").strip()
            targets["email"] = te or account_email
        if not (targets.get("telegram") or "").strip():
            targets["telegram"] = (settings.get("telegram_chat_id") or "").strip()
        if not (targets.get("telegram_bot_token") or "").strip():
            targets["telegram_bot_token"] = (settings.get("telegram_bot_token") or "").strip()
        if not (targets.get("webhook") or "").strip():
            targets["webhook"] = (settings.get("webhook_url") or "").strip()
    except Exception as e:
        logger.warning(f"_resolve_notification_delivery: load user {user_id} settings failed: {e}")

    def _can_deliver(ch: str) -> bool:
        if ch == "browser":
            return True
        if ch == "email":
            return bool((targets.get("email") or "").strip())
        if ch == "telegram":
            return bool((targets.get("telegram") or "").strip())
        if ch == "webhook":
            return bool((targets.get("webhook") or "").strip())
        return False

    if not any(_can_deliver(c) for c in channels):
        channels = list(dict.fromkeys(list(channels) + ["browser"]))

    cfg["channels"] = channels
    cfg["targets"] = targets
    return cfg


def _safe_json_loads(value, default=None):
    """Safely parse JSON string."""
    if default is None:
        default = {}
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def _get_positions_for_monitor(position_ids: List[int] = None, user_id: int = None) -> List[Dict[str, Any]]:
    """Get positions, optionally filtered by IDs and user_id."""
    try:
        kline_service = KlineService()
        effective_user_id = user_id if user_id is not None else DEFAULT_USER_ID
        
        with get_db_connection() as db:
            cur = db.cursor()
            if position_ids:
                placeholders = ','.join(['?' for _ in position_ids])
                cur.execute(
                    f"""
                    SELECT id, market, symbol, name, side, quantity, entry_price, group_name
                    FROM qd_manual_positions
                    WHERE user_id = ? AND id IN ({placeholders})
                    """,
                    [effective_user_id] + list(position_ids)
                )
            else:
                cur.execute(
                    """
                    SELECT id, market, symbol, name, side, quantity, entry_price, group_name
                    FROM qd_manual_positions
                    WHERE user_id = ?
                    """,
                    (effective_user_id,)
                )
            rows = cur.fetchall() or []
            cur.close()

        positions = []
        for row in rows:
            market = row.get('market')
            symbol = row.get('symbol')
            entry_price = float(row.get('entry_price') or 0)
            quantity = float(row.get('quantity') or 0)
            side = row.get('side') or 'long'
            group_name = row.get('group_name')
            
            # Get current price (use realtime price API)
            current_price = 0
            try:
                price_data = kline_service.get_realtime_price(market, symbol)
                current_price = float(price_data.get('price') or 0)
            except Exception:
                pass
            
            # Calculate PnL
            if side == 'long':
                pnl = (current_price - entry_price) * quantity
            else:
                pnl = (entry_price - current_price) * quantity
            
            pnl_percent = round(pnl / (entry_price * quantity) * 100, 2) if entry_price * quantity > 0 else 0
            
            positions.append({
                'id': row.get('id'),
                'market': market,
                'symbol': symbol,
                'name': row.get('name') or symbol,
                'side': side,
                'quantity': quantity,
                'entry_price': entry_price,
                'current_price': current_price,
                'pnl': round(pnl, 2),
                'pnl_percent': pnl_percent,
                'group_name': group_name
            })
        
        return positions
    except Exception as e:
        logger.error(f"_get_positions_for_monitor failed: {e}")
        return []


MAX_PARALLEL_ANALYSIS = 5


def _analyze_single_position(pos: Dict[str, Any], language: str, user_id: int = None) -> Dict[str, Any]:
    """Analyze a single position (designed to run inside a thread pool)."""
    market = pos.get('market')
    symbol = pos.get('symbol')
    name = pos.get('name') or symbol
    group_name = pos.get('group_name')

    if not market or not symbol:
        return {'market': market, 'symbol': symbol, 'name': name, 'error': 'missing market/symbol'}

    try:
        logger.info(f"Running fast AI analysis for {market}:{symbol} (user={user_id})")
        service = get_fast_analysis_service()
        analysis_result = service.analyze(
            market=market, symbol=symbol, language=language, timeframe='1D',
            user_id=user_id,
        )

        detailed = analysis_result.get('detailed_analysis', {})
        trading_plan = analysis_result.get('trading_plan', {})
        scores = analysis_result.get('scores', {})
        risks = analysis_result.get('risks', [])
        risk_report = '\n'.join([f"• {r}" for r in risks]) if risks else ''

        result = {
            'market': market, 'symbol': symbol, 'name': name, 'group_name': group_name,
            'entry_price': pos.get('entry_price'),
            'current_price': pos.get('current_price') or analysis_result.get('market_data', {}).get('current_price'),
            'pnl': pos.get('pnl'), 'pnl_percent': pos.get('pnl_percent'),
            'quantity': pos.get('quantity'), 'side': pos.get('side'),
            'final_decision': analysis_result.get('decision', 'HOLD'),
            'confidence': analysis_result.get('confidence', 50),
            'reasoning': analysis_result.get('summary', ''),
            'trader_decision': analysis_result.get('decision', 'HOLD'),
            'trader_reasoning': analysis_result.get('summary', ''),
            'overview_report': detailed.get('technical', ''),
            'fundamental_report': detailed.get('fundamental', ''),
            'sentiment_report': detailed.get('sentiment', ''),
            'risk_report': risk_report,
            'suggested_entry': trading_plan.get('entry_price'),
            'suggested_stop_loss': trading_plan.get('stop_loss'),
            'suggested_take_profit': trading_plan.get('take_profit'),
            'technical_score': scores.get('technical', 50),
            'fundamental_score': scores.get('fundamental', 50),
            'sentiment_score': scores.get('sentiment', 50),
            'key_reasons': analysis_result.get('reasons', []),
            'error': analysis_result.get('error')
        }
        logger.info(f"Fast analysis completed for {market}:{symbol}: {analysis_result.get('decision', 'N/A')}")
        return result
    except Exception as e:
        logger.error(f"Failed to analyze {market}:{symbol}: {e}")
        return {'market': market, 'symbol': symbol, 'name': name, 'error': str(e)}


def _run_ai_analysis(positions: List[Dict[str, Any]], config: Dict[str, Any], user_id: int = None) -> Dict[str, Any]:
    """
    Run fast AI analysis on positions **in parallel** using a thread pool.
    Same (market, symbol) is analyzed only once; the result is shared across
    duplicate positions so we don't waste LLM calls or show redundant entries.
    """
    try:
        language = config.get('language', 'en-US')
        custom_prompt = config.get('prompt', '')

        # ── Deduplicate by (market, symbol) ──
        unique_map: Dict[str, int] = {}          # "market|symbol" -> index in unique_positions
        unique_positions: List[Dict[str, Any]] = []
        pos_to_unique: List[int] = []             # positions[i] -> unique_positions index
        for pos in positions:
            key = f"{pos.get('market')}|{pos.get('symbol')}"
            if key not in unique_map:
                unique_map[key] = len(unique_positions)
                unique_positions.append(pos)
            pos_to_unique.append(unique_map[key])

        workers = min(len(unique_positions), MAX_PARALLEL_ANALYSIS)
        unique_analyses: List[Dict[str, Any]] = [None] * len(unique_positions)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_idx = {
                executor.submit(_analyze_single_position, pos, language, user_id): idx
                for idx, pos in enumerate(unique_positions)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    unique_analyses[idx] = future.result()
                except Exception as e:
                    pos = unique_positions[idx]
                    unique_analyses[idx] = {
                        'market': pos.get('market'), 'symbol': pos.get('symbol'),
                        'name': pos.get('name') or pos.get('symbol'), 'error': str(e)
                    }

        # ── Map back: each position gets its own copy with position-specific P&L ──
        position_analyses: List[Dict[str, Any]] = []
        seen_keys: set = set()
        for i, pos in enumerate(positions):
            key = f"{pos.get('market')}|{pos.get('symbol')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            base = dict(unique_analyses[pos_to_unique[i]])
            base['entry_price'] = pos.get('entry_price')
            base['current_price'] = base.get('current_price') or pos.get('current_price')
            combined_qty = sum(
                float(p.get('quantity') or 0)
                for j, p in enumerate(positions)
                if f"{p.get('market')}|{p.get('symbol')}" == key
            )
            combined_cost = sum(
                float(p.get('entry_price') or 0) * float(p.get('quantity') or 0)
                for j, p in enumerate(positions)
                if f"{p.get('market')}|{p.get('symbol')}" == key
            )
            cur_price = float(base.get('current_price') or 0)
            combined_pnl = sum(
                float(p.get('pnl') or 0)
                for j, p in enumerate(positions)
                if f"{p.get('market')}|{p.get('symbol')}" == key
            )
            avg_entry = round(combined_cost / combined_qty, 4) if combined_qty else 0
            pnl_pct = round(combined_pnl / combined_cost * 100, 2) if combined_cost else 0
            base['quantity'] = combined_qty
            base['entry_price'] = avg_entry
            base['pnl'] = round(combined_pnl, 2)
            base['pnl_percent'] = pnl_pct
            position_analyses.append(base)

        # Also provide deduplicated positions list for report building
        deduped_positions = []
        seen_keys2: set = set()
        for i, pos in enumerate(positions):
            key = f"{pos.get('market')}|{pos.get('symbol')}"
            if key in seen_keys2:
                continue
            seen_keys2.add(key)
            merged = dict(pos)
            merged['quantity'] = position_analyses[len(deduped_positions)].get('quantity', pos.get('quantity'))
            merged['entry_price'] = position_analyses[len(deduped_positions)].get('entry_price', pos.get('entry_price'))
            merged['pnl'] = position_analyses[len(deduped_positions)].get('pnl', pos.get('pnl'))
            merged['pnl_percent'] = position_analyses[len(deduped_positions)].get('pnl_percent', pos.get('pnl_percent'))
            deduped_positions.append(merged)

        analysis_report = _build_comprehensive_report(deduped_positions, position_analyses, language, custom_prompt)

        return {
            'success': True,
            'analysis': analysis_report,
            'position_analyses': position_analyses,
            'positions': deduped_positions,
            'position_count': len(deduped_positions),
            'analyzed_count': len([p for p in position_analyses if not p.get('error')]),
            'timestamp': _now_ts()
        }

    except Exception as e:
        logger.error(f"_run_ai_analysis failed: {e}")
        logger.error(traceback.format_exc())
        return {'success': False, 'error': str(e), 'timestamp': _now_ts()}


def _build_comprehensive_report(
    positions: List[Dict[str, Any]],
    position_analyses: List[Dict[str, Any]],
    language: str,
    custom_prompt: str = ''
) -> str:
    """Build a comprehensive text report (backward compatible)."""
    # Use HTML report as the main format
    return _build_html_report(positions, position_analyses, language, custom_prompt)


def _build_html_report(
    positions: List[Dict[str, Any]],
    position_analyses: List[Dict[str, Any]],
    language: str,
    custom_prompt: str = ''
) -> str:
    """Build a beautiful HTML report with collapsible sections."""
    
    # Calculate portfolio summary
    total_cost = sum(float(p.get('entry_price', 0)) * float(p.get('quantity', 0)) for p in positions)
    total_pnl = sum(float(p.get('pnl', 0)) for p in positions)
    total_pnl_percent = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0
    total_market_value = sum(float(p.get('current_price', 0)) * float(p.get('quantity', 0)) for p in positions)
    
    # Count recommendations
    buy_count = len([p for p in position_analyses if p.get('final_decision') == 'BUY'])
    sell_count = len([p for p in position_analyses if p.get('final_decision') == 'SELL'])
    hold_count = len([p for p in position_analyses if p.get('final_decision') == 'HOLD'])
    
    is_zh = language.startswith('zh')
    
    # Text translations
    texts = {
        'title': '投资组合AI分析报告' if is_zh else 'Portfolio AI Analysis Report',
        'subtitle': '由 QuantDinger AI 快速分析引擎生成' if is_zh else 'Generated by QuantDinger Fast AI Analysis Engine',
        'overview': '组合概览' if is_zh else 'Portfolio Overview',
        'positions': '持仓数量' if is_zh else 'Positions',
        'total_value': '总市值' if is_zh else 'Total Value',
        'total_cost': '总成本' if is_zh else 'Total Cost',
        'total_pnl': '总盈亏' if is_zh else 'Total P&L',
        'ai_recommendations': '🤖 AI智能分析建议' if is_zh else '🤖 AI Recommendations',
        'buy': '买入' if is_zh else 'Buy',
        'sell': '卖出' if is_zh else 'Sell',
        'hold': '持有' if is_zh else 'Hold',
        'position_analysis': '📈 各持仓详细分析' if is_zh else '📈 Position Analysis',
        'current_price': '当前价格' if is_zh else 'Current',
        'entry_price': '买入价' if is_zh else 'Entry',
        'pnl': '盈亏' if is_zh else 'P&L',
        'quantity': '数量' if is_zh else 'Qty',
        'side': '方向' if is_zh else 'Side',
        'long': '做多' if is_zh else 'Long',
        'short': '做空' if is_zh else 'Short',
        'ai_decision': 'AI决策' if is_zh else 'AI Decision',
        'confidence': '置信度' if is_zh else 'Confidence',
        'reasoning': '分析摘要' if is_zh else 'Summary',
        'trader_report': '📋 交易员详细评估' if is_zh else '📋 Trader Analysis',
        'risk_report': '⚠️ 风险评估' if is_zh else '⚠️ Risk Assessment',
        'overview_report': '📊 市场概览' if is_zh else '📊 Market Overview',
        'click_expand': '点击展开详情' if is_zh else 'Click to expand',
        'user_focus': '👤 用户关注点' if is_zh else '👤 User Focus',
        'generated_at': '报告生成时间' if is_zh else 'Generated at',
        'disclaimer': '本报告仅供参考，不构成投资建议。' if is_zh else 'For reference only. Not investment advice.',
        'analysis_failed': '分析失败' if is_zh else 'Analysis failed'
    }
    
    # CSS Styles
    css = '''
    <style>
        .qd-report { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 16px; }
        .qd-report * { box-sizing: border-box; }
        .qd-header { text-align: center; color: #fff; padding: 20px 0 30px; }
        .qd-header h1 { margin: 0 0 8px; font-size: 24px; font-weight: 700; text-shadow: 0 2px 4px rgba(0,0,0,0.2); }
        .qd-header .subtitle { font-size: 13px; opacity: 0.9; }
        .qd-content { background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.15); }
        .qd-section { margin-bottom: 24px; }
        .qd-section:last-child { margin-bottom: 0; }
        .qd-section-title { font-size: 16px; font-weight: 600; color: #1a1a2e; margin: 0 0 16px; padding-bottom: 8px; border-bottom: 2px solid #667eea; }
        .qd-overview-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .qd-stat-card { background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf3 100%); border-radius: 10px; padding: 16px; text-align: center; }
        .qd-stat-card .label { font-size: 12px; color: #666; margin-bottom: 6px; }
        .qd-stat-card .value { font-size: 20px; font-weight: 700; color: #1a1a2e; }
        .qd-stat-card .value.positive { color: #10b981; }
        .qd-stat-card .value.negative { color: #ef4444; }
        .qd-stat-card .percent { font-size: 12px; font-weight: 500; margin-left: 4px; }
        .qd-rec-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
        .qd-rec-card { border-radius: 10px; padding: 16px; text-align: center; }
        .qd-rec-card.buy { background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%); }
        .qd-rec-card.sell { background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%); }
        .qd-rec-card.hold { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); }
        .qd-rec-card .emoji { font-size: 28px; margin-bottom: 8px; }
        .qd-rec-card .count { font-size: 24px; font-weight: 700; }
        .qd-rec-card.buy .count { color: #059669; }
        .qd-rec-card.sell .count { color: #dc2626; }
        .qd-rec-card.hold .count { color: #d97706; }
        .qd-rec-card .label { font-size: 13px; color: #666; margin-top: 4px; }
        .qd-position { background: #f8fafc; border-radius: 12px; margin-bottom: 16px; overflow: hidden; border: 1px solid #e2e8f0; }
        .qd-position:last-child { margin-bottom: 0; }
        .qd-pos-header { display: flex; justify-content: space-between; align-items: center; padding: 16px; background: #fff; cursor: default; }
        .qd-pos-symbol { display: flex; align-items: center; gap: 12px; }
        .qd-pos-symbol .icon { width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; color: #fff; }
        .qd-pos-symbol .icon.buy { background: linear-gradient(135deg, #10b981 0%, #059669 100%); }
        .qd-pos-symbol .icon.sell { background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); }
        .qd-pos-symbol .icon.hold { background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); }
        .qd-pos-symbol .name { font-weight: 600; font-size: 15px; color: #1a1a2e; }
        .qd-pos-symbol .market { font-size: 12px; color: #666; }
        .qd-pos-decision { text-align: right; }
        .qd-pos-decision .decision-tag { display: inline-block; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 13px; }
        .qd-pos-decision .decision-tag.buy { background: #d1fae5; color: #059669; }
        .qd-pos-decision .decision-tag.sell { background: #fee2e2; color: #dc2626; }
        .qd-pos-decision .decision-tag.hold { background: #fef3c7; color: #d97706; }
        .qd-pos-decision .confidence { font-size: 12px; color: #666; margin-top: 4px; }
        .qd-pos-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: #e2e8f0; }
        .qd-pos-stats .stat { background: #fff; padding: 12px; text-align: center; }
        .qd-pos-stats .stat .label { font-size: 11px; color: #666; margin-bottom: 4px; }
        .qd-pos-stats .stat .value { font-size: 14px; font-weight: 600; color: #1a1a2e; }
        .qd-pos-stats .stat .value.positive { color: #10b981; }
        .qd-pos-stats .stat .value.negative { color: #ef4444; }
        .qd-pos-reasoning { padding: 16px; background: #fff; border-top: 1px solid #e2e8f0; }
        .qd-pos-reasoning .label { font-size: 12px; font-weight: 600; color: #666; margin-bottom: 6px; }
        .qd-pos-reasoning .text { font-size: 13px; color: #374151; line-height: 1.6; }
        .qd-collapsible { border-top: 1px solid #e2e8f0; }
        .qd-collapsible input[type="checkbox"] { display: none; }
        .qd-collapsible-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: #f1f5f9; cursor: pointer; user-select: none; }
        .qd-collapsible-header:hover { background: #e2e8f0; }
        .qd-collapsible-header .title { font-size: 13px; font-weight: 600; color: #475569; }
        .qd-collapsible-header .arrow { transition: transform 0.2s; color: #94a3b8; display: inline-block; }
        .qd-collapsible-content { display: none; padding: 16px; background: #fff; font-size: 13px; color: #475569; line-height: 1.7; border-top: 1px solid #e2e8f0; }
        .qd-collapsible input[type="checkbox"]:checked ~ .qd-collapsible-content { display: block; }
        .qd-collapsible input[type="checkbox"]:checked + .qd-collapsible-header .arrow { transform: rotate(180deg); }
        .qd-user-focus { background: linear-gradient(135deg, #ede9fe 0%, #ddd6fe 100%); border-radius: 10px; padding: 16px; font-size: 13px; color: #5b21b6; line-height: 1.6; }
        .qd-footer { text-align: center; padding: 20px 0 0; font-size: 12px; color: #666; border-top: 1px solid #e2e8f0; margin-top: 24px; }
        .qd-footer .time { margin-bottom: 4px; }
        .qd-footer .disclaimer { opacity: 0.8; }
        .qd-error { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px; color: #dc2626; font-size: 13px; }
        @media (max-width: 600px) {
            .qd-report { padding: 12px; border-radius: 0; }
            .qd-overview-grid { grid-template-columns: repeat(2, 1fr); }
            .qd-rec-grid { grid-template-columns: repeat(3, 1fr); }
            .qd-pos-stats { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
    '''
    
    # Build HTML
    pnl_class = 'positive' if total_pnl >= 0 else 'negative'
    pnl_sign = '+' if total_pnl >= 0 else ''
    
    html = f'''
    {css}
    <div class="qd-report">
        <div class="qd-header">
            <h1>{texts['title']}</h1>
            <div class="subtitle">{texts['subtitle']}</div>
        </div>
        <div class="qd-content">
            <!-- Overview Section -->
            <div class="qd-section">
                <h2 class="qd-section-title">{texts['overview']}</h2>
                <div class="qd-overview-grid">
                    <div class="qd-stat-card">
                        <div class="label">{texts['positions']}</div>
                        <div class="value">{len(positions)}</div>
                    </div>
                    <div class="qd-stat-card">
                        <div class="label">{texts['total_value']}</div>
                        <div class="value">${total_market_value:,.2f}</div>
                    </div>
                    <div class="qd-stat-card">
                        <div class="label">{texts['total_cost']}</div>
                        <div class="value">${total_cost:,.2f}</div>
                    </div>
                    <div class="qd-stat-card">
                        <div class="label">{texts['total_pnl']}</div>
                        <div class="value {pnl_class}">{pnl_sign}${total_pnl:,.2f}<span class="percent">({pnl_sign}{total_pnl_percent:.1f}%)</span></div>
                    </div>
                </div>
            </div>
            
            <!-- AI Recommendations Section -->
            <div class="qd-section">
                <h2 class="qd-section-title">{texts['ai_recommendations']}</h2>
                <div class="qd-rec-grid">
                    <div class="qd-rec-card buy">
                        <div class="emoji">🟢</div>
                        <div class="count">{buy_count}</div>
                        <div class="label">{texts['buy']}</div>
                    </div>
                    <div class="qd-rec-card sell">
                        <div class="emoji">🔴</div>
                        <div class="count">{sell_count}</div>
                        <div class="label">{texts['sell']}</div>
                    </div>
                    <div class="qd-rec-card hold">
                        <div class="emoji">🟡</div>
                        <div class="count">{hold_count}</div>
                        <div class="label">{texts['hold']}</div>
                    </div>
                </div>
            </div>
            
            <!-- Position Analysis Section -->
            <div class="qd-section">
                <h2 class="qd-section-title">{texts['position_analysis']}</h2>
    '''
    
    for pa in position_analyses:
        symbol = pa.get('symbol', '')
        name = pa.get('name', symbol)
        market = pa.get('market', '')
        group_name = pa.get('group_name', '')
        
        if pa.get('error'):
            html += f'''
                <div class="qd-position">
                    <div class="qd-pos-header">
                        <div class="qd-pos-symbol">
                            <div class="icon hold">⚠️</div>
                            <div>
                                <div class="name">{name}</div>
                                <div class="market">{market}/{symbol}</div>
                            </div>
                        </div>
                    </div>
                    <div class="qd-error" style="margin: 16px;">{texts['analysis_failed']}: {pa.get('error')}</div>
                </div>
            '''
            continue
        
        decision = pa.get('final_decision', 'HOLD')
        decision_lower = decision.lower()
        decision_text = texts.get(decision_lower, decision)
        confidence = pa.get('confidence', 50)
        
        current_price = pa.get('current_price', 0)
        entry_price = pa.get('entry_price', 0)
        pnl = pa.get('pnl', 0)
        pnl_pct = pa.get('pnl_percent', 0)
        quantity = pa.get('quantity', 0)
        side = pa.get('side', 'long')
        side_text = texts['long'] if side == 'long' else texts['short']
        
        pnl_class = 'positive' if pnl >= 0 else 'negative'
        pnl_sign = '+' if pnl >= 0 else ''
        
        reasoning = pa.get('reasoning', '')
        trader_reasoning = pa.get('trader_reasoning', '')
        overview_report = pa.get('overview_report', '')
        risk_report = pa.get('risk_report', '')
        
        html += f'''
                <div class="qd-position">
                    <div class="qd-pos-header">
                        <div class="qd-pos-symbol">
                            <div class="icon {decision_lower}">{decision[0]}</div>
                            <div>
                                <div class="name">{name}</div>
                                <div class="market">{market}/{symbol}</div>
                            </div>
                        </div>
                        <div class="qd-pos-decision">
                            <div class="decision-tag {decision_lower}">{decision_text}</div>
                            <div class="confidence">{texts['confidence']}: {confidence}%</div>
                        </div>
                    </div>
                    <div class="qd-pos-stats">
                        <div class="stat">
                            <div class="label">{texts['current_price']}</div>
                            <div class="value">${current_price:.4f}</div>
                        </div>
                        <div class="stat">
                            <div class="label">{texts['entry_price']}</div>
                            <div class="value">${entry_price:.4f}</div>
                        </div>
                        <div class="stat">
                            <div class="label">{texts['pnl']}</div>
                            <div class="value {pnl_class}">{pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)</div>
                        </div>
                        <div class="stat">
                            <div class="label">{texts['quantity']} / {texts['side']}</div>
                            <div class="value">{quantity} / {side_text}</div>
                        </div>
                    </div>
        '''
        
        # Reasoning summary
        if reasoning:
            html += f'''
                    <div class="qd-pos-reasoning">
                        <div class="label">{texts['reasoning']}</div>
                        <div class="text">{reasoning[:500]}{'...' if len(reasoning) > 500 else ''}</div>
                    </div>
            '''
        
        # Generate unique ID for collapsible sections (use symbol hash to avoid special chars)
        section_id_base = hashlib.md5(f"{symbol}_{market}_{group_name}".encode()).hexdigest()[:8]
        
        # Collapsible: Trader Analysis
        if trader_reasoning:
            trader_id = f"trader_{section_id_base}"
            html += f'''
                    <div class="qd-collapsible">
                        <input type="checkbox" id="{trader_id}">
                        <label for="{trader_id}" class="qd-collapsible-header">
                            <span class="title">{texts['trader_report']}</span>
                            <span class="arrow">▼</span>
                        </label>
                        <div class="qd-collapsible-content">{trader_reasoning.replace(chr(10), '<br>')}</div>
                    </div>
            '''
        
        # Collapsible: Market Overview
        if overview_report:
            overview_id = f"overview_{section_id_base}"
            html += f'''
                    <div class="qd-collapsible">
                        <input type="checkbox" id="{overview_id}">
                        <label for="{overview_id}" class="qd-collapsible-header">
                            <span class="title">{texts['overview_report']}</span>
                            <span class="arrow">▼</span>
                        </label>
                        <div class="qd-collapsible-content">{overview_report.replace(chr(10), '<br>')}</div>
                    </div>
            '''
        
        # Collapsible: Risk Assessment
        if risk_report:
            risk_id = f"risk_{section_id_base}"
            html += f'''
                    <div class="qd-collapsible">
                        <input type="checkbox" id="{risk_id}">
                        <label for="{risk_id}" class="qd-collapsible-header">
                            <span class="title">{texts['risk_report']}</span>
                            <span class="arrow">▼</span>
                        </label>
                        <div class="qd-collapsible-content">{risk_report.replace(chr(10), '<br>')}</div>
                    </div>
            '''
        
        html += '''
                </div>
        '''
    
    # User focus section
    if custom_prompt:
        html += f'''
            </div>
            <div class="qd-section">
                <h2 class="qd-section-title">{texts['user_focus']}</h2>
                <div class="qd-user-focus">{custom_prompt}</div>
        '''
    
    # Footer
    html += f'''
            </div>
            <div class="qd-footer">
                <div class="time">{texts['generated_at']}: {time.strftime('%Y-%m-%d %H:%M:%S')}</div>
                <div class="disclaimer">{texts['disclaimer']}</div>
            </div>
        </div>
    </div>
    '''
    
    return html


def _build_telegram_report(
    positions: List[Dict[str, Any]],
    position_analyses: List[Dict[str, Any]],
    language: str,
    custom_prompt: str = ''
) -> str:
    """Build a concise report suitable for Telegram (HTML format).

    Positions with quantity>0 and entry_price>0 are shown with P&L;
    others are treated as watchlist items and only show current price.
    """

    def _has_holding(pa: Dict[str, Any]) -> bool:
        return float(pa.get('quantity') or 0) > 0 and float(pa.get('entry_price') or 0) > 0

    held = [p for p in position_analyses if _has_holding(p) and not p.get('error')]
    watched = [p for p in position_analyses if not _has_holding(p) and not p.get('error')]
    errored = [p for p in position_analyses if p.get('error')]

    total_cost = sum(float(p.get('entry_price', 0)) * float(p.get('quantity', 0)) for p in held)
    total_pnl = sum(float(p.get('pnl', 0)) for p in held)
    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0
    pnl_sign = '+' if total_pnl >= 0 else ''

    buy_count = len([p for p in position_analyses if p.get('final_decision') == 'BUY'])
    sell_count = len([p for p in position_analyses if p.get('final_decision') == 'SELL'])
    hold_count = len([p for p in position_analyses if p.get('final_decision') == 'HOLD'])

    is_zh = language.startswith('zh')

    # ── Header / Overview ──
    if is_zh:
        lines: List[str] = ["<b>📊 AI资产分析报告</b>", ""]
        overview = ["<b>📈 概览</b>"]
        if held:
            overview.append(f"• 持仓: {len(held)} 个")
            overview.append(f"• 总成本: ${total_cost:,.2f}")
            overview.append(f"• 总盈亏: {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{total_pnl_pct:.1f}%)")
        if watched:
            overview.append(f"• 观察: {len(watched)} 个")
        lines.extend(overview)
        lines.extend([
            "",
            "<b>🤖 AI建议汇总</b>",
            f"🟢 买入: {buy_count} | 🔴 卖出: {sell_count} | 🟡 持有: {hold_count}",
        ])
    else:
        lines = ["<b>📊 AI Asset Analysis Report</b>", ""]
        overview = ["<b>📈 Overview</b>"]
        if held:
            overview.append(f"• Holdings: {len(held)}")
            overview.append(f"• Total Cost: ${total_cost:,.2f}")
            overview.append(f"• Total P&L: {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{total_pnl_pct:.1f}%)")
        if watched:
            overview.append(f"• Watchlist: {len(watched)}")
        lines.extend(overview)
        lines.extend([
            "",
            "<b>🤖 AI Recommendations</b>",
            f"🟢 Buy: {buy_count} | 🔴 Sell: {sell_count} | 🟡 Hold: {hold_count}",
        ])

    # ── Helper: render one analysis entry ──
    def _render_pa(pa: Dict[str, Any], show_pnl: bool) -> None:
        decision = pa.get('final_decision', 'HOLD')
        emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}.get(decision, '⚪')
        d_text = decision
        if is_zh:
            d_text = {'BUY': '买入', 'SELL': '卖出', 'HOLD': '持有'}.get(decision, '持有')
        lines.append(f"\n{emoji} <b>{pa.get('name', pa.get('symbol'))}</b> ({pa.get('market')}/{pa.get('symbol')})")
        if show_pnl:
            pnl = pa.get('pnl', 0)
            pnl_pct = pa.get('pnl_percent', 0)
            ps = '+' if pnl >= 0 else ''
            lines.append(
                f"   💰 ${pa.get('current_price', 0):,.2f} | "
                f"{'盈亏' if is_zh else 'P&L'}: {ps}${pnl:,.2f} ({ps}{pnl_pct:.1f}%)"
            )
        else:
            lines.append(f"   💰 {'现价' if is_zh else 'Price'}: ${pa.get('current_price', 0):,.2f}")
        lines.append(
            f"   🎯 {'建议' if is_zh else 'Rec'}: <b>{d_text}</b> "
            f"({'置信度' if is_zh else 'Conf'}: {pa.get('confidence', 50)}%)"
        )
        reasoning = pa.get('reasoning', '')
        if reasoning:
            lines.append(f"   📝 {reasoning[:150]}{'...' if len(reasoning) > 150 else ''}")

    # ── Holdings section ──
    if held:
        lines.extend(["", f"<b>📋 {'持仓分析' if is_zh else 'Holdings'}</b>"])
        for pa in held:
            _render_pa(pa, show_pnl=True)

    # ── Watchlist section ──
    if watched:
        lines.extend(["", f"<b>👁 {'观察列表' if is_zh else 'Watchlist'}</b>"])
        for pa in watched:
            _render_pa(pa, show_pnl=False)

    # ── Errors ──
    for pa in errored:
        label = pa.get('name') or pa.get('symbol') or '?'
        lines.append(f"\n⚠️ <b>{label}</b>: {'分析失败' if is_zh else 'Analysis failed'}")

    if custom_prompt:
        lines.extend(["", f"<b>👤 {'关注点' if is_zh else 'Focus'}:</b> {custom_prompt}"])

    lines.extend([
        "",
        "─────────────────────",
        f"<i>⏰ {time.strftime('%Y-%m-%d %H:%M')}</i>",
        f"<i>{'由 QuantDinger 多智能体系统生成' if is_zh else 'Generated by QuantDinger Multi-Agent System'}</i>",
    ])

    return '\n'.join(lines)


def _build_batch_telegram_report(
    monitor_results: List[Dict[str, Any]],
    language: str,
) -> str:
    """Build a single Telegram report that combines multiple monitor results."""
    is_zh = language.startswith('zh')

    def _has_holding(pa: Dict[str, Any]) -> bool:
        return float(pa.get('quantity') or 0) > 0 and float(pa.get('entry_price') or 0) > 0

    all_analyses: List[Dict[str, Any]] = []
    monitor_sections: List[str] = []

    for res in monitor_results:
        meta = res.get('_meta', {})
        m_name = meta.get('monitor_name', '?')
        m_analyses = meta.get('position_analyses', [])
        all_analyses.extend(m_analyses)

        section_lines: List[str] = [f"\n<b>📋 {m_name}</b>"]
        for pa in m_analyses:
            if pa.get('error'):
                label = pa.get('name') or pa.get('symbol') or '?'
                section_lines.append(f"  ⚠️ {label}: {'分析失败' if is_zh else 'Failed'}")
                continue
            decision = pa.get('final_decision', 'HOLD')
            emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}.get(decision, '⚪')
            d_text = ({'BUY': '买入', 'SELL': '卖出', 'HOLD': '持有'}.get(decision, '持有')) if is_zh else decision
            cur_price = pa.get('current_price', 0)
            section_lines.append(
                f"{emoji} <b>{pa.get('name', pa.get('symbol'))}</b> ({pa.get('market')}/{pa.get('symbol')})"
            )
            if _has_holding(pa):
                pnl = pa.get('pnl', 0)
                pnl_s = '+' if pnl >= 0 else ''
                pnl_pct = pa.get('pnl_percent', 0)
                section_lines.append(
                    f"   💰 ${cur_price:,.2f} | {'盈亏' if is_zh else 'P&L'}: {pnl_s}${pnl:,.2f} ({pnl_s}{pnl_pct:.1f}%)"
                )
            else:
                section_lines.append(f"   💰 {'现价' if is_zh else 'Price'}: ${cur_price:,.2f}")
            section_lines.append(
                f"   🎯 {'建议' if is_zh else 'Rec'}: <b>{d_text}</b> "
                f"({'置信度' if is_zh else 'Conf'}: {pa.get('confidence', 50)}%)"
            )
            reasoning = pa.get('reasoning', '')
            if reasoning:
                section_lines.append(f"   📝 {reasoning[:120]}{'...' if len(reasoning) > 120 else ''}")
        monitor_sections.append('\n'.join(section_lines))

    held = [a for a in all_analyses if _has_holding(a) and not a.get('error')]
    watched = [a for a in all_analyses if not _has_holding(a) and not a.get('error')]
    total_cost = sum(float(a.get('entry_price', 0)) * float(a.get('quantity', 0)) for a in held)
    total_pnl = sum(float(a.get('pnl', 0)) for a in held)
    total_pnl_pct = round(total_pnl / total_cost * 100, 2) if total_cost else 0
    pnl_sign = '+' if total_pnl >= 0 else ''
    buy_c = len([a for a in all_analyses if a.get('final_decision') == 'BUY'])
    sell_c = len([a for a in all_analyses if a.get('final_decision') == 'SELL'])
    hold_c = len([a for a in all_analyses if a.get('final_decision') == 'HOLD'])

    if is_zh:
        header = [
            "<b>📊 定时资产监测报告</b>",
            "",
            "<b>📈 综合概览</b>",
            f"• 监控任务: {len(monitor_results)} 个",
            f"• 标的数量: {len(all_analyses)} 个",
        ]
        if held:
            header.append(f"• 持仓: {len(held)} 个 | 总成本: ${total_cost:,.2f} | 盈亏: {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{total_pnl_pct:.1f}%)")
        if watched:
            header.append(f"• 观察: {len(watched)} 个")
        header.extend([
            "",
            "<b>🤖 AI建议汇总</b>",
            f"🟢 买入: {buy_c} | 🔴 卖出: {sell_c} | 🟡 持有: {hold_c}",
        ])
    else:
        header = [
            "<b>📊 Scheduled Portfolio Report</b>",
            "",
            "<b>📈 Summary</b>",
            f"• Monitors: {len(monitor_results)}",
            f"• Symbols: {len(all_analyses)}",
        ]
        if held:
            header.append(f"• Holdings: {len(held)} | Cost: ${total_cost:,.2f} | P&L: {pnl_sign}${total_pnl:,.2f} ({pnl_sign}{total_pnl_pct:.1f}%)")
        if watched:
            header.append(f"• Watchlist: {len(watched)}")
        header.extend([
            "",
            "<b>🤖 AI Recommendations</b>",
            f"🟢 Buy: {buy_c} | 🔴 Sell: {sell_c} | 🟡 Hold: {hold_c}",
        ])

    footer = [
        "",
        "─────────────────────",
        f"<i>⏰ {time.strftime('%Y-%m-%d %H:%M')}</i>",
        f"<i>{'由 QuantDinger 多智能体系统生成' if is_zh else 'Generated by QuantDinger Multi-Agent System'}</i>",
    ]

    return '\n'.join(header + monitor_sections + footer)


def _build_batch_html_report(
    monitor_results: List[Dict[str, Any]],
    language: str,
) -> str:
    """Build a combined HTML report for browser / email channel."""
    parts: List[str] = []
    for res in monitor_results:
        report = res.get('analysis', '')
        if report:
            parts.append(report)
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    divider = '<hr style="border:none;border-top:1px solid #e8e8e8;margin:24px 0;">'
    return divider.join(parts)


def _send_batch_notification(
    user_id: int,
    monitor_results: List[Dict[str, Any]],
) -> None:
    """Send a single combined notification for multiple monitor results belonging to one user."""
    if not monitor_results:
        return

    successful = [r for r in monitor_results if r.get('success')]
    if not successful:
        for r in monitor_results:
            meta = r.get('_meta', {})
            _send_monitor_notification(
                monitor_name=meta.get('monitor_name', '?'),
                result=r,
                notification_config=meta.get('notification_config', {}),
                positions=meta.get('positions', []),
                position_analyses=meta.get('position_analyses', []),
                language=meta.get('language', 'en-US'),
                custom_prompt=meta.get('custom_prompt', ''),
                user_id=user_id,
            )
        return

    first_meta = successful[0].get('_meta', {})
    language = first_meta.get('language', 'en-US')

    # Merge channels from all monitors (union)
    all_channels: set = set()
    for r in successful:
        m = r.get('_meta', {})
        nc = m.get('notification_config', {})
        chs = nc.get('channels')
        if isinstance(chs, str):
            chs = [chs]
        elif not isinstance(chs, list):
            chs = []
        for c in chs:
            if c:
                all_channels.add(str(c).strip().lower())
    if not all_channels:
        all_channels = {'browser'}

    merged_nc = {'channels': list(all_channels), 'targets': {}}
    resolved_nc = _resolve_notification_delivery(user_id, merged_nc)
    channels = resolved_nc.get('channels') or ['browser']
    targets = resolved_nc.get('targets', {})

    is_zh = language.startswith('zh')
    names = ', '.join(r.get('_meta', {}).get('monitor_name', '?') for r in successful)
    title = f"📊 定时资产监测: {names}" if is_zh else f"📊 Scheduled Report: {names}"
    if len(title) > 255:
        title = title[:252] + '...'

    html_report = _build_batch_html_report(successful, language)
    telegram_report = _build_batch_telegram_report(successful, language)

    try:
        notifier = SignalNotifier()
        for channel in channels:
            try:
                ch = str(channel).strip().lower()
                if ch == 'browser':
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            INSERT INTO qd_strategy_notifications
                            (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NOW())
                            """,
                            (user_id, 'PORTFOLIO', 'ai_monitor', 'browser', title, html_report,
                             json.dumps({'batch': True, 'count': len(successful)}, ensure_ascii=False, default=str)),
                        )
                        db.commit()
                        cur.close()
                elif ch == 'telegram':
                    chat_id = targets.get('telegram', '')
                    token_override = targets.get('telegram_bot_token', '')
                    if chat_id:
                        notifier._notify_telegram(
                            chat_id=chat_id, text=telegram_report,
                            token_override=token_override, parse_mode="HTML",
                        )
                elif ch == 'email':
                    to_email = targets.get('email', '')
                    if to_email:
                        notifier._notify_email(
                            to_email=to_email, subject=title,
                            body_text=html_report, body_html=html_report,
                        )
                elif ch == 'webhook':
                    url = targets.get('webhook', '')
                    if url:
                        notifier._notify_webhook(url=url, payload={
                            'type': 'portfolio_monitor_batch',
                            'monitors': [r.get('_meta', {}).get('monitor_name') for r in successful],
                            'html_report': html_report,
                        })
            except Exception as e:
                logger.warning(f"Batch notification channel {channel} failed: {e}")
    except Exception as e:
        logger.error(f"_send_batch_notification failed: {e}")


def _send_monitor_notification(
    monitor_name: str,
    result: Dict[str, Any],
    notification_config: Dict[str, Any],
    positions: List[Dict[str, Any]] = None,
    position_analyses: List[Dict[str, Any]] = None,
    language: str = 'en-US',
    custom_prompt: str = '',
    user_id: int = None
) -> None:
    """Send notification with analysis result using appropriate format for each channel."""
    try:
        notifier = SignalNotifier()
        effective_user_id = user_id if user_id is not None else DEFAULT_USER_ID
        notification_config = _resolve_notification_delivery(effective_user_id, notification_config)

        channels = notification_config.get('channels') or ['browser']
        targets = notification_config.get('targets', {})

        title = f"📊 资产监测: {monitor_name}" if language.startswith('zh') else f"📊 Portfolio Monitor: {monitor_name}"
        if len(title) > 255:
            title = title[:252] + '...'
        
        if not result.get('success'):
            error_title = f"⚠️ 资产监测失败: {monitor_name}" if language.startswith('zh') else f"⚠️ Monitor Failed: {monitor_name}"
            if len(error_title) > 255:
                error_title = error_title[:252] + '...'
            error_msg = f"分析失败: {result.get('error', 'Unknown error')}" if language.startswith('zh') else f"Analysis failed: {result.get('error', 'Unknown error')}"
            
            for channel in channels:
                try:
                    ch = str(channel).strip().lower()
                    if ch == 'browser':
                        with get_db_connection() as db:
                            cur = db.cursor()
                            cur.execute(
                                """
                                INSERT INTO qd_strategy_notifications
                                (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NOW())
                                """,
                                (effective_user_id, 'PORTFOLIO', 'ai_monitor', 'browser', error_title, error_msg,
                                 json.dumps(result, ensure_ascii=False, default=str))
                            )
                            db.commit()
                            cur.close()
                    elif ch == 'telegram':
                        chat_id = targets.get('telegram', '')
                        token_override = targets.get('telegram_bot_token', '')
                        if chat_id:
                            notifier._notify_telegram(chat_id=chat_id, text=f"<b>{error_title}</b>\n\n{error_msg}", token_override=token_override, parse_mode="HTML")
                    elif ch == 'email':
                        to_email = targets.get('email', '')
                        if to_email:
                            notifier._notify_email(to_email=to_email, subject=error_title, body_text=error_msg)
                except Exception as e:
                    logger.warning(f"Failed to send error notification to {channel}: {e}")
            return
        
        # Generate reports for different channels
        html_report = result.get('analysis', '')  # This is already HTML from _build_html_report
        
        # Generate Telegram-specific report if we have the data
        telegram_report = ''
        if positions is not None and position_analyses is not None:
            telegram_report = _build_telegram_report(positions, position_analyses, language, custom_prompt)
        else:
            # Fallback: strip HTML tags for Telegram
            import re
            telegram_report = re.sub(r'<[^>]+>', '', html_report)
            if len(telegram_report) > 4000:
                telegram_report = telegram_report[:4000] + '...'
        
        # Send to each channel
        for channel in channels:
            try:
                ch = str(channel).strip().lower()
                
                if ch == 'browser':
                    # Browser notification uses HTML report
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            INSERT INTO qd_strategy_notifications
                            (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NOW())
                            """,
                            (effective_user_id, 'PORTFOLIO', 'ai_monitor', 'browser', title, html_report,
                             json.dumps(result, ensure_ascii=False, default=str))
                        )
                        db.commit()
                        cur.close()
                
                elif ch == 'telegram':
                    chat_id = targets.get('telegram', '')
                    token_override = targets.get('telegram_bot_token', '')
                    if chat_id:
                        # Use Telegram-optimized format
                        notifier._notify_telegram(
                            chat_id=chat_id,
                            text=telegram_report,
                            token_override=token_override,
                            parse_mode="HTML"
                        )
                
                elif ch == 'email':
                    to_email = targets.get('email', '')
                    if to_email:
                        # Email uses full HTML report
                        notifier._notify_email(
                            to_email=to_email,
                            subject=title,
                            body_text=html_report,
                            body_html=html_report  # Send as HTML email
                        )
                
                elif ch == 'webhook':
                    url = targets.get('webhook', '')
                    if url:
                        notifier._notify_webhook(
                            url=url,
                            payload={
                                'type': 'portfolio_monitor',
                                'monitor_name': monitor_name,
                                'result': result,
                                'html_report': html_report
                            }
                        )
                        
            except Exception as e:
                logger.warning(f"Failed to send notification to {channel}: {e}")
                
    except Exception as e:
        logger.error(f"_send_monitor_notification failed: {e}")


def run_single_monitor(
    monitor_id: int,
    override_language: str = None,
    user_id: int = None,
    skip_notification: bool = False,
) -> Dict[str, Any]:
    """Run a single monitor and return the result.

    Args:
        monitor_id: The monitor ID to run
        override_language: Optional language override (e.g., 'zh-CN', 'en-US')
        user_id: Optional user ID for user isolation
        skip_notification: If True, do NOT send a notification (caller will batch-send later)
    """
    try:
        effective_user_id = user_id if user_id is not None else DEFAULT_USER_ID

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, name, position_ids, monitor_type, config, notification_config
                FROM qd_position_monitors
                WHERE id = ? AND user_id = ?
                """,
                (monitor_id, effective_user_id)
            )
            row = cur.fetchone()
            cur.close()

        if not row:
            return {'success': False, 'error': 'Monitor not found'}

        monitor_user_id = int(row.get('user_id') or effective_user_id)
        name = row.get('name') or f'Monitor #{monitor_id}'
        position_ids = _safe_json_loads(row.get('position_ids'), [])
        monitor_type = row.get('monitor_type') or 'ai'
        config = _safe_json_loads(row.get('config'), {})
        notification_config = _safe_json_loads(row.get('notification_config'), {})

        if override_language:
            config['language'] = override_language

        interval_minutes = int(
            config.get('run_interval_minutes')
            or config.get('interval_minutes')
            or 60
        )

        if position_ids:
            positions = _get_positions_for_monitor(position_ids, user_id=monitor_user_id)
        elif config.get('symbol'):
            target_sym = config['symbol'].strip().upper()
            target_mkt = (config.get('market') or '').strip()

            # Rule 4: symbol deleted from watchlist → skip
            still_in_watchlist = False
            try:
                with get_db_connection() as db:
                    cur = db.cursor()
                    wl_sql = "SELECT 1 FROM qd_watchlist WHERE user_id = ? AND UPPER(symbol) = ?"
                    wl_args: list = [monitor_user_id, target_sym]
                    if target_mkt:
                        wl_sql += " AND market = ?"
                        wl_args.append(target_mkt)
                    wl_sql += " LIMIT 1"
                    cur.execute(wl_sql, tuple(wl_args))
                    still_in_watchlist = cur.fetchone() is not None
                    cur.close()
            except Exception as e:
                logger.warning(f"Monitor #{monitor_id} watchlist check failed: {e}")

            if not still_in_watchlist:
                logger.info(f"Monitor #{monitor_id} skipped: {target_mkt}:{target_sym} removed from watchlist")
                skip_result = {
                    'success': False,
                    'error': 'Symbol removed from watchlist',
                    'skipped': True,
                    'timestamp': _now_ts(),
                }
                _bump_monitor_schedule(monitor_id, interval_minutes, skip_result, skipped=True)
                return skip_result

            # Rules 1&2: match real position if exists, otherwise virtual observation
            matched = _get_positions_for_monitor(None, user_id=monitor_user_id)
            positions = [
                p for p in matched
                if (p.get('symbol') or '').strip().upper() == target_sym
                and (not target_mkt or (p.get('market') or '').strip() == target_mkt)
            ]
            if not positions:
                positions = [{
                    'market': target_mkt,
                    'symbol': config['symbol'].strip(),
                    'name': config.get('name', config['symbol']).strip(),
                    'side': 'long',
                    'quantity': 0,
                    'entry_price': 0,
                    'current_price': 0,
                    'pnl': 0,
                    'pnl_percent': 0,
                }]
        else:
            # Rule 5: no position_ids, no config.symbol → nothing to analyze
            positions = []

        if not positions:
            logger.info(f"Monitor #{monitor_id} skipped: no matching positions found")
            skip_result = {
                'success': False,
                'error': 'No matching positions found',
                'skipped': True,
                'timestamp': _now_ts(),
            }
            _bump_monitor_schedule(monitor_id, interval_minutes, skip_result, skipped=True)
            return skip_result

        # ── Billing ──
        billing = get_billing_service()
        symbol_count = len(positions)
        per_symbol_cost = billing.get_feature_cost('ai_analysis')
        total_cost = per_symbol_cost * symbol_count

        if total_cost > 0 and billing.is_billing_enabled():
            user_credits = billing.get_user_credits(monitor_user_id)
            if user_credits < total_cost:
                logger.warning(
                    f"Monitor #{monitor_id} skipped: insufficient credits "
                    f"({user_credits} < {total_cost} for {symbol_count} symbols)"
                )
                skip_result = {
                    'success': False,
                    'error': f'Insufficient credits: need {total_cost}, have {user_credits}',
                    'skipped': True,
                    'timestamp': _now_ts(),
                }
                _bump_monitor_schedule(monitor_id, interval_minutes, skip_result, skipped=True)
                return skip_result
            for i in range(symbol_count):
                pos = positions[i]
                ok, msg = billing.check_and_consume(
                    user_id=monitor_user_id,
                    feature='ai_analysis',
                    reference_id=f"monitor_{monitor_id}_{pos.get('symbol', '')}"
                )
                if not ok:
                    logger.warning(f"Monitor #{monitor_id} billing failed at symbol #{i+1}: {msg}")
                    break

        if monitor_type == 'ai':
            result = _run_ai_analysis(positions, config, user_id=monitor_user_id)
        else:
            result = {'success': False, 'error': f'Unsupported monitor type: {monitor_type}'}

        # Advance the schedule using PostgreSQL's NOW() / INTERVAL to stay
        # consistent with the INSERT path (routes/portfolio.py) and avoid any
        # naive vs aware timestamp drift on the TIMESTAMP columns.
        _bump_monitor_schedule(monitor_id, interval_minutes, result, skipped=False)

        language = config.get('language', 'en-US')
        custom_prompt = config.get('prompt', '')
        position_analyses = result.get('position_analyses', [])
        deduped_positions = result.get('positions', positions)

        # Attach metadata used by batch notification / history
        result['_meta'] = {
            'monitor_id': monitor_id,
            'monitor_name': name,
            'user_id': monitor_user_id,
            'language': language,
            'custom_prompt': custom_prompt,
            'notification_config': notification_config,
            'positions': deduped_positions,
            'position_analyses': position_analyses,
        }

        if not skip_notification:
            _send_monitor_notification(
                monitor_name=name,
                result=result,
                notification_config=notification_config,
                positions=deduped_positions,
                position_analyses=position_analyses,
                language=language,
                custom_prompt=custom_prompt,
                user_id=monitor_user_id,
            )

        return result
    except Exception as e:
        logger.error(f"run_single_monitor failed: {e}")
        logger.error(traceback.format_exc())
        return {'success': False, 'error': str(e)}


def _check_position_alerts():
    """Check all active alerts and trigger notifications if conditions are met."""
    from datetime import datetime, timezone
    try:
        kline_service = KlineService()
        notifier = SignalNotifier()
        now = datetime.now(timezone.utc)
        
        with get_db_connection() as db:
            cur = db.cursor()
            # Get active alerts for all users that haven't been triggered (or can repeat)
            cur.execute(
                """
                SELECT a.id, a.user_id, a.position_id, a.market, a.symbol, a.alert_type, a.threshold,
                       a.notification_config, a.is_triggered, a.last_triggered_at, a.repeat_interval,
                       p.entry_price, p.quantity, p.side, p.name as position_name
                FROM qd_position_alerts a
                LEFT JOIN qd_manual_positions p ON a.position_id = p.id
                WHERE a.is_active = 1
                """
            )
            alerts = cur.fetchall() or []
            cur.close()
        
        for alert in alerts:
            try:
                alert_id = alert.get('id')
                alert_user_id = int(alert.get('user_id') or 1)
                alert_type = alert.get('alert_type')
                threshold = float(alert.get('threshold') or 0)
                market = alert.get('market')
                symbol = alert.get('symbol')
                is_triggered = bool(alert.get('is_triggered'))
                last_triggered_at = alert.get('last_triggered_at')  # datetime or None
                repeat_interval = int(alert.get('repeat_interval') or 0)
                notification_config = _safe_json_loads(alert.get('notification_config'), {})
                
                # Check if we can trigger (not triggered yet, or repeat interval passed)
                can_trigger = not is_triggered
                if is_triggered and repeat_interval > 0 and last_triggered_at:
                    # Convert last_triggered_at to timezone-aware if needed
                    if last_triggered_at.tzinfo is None:
                        last_triggered_at = last_triggered_at.replace(tzinfo=timezone.utc)
                    elapsed_seconds = (now - last_triggered_at).total_seconds()
                    if elapsed_seconds >= repeat_interval:
                        can_trigger = True
                
                if not can_trigger:
                    continue
                
                # Get current price (use realtime price API)
                current_price = 0
                try:
                    price_data = kline_service.get_realtime_price(market, symbol)
                    current_price = float(price_data.get('price') or 0)
                except Exception:
                    continue
                
                if current_price <= 0:
                    continue
                
                triggered = False
                alert_message = ""
                
                # Get language from notification_config (saved when alert was created)
                alert_language = notification_config.get('language', 'en-US')
                
                if alert_type == 'price_above':
                    if current_price >= threshold:
                        triggered = True
                        alert_message = _get_alert_message(
                            'price_above', alert_language,
                            symbol=symbol, current_price=current_price, threshold=threshold
                        )
                
                elif alert_type == 'price_below':
                    if current_price <= threshold:
                        triggered = True
                        alert_message = _get_alert_message(
                            'price_below', alert_language,
                            symbol=symbol, current_price=current_price, threshold=threshold
                        )
                
                elif alert_type in ('pnl_above', 'pnl_below'):
                    entry_price = float(alert.get('entry_price') or 0)
                    quantity = float(alert.get('quantity') or 0)
                    side = alert.get('side') or 'long'
                    
                    if entry_price > 0 and quantity > 0:
                        if side == 'long':
                            pnl = (current_price - entry_price) * quantity
                        else:
                            pnl = (entry_price - current_price) * quantity
                        pnl_percent = pnl / (entry_price * quantity) * 100
                        
                        if alert_type == 'pnl_above' and pnl_percent >= threshold:
                            triggered = True
                            alert_message = _get_alert_message(
                                'pnl_above', alert_language,
                                symbol=symbol, pnl_percent=pnl_percent, threshold=threshold
                            )
                        elif alert_type == 'pnl_below' and pnl_percent <= threshold:
                            triggered = True
                            alert_message = _get_alert_message(
                                'pnl_below', alert_language,
                                symbol=symbol, pnl_percent=pnl_percent, threshold=threshold
                            )
                
                if triggered:
                    logger.info(f"Alert #{alert_id} triggered: {alert_message}")
                    
                    # Update alert status
                    with get_db_connection() as db:
                        cur = db.cursor()
                        cur.execute(
                            """
                            UPDATE qd_position_alerts
                            SET is_triggered = 1, last_triggered_at = NOW(), trigger_count = trigger_count + 1, updated_at = NOW()
                            WHERE id = ?
                            """,
                            (alert_id,)
                        )
                        db.commit()
                        cur.close()
                    
                    resolved = _resolve_notification_delivery(alert_user_id, notification_config)
                    channels = resolved.get('channels') or ['browser']
                    targets = resolved.get('targets', {})
                    alert_title = _get_alert_title(alert_language)
                    
                    for channel in channels:
                        try:
                            ch = str(channel).strip().lower()
                            if ch == 'browser':
                                with get_db_connection() as db:
                                    cur = db.cursor()
                                    cur.execute(
                                        """
                                        INSERT INTO qd_strategy_notifications
                                        (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                                        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NOW())
                                        """,
                                        (alert_user_id, symbol, 'price_alert', 'browser', alert_title, alert_message,
                                         json.dumps({'alert_id': alert_id, 'alert_type': alert_type}, ensure_ascii=False))
                                    )
                                    db.commit()
                                    cur.close()
                            elif ch == 'telegram':
                                chat_id = targets.get('telegram', '')
                                token_override = targets.get('telegram_bot_token', '')
                                if chat_id:
                                    notifier._notify_telegram(chat_id=chat_id, text=alert_message, token_override=token_override, parse_mode="HTML")
                            elif ch == 'email':
                                to_email = targets.get('email', '')
                                if to_email:
                                    notifier._notify_email(to_email=to_email, subject=alert_title, body_text=alert_message)
                        except Exception as e:
                            logger.warning(f"Failed to send alert notification: {e}")
                            
            except Exception as e:
                logger.warning(f"Error processing alert: {e}")
                
    except Exception as e:
        logger.error(f"_check_position_alerts failed: {e}")


def notify_strategy_signal_for_positions(market: str, symbol: str, signal_type: str, signal_detail: str, user_id: int = None):
    """
    Called when a strategy signal is triggered.
    Check if the **strategy owner** has manual positions in this symbol and send a
    notification to them.

    Security note: this function is fail-closed on ``user_id``. Earlier versions
    silently fell back to ``WHERE symbol = ?`` (no user filter) when callers
    omitted ``user_id``, which caused user A's strategy signal to fan out to
    every user holding the same symbol and leaked strategy name / signal
    details across tenants. Callers MUST pass the strategy owner's ``user_id``.
    """
    try:
        symbol = (symbol or '').strip().upper()
        if not symbol:
            return

        if user_id is None:
            logger.warning(
                "notify_strategy_signal_for_positions called without user_id; "
                "refusing to broadcast across users (symbol=%s, signal=%s)",
                symbol, signal_type,
            )
            return

        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, market, symbol, name, side, quantity, entry_price, group_name
                FROM qd_manual_positions
                WHERE user_id = ? AND symbol = ?
                """,
                (user_id, symbol),
            )
            positions = cur.fetchall() or []
            cur.close()
        
        if not positions:
            return
        
        # User has positions in this symbol - send notification
        notifier = SignalNotifier()
        now = _now_ts()
        
        for pos in positions:
            pos_user_id = int(pos.get('user_id') or 1)
            pos_name = pos.get('name') or symbol
            pos_side = pos.get('side') or 'long'
            quantity = float(pos.get('quantity') or 0)
            entry_price = float(pos.get('entry_price') or 0)
            
            title = f"🔗 策略信号联动: {pos_name}"
            message = f"""策略发出 {signal_type} 信号!

标的: {market}/{symbol}
您的持仓: {pos_side.upper()} {quantity} @ {entry_price:.4f}

信号详情:
{signal_detail}

请注意检查您的持仓是否需要调整。"""
            
            # Save browser notification
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_strategy_notifications
                    (user_id, strategy_id, symbol, signal_type, channels, title, message, payload_json, created_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NOW())
                    """,
                    (pos_user_id, symbol, 'strategy_linkage', 'browser', title, message,
                     json.dumps({'signal_type': signal_type}, ensure_ascii=False))
                )
                db.commit()
                cur.close()
        
        logger.info(f"Strategy signal linkage: notified {len(positions)} position(s) for {symbol}")
        
    except Exception as e:
        logger.error(f"notify_strategy_signal_for_positions failed: {e}")


def _monitor_loop():
    """Background loop that checks and runs due monitors.

    All monitors due in the same cycle are executed first (with skip_notification),
    then results are grouped by user_id and sent as one combined notification per user.
    """
    logger.info("Portfolio monitor background loop started")

    while not _stop_event.is_set():
        try:
            _check_position_alerts()

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT id, user_id FROM qd_position_monitors
                    WHERE is_active = 1 AND next_run_at <= NOW()
                    ORDER BY next_run_at ASC
                    LIMIT 20
                    """
                )
                rows = cur.fetchall() or []
                cur.close()

            # Collect results per user
            user_results: Dict[int, List[Dict[str, Any]]] = {}
            for row in rows:
                if _stop_event.is_set():
                    break
                monitor_id = row.get('id')
                monitor_user_id = int(row.get('user_id') or 1)
                if not monitor_id:
                    continue
                logger.info(f"Running due monitor #{monitor_id} for user #{monitor_user_id}")
                try:
                    result = run_single_monitor(
                        monitor_id,
                        user_id=monitor_user_id,
                        skip_notification=True,
                    )
                    user_results.setdefault(monitor_user_id, []).append(result)
                except Exception as e:
                    logger.error(f"Monitor #{monitor_id} execution failed: {e}")

            # Send one combined notification per user
            for uid, results in user_results.items():
                try:
                    if len(results) == 1:
                        meta = results[0].get('_meta', {})
                        _send_monitor_notification(
                            monitor_name=meta.get('monitor_name', '?'),
                            result=results[0],
                            notification_config=meta.get('notification_config', {}),
                            positions=meta.get('positions', []),
                            position_analyses=meta.get('position_analyses', []),
                            language=meta.get('language', 'en-US'),
                            custom_prompt=meta.get('custom_prompt', ''),
                            user_id=uid,
                        )
                    else:
                        _send_batch_notification(uid, results)
                except Exception as e:
                    logger.error(f"Batch notification for user #{uid} failed: {e}")

        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        _stop_event.wait(30)

    logger.info("Portfolio monitor background loop stopped")


def start_monitor_service():
    """Start the background monitor service."""
    global _monitor_thread
    
    if _monitor_thread and _monitor_thread.is_alive():
        logger.info("Portfolio monitor service already running")
        return
    
    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="PortfolioMonitor")
    _monitor_thread.start()
    logger.info("Portfolio monitor service started")


def stop_monitor_service():
    """Stop the background monitor service."""
    global _monitor_thread
    
    _stop_event.set()
    if _monitor_thread:
        _monitor_thread.join(timeout=5)
        _monitor_thread = None
    logger.info("Portfolio monitor service stopped")
