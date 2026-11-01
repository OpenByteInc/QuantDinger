"""Factor catalog and research APIs."""

from flask import g, jsonify, request

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
from app.services.custom_factors import (
    CustomFactorError,
    analyze_custom_factor,
    archive_custom_factor,
    get_custom_factor,
    list_custom_factors,
    save_custom_factor,
)
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
            include_feature_families=True,
        )
        custom = list_custom_factors(int(g.user_id))
        if category:
            custom = [item for item in custom if item.get("category") == category]
        if factor_type:
            custom = [item for item in custom if item.get("factor_type") == factor_type]
        data.extend(custom)
        if provider in ("ta-lib", "talib"):
            data = [item for item in data if item.get("provider") == "ta-lib"]
        elif provider in ("microsoft-qlib", "qlib"):
            data = [item for item in data if item.get("provider") == "microsoft-qlib"]
        elif provider:
            data = [item for item in data if not item.get("provider")]
        talib_count = sum(1 for item in data if item.get("provider") == "ta-lib")
        qlib_count = sum(1 for item in data if item.get("provider") == "microsoft-qlib")
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": data,
            "meta": {
                "builtInCount": len(data) - talib_count - qlib_count,
                "talibCount": talib_count,
                "qlibCount": qlib_count,
                "customCount": len(custom),
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
        if factor_id.lower().startswith("custom:"):
            return jsonify({"code": 1, "msg": "success", "data": get_custom_factor(int(g.user_id), factor_id)})
        return jsonify({"code": 1, "msg": "success", "data": get_factor_metadata(factor_id)})
    except (CustomFactorError, FactorError, TalibFactorError) as exc:
        return jsonify({"code": 0, "msg": exc.code, "data": None}), 404


@factors_blp.route("/custom/validate", methods=["POST"])
@login_required
def validate_custom_factor():
    try:
        payload = request.get_json(silent=True) or {}
        params = payload.get("defaultParams") or {}
        analysis = analyze_custom_factor(str(payload.get("expression") or ""), params)
        return jsonify({
            "code": 1,
            "msg": "success",
            "data": {
                "requiredFields": list(analysis.required_fields),
                "dependencies": list(analysis.dependencies),
                "warmupBars": analysis.warmup_bars,
            },
        })
    except CustomFactorError as exc:
        return jsonify({"code": 0, "msg": exc.code, "data": None}), 400


@factors_blp.route("/custom", methods=["POST"])
@login_required
def create_custom_factor():
    try:
        data = save_custom_factor(int(g.user_id), request.get_json(silent=True) or {})
        return jsonify({"code": 1, "msg": "success", "data": data}), 201
    except CustomFactorError as exc:
        return jsonify({"code": 0, "msg": exc.code, "data": None}), 400
    except Exception:
        logger.exception("custom factor creation failed")
        return jsonify({"code": 0, "msg": "factor.customSaveFailed", "data": None}), 500


@factors_blp.route("/custom/<int:factor_id>", methods=["DELETE"])
@login_required
def delete_custom_factor(factor_id: int):
    try:
        archive_custom_factor(int(g.user_id), factor_id)
        return jsonify({"code": 1, "msg": "success", "data": {"id": factor_id}})
    except CustomFactorError as exc:
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
