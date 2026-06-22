"""
OpenAPI / flask-smorest integration for the QuantDinger human web API.

Documented routes live here and are merged into ``docs/api/openapi.yaml`` by
``scripts/export_openapi.py``. Legacy Blueprint routes remain in ``app/routes/``
until migrated module-by-module (see ``docs/API_CONVENTIONS.md``).
"""
from __future__ import annotations

import os

from flask import Flask
from flask_smorest import Api

from app._version import APP_VERSION


def _openapi_enabled() -> bool:
    """Expose Swagger/ReDoc when explicitly enabled or in debug mode."""
    flag = os.getenv("OPENAPI_ENABLED", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    debug = os.getenv("PYTHON_API_DEBUG", "false").lower() == "true"
    return debug


def init_openapi(app: Flask) -> Api:
    """Register flask-smorest, shared components, and documented blueprints."""
    app.config.setdefault("API_TITLE", "QuantDinger Web API")
    app.config.setdefault("API_VERSION", "1.0.0")
    app.config.setdefault("OPENAPI_VERSION", "3.0.3")
    app.config.setdefault(
        "OPENAPI_DESCRIPTION",
        (
            "Human-facing REST API for QuantDinger. "
            "Agent integrations use `/api/agent/v1` — see "
            "`docs/agent/agent-openapi.json`. "
            "Conventions: `docs/API_CONVENTIONS.md`."
        ),
    )
    app.config.setdefault("OPENAPI_URL_PREFIX", "/")
    app.config.setdefault(
        "API_SPEC_OPTIONS",
        {
            "info": {
                "description": app.config.get("OPENAPI_DESCRIPTION", ""),
                "contact": {
                    "name": "QuantDinger",
                    "url": "https://github.com/brokermr810/quantdinger",
                },
                "license": {
                    "name": "See repository LICENSE",
                },
                "x-api-app-version": APP_VERSION,
            },
            "components": {
                "securitySchemes": {
                    "HumanJWT": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT",
                        "description": (
                            "User session JWT from `POST /api/auth/login`. "
                            "Send as `Authorization: Bearer <token>`."
                        ),
                    },
                    "AgentToken": {
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "qd_agent_xxx",
                        "description": (
                            "Agent Gateway token for `/api/agent/v1/*` only. "
                            "See docs/agent/AGENT_QUICKSTART.md."
                        ),
                    },
                },
            },
            "tags": [
                {
                    "name": "Health",
                    "description": "Liveness and API metadata (Public)",
                },
            ],
            "servers": [
                {
                    "url": "http://localhost:5000",
                    "description": "Backend direct (python run.py)",
                },
                {
                    "url": "http://localhost:8888",
                    "description": "Docker Compose (via reverse proxy)",
                },
            ],
        },
    )

    if _openapi_enabled():
        app.config.setdefault("OPENAPI_SWAGGER_UI_PATH", "/api/docs/swagger")
        app.config.setdefault(
            "OPENAPI_SWAGGER_UI_URL",
            "https://cdn.jsdelivr.net/npm/swagger-ui-dist/",
        )
        app.config.setdefault("OPENAPI_REDOC_PATH", "/api/docs/redoc")
        app.config.setdefault(
            "OPENAPI_REDOC_URL",
            "https://cdn.redoc.ly/redoc/v2.4.0/bundles/redoc.standalone.js",
        )

    api = Api(app)

    # Register component schemas so they appear in the exported spec even
    # before every route uses them explicitly.
    from app.openapi.schemas.common import (
        AgentErrorSchema,
        AgentSuccessEnvelopeSchema,
        HumanErrorEnvelopeSchema,
        HumanSuccessEnvelopeSchema,
        PaginationMetaSchema,
    )

    for name, schema in (
        ("HumanSuccessEnvelope", HumanSuccessEnvelopeSchema),
        ("HumanErrorEnvelope", HumanErrorEnvelopeSchema),
        ("AgentSuccessEnvelope", AgentSuccessEnvelopeSchema),
        ("AgentError", AgentErrorSchema),
        ("PaginationMeta", PaginationMetaSchema),
    ):
        api.spec.components.schema(name, schema=schema)

    from app.openapi.register import register_human_blueprints

    register_human_blueprints(api)

    app.extensions["quantdinger_openapi_api"] = api
    return api


def get_openapi_api(app: Flask) -> Api | None:
    return app.extensions.get("quantdinger_openapi_api")
