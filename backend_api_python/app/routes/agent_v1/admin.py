"""Token administration.

Issuance is HUMAN-only — these endpoints require a regular admin JWT, not an
agent token, so an agent can never escalate its own privileges or mint new
tokens for itself or for other tenants.

Endpoints:
  POST   /admin/tokens         issue a new token (admin only; may include C scope)
  GET    /admin/tokens         list tokens (admin only)
  DELETE /admin/tokens/{id}    revoke (admin only)
  GET    /admin/audit          audit log (admin only)

Self-service users manage their own tokens at ``/me/tokens`` (see ``me_tokens.py``).
"""
from __future__ import annotations

from app.services.agent_token_service import (
    TokenIssueError,
    is_saas_mode,
    issue_agent_token,
    list_agent_audit,
    list_agent_tokens,
    revoke_agent_token,
)
from app.utils.auth import admin_required, get_current_user_id, login_required
from flask import request

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400

# Re-export for tests that patch ``admin_routes._is_saas_mode``.
_is_saas_mode = is_saas_mode


@agent_v1_bp.route("/admin/tokens", methods=["POST"])
@login_required
@admin_required
def issue_token():
    """Issue a new agent token for the calling admin's tenant."""
    body, err = get_json_or_400()
    if err:
        return err

    user_id = int(get_current_user_id() or 1)
    try:
        data = issue_agent_token(user_id, body, allow_c_scope=True)
    except TokenIssueError as exc:
        return error(exc.code, exc.message, details=exc.details, http=exc.http)
    return envelope(data, message="issued")


@agent_v1_bp.route("/admin/tokens", methods=["GET"])
@login_required
@admin_required
def list_tokens():
    user_id = int(get_current_user_id() or 1)
    return envelope(list_agent_tokens(user_id))


@agent_v1_bp.route("/admin/tokens/<int:token_id>", methods=["DELETE"])
@login_required
@admin_required
def revoke_token(token_id: int):
    user_id = int(get_current_user_id() or 1)
    if not revoke_agent_token(user_id, token_id):
        return error(404, "Token not found", http=404)
    return envelope({"id": token_id, "status": "revoked"})


@agent_v1_bp.route("/admin/audit", methods=["GET"])
@login_required
@admin_required
def list_audit():
    user_id = int(get_current_user_id() or 1)
    try:
        limit = max(1, min(int(request.args.get("limit") or 100), 500))
    except Exception:
        limit = 100
    return envelope(list_agent_audit(user_id, limit))
