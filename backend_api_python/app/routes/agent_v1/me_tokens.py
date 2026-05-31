"""Self-service Agent Token management for the logged-in human user.

Each user can issue, list, and revoke tokens bound to their own ``user_id``.
Unlike ``/admin/tokens``, these routes accept any authenticated JWT (not
admin-only) and never allow the ``C`` (credentials) scope.

Endpoints:
  GET    /me/tokens/policy   issuance policy + risk disclosure
  POST   /me/tokens          issue a token (shown once)
  GET    /me/tokens          list own tokens
  DELETE /me/tokens/{id}     revoke own token
  GET    /me/audit           recent audit entries for own tenant
"""
from __future__ import annotations

from app.services.agent_token_service import (
    TokenIssueError,
    get_token_policy,
    issue_agent_token,
    list_agent_audit,
    list_agent_tokens,
    revoke_agent_token,
)
from app.utils.auth import get_current_user_id, login_required
from flask import request

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400


@agent_v1_bp.route("/me/tokens/policy", methods=["GET"])
@login_required
def my_token_policy():
    return envelope(get_token_policy(for_admin=False))


@agent_v1_bp.route("/me/tokens", methods=["POST"])
@login_required
def issue_my_token():
    body, err = get_json_or_400()
    if err:
        return err
    user_id = int(get_current_user_id() or 0)
    if not user_id:
        return error(401, "Authentication required", http=401)
    try:
        data = issue_agent_token(user_id, body, allow_c_scope=False)
    except TokenIssueError as exc:
        return error(exc.code, exc.message, details=exc.details, http=exc.http)
    return envelope(data, message="issued")


@agent_v1_bp.route("/me/tokens", methods=["GET"])
@login_required
def list_my_tokens():
    user_id = int(get_current_user_id() or 0)
    if not user_id:
        return error(401, "Authentication required", http=401)
    return envelope(list_agent_tokens(user_id))


@agent_v1_bp.route("/me/tokens/<int:token_id>", methods=["DELETE"])
@login_required
def revoke_my_token(token_id: int):
    user_id = int(get_current_user_id() or 0)
    if not user_id:
        return error(401, "Authentication required", http=401)
    if not revoke_agent_token(user_id, token_id):
        return error(404, "Token not found", http=404)
    return envelope({"id": token_id, "status": "revoked"})


@agent_v1_bp.route("/me/audit", methods=["GET"])
@login_required
def list_my_audit():
    user_id = int(get_current_user_id() or 0)
    if not user_id:
        return error(401, "Authentication required", http=401)
    try:
        limit = max(1, min(int(request.args.get("limit") or 100), 500))
    except Exception:
        limit = 100
    return envelope(list_agent_audit(user_id, limit))
