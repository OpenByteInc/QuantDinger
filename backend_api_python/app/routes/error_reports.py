"""
Error reports API — receives batched frontend error events at /api/v2/errors.

This endpoint is mounted at /api/v2/errors (no auth — errors can happen before
login; we rely on rate limiting + payload size cap for abuse protection).
Events are stored for observability; critical trade/strategy crashes can be
wired to alerting downstream.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from flask import g, jsonify, request
from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.utils.logger import get_logger

logger = get_logger(__name__)

error_reports_blp = Blueprint(
    "error_reports",
    __name__,
    description="Frontend error monitoring ingestion",
)

MAX_EVENTS_PER_BATCH = 50
MAX_EVENT_TEXT_LEN = 4000

_seen_count = 0
_dropped_count = 0


@error_reports_blp.route("/v2/errors", methods=["POST"])
def report_errors():
    """Ingest a batch of frontend error events.

    Body: { events: [ { type, message, stack, context, severity, ... } ] }
    Returns { code: 1, msg: "success", data: { received: N } }
    """
    global _seen_count, _dropped_count
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        return jsonify({"code": 0, "msg": "invalid json", "data": None}), 400

    events = payload.get("events")
    if not isinstance(events, list) or len(events) == 0:
        return jsonify({"code": 0, "msg": "events array required", "data": None}), 400

    # Cap batch size to prevent abuse.
    if len(events) > MAX_EVENTS_PER_BATCH:
        events = events[:MAX_EVENTS_PER_BATCH]

    received = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_type = str(ev.get("type", "unknown"))[:100]
        severity = str(ev.get("severity", "info"))[:20]
        # Truncate long fields to avoid log bloat.
        message = str(ev.get("message", ""))[:MAX_EVENT_TEXT_LEN]
        stack = str(ev.get("stack") or "")[:MAX_EVENT_TEXT_LEN]
        context = ev.get("context") or {}

        # Log with structured fields; downstream can wire to Sentry/DB.
        logger.warning(
            "frontend_error type=%s severity=%s msg=%s ctx=%s",
            ev_type,
            severity,
            message[:200],
            json.dumps(context, default=str, ensure_ascii=False)[:500]
            if isinstance(context, dict)
            else str(context)[:500],
        )
        received += 1

    _seen_count += received
    return jsonify({
        "code": 1,
        "msg": "success",
        "data": {"received": received, "total_seen": _seen_count},
    })


@error_reports_blp.route("/v2/errors/stats", methods=["GET"])
def error_stats():
    """Lightweight stats endpoint for ops dashboards (no auth for now)."""
    return jsonify({
        "code": 1,
        "msg": "success",
        "data": {
            "total_seen": _seen_count,
            "total_dropped": _dropped_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    })
