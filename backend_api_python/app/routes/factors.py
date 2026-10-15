"""Factor catalog and research APIs."""

from flask import jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.factors import (
    FactorError,
    TalibFactorError,
    get_factor_metadata,
    is_talib_available,
    list_factor_catalog,
)
from app.services.factors.research import information_coefficient, quantile_returns, winsorize_zscore
from app.services.fundamental_data import get_fundamental_data_service
from app.utils.auth import login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)
factors_blp = Blueprint("factors", __name__)


@factors_blp.route("", methods=["GET"])
@login_required
def factor_catalog():
    try:
        factor_type = str(request.args.get("type") or request.args.get("factor_type") or "").strip()
        category = str(request.args.get("category") or "").strip()
        provider = str(request.args.get("provider") or "").strip().lower()
        data = list_factor_catalog(
            category=category,
            factor_type=factor_type,
        )
        if provider in ("ta-lib", "talib"):
            data = [item for item in data if item.get("provider") == "ta-lib"]
        elif provider:
            data = [item for item in data if item.get("provider") != "ta-lib"]
        talib_count = sum(1 for item in data if item.get("provider") == "ta-lib")
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": data,
            "meta": {
                "builtInCount": len(data) - talib_count,
                "talibCount": talib_count,
                "talibAvailable": is_talib_available(),
            },
        })
    except TalibFactorError as exc:
        return jsonify({"code": 0, "msg": exc.code, "data": None}), 503
    except Exception:
        logger.exception("factor catalog failed")
        return jsonify({"code": 0, "msg": "factor.listFailed", "data": None}), 500


@factors_blp.route("/<string:factor_id>", methods=["GET"])
@login_required
def factor_detail(factor_id: str):
    try:
        return jsonify({"code": 1, "msg": "success", "data": get_factor_metadata(factor_id)})
    except (FactorError, TalibFactorError) as exc:
        return jsonify({"code": 0, "msg": exc.code, "data": None}), 404


@factors_blp.route("/research", methods=["POST"])
@login_required
def factor_research():
    try:
        payload = request.get_json(silent=True) or {}
        scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
        returns = payload.get("forwardReturns") or payload.get("forward_returns") or {}
        if not isinstance(returns, dict):
            returns = {}
        normalized = winsorize_zscore(scores)
        data = {
            "normalizedScores": normalized,
            "statistics": information_coefficient(normalized, returns),
            "quantileReturns": quantile_returns(
                normalized,
                returns,
                quantiles=int(payload.get("quantiles") or 5),
            ),
        }
        return jsonify({"code": 1, "msg": "success", "data": data})
    except Exception:
        logger.exception("factor research failed")
        return jsonify({"code": 0, "msg": "factor.researchFailed", "data": None}), 500


@factors_blp.route("/fundamentals/coverage", methods=["GET"])
@login_required
def fundamental_coverage():
    try:
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": get_fundamental_data_service().coverage(),
        })
    except Exception:
        logger.exception("fundamental coverage failed")
        return jsonify({"code": 0, "msg": "factor.fundamentalCoverageFailed", "data": None}), 500


@factors_blp.route("/fundamentals/sync", methods=["POST"])
@login_required
def fundamental_sync():
    try:
        payload = request.get_json(silent=True) or {}
        service = get_fundamental_data_service()
        sync_method = service.sync_history if bool(payload.get("history")) else service.sync_current
        data = sync_method(
            market=str(payload.get("market") or ""),
            symbol=str(payload.get("symbol") or ""),
        )
        return jsonify({"code": 1, "msg": "success", "data": data})
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception:
        logger.exception("fundamental sync failed")
        return jsonify({"code": 0, "msg": "factor.fundamentalSyncFailed", "data": None}), 500
