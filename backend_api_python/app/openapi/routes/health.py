"""Health and status routes (OpenAPI-documented via flask-smorest)."""
from datetime import datetime, timezone

from flask_smorest import Blueprint

from app._version import APP_VERSION
from app.openapi.schemas.common import ApiInfoSchema, HealthStatusSchema

blp = Blueprint(
    "health",
    __name__,
    url_prefix="",
    description="Health checks and API identity",
)


def _health_payload():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc),
    }


@blp.route("/", methods=["GET"])
@blp.response(200, ApiInfoSchema)
@blp.doc(summary="API root", tags=["Health"], operationId="getApiRoot")
def index():
    """Return API name, version, and running status."""
    return {
        "name": "QuantDinger Python API",
        "version": APP_VERSION,
        "status": "running",
        "timestamp": datetime.now(timezone.utc),
    }


@blp.route("/health", methods=["GET"])
@blp.response(200, HealthStatusSchema)
@blp.doc(summary="Health check", tags=["Health"], operationId="getHealth")
def health_check():
    """Liveness probe."""
    return _health_payload()


@blp.route("/api/health", methods=["GET"])
@blp.response(200, HealthStatusSchema)
@blp.doc(
    summary="Health check (compat path)",
    description="Used by Docker health checks and reverse-proxy probes.",
    tags=["Health"],
    operationId="getApiHealthCompat",
)
def api_health_check():
    """Same payload as ``GET /health``."""
    return _health_payload()
