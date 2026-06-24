"""Strategy grid route facade."""
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


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
        sync = request.args.get('sync', '').lower() in ('1', 'true', 'yes')

        if sync:
            try:
                from app.services.grid.poller import sync_strategy_grid_orders

                sync_strategy_grid_orders(int(strategy_id))
            except Exception as sync_err:
                logger.debug("grid-resting sync sid=%s: %s", strategy_id, sync_err)

        from app.services.grid.resting_orders_repo import GridRestingOrderRepository
        from app.utils.trade_close_reason import label_for_reason

        lang = str(request.args.get("lang") or request.headers.get("Accept-Language") or "zh")
        if lang.lower().startswith("en"):
            lang = "en"
        else:
            lang = "zh"

        repo = GridRestingOrderRepository()
        rows = repo.list_for_strategy(strategy_id, status=status, limit=limit or 200)
        out = []
        for o in rows:
            purpose = o.purpose
            out.append({
                'id': o.id,
                'strategy_id': o.strategy_id,
                'symbol': o.symbol,
                'cell_index': o.cell_index,
                'purpose': purpose,
                'purpose_label': label_for_reason(purpose, lang=lang),
                'purpose_label_en': label_for_reason(purpose, lang="en"),
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
