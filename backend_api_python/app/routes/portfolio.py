"""
Portfolio API routes (local-only).
Manages manual positions (user's existing holdings) and AI monitoring tasks.
"""
from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from datetime import date, datetime, timezone
import os
import json
import traceback
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.services.kline import KlineService
from app.utils.logger import get_logger
from app.utils.cache import CacheManager
from app.utils.db import get_db_connection
from app.utils.auth import login_required
from app.services.symbol_name import resolve_symbol_name, normalize_crypto_symbol
from app.data.market_symbols_seed import get_symbol_name as seed_get_symbol_name

logger = get_logger(__name__)

portfolio_blp = Blueprint('portfolio', __name__)
kline_service = KlineService()
cache = CacheManager()

# Thread pool for parallel price fetching.
# Lower concurrency to avoid triggering API limits (especially for forex/US
# stocks) and to keep pressure off the DB connection pool.
# Tunable via PORTFOLIO_EXECUTOR_WORKERS env.
def _portfolio_executor_workers() -> int:
    try:
        v = int(os.getenv("PORTFOLIO_EXECUTOR_WORKERS", "3"))
        return v if v > 0 else 3
    except Exception:
        return 3

executor = ThreadPoolExecutor(max_workers=_portfolio_executor_workers())

# Request interval (seconds) to avoid too frequent requests
REQUEST_INTERVAL = 0.3

# Rate limiting related
_request_lock = threading.Lock()
_last_request_time = {}  # {market: timestamp}


def _now_ts() -> int:
    return int(time.time())


def _serialize_monitor_ts(value):
    """Serialize a TIMESTAMP value for the frontend.

    DB columns are ``TIMESTAMP WITHOUT TIME ZONE`` and PostgreSQL writes them
    as wall-clock in the container's ``TZ`` (see docker-compose ``TZ`` env
    var).  The old implementation assumed naive = UTC, which was wrong on any
    deployment whose container TZ wasn't UTC (default is Asia/Shanghai for
    this project) and caused an 8-hour drift on the dashboard.

    Delegating to ``to_utc_iso`` keeps this consistent with the global
    ``SafeJSONProvider`` rule: naive timestamps are interpreted in the server's
    local time zone, then re-emitted as UTC ISO 8601 with a ``Z`` suffix.
    """
    from app.utils.timeutil import to_utc_iso

    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize_symbol(symbol: str) -> str:
    return (symbol or '').strip().upper()


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


def _get_single_price(market: str, symbol: str, force_refresh: bool = False) -> dict:
    """
    Get price data for a single symbol.
    
    优先使用实时报价 API（ticker），降级使用分钟/日线 K 线数据。
    这样可以在交易时段获取更实时的价格，而不是只显示日线收盘价。
    
    内置速率限制：同一市场的请求间隔至少 REQUEST_INTERVAL 秒，
    避免触发 API 限制（如 yfinance、Tiingo、Finnhub 等）。
    
    Args:
        force_refresh: 是否强制刷新（跳过缓存）
    """
    try:
        # 速率限制：同一市场的请求间隔
        with _request_lock:
            now = time.time()
            last_time = _last_request_time.get(market, 0)
            wait_time = REQUEST_INTERVAL - (now - last_time)
            if wait_time > 0:
                time.sleep(wait_time)
            _last_request_time[market] = time.time()
        
        # 使用新的 get_realtime_price 方法获取实时价格
        price_data = kline_service.get_realtime_price(market, symbol, force_refresh=force_refresh)
        
        return {
            'market': market,
            'symbol': symbol,
            'price': price_data.get('price', 0),
            'change': price_data.get('change', 0),
            'changePercent': price_data.get('changePercent', 0),
            'source': price_data.get('source', 'unknown')  # 记录数据来源，便于调试
        }
    except Exception as e:
        logger.error(f"Failed to fetch price {market}:{symbol} - {str(e)}")
        return {
            'market': market,
            'symbol': symbol,
            'price': 0,
            'change': 0,
            'changePercent': 0,
            'source': 'error'
        }


# ==================== Position CRUD ====================

