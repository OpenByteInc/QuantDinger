"""Indicator signal alert routes."""
from __future__ import annotations

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.indicator_signal_alerts import IndicatorSignalAlertService
from app.utils.auth import login_required
from app.utils.language import detect_request_language
from app.utils.logger import get_logger


logger = get_logger(__name__)
indicator_signal_alerts_blp = Blueprint("indicator_signal_alerts", __name__)
service = IndicatorSignalAlertService()


def _ok(data=None, msg: str = "success"):
    return jsonify({"code": 1, "msg": msg, "data": data})


def _fail(msg: str, status: int = 400):
    return jsonify({"code": 0, "msg": msg, "data": None}), status


def _user_id() -> int:
    direct = getattr(g, "user_id", None)
    if direct:
        return int(direct)
    user = getattr(g, "user", None)
    if isinstance(user, dict):
        return int(user.get("id") or 1)
    return 1


@indicator_signal_alerts_blp.route("/signal-alerts", methods=["GET"])
@login_required
def list_indicator_signal_alerts():
    """List current user's indicator signal alert tasks."""
    try:
        return _ok(service.list_tasks(_user_id()))
    except Exception as exc:
        logger.warning("list indicator signal alerts failed: %s", exc)
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts", methods=["POST"])
@login_required
def create_indicator_signal_alert():
    """Create an indicator signal alert task."""
    try:
        payload = request.get_json(silent=True) or {}
        return _ok(service.create_task(
            _user_id(),
            payload,
            language=detect_request_language(request, payload),
            timezone_name=request.headers.get("X-App-Timezone", ""),
        ))
    except ValueError as exc:
        return _fail(str(exc), 400)
    except Exception as exc:
        logger.exception("create indicator signal alert failed")
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts/<int:task_id>", methods=["PUT"])
@login_required
def update_indicator_signal_alert(task_id: int):
    """Update an indicator signal alert task."""
    try:
        payload = request.get_json(silent=True) or {}
        return _ok(service.update_task(
            _user_id(),
            task_id,
            payload,
            language=detect_request_language(request, payload),
            timezone_name=request.headers.get("X-App-Timezone", ""),
        ))
    except ValueError as exc:
        return _fail(str(exc), 400)
    except Exception as exc:
        logger.exception("update indicator signal alert failed")
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts/<int:task_id>", methods=["DELETE"])
@login_required
def delete_indicator_signal_alert(task_id: int):
    """Delete an indicator signal alert task."""
    try:
        service.delete_task(_user_id(), task_id)
        return _ok(True)
    except Exception as exc:
        logger.exception("delete indicator signal alert failed")
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts/<int:task_id>/pause", methods=["POST"])
@login_required
def pause_indicator_signal_alert(task_id: int):
    """Pause an indicator signal alert task."""
    try:
        return _ok(service.set_status(_user_id(), task_id, "paused"))
    except ValueError as exc:
        return _fail(str(exc), 404)
    except Exception as exc:
        logger.exception("pause indicator signal alert failed")
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts/<int:task_id>/resume", methods=["POST"])
@login_required
def resume_indicator_signal_alert(task_id: int):
    """Resume an indicator signal alert task."""
    try:
        return _ok(service.set_status(_user_id(), task_id, "running"))
    except ValueError as exc:
        return _fail(str(exc), 404)
    except Exception as exc:
        logger.exception("resume indicator signal alert failed")
        return _fail(str(exc), 500)


@indicator_signal_alerts_blp.route("/signal-alerts/<int:task_id>/test", methods=["POST"])
@login_required
def test_indicator_signal_alert(task_id: int):
    """Evaluate a task immediately for diagnostics."""
    try:
        # Ownership is checked by loading through the user-visible list.
        if not any(int(t.get("id")) == int(task_id) for t in service.list_tasks(_user_id())):
            return _fail("Task not found", 404)
        return _ok(service.evaluate_task(task_id))
    except Exception as exc:
        logger.exception("test indicator signal alert failed")
        return _fail(str(exc), 500)
