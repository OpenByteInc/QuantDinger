"""Account snapshot and account position routes used by strategy screens."""
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


@strategy_blp.route('/account/snapshot', methods=['GET'])
@login_required
def get_account_snapshot():
    """Live swap/spot positions + open orders for a saved credential."""
    try:
        user_id = g.user_id
        credential_id = request.args.get('credential_id', type=int)
        if not credential_id:
            return jsonify({
                'code': 0,
                'msg': 'Missing credential_id',
                'data': {'swap_positions': [], 'spot_positions': [], 'open_orders': []},
            }), 400

        from app.services.live_trading.account_snapshot import fetch_account_snapshot

        snap = fetch_account_snapshot(user_id=int(user_id), credential_id=int(credential_id))
        msg = "success"
        if snap.get("error"):
            msg = str(snap.get("error") or "")
        elif snap.get("warnings"):
            msg = str(snap["warnings"][0])
        return jsonify({'code': 1, 'msg': msg, 'data': snap})
    except Exception as e:
        logger.error(f"get_account_snapshot failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'code': 0,
            'msg': str(e),
            'data': {'swap_positions': [], 'spot_positions': [], 'open_orders': []},
        }), 500


@strategy_blp.route('/account/positions', methods=['GET'])
@login_required
def get_account_positions():
    """L1 account position mirror for Quick Trade / asset views."""
    try:
        user_id = g.user_id
        credential_id = request.args.get('credential_id', type=int)
        market_type = request.args.get('market_type', type=str)

        from app.services.live_trading.account_positions import list_account_positions

        rows = list_account_positions(
            user_id=int(user_id),
            credential_id=credential_id,
            market_type=market_type,
        )
        return jsonify({
            'code': 1,
            'msg': 'success',
            'data': {'positions': rows, 'items': rows},
        })
    except Exception as e:
        logger.error(f"get_account_positions failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': {'positions': [], 'items': []}}), 500
