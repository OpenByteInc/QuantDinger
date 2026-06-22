"""Structured notification display metadata for in-app (browser) notifications.

The UI renders ``payload.display`` with vue-i18n so stored rows stay locale-neutral.
Legacy rows without ``display`` fall back to stored title/message.
"""
from __future__ import annotations

from typing import Any


def with_display(payload: dict[str, Any] | None, template: str, params: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload or {})
    out["display"] = {"template": str(template), "params": dict(params or {})}
    return out
