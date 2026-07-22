"""Strategy review report routes."""
import traceback
from typing import Any
from uuid import uuid4

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.routes.strategy_services import get_strategy_service
from app.services.billing_service import get_billing_service
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)


def _consume_ai_review_credits(
    user_id: int,
    strategy_id: int,
    *,
    include_ai: bool,
) -> tuple[Any, dict[str, Any]]:
    """Charge an AI-assisted review; rule-only reviews remain free."""
    billing = get_billing_service()
    enabled = bool(billing.is_billing_enabled())
    cost = max(0, int(billing.get_feature_cost("ai_review") or 0))
    reference_id = f"strategy_review:{strategy_id}:{uuid4().hex}"
    charge = {
        "enabled": enabled,
        "requested": bool(include_ai),
        "cost": cost,
        "charged": 0,
        "remaining": float(billing.get_user_credits(user_id)),
        "referenceId": reference_id,
    }
    if not include_ai or not enabled or cost <= 0:
        return billing, charge

    success, message = billing.check_and_consume(
        user_id=user_id,
        feature="ai_review",
        reference_id=reference_id,
    )
    if not success:
        current = float(billing.get_user_credits(user_id))
        if str(message).startswith("insufficient_credits:"):
            return billing, {
                **charge,
                "error": "insufficient_credits",
                "current": current,
                "required": cost,
                "shortage": max(0, cost - current),
            }
        return billing, {**charge, "error": "billing_error", "message": str(message)}

    charge["charged"] = cost
    charge["remaining"] = float(billing.get_user_credits(user_id))
    return billing, charge


def _refund_ai_review_credits(
    billing: Any,
    user_id: int,
    charge: dict[str, Any],
    *,
    reason: str,
) -> None:
    cost = int(charge.get("charged") or 0)
    if not billing or cost <= 0:
        return
    refunded, message = billing.add_credits(
        user_id=user_id,
        amount=cost,
        action="refund",
        remark=f"Automatic refund: AI strategy review {reason}",
        reference_id=str(charge.get("referenceId") or ""),
    )
    if not refunded:
        logger.error("AI strategy review credit refund failed for user %s: %s", user_id, message)
        return
    charge["refunded"] = cost
    charge["charged"] = 0
    charge["remaining"] = float(billing.get_user_credits(user_id))


@strategy_blp.route('/strategies/review-report', methods=['POST'])
@login_required
def get_strategy_review_report():
    """Build an AI-assisted strategy review report from factual trade records."""
    billing = None
    charge: dict[str, Any] = {}
    user_id = 0
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

        billing, charge = _consume_ai_review_credits(
            user_id,
            strategy_id,
            include_ai=include_ai,
        )
        if charge.get("error") == "insufficient_credits":
            return jsonify({
                "code": 0,
                "msg": "insufficient_credits",
                "data": {
                    "error_type": "INSUFFICIENT_CREDITS",
                    "feature": "ai_review",
                    "current": charge["current"],
                    "required": charge["required"],
                    "shortage": charge["shortage"],
                },
            }), 402
        if charge.get("error"):
            return jsonify({
                "code": 0,
                "msg": "billing_error",
                "data": {"feature": "ai_review", "detail": charge.get("message")},
            }), 503

        from app.services.strategy_review import StrategyReviewService
        report = StrategyReviewService().build_report(
            strategy_id=int(strategy_id),
            user_id=user_id,
            lookback_days=lookback_days,
            include_ai=include_ai,
            language=language,
        )
        ai_status = str(((report.get("ai") or {}).get("status") or "skipped")).lower()
        if include_ai and ai_status != "ok":
            _refund_ai_review_credits(
                billing,
                user_id,
                charge,
                reason=f"returned {ai_status}",
            )
        report["billing"] = charge
        return jsonify({'code': 1, 'msg': 'success', 'data': report})
    except Exception as e:
        _refund_ai_review_credits(
            billing,
            user_id,
            charge,
            reason="failed",
        )
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
