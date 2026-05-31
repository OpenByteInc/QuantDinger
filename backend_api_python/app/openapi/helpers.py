"""
Helpers for OpenAPI-documented human API routes.
"""
from __future__ import annotations

from typing import Any, Tuple

from flask import jsonify


def ok(data: Any = None, msg: str = "success") -> dict:
    """Human API success envelope as a plain dict (flask-smorest friendly)."""
    return {"code": 1, "msg": msg, "data": data}


def fail(msg: str, *, code: int = 0, data: Any = None, http_status: int = 400):
    """Human API error envelope. Returns (body, status) for Flask views."""
    return jsonify({"code": code, "msg": msg, "data": data}), http_status


def visibility_doc(level: str) -> dict:
    """OpenAPI extension for Public / Internal / Private tiers."""
    return {"x-visibility": level}
