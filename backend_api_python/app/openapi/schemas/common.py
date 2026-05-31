"""
Shared Marshmallow schemas for OpenAPI generation.

Human-facing routes use ``{code, msg, data}``. Agent Gateway routes use
``{code, message, data}`` on success and ``{code, message, details, retriable}``
on error — see ``docs/agent/agent-openapi.json``.
"""
from marshmallow import Schema, fields


class HumanSuccessEnvelopeSchema(Schema):
    """Standard success wrapper for human web API routes."""

    code = fields.Integer(
        metadata={"description": "1 on success", "example": 1},
    )
    msg = fields.String(metadata={"example": "success"})
    data = fields.Raw(allow_none=True)


class HumanErrorEnvelopeSchema(Schema):
    """Standard error wrapper for human web API routes."""

    code = fields.Integer(
        metadata={"description": "0 on error", "example": 0},
    )
    msg = fields.String()
    data = fields.Raw(allow_none=True)


class AgentSuccessEnvelopeSchema(Schema):
    """Success wrapper for ``/api/agent/v1`` routes."""

    code = fields.Integer(
        metadata={"description": "0 on success", "example": 0},
    )
    message = fields.String()
    data = fields.Raw(allow_none=True)


class AgentErrorSchema(Schema):
    """Error body for ``/api/agent/v1`` routes."""

    code = fields.Integer()
    message = fields.String()
    details = fields.Raw(allow_none=True)
    retriable = fields.Boolean()


class PaginationMetaSchema(Schema):
    """Common list pagination metadata."""

    page = fields.Integer()
    page_size = fields.Integer()
    total = fields.Integer()


class ApiInfoSchema(Schema):
    """GET / — API identity payload."""

    name = fields.String(metadata={"example": "QuantDinger Python API"})
    version = fields.String(metadata={"example": "3.0.22"})
    status = fields.String(metadata={"example": "running"})
    timestamp = fields.DateTime(format="iso")


class HealthStatusSchema(Schema):
    """GET /health and GET /api/health."""

    status = fields.String(metadata={"example": "healthy"})
    timestamp = fields.DateTime(format="iso")
