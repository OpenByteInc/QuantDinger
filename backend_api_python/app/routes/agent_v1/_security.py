"""Agent Gateway security helpers for secret redaction and payload bounds."""
from __future__ import annotations

from typing import Any

from app.utils.structured_redaction import redact_structured_secrets

# Upper bound for indicator Python source accepted via agent/MCP paths.
MAX_SOURCE_CODE_BYTES = 512 * 1024
MAX_INDICATOR_CODE_BYTES = MAX_SOURCE_CODE_BYTES

def indicator_code_too_large(code: str) -> bool:
    return len((code or "").encode("utf-8")) > MAX_INDICATOR_CODE_BYTES


def assert_indicator_code_size(code: str) -> None:
    if indicator_code_too_large(code):
        raise ValueError(
            f"Indicator code exceeds {MAX_INDICATOR_CODE_BYTES // 1024} KiB limit"
        )


def assert_strategy_code_size(code: str) -> None:
    if len((code or "").encode("utf-8")) > MAX_SOURCE_CODE_BYTES:
        raise ValueError(
            f"Strategy code exceeds {MAX_SOURCE_CODE_BYTES // 1024} KiB limit"
        )


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = 6) -> Any:
    """Return a copy with known credential fields masked."""
    return redact_structured_secrets(value, depth=depth, max_depth=max_depth)


def redact_strategy_row(row: dict | None) -> dict | None:
    """Mask credential-like fields before returning a strategy to an agent."""
    if not row:
        return row
    out = dict(row)
    for field in ("exchange_config", "trading_config", "notification_config"):
        if isinstance(out.get(field), dict):
            out[field] = redact_secrets(out[field])
    return out
