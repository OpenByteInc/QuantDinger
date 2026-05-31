"""
Agent Gateway v1 — versioned, scoped surface for AI agents.

Mounted at `/api/agent/v1`. Read `docs/agent/AI_INTEGRATION_DESIGN.md` before
adding new endpoints.

This package is intentionally separate from the human-facing routes so:
  * Identity is exclusively agent-token (never JWT user sessions).
  * Every call is audited via `qd_agent_audit`.
  * Capability classes (R/W/B/N/C/T) are enforced per route.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from app.utils.logger import get_logger

logger = get_logger(__name__)


agent_v1_bp = Blueprint("agent_v1", __name__)


@agent_v1_bp.errorhandler(404)
def _not_found(_e):
    return jsonify({
        "code": 404,
        "message": "Route not found in /api/agent/v1",
        "details": None,
        "retriable": False,
    }), 404


def register(app) -> None:
    """Register the gateway and all sub-routes."""
    # Import sub-modules so their @agent_v1_bp.route decorators fire.
    from . import health  # noqa: F401
    from . import markets  # noqa: F401
    from . import strategies  # noqa: F401
    from . import backtests  # noqa: F401
    from . import experiments  # noqa: F401
    from . import portfolio  # noqa: F401
    from . import quick_trade  # noqa: F401
    from . import jobs as jobs_module  # noqa: F401
    from . import indicators  # noqa: F401
    from . import admin  # noqa: F401
    from . import me_tokens  # noqa: F401

    app.register_blueprint(agent_v1_bp, url_prefix="/api/agent/v1")
    logger.info("Agent Gateway v1 mounted at /api/agent/v1")
