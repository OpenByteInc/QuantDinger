"""
Human-facing API blueprint with default OpenAPI response envelopes.

Routes that return SSE streams skip the JSON envelope schemas.
"""
from __future__ import annotations

from flask_smorest import Blueprint as SmorestBlueprint

from app.openapi.schemas.common import HumanErrorEnvelopeSchema, HumanSuccessEnvelopeSchema

# Path suffixes that return text/event-stream instead of JSON envelopes.
_SSE_RULE_SUFFIXES = (
    "/aiGenerate",
    "/ai-optimize",
    "/ai-generate",
)


class HumanBlueprint(SmorestBlueprint):
    """flask-smorest Blueprint that documents standard human API envelopes."""

    def route(self, rule: str, *, methods=None, **options):
        skip_envelope = any(rule.endswith(suffix) for suffix in _SSE_RULE_SUFFIXES)

        def decorator(f):
            if not skip_envelope:
                f = self.response(400, HumanErrorEnvelopeSchema)(f)
                f = self.response(200, HumanSuccessEnvelopeSchema)(f)
            return SmorestBlueprint.route(self, rule, methods=methods, **options)(f)

        return decorator
