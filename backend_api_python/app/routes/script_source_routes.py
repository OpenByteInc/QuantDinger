"""Script source library API routes."""

from flask import g, jsonify, request

from app.routes.strategy_blueprint import strategy_blp
from app.services.script_source import get_script_source_service
from app.utils.auth import login_required
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _source_payload() -> dict:
    payload = request.get_json(silent=True) or {}
    return payload if isinstance(payload, dict) else {}


@strategy_blp.route("/strategies/script-sources", methods=["GET"])
@login_required
def list_script_sources():
    try:
        items = get_script_source_service().list_sources(g.user_id)
        return jsonify({"code": 1, "msg": "success", "data": {"items": items, "sources": items}})
    except Exception as exc:
        logger.error("list_script_sources failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": {"items": []}}), 500


@strategy_blp.route("/strategies/script-sources/detail", methods=["GET"])
@login_required
def get_script_source_detail():
    try:
        source_id = int(request.args.get("id") or request.args.get("sourceId") or 0)
        if not source_id:
            return jsonify({"code": 0, "msg": "source id is required", "data": None}), 400
        item = get_script_source_service().get_source(source_id, user_id=g.user_id)
        if not item:
            return jsonify({"code": 0, "msg": "script source not found", "data": None}), 404
        return jsonify({"code": 1, "msg": "success", "data": item})
    except Exception as exc:
        logger.error("get_script_source_detail failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@strategy_blp.route("/strategies/script-sources/create", methods=["POST"])
@login_required
def create_script_source():
    try:
        payload = _source_payload()
        payload["user_id"] = g.user_id
        if not str(payload.get("code") or payload.get("strategy_code") or "").strip():
            return jsonify({"code": 0, "msg": "script code is required", "data": None}), 400
        source_id = get_script_source_service().create_source(payload)
        item = get_script_source_service().get_source(source_id, user_id=g.user_id)
        return jsonify({"code": 1, "msg": "success", "data": item})
    except Exception as exc:
        logger.error("create_script_source failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@strategy_blp.route("/strategies/script-sources/update", methods=["PUT", "POST"])
@login_required
def update_script_source():
    try:
        source_id = int(request.args.get("id") or request.args.get("sourceId") or 0)
        payload = _source_payload()
        source_id = int(payload.get("id") or payload.get("sourceId") or source_id or 0)
        if not source_id:
            return jsonify({"code": 0, "msg": "source id is required", "data": None}), 400
        ok = get_script_source_service().update_source(source_id, g.user_id, payload)
        if not ok:
            return jsonify({"code": 0, "msg": "script source not found", "data": None}), 404
        item = get_script_source_service().get_source(source_id, user_id=g.user_id)
        return jsonify({"code": 1, "msg": "success", "data": item})
    except Exception as exc:
        logger.error("update_script_source failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@strategy_blp.route("/strategies/script-sources/delete", methods=["DELETE", "POST"])
@login_required
def delete_script_source():
    try:
        payload = _source_payload()
        source_id = int(payload.get("id") or payload.get("sourceId") or request.args.get("id") or 0)
        if not source_id:
            return jsonify({"code": 0, "msg": "source id is required", "data": None}), 400
        ok = get_script_source_service().delete_source(source_id, g.user_id)
        if not ok:
            return jsonify({"code": 0, "msg": "script source not found", "data": None}), 404
        return jsonify({"code": 1, "msg": "success", "data": {"id": source_id}})
    except Exception as exc:
        logger.error("delete_script_source failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500


@strategy_blp.route("/strategies/script-sources/publish", methods=["POST"])
@login_required
def publish_script_source():
    try:
        payload = _source_payload()
        source_id = int(payload.get("sourceId") or payload.get("source_id") or payload.get("id") or 0)
        if not source_id:
            return jsonify({"code": 0, "msg": "source id is required", "data": None}), 400
        source = get_script_source_service().get_source(source_id, user_id=g.user_id)
        if not source:
            return jsonify({"code": 0, "msg": "script source not found", "data": None}), 404

        from app.routes.strategy import _validate_strategy_code_internal
        validation = _validate_strategy_code_internal(source.get("code") or "")
        if not validation.get("success"):
            return jsonify({"code": 0, "msg": validation.get("message") or "Code verification failed", "data": validation}), 400

        user_role = getattr(g, "user_role", "user")
        is_admin = user_role == "admin"
        from app.services.community_service import get_community_service
        ok, msg, data = get_community_service().publish_script_template_from_strategy(
            user_id=g.user_id,
            strategy_id=0,
            code=source.get("code") or "",
            name=(payload.get("name") or source.get("name") or "").strip(),
            description=(payload.get("description") or source.get("description") or "").strip(),
            pricing_type=(payload.get("pricingType") or payload.get("pricing_type") or "free").strip() or "free",
            price=payload.get("price") or 0,
            is_admin=is_admin,
            existing_indicator_id=int(payload.get("indicatorId") or payload.get("indicator_id") or 0),
        )
        if data is not None:
            data["source_id"] = source_id
        if not ok:
            return jsonify({"code": 0, "msg": msg, "data": data}), 400
        return jsonify({"code": 1, "msg": "success", "data": data})
    except Exception as exc:
        logger.error("publish_script_source failed: %s", exc)
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 500
