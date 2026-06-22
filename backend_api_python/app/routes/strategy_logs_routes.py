"""Strategy runtime log routes."""
from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app.utils.auth import login_required
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

try:
    from psycopg2.errors import UndefinedTable as PgUndefinedTable
except Exception:  # pragma: no cover
    PgUndefinedTable = None  # type: ignore


logger = get_logger(__name__)


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
        # Already ORDER BY id DESC; newest first for the UI log panel.
        return jsonify({'code': 1, 'msg': 'success', 'data': out})
    except Exception as e:
        if PgUndefinedTable is not None and isinstance(e, PgUndefinedTable):
            return jsonify({'code': 1, 'msg': 'success', 'data': []})
        el = str(e).lower()
        if 'qd_strategy_logs' in el and ('does not exist' in el or 'no such table' in el):
            return jsonify({'code': 1, 'msg': 'success', 'data': []})
        logger.error(f"get_strategy_logs failed: {str(e)}")
        return jsonify({'code': 0, 'msg': str(e)}), 500
