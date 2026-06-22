"""Indicator workspace endpoints for external AI agents.

Read (R): contract, list, get, validate
Write (W): save / update private indicators in ``qd_indicator_codes``
"""
from __future__ import annotations

from app.services.indicator_workspace import (
    get_indicator_authoring_contract,
    get_user_indicator,
    link_indicator_config,
    list_user_indicators,
    save_user_indicator,
    validate_indicator_code,
)
from app.utils.agent_auth import SCOPE_R, SCOPE_W, agent_required, current_user_id
from app.utils.logger import get_logger
from flask import request

from ._security import assert_indicator_code_size
from . import agent_v1_bp
from ._helpers import clip_int, envelope, error, get_json_or_400

logger = get_logger(__name__)


@agent_v1_bp.route("/indicators/authoring-contract", methods=["GET"])
@agent_required(SCOPE_R)
def indicator_authoring_contract():
    """Return starter template + required I/O contract for AI code generation."""
    return envelope(get_indicator_authoring_contract())


@agent_v1_bp.route("/indicators", methods=["GET"])
@agent_required(SCOPE_R)
def list_indicators():
    """List tenant indicators (compact; no code body)."""
    limit = clip_int(request.args.get("limit"), default=50, lo=1, hi=200)
    rows = list_user_indicators(current_user_id(), limit=limit)
    return envelope(rows)


@agent_v1_bp.route("/indicators/<int:indicator_id>", methods=["GET"])
@agent_required(SCOPE_R)
def get_indicator(indicator_id: int):
    """Fetch one indicator including ``code``."""
    row = get_user_indicator(current_user_id(), indicator_id)
    if not row:
        return error(404, "Indicator not found", http=404)
    return envelope(row)


@agent_v1_bp.route("/indicators/validate", methods=["POST"])
@agent_required(SCOPE_R)
def validate_indicator():
    """Sandbox-run indicator code without persisting."""
    body, err = get_json_or_400()
    if err:
        return err
    code = (body.get("code") or body.get("indicator_code") or "").strip()
    if not code:
        return error(400, "code is required")
    try:
        assert_indicator_code_size(code)
    except ValueError as ve:
        return error(400, str(ve))
    params = body.get("indicator_params") or body.get("params") or {}
    result = validate_indicator_code(code, params)
    return envelope(result, message="validated" if result.get("success") else "validation_failed")


@agent_v1_bp.route("/indicators", methods=["POST"])
@agent_required(SCOPE_W)
def save_indicator():
    """Save indicator into ``qd_indicator_codes`` (private; not community publish)."""
    body, err = get_json_or_400()
    if err:
        return err
    code = (body.get("code") or body.get("indicator_code") or "").strip()
    if not code:
        return error(400, "code is required")
    try:
        assert_indicator_code_size(code)
    except ValueError as ve:
        return error(400, str(ve))

    validate_first = body.get("validate", True)
    if validate_first is not False and str(validate_first).lower() not in ("0", "false", "no"):
        validation = validate_indicator_code(
            code,
            body.get("indicator_params") or body.get("params") or {},
        )
        if not validation.get("success"):
            return error(
                400,
                validation.get("msg") or "Indicator validation failed",
                details=validation,
                http=400,
            )

    try:
        indicator_id = int(body.get("id") or body.get("indicator_id") or 0)
    except (TypeError, ValueError):
        indicator_id = 0

    try:
        new_id = save_user_indicator(
            user_id=current_user_id(),
            code=code,
            name=body.get("name") or body.get("indicator_name"),
            description=body.get("description") or body.get("indicator_description"),
            indicator_id=indicator_id,
        )
    except ValueError as ve:
        return error(400, str(ve))
    except Exception as exc:
        logger.error(f"agent_v1/indicators save failed: {exc}", exc_info=True)
        return error(500, "save_indicator failed", details=str(exc), http=500)

    row = get_user_indicator(current_user_id(), new_id)
    return envelope(
        {"indicator_id": new_id, "indicator": row},
        message="saved",
    )


@agent_v1_bp.route("/indicators/link-config", methods=["POST"])
@agent_required(SCOPE_W)
def link_indicator():
    """Normalize ``indicator_config`` dict: auto-save embedded code + set indicator_id."""
    body, err = get_json_or_400()
    if err:
        return err
    ic = body.get("indicator_config") or body
    linked = link_indicator_config(current_user_id(), ic, auto_save=True)
    return envelope(linked, message="linked")
