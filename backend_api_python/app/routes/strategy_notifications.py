"""Strategy notification routes."""
from datetime import timezone as dt_tz
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.utils.auth import login_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _current_user_strategy_ids(user_id: int) -> list[int]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_strategies_trading WHERE user_id = ?", (user_id,))
        rows = cur.fetchall() or []
        cur.close()
    return [r.get('id') for r in rows if r.get('id')]


def _user_notification_scope(user_id: int, strategy_id: int | None = None) -> tuple[list[str], list]:
    user_strategy_ids = _current_user_strategy_ids(user_id)
    where: list[str] = []
    args: list = []

    if strategy_id:
        if strategy_id in user_strategy_ids:
            where.append("strategy_id = ?")
            args.append(int(strategy_id))
        else:
            where.append("1 = 0")
        return where, args

    if user_strategy_ids:
        placeholders = ",".join(["?"] * len(user_strategy_ids))
        where.append(f"(strategy_id IN ({placeholders}) OR (strategy_id IS NULL AND user_id = ?))")
        args.extend(user_strategy_ids)
        args.append(user_id)
    else:
        where.append("strategy_id IS NULL AND user_id = ?")
        args.append(user_id)
    return where, args


@strategy_blp.route('/strategies/notifications', methods=['GET'])
@login_required
def get_strategy_notifications():
    """Strategy signal notifications for the current user."""
    try:
        user_id = g.user_id
        strategy_id = request.args.get('id', type=int)
        limit = request.args.get('limit', type=int) or 50
        limit = max(1, min(200, int(limit)))
        since_id = request.args.get('since_id', type=int) or 0

        where, args = _user_notification_scope(user_id, strategy_id)
        if since_id:
            where.append("id > ?")
            args.append(int(since_id))
        where_sql = "WHERE " + " AND ".join(where)

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

        processed_rows = []
        for row in rows:
            item = dict(row)
            created_at = item.get('created_at')
            if created_at:
                if hasattr(created_at, 'timestamp'):
                    if getattr(created_at, 'tzinfo', None) is None:
                        created_at = created_at.replace(tzinfo=dt_tz.utc)
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
    """Get unread notification count for the current user."""
    try:
        user_id = g.user_id
        where, args = _user_notification_scope(user_id)
        where.insert(0, "is_read = 0")
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
