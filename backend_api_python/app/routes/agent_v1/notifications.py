"""Notification automation endpoints (capability class N)."""
from __future__ import annotations

from flask import request

from app.services.indicator_signal_alerts import IndicatorSignalAlertService
from app.utils.agent_auth import SCOPE_N, agent_required, current_user_id

from . import agent_v1_bp
from ._helpers import clip_int, envelope, error, get_json_or_400
from ._security import redact_secrets

_alerts = IndicatorSignalAlertService()


def _owned_task(task_id: int):
    return next(
        (item for item in _alerts.list_tasks(current_user_id()) if int(item.get("id") or 0) == int(task_id)),
        None,
    )


@agent_v1_bp.route("/notifications/signal-alerts", methods=["GET"])
@agent_required(SCOPE_N)
def list_signal_alerts():
    rows = _alerts.list_tasks(current_user_id())
    limit = clip_int(request.args.get("limit"), default=50, lo=1, hi=200)
    cursor = clip_int(request.args.get("cursor"), default=0, lo=0, hi=1_000_000)
    items = redact_secrets(rows[cursor:cursor + limit])
    next_cursor = cursor + len(items) if cursor + len(items) < len(rows) else None
    return envelope({"items": items, "next_cursor": next_cursor, "total": len(rows)})


@agent_v1_bp.route("/notifications/signal-alerts", methods=["POST"])
@agent_required(SCOPE_N)
def create_signal_alert():
    body, err = get_json_or_400()
    if err:
        return err
    try:
        return envelope(
            redact_secrets(_alerts.create_task(current_user_id(), body)),
            message="created",
        )
    except ValueError as exc:
        return error(400, str(exc))


@agent_v1_bp.route("/notifications/signal-alerts/<int:task_id>", methods=["PATCH"])
@agent_required(SCOPE_N)
def update_signal_alert(task_id: int):
    body, err = get_json_or_400()
    if err:
        return err
    try:
        return envelope(
            redact_secrets(_alerts.update_task(current_user_id(), task_id, body)),
            message="updated",
        )
    except ValueError as exc:
        return error(404 if "not found" in str(exc).lower() else 400, str(exc), http=404 if "not found" in str(exc).lower() else 400)


@agent_v1_bp.route("/notifications/signal-alerts/<int:task_id>", methods=["DELETE"])
@agent_required(SCOPE_N)
def delete_signal_alert(task_id: int):
    if not _owned_task(task_id):
        return error(404, "Task not found", http=404)
    _alerts.delete_task(current_user_id(), task_id)
    return envelope({"task_id": task_id}, message="deleted")


@agent_v1_bp.route("/notifications/signal-alerts/<int:task_id>/status", methods=["POST"])
@agent_required(SCOPE_N)
def set_signal_alert_status(task_id: int):
    body, err = get_json_or_400()
    if err:
        return err
    status = str(body.get("status") or "").strip().lower()
    if status not in {"running", "paused"}:
        return error(400, "status must be running or paused")
    try:
        return envelope(
            redact_secrets(_alerts.set_status(current_user_id(), task_id, status)),
            message=status,
        )
    except ValueError as exc:
        return error(404, str(exc), http=404)


@agent_v1_bp.route("/notifications/signal-alerts/<int:task_id>/run", methods=["POST"])
@agent_required(SCOPE_N)
def run_signal_alert(task_id: int):
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return error(400, "confirm=true is required because this may deliver notifications")
    if not _owned_task(task_id):
        return error(404, "Task not found", http=404)
    return envelope(redact_secrets(_alerts.evaluate_task(task_id)), message="evaluated")
