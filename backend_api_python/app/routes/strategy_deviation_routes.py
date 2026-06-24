"""Dry-run deviation report routes."""
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


@strategy_blp.route('/strategies/dry-run-deviation', methods=['GET'])
@login_required
def get_dry_run_deviation():
    """Quantify how far live fills drifted from backtest signal closes."""
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
