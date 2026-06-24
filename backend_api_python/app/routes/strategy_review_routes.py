"""Strategy review report routes."""
import traceback

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


@strategy_blp.route('/strategies/review-report', methods=['POST'])
@login_required
def get_strategy_review_report():
    """Build an AI-assisted strategy review report from factual trade records."""
    try:
        user_id = int(g.user_id)
        data = request.get_json(silent=True) or {}

        try:
            strategy_id = int(request.args.get('id') or data.get('id') or data.get('strategy_id') or 0)
        except Exception:
            strategy_id = 0
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400

        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        try:
            lookback_days = int(data.get('lookback_days') or request.args.get('lookback_days') or 30)
        except Exception:
            lookback_days = 30

        include_ai_raw = data.get('include_ai')
        if include_ai_raw is None:
            include_ai_raw = request.args.get('include_ai', '1')
        include_ai = str(include_ai_raw).strip().lower() not in ('0', 'false', 'no', 'off')
        language = str(data.get('language') or request.args.get('lang') or request.headers.get('Accept-Language') or 'zh-CN')

        from app.services.strategy_review import StrategyReviewService
        report = StrategyReviewService().build_report(
            strategy_id=int(strategy_id),
            user_id=user_id,
            lookback_days=lookback_days,
            include_ai=include_ai,
            language=language,
        )
        return jsonify({'code': 1, 'msg': 'success', 'data': report})
    except Exception as e:
        logger.error(f"get_strategy_review_report failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500


@strategy_blp.route('/strategies/review-report/history', methods=['GET'])
@login_required
def get_strategy_review_report_history():
    """List or load saved AI strategy review reports."""
    try:
        user_id = int(g.user_id)
        try:
            strategy_id = int(request.args.get('id') or request.args.get('strategy_id') or 0)
        except Exception:
            strategy_id = 0
        if not strategy_id:
            return jsonify({'code': 0, 'msg': 'Missing strategy id parameter', 'data': None}), 400

        st = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
        if not st:
            return jsonify({'code': 0, 'msg': 'Strategy not found', 'data': None}), 404

        from app.services.strategy_review import StrategyReviewService
        service = StrategyReviewService()
        try:
            report_id = int(request.args.get('report_id') or 0)
        except Exception:
            report_id = 0

        if report_id:
            report = service.get_history_report(
                report_id=report_id,
                strategy_id=strategy_id,
                user_id=user_id,
            )
            if not report:
                return jsonify({'code': 0, 'msg': 'Review report not found', 'data': None}), 404
            return jsonify({'code': 1, 'msg': 'success', 'data': report})

        try:
            limit = int(request.args.get('limit') or 20)
        except Exception:
            limit = 20
        history = service.list_history(strategy_id=strategy_id, user_id=user_id, limit=limit)
        return jsonify({'code': 1, 'msg': 'success', 'data': history})
    except Exception as e:
        logger.error(f"get_strategy_review_report_history failed: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'code': 0, 'msg': str(e), 'data': None}), 500
