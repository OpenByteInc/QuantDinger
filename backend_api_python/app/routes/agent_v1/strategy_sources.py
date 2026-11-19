"""Strategy API V2 source workspace for external agents."""
from __future__ import annotations

from typing import Any

from app.services.script_source import get_script_source_service
from app.services.strategy_authoring import get_strategy_authoring_contract
from app.services.strategy_v2 import canonical_source_metadata, inspect_strategy_v2
from app.utils.agent_auth import SCOPE_R, SCOPE_W, agent_required, current_user_id
from app.utils.logger import get_logger
from flask import request

from . import agent_v1_bp
from ._helpers import clip_int, envelope, error, get_json_or_400
from ._security import assert_strategy_code_size

logger = get_logger(__name__)

_LIST_FIELDS = (
    "id",
    "name",
    "description",
    "asset_type",
    "template_key",
    "param_schema",
    "visibility",
    "status",
    "metadata",
    "created_at",
    "updated_at",
)


@agent_v1_bp.route("/strategy-sources/authoring-contract", methods=["GET"])
@agent_required(SCOPE_R)
def strategy_authoring_contract():
    """Return the canonical Strategy API V2 contract and starter source."""
    return envelope(get_strategy_authoring_contract())


def _is_hidden_source(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return bool(item.get("code_hidden") or metadata.get("code_hidden"))


def _project_source(item: dict | None, *, include_code: bool) -> dict | None:
    if not isinstance(item, dict):
        return None
    out = {key: item.get(key) for key in _LIST_FIELDS if key in item}
    if include_code:
        out["code"] = "" if _is_hidden_source(item) else str(item.get("code") or "")
        if _is_hidden_source(item):
            out["code_hidden"] = True
    return out


def _compile_payload(code: str, metadata: Any = None) -> tuple[dict, dict]:
    source = str(code or "").strip()
    if not source:
        raise ValueError("strategyV2.codeRequired")
    assert_strategy_code_size(source)
    base_metadata = metadata if isinstance(metadata, dict) else {}
    canonical_metadata, manifest = canonical_source_metadata(source, base_metadata)
    return canonical_metadata, manifest


@agent_v1_bp.route("/strategy-sources/templates", methods=["GET"])
@agent_required(SCOPE_R)
def list_strategy_templates():
    """List system Strategy API V2 templates with starter code."""
    limit = clip_int(request.args.get("limit"), default=20, lo=1, hi=100)
    try:
        rows = get_script_source_service().list_templates() or []
        return envelope(rows[:limit])
    except Exception as exc:
        logger.error("agent_v1 strategy template list failed: %s", exc, exc_info=True)
        return error(500, "list_strategy_templates failed", details=str(exc), http=500)


@agent_v1_bp.route("/strategy-sources/compile", methods=["POST"])
@agent_required(SCOPE_W)
def compile_strategy_source():
    """Compile Strategy API V2 source without persisting it."""
    body, err = get_json_or_400()
    if err:
        return err
    code = str(body.get("code") or "").strip()
    try:
        source_id = int(body.get("source_id") or body.get("sourceId") or 0)
    except (TypeError, ValueError):
        return error(400, "source_id must be an integer")
    if not code and source_id:
        source = get_script_source_service().get_source(source_id, user_id=current_user_id())
        if not source:
            return error(404, "Strategy source not found", http=404)
        if _is_hidden_source(source):
            return error(403, "Strategy source code is hidden", http=403)
        code = str(source.get("code") or "").strip()
    try:
        assert_strategy_code_size(code)
        manifest = inspect_strategy_v2(code).manifest.metadata()
    except ValueError as exc:
        return error(400, str(exc))
    except Exception as exc:
        logger.error("agent_v1 strategy compile failed: %s", exc, exc_info=True)
        return error(500, "strategyV2.compileFailed", http=500)
    return envelope({"manifest": manifest}, message="compiled")


@agent_v1_bp.route("/strategy-sources", methods=["GET"])
@agent_required(SCOPE_R)
def list_strategy_sources():
    """List tenant-owned Strategy API V2 sources without code bodies."""
    limit = clip_int(request.args.get("limit"), default=50, lo=1, hi=200)
    try:
        rows = get_script_source_service().list_sources(current_user_id()) or []
        return envelope([_project_source(row, include_code=False) for row in rows[:limit]])
    except Exception as exc:
        logger.error("agent_v1 strategy source list failed: %s", exc, exc_info=True)
        return error(500, "list_strategy_sources failed", details=str(exc), http=500)


@agent_v1_bp.route("/strategy-sources/<int:source_id>", methods=["GET"])
@agent_required(SCOPE_R)
def get_strategy_source(source_id: int):
    """Get one tenant-owned Strategy API V2 source."""
    source = get_script_source_service().get_source(source_id, user_id=current_user_id())
    if not source:
        return error(404, "Strategy source not found", http=404)
    return envelope(_project_source(source, include_code=True))


@agent_v1_bp.route("/strategy-sources", methods=["POST"])
@agent_required(SCOPE_W)
def create_strategy_source():
    """Compile and save a private Strategy API V2 source."""
    body, err = get_json_or_400()
    if err:
        return err
    name = str(body.get("name") or "").strip()
    if not name:
        return error(400, "Strategy source name is required")
    try:
        metadata, manifest = _compile_payload(body.get("code"), body.get("metadata"))
        payload = dict(body)
        payload.update({
            "user_id": current_user_id(),
            "name": name,
            "metadata": metadata,
            "asset_type": "portfolio_strategy" if manifest.get("strategyType") == "portfolio" else "script",
            "visibility": "private",
            "status": "draft",
        })
        source_id = get_script_source_service().create_source(payload)
        source = get_script_source_service().get_source(source_id, user_id=current_user_id())
    except ValueError as exc:
        return error(400, str(exc))
    except Exception as exc:
        logger.error("agent_v1 strategy source create failed: %s", exc, exc_info=True)
        return error(500, "create_strategy_source failed", details=str(exc), http=500)
    return envelope(_project_source(source, include_code=True), message="created")


@agent_v1_bp.route("/strategy-sources/<int:source_id>", methods=["PATCH"])
@agent_required(SCOPE_W)
def update_strategy_source(source_id: int):
    """Compile and update one tenant-owned Strategy API V2 source."""
    body, err = get_json_or_400()
    if err:
        return err
    current = get_script_source_service().get_source(source_id, user_id=current_user_id())
    if not current:
        return error(404, "Strategy source not found", http=404)
    if _is_hidden_source(current):
        return error(403, "Hidden marketplace source cannot be edited", http=403)
    code = body.get("code") if body.get("code") is not None else current.get("code")
    try:
        metadata, manifest = _compile_payload(code, body.get("metadata", current.get("metadata")))
        payload = dict(body)
        payload.update({
            "code": code,
            "metadata": metadata,
            "asset_type": "portfolio_strategy" if manifest.get("strategyType") == "portfolio" else "script",
        })
        ok = get_script_source_service().update_source(source_id, current_user_id(), payload)
        if not ok:
            return error(404, "Strategy source not found", http=404)
        source = get_script_source_service().get_source(source_id, user_id=current_user_id())
    except ValueError as exc:
        return error(400, str(exc))
    except Exception as exc:
        logger.error("agent_v1 strategy source update failed: %s", exc, exc_info=True)
        return error(500, "update_strategy_source failed", details=str(exc), http=500)
    return envelope(_project_source(source, include_code=True), message="updated")


@agent_v1_bp.route("/strategy-sources/<int:source_id>/versions", methods=["GET"])
@agent_required(SCOPE_R)
def list_strategy_source_versions(source_id: int):
    """List version snapshots for a tenant-owned strategy source."""
    ok, rows = get_script_source_service().list_versions(source_id, current_user_id())
    if not ok:
        return error(404, "Strategy source not found", http=404)
    return envelope(rows)


@agent_v1_bp.route(
    "/strategy-sources/<int:source_id>/versions/<int:version_id>/restore",
    methods=["POST"],
)
@agent_required(SCOPE_W)
def restore_strategy_source_version(source_id: int, version_id: int):
    """Restore a source snapshot only after explicit confirmation."""
    body, err = get_json_or_400()
    if err:
        return err
    if body.get("confirm") is not True:
        return error(400, "confirm=true is required to restore a strategy source version")
    version = get_script_source_service().get_version(version_id, current_user_id())
    if not version or int(version.get("source_id") or 0) != int(source_id):
        return error(404, "Strategy source version not found", http=404)
    source = get_script_source_service().get_source(source_id, user_id=current_user_id())
    if _is_hidden_source(source):
        return error(403, "Hidden marketplace source cannot be restored", http=403)
    restored = get_script_source_service().restore_version(version_id, current_user_id())
    if not restored:
        return error(500, "restore_strategy_source_version failed", http=500)
    return envelope(_project_source(restored, include_code=True), message="restored")