@portfolio_blp.route('/positions', methods=['GET'])
@login_required
def get_positions():
    """Get all manual positions with current prices for the current user."""
    try:
        user_id = g.user_id
        # Check if force refresh (skip cache)
        force_refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, market, symbol, name, side, quantity, entry_price, entry_time, notes, tags, group_name, created_at, updated_at
                FROM qd_manual_positions
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        positions = []
        price_futures = {}
        
        # Prepare positions and submit price fetch tasks
        for row in rows:
            pos = {
                'id': row.get('id'),
                'market': row.get('market'),
                'symbol': row.get('symbol'),
                'name': row.get('name') or row.get('symbol'),
                'side': row.get('side') or 'long',
                'quantity': float(row.get('quantity') or 0),
                'entry_price': float(row.get('entry_price') or 0),
                'entry_time': row.get('entry_time'),
                'notes': row.get('notes') or '',
                'tags': _safe_json_loads(row.get('tags'), []),
                'group_name': row.get('group_name') or '',
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                # Will be filled later
                'current_price': 0,
                'price_change': 0,
                'price_change_percent': 0,
                'market_value': 0,
                'cost_value': 0,
                'pnl': 0,
                'pnl_percent': 0
            }
            positions.append(pos)
            
            # Submit price fetch task (with force_refresh support)
            market = row.get('market')
            symbol = row.get('symbol')
            if market and symbol:
                key = f"{market}:{symbol}"
                if key not in price_futures:
                    future = executor.submit(_get_single_price, market, symbol, force_refresh)
                    price_futures[key] = future

        # Collect price results
        price_map = {}
        for key, future in price_futures.items():
            try:
                result = future.result(timeout=10)
                price_map[key] = result
            except Exception as e:
                logger.warning(f"Price fetch failed for {key}: {e}")

        # Calculate PnL for each position
        for pos in positions:
            key = f"{pos['market']}:{pos['symbol']}"
            price_data = price_map.get(key, {})
            
            current_price = float(price_data.get('price') or 0)
            entry_price = pos['entry_price']
            quantity = pos['quantity']
            side = pos['side']
            
            pos['current_price'] = current_price
            pos['price_change'] = price_data.get('change', 0)
            pos['price_change_percent'] = price_data.get('changePercent', 0)
            
            # Calculate values
            pos['market_value'] = current_price * quantity
            pos['cost_value'] = entry_price * quantity
            
            # Calculate PnL based on side
            if side == 'long':
                pos['pnl'] = (current_price - entry_price) * quantity
            else:  # short
                pos['pnl'] = (entry_price - current_price) * quantity
            
            if pos['cost_value'] > 0:
                pos['pnl_percent'] = round(pos['pnl'] / pos['cost_value'] * 100, 2)

        return jsonify({'code': 1, 'msg': 'success', 'data': positions})
    except Exception as e:
        logger.error(f"get_positions failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@portfolio_blp.route('/positions', methods=['POST'])
@login_required
def add_position():
    """Add a new manual position for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        market = (data.get('market') or '').strip()
        symbol = _normalize_symbol(data.get('symbol'))
        name_in = (data.get('name') or '').strip()
        side = (data.get('side') or 'long').strip().lower()
        quantity = float(data.get('quantity') or 0)
        entry_price = float(data.get('entry_price') or 0)
        entry_time = data.get('entry_time') or _now_ts()
        notes = (data.get('notes') or '').strip()
        tags = data.get('tags') or []
        group_name = (data.get('group_name') or '').strip()
        
        if not market or not symbol:
            return jsonify({'code': 0, 'msg': 'Missing market or symbol', 'data': None}), 400

        # Canonicalise Crypto symbols (BTC -> BTC/USDT, BTCUSDT -> BTC/USDT)
        # so positions, watchlist and strategies all dedupe on the same key.
        # See app.services.symbol_name.normalize_crypto_symbol for the full
        # contract.
        if market == 'Crypto':
            symbol = normalize_crypto_symbol(symbol)

        if quantity <= 0:
            return jsonify({'code': 0, 'msg': 'Quantity must be positive', 'data': None}), 400
        
        if entry_price <= 0:
            return jsonify({'code': 0, 'msg': 'Entry price must be positive', 'data': None}), 400
        
        if side not in ('long', 'short'):
            side = 'long'
        
        # Resolve display name
        resolved = resolve_symbol_name(market, symbol) or seed_get_symbol_name(market, symbol)
        name = name_in or resolved or symbol
        
        tags_json = json.dumps(tags if isinstance(tags, list) else [], ensure_ascii=False)
        
        with get_db_connection() as db:
            cur = db.cursor()
            # Delete any existing positions for this symbol (regardless of side),
            # ensuring only one position per symbol per user per group.
            cur.execute(
                "DELETE FROM qd_manual_positions WHERE user_id = ? AND market = ? AND symbol = ? AND group_name = ?",
                (user_id, market, symbol, group_name)
            )
            cur.execute(
                """
                INSERT INTO qd_manual_positions 
                (user_id, market, symbol, name, side, quantity, entry_price, entry_time, notes, tags, group_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                """,
                (user_id, market, symbol, name, side, quantity, entry_price, entry_time, notes, tags_json, group_name)
            )
            position_id = cur.lastrowid
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': {'id': position_id}})
    except Exception as e:
        logger.error(f"add_position failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/positions/<int:position_id>', methods=['PUT'])
@login_required
def update_position(position_id):
    """Update an existing position for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        
        updates = []
        params = []
        
        if 'name' in data:
            updates.append('name = ?')
            params.append((data.get('name') or '').strip())
        
        if 'quantity' in data:
            quantity = float(data.get('quantity') or 0)
            if quantity <= 0:
                return jsonify({'code': 0, 'msg': 'Quantity must be positive', 'data': None}), 400
            updates.append('quantity = ?')
            params.append(quantity)
        
        if 'entry_price' in data:
            entry_price = float(data.get('entry_price') or 0)
            if entry_price <= 0:
                return jsonify({'code': 0, 'msg': 'Entry price must be positive', 'data': None}), 400
            updates.append('entry_price = ?')
            params.append(entry_price)
        
        if 'entry_time' in data:
            updates.append('entry_time = ?')
            params.append(data.get('entry_time'))
        
        if 'notes' in data:
            updates.append('notes = ?')
            params.append((data.get('notes') or '').strip())
        
        if 'tags' in data:
            tags = data.get('tags') or []
            updates.append('tags = ?')
            params.append(json.dumps(tags if isinstance(tags, list) else [], ensure_ascii=False))
        
        if 'group_name' in data:
            updates.append('group_name = ?')
            params.append((data.get('group_name') or '').strip())
        
        if not updates:
            return jsonify({'code': 0, 'msg': 'No fields to update', 'data': None}), 400
        
        updates.append('updated_at = NOW()')
        params.append(position_id)
        params.append(user_id)
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"UPDATE qd_manual_positions SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"update_position failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/positions/<int:position_id>', methods=['DELETE'])
@login_required
def delete_position(position_id):
    """Delete a position for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM qd_manual_positions WHERE id = ? AND user_id = ?",
                (position_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"delete_position failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/summary', methods=['GET'])
@login_required
def get_portfolio_summary():
    """Get portfolio summary with total value, PnL, and market distribution for the current user."""
    try:
        user_id = g.user_id
        # Check if force refresh
        force_refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, market, symbol, side, quantity, entry_price
                FROM qd_manual_positions
                WHERE user_id = ?
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        if not rows:
            return jsonify({
                'code': 1,
                'msg': 'success',
                'data': {
                    'total_cost': 0,
                    'total_market_value': 0,
                    'total_pnl': 0,
                    'total_pnl_percent': 0,
                    'position_count': 0,
                    'market_distribution': []
                }
            })

        # Fetch prices in parallel (with force_refresh support)
        price_futures = {}
        for row in rows:
            market = row.get('market')
            symbol = row.get('symbol')
            key = f"{market}:{symbol}"
            if key not in price_futures:
                future = executor.submit(_get_single_price, market, symbol, force_refresh)
                price_futures[key] = future

        price_map = {}
        for key, future in price_futures.items():
            try:
                result = future.result(timeout=10)
                price_map[key] = result
            except Exception:
                pass

        # Calculate totals
        total_cost = 0
        total_market_value = 0
        total_pnl = 0
        market_values = {}  # {market: market_value}
        
        for row in rows:
            market = row.get('market')
            symbol = row.get('symbol')
            side = row.get('side') or 'long'
            quantity = float(row.get('quantity') or 0)
            entry_price = float(row.get('entry_price') or 0)
            
            key = f"{market}:{symbol}"
            price_data = price_map.get(key, {})
            current_price = float(price_data.get('price') or 0)
            
            cost = entry_price * quantity
            market_val = current_price * quantity
            
            if side == 'long':
                pnl = (current_price - entry_price) * quantity
            else:
                pnl = (entry_price - current_price) * quantity
            
            total_cost += cost
            total_market_value += market_val
            total_pnl += pnl
            
            # Market distribution
            if market not in market_values:
                market_values[market] = 0
            market_values[market] += market_val

        total_pnl_percent = round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0
        
        # Build market distribution
        market_distribution = []
        for market, value in market_values.items():
            percent = round(value / total_market_value * 100, 2) if total_market_value > 0 else 0
            market_distribution.append({
                'market': market,
                'value': round(value, 2),
                'percent': percent
            })
        
        market_distribution.sort(key=lambda x: x['value'], reverse=True)

        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'total_cost': round(total_cost, 2),
                'total_market_value': round(total_market_value, 2),
                'total_pnl': round(total_pnl, 2),
                'total_pnl_percent': total_pnl_percent,
                'position_count': len(rows),
                'market_distribution': market_distribution
            }
        })
    except Exception as e:
        logger.error(f"get_portfolio_summary failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Monitor CRUD ====================

@portfolio_blp.route('/monitors', methods=['GET'])
@login_required
def get_monitors():
    """Get all position monitors for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, name, position_ids, monitor_type, config, notification_config, 
                       is_active, last_run_at, next_run_at, last_result, run_count, created_at, updated_at
                FROM qd_position_monitors
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        monitors = []
        for row in rows:
            monitors.append({
                'id': row.get('id'),
                'name': row.get('name') or '',
                'position_ids': _safe_json_loads(row.get('position_ids'), []),
                'monitor_type': row.get('monitor_type') or 'ai',
                'config': _safe_json_loads(row.get('config'), {}),
                'notification_config': _safe_json_loads(row.get('notification_config'), {}),
                'is_active': bool(row.get('is_active')),
                'last_run_at': _serialize_monitor_ts(row.get('last_run_at')),
                'next_run_at': _serialize_monitor_ts(row.get('next_run_at')),
                'last_result': _safe_json_loads(row.get('last_result'), {}),
                'run_count': row.get('run_count') or 0,
                'created_at': _serialize_monitor_ts(row.get('created_at')),
                'updated_at': _serialize_monitor_ts(row.get('updated_at'))
            })

        return jsonify({'code': 1, 'msg': 'success', 'data': monitors})
    except Exception as e:
        logger.error(f"get_monitors failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@portfolio_blp.route('/monitors', methods=['POST'])
@login_required
def add_monitor():
    """Add a new position monitor for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        position_ids = data.get('position_ids') or []
        monitor_type = (data.get('monitor_type') or 'ai').strip()
        config = data.get('config') or {}
        notification_config = data.get('notification_config') or {}
        is_active = bool(data.get('is_active', True))
        
        if not name:
            return jsonify({'code': 0, 'msg': 'Monitor name is required', 'data': None}), 400
        
        if monitor_type not in ('ai', 'price_alert', 'pnl_alert'):
            monitor_type = 'ai'
        
        # Calculate next_run_at based on interval (frontend sends run_interval_minutes)
        interval_minutes = int(config.get('run_interval_minutes') or config.get('interval_minutes') or 60)
        
        position_ids_json = json.dumps(position_ids if isinstance(position_ids, list) else [], ensure_ascii=False)
        config_json = json.dumps(config if isinstance(config, dict) else {}, ensure_ascii=False)
        notification_config_json = json.dumps(notification_config if isinstance(notification_config, dict) else {}, ensure_ascii=False)
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO qd_position_monitors 
                (user_id, name, position_ids, monitor_type, config, notification_config, is_active, next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NOW() + INTERVAL '%s minutes', NOW(), NOW())
                """,
                (user_id, name, position_ids_json, monitor_type, config_json, notification_config_json, 
                 1 if is_active else 0, interval_minutes)
            )
            monitor_id = cur.lastrowid
            db.commit()
            cur.close()

        # 创建后立即在后台跑一轮：立刻发通知，并以完成时刻为基准写入 next_run_at（间隔后再次执行）
        if is_active and monitor_id:
            try:
                from app.services.portfolio_monitor import run_single_monitor as _run_single_monitor

                def _initial_run():
                    try:
                        _run_single_monitor(int(monitor_id), user_id=int(user_id))
                    except Exception as ex:
                        logger.error(f"Initial portfolio monitor run failed #{monitor_id}: {ex}")

                threading.Thread(
                    target=_initial_run,
                    daemon=True,
                    name=f"monitor-init-{monitor_id}",
                ).start()
            except Exception as ex:
                logger.error(f"Failed to schedule initial monitor run #{monitor_id}: {ex}")

        return jsonify({'code': 1, 'msg': 'success', 'data': {'id': monitor_id}})
    except Exception as e:
        logger.error(f"add_monitor failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/monitors/<int:monitor_id>', methods=['PUT'])
@login_required
def update_monitor(monitor_id):
    """Update an existing monitor for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        
        updates = []
        params = []
        
        if 'name' in data:
            updates.append('name = ?')
            params.append((data.get('name') or '').strip())
        
        if 'position_ids' in data:
            position_ids = data.get('position_ids') or []
            updates.append('position_ids = ?')
            params.append(json.dumps(position_ids if isinstance(position_ids, list) else [], ensure_ascii=False))
        
        if 'monitor_type' in data:
            updates.append('monitor_type = ?')
            params.append((data.get('monitor_type') or 'ai').strip())
        
        next_run_interval = None  # Will store interval for special handling
        if 'config' in data:
            config = data.get('config') or {}
            updates.append('config = ?')
            params.append(json.dumps(config if isinstance(config, dict) else {}, ensure_ascii=False))
            
            # Recalculate next_run_at if interval changed
            next_run_interval = int(config.get('run_interval_minutes') or config.get('interval_minutes') or 60)
        
        if 'notification_config' in data:
            notification_config = data.get('notification_config') or {}
            updates.append('notification_config = ?')
            params.append(json.dumps(notification_config if isinstance(notification_config, dict) else {}, ensure_ascii=False))
        
        if 'is_active' in data:
            updates.append('is_active = ?')
            params.append(1 if data.get('is_active') else 0)
        
        if not updates:
            return jsonify({'code': 0, 'msg': 'No fields to update', 'data': None}), 400
        
        # Add next_run_at update if interval was changed
        if next_run_interval is not None:
            # Bind minutes to avoid SQL string interpolation; Postgres: (N || ' minutes')::interval
            updates.append("next_run_at = NOW() + (? || ' minutes')::interval")
            params.append(int(next_run_interval))
        
        updates.append('updated_at = NOW()')
        params.append(monitor_id)
        params.append(user_id)
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"UPDATE qd_position_monitors SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"update_monitor failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/monitors/<int:monitor_id>', methods=['DELETE'])
@login_required
def delete_monitor(monitor_id):
    """Delete a monitor for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM qd_position_monitors WHERE id = ? AND user_id = ?",
                (monitor_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"delete_monitor failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/monitors/<int:monitor_id>/run', methods=['POST'])
@login_required
def run_monitor_now(monitor_id):
    """Manually trigger a monitor to run immediately.
    
    Supports two modes:
    - async=true (default): Returns immediately, runs in background, notifies via notification system
    - async=false: Waits for completion and returns result (may timeout for large portfolios)
    """
    try:
        from app.services.portfolio_monitor import run_single_monitor
        
        user_id = g.user_id
        
        # Get parameters from request body
        data = request.get_json(force=True, silent=True) or {}
        language = data.get('language')
        async_mode = data.get('async', True)  # Default to async mode
        
        # Fallback to Accept-Language header for language
        if not language:
            accept_lang = request.headers.get('Accept-Language', '')
            if 'zh' in accept_lang.lower():
                language = 'zh-CN'
            else:
                language = 'en-US'
        
        if async_mode:
            # Async mode: Start background thread and return immediately
            import threading
            
            def run_in_background(mid, lang, uid):
                try:
                    run_single_monitor(mid, override_language=lang, user_id=uid)
                except Exception as e:
                    logger.error(f"Background monitor run failed: {e}")
            
            thread = threading.Thread(
                target=run_in_background,
                args=(monitor_id, language, user_id),
                daemon=True
            )
            thread.start()
            
            return jsonify({
                'code': 1, 
                'msg': 'success', 
                'data': {
                    'status': 'running',
                    'message': 'Monitor is running in background. Results will be sent via notification.'
                }
            })
        else:
            # Sync mode: Wait for completion (may timeout)
            result = run_single_monitor(monitor_id, override_language=language, user_id=user_id)
            return jsonify({'code': 1, 'msg': 'success', 'data': result})
            
    except Exception as e:
        logger.error(f"run_monitor_now failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Alerts CRUD ====================

@portfolio_blp.route('/alerts', methods=['GET'])
@login_required
def get_alerts():
    """Get all position alerts for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT a.id, a.position_id, a.market, a.symbol, a.alert_type, a.threshold,
                       a.notification_config, a.is_active, a.is_triggered, a.last_triggered_at,
                       a.trigger_count, a.repeat_interval, a.notes, a.created_at, a.updated_at,
                       p.name as position_name, p.side as position_side
                FROM qd_position_alerts a
                LEFT JOIN qd_manual_positions p ON a.position_id = p.id
                WHERE a.user_id = ?
                ORDER BY a.id DESC
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            cur.close()

        alerts = []
        for row in rows:
            alerts.append({
                'id': row.get('id'),
                'position_id': row.get('position_id'),
                'market': row.get('market') or '',
                'symbol': row.get('symbol') or '',
                'alert_type': row.get('alert_type') or 'price_above',
                'threshold': float(row.get('threshold') or 0),
                'notification_config': _safe_json_loads(row.get('notification_config'), {}),
                'is_active': bool(row.get('is_active')),
                'is_triggered': bool(row.get('is_triggered')),
                'last_triggered_at': row.get('last_triggered_at'),
                'trigger_count': row.get('trigger_count') or 0,
                'repeat_interval': row.get('repeat_interval') or 0,
                'notes': row.get('notes') or '',
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'position_name': row.get('position_name') or '',
                'position_side': row.get('position_side') or 'long'
            })

        return jsonify({'code': 1, 'msg': 'success', 'data': alerts})
    except Exception as e:
        logger.error(f"get_alerts failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': []}), 500


@portfolio_blp.route('/alerts', methods=['POST'])
@login_required
def add_alert():
    """Add a new position alert for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        position_id = data.get('position_id')  # Can be None for symbol-level alerts
        market = (data.get('market') or '').strip()
        symbol = _normalize_symbol(data.get('symbol'))
        alert_type = (data.get('alert_type') or 'price_above').strip()
        threshold = float(data.get('threshold') or 0)
        notification_config = data.get('notification_config') or {}
        is_active = bool(data.get('is_active', True))
        repeat_interval = int(data.get('repeat_interval') or 0)
        notes = (data.get('notes') or '').strip()
        
        # Validate alert_type
        valid_types = ('price_above', 'price_below', 'pnl_above', 'pnl_below')
        if alert_type not in valid_types:
            return jsonify({'code': 0, 'msg': f'Invalid alert_type. Must be one of: {valid_types}', 'data': None}), 400
        
        # If position_id provided, get market/symbol from position
        if position_id:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT market, symbol FROM qd_manual_positions WHERE id = ? AND user_id = ?",
                    (position_id, user_id)
                )
                pos = cur.fetchone()
                cur.close()
                if pos:
                    market = pos.get('market') or market
                    symbol = pos.get('symbol') or symbol
        
        if not market or not symbol:
            return jsonify({'code': 0, 'msg': 'Market and symbol are required', 'data': None}), 400
        
        if threshold <= 0 and alert_type.startswith('price_'):
            return jsonify({'code': 0, 'msg': 'Threshold must be positive for price alerts', 'data': None}), 400
        
        notification_config_json = json.dumps(notification_config if isinstance(notification_config, dict) else {}, ensure_ascii=False)
        
        with get_db_connection() as db:
            cur = db.cursor()
            
            # Check if alert already exists for this position (unique constraint)
            existing_alert_id = None
            if position_id:
                cur.execute(
                    "SELECT id FROM qd_position_alerts WHERE position_id = ? AND user_id = ?",
                    (position_id, user_id)
                )
                existing = cur.fetchone()
                if existing:
                    existing_alert_id = existing.get('id')
            
            if existing_alert_id:
                # Update existing alert instead of creating a new one
                cur.execute(
                    """
                    UPDATE qd_position_alerts 
                    SET alert_type = ?, threshold = ?, notification_config = ?, 
                        is_active = ?, is_triggered = 0, repeat_interval = ?, notes = ?, updated_at = NOW()
                    WHERE id = ?
                    """,
                    (alert_type, threshold, notification_config_json,
                     1 if is_active else 0, repeat_interval, notes, existing_alert_id)
                )
                alert_id = existing_alert_id
            else:
                # Create new alert
                cur.execute(
                    """
                    INSERT INTO qd_position_alerts 
                    (user_id, position_id, market, symbol, alert_type, threshold, notification_config, 
                     is_active, repeat_interval, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NOW(), NOW())
                    """,
                    (user_id, position_id, market, symbol, alert_type, threshold, notification_config_json,
                     1 if is_active else 0, repeat_interval, notes)
                )
                alert_id = cur.lastrowid
            
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': {'id': alert_id}})
    except Exception as e:
        logger.error(f"add_alert failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/alerts/<int:alert_id>', methods=['PUT'])
@login_required
def update_alert(alert_id):
    """Update an existing alert for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        
        updates = []
        params = []
        
        if 'alert_type' in data:
            updates.append('alert_type = ?')
            params.append((data.get('alert_type') or 'price_above').strip())
        
        if 'threshold' in data:
            updates.append('threshold = ?')
            params.append(float(data.get('threshold') or 0))
        
        if 'notification_config' in data:
            notification_config = data.get('notification_config') or {}
            updates.append('notification_config = ?')
            params.append(json.dumps(notification_config if isinstance(notification_config, dict) else {}, ensure_ascii=False))
        
        if 'is_active' in data:
            updates.append('is_active = ?')
            params.append(1 if data.get('is_active') else 0)
            # Reset triggered state when re-activating
            if data.get('is_active'):
                updates.append('is_triggered = ?')
                params.append(0)
        
        if 'repeat_interval' in data:
            updates.append('repeat_interval = ?')
            params.append(int(data.get('repeat_interval') or 0))
        
        if 'notes' in data:
            updates.append('notes = ?')
            params.append((data.get('notes') or '').strip())
        
        if not updates:
            return jsonify({'code': 0, 'msg': 'No fields to update', 'data': None}), 400
        
        updates.append('updated_at = NOW()')
        params.append(alert_id)
        params.append(user_id)
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"UPDATE qd_position_alerts SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
                params
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"update_alert failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/alerts/<int:alert_id>', methods=['DELETE'])
@login_required
def delete_alert(alert_id):
    """Delete an alert for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM qd_position_alerts WHERE id = ? AND user_id = ?",
                (alert_id, user_id)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"delete_alert failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


# ==================== Groups ====================

@portfolio_blp.route('/groups', methods=['GET'])
@login_required
def get_groups():
    """Get list of all groups with position counts for the current user."""
    try:
        user_id = g.user_id
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT group_name, COUNT(*) as count
                FROM qd_manual_positions
                WHERE user_id = ? AND group_name != ''
                GROUP BY group_name
                ORDER BY group_name
                """,
                (user_id,)
            )
            rows = cur.fetchall() or []
            
            # Also get count of ungrouped
            cur.execute(
                "SELECT COUNT(*) as count FROM qd_manual_positions WHERE user_id = ? AND (group_name IS NULL OR group_name = '')",
                (user_id,)
            )
            ungrouped = cur.fetchone()
            cur.close()

        groups = []
        for row in rows:
            groups.append({
                'name': row.get('group_name'),
                'count': row.get('count') or 0
            })
        
        # Add ungrouped count
        ungrouped_count = (ungrouped.get('count') or 0) if ungrouped else 0
        
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {
                'groups': groups,
                'ungrouped_count': ungrouped_count
            }
        })
    except Exception as e:
        logger.error(f"get_groups failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@portfolio_blp.route('/groups/rename', methods=['POST'])
@login_required
def rename_group():
    """Rename a group for the current user."""
    try:
        user_id = g.user_id
        data = request.get_json() or {}
        old_name = (data.get('old_name') or '').strip()
        new_name = (data.get('new_name') or '').strip()
        
        if not old_name:
            return jsonify({'code': 0, 'msg': 'old_name is required', 'data': None}), 400
        
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                "UPDATE qd_manual_positions SET group_name = ?, updated_at = NOW() WHERE user_id = ? AND group_name = ?",
                (new_name, user_id, old_name)
            )
            db.commit()
            cur.close()

        return jsonify({'code': 1, 'msg': 'success', 'data': None})
    except Exception as e:
        logger.error(f"rename_group failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500

# openapi-compat: legacy import name
portfolio_bp = portfolio_blp
