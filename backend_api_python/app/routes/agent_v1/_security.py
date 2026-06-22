"""Agent Gateway security helpers — secret redaction and payload bounds."""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping

# Upper bound for indicator Python source accepted via agent/MCP paths.
MAX_INDICATOR_CODE_BYTES = 512 * 1024

# Keys stripped or masked anywhere in agent-facing JSON (case-sensitive).
_SECRET_KEYS = frozenset({
    "api_key", "secret_key", "passphrase", "apiKey", "secret", "password",
    "private_key", "access_token", "refresh_token", "bot_token",
    "webhook_secret", "signing_secret", "client_secret",
})


def indicator_code_too_large(code: str) -> bool:
    return len((code or "").encode("utf-8")) > MAX_INDICATOR_CODE_BYTES


def assert_indicator_code_size(code: str) -> None:
    if indicator_code_too_large(code):
        raise ValueError(
            f"Indicator code exceeds {MAX_INDICATOR_CODE_BYTES // 1024} KiB limit"
        )


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = 6) -> Any:
    """Return a copy with known credential fields masked."""
    if depth > max_depth:
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key in _SECRET_KEYS and v not in (None, "", False):
                out[key] = "***"
            elif isinstance(v, Mapping):
                out[key] = redact_secrets(v, depth=depth + 1, max_depth=max_depth)
            elif isinstance(v, list):
                out[key] = [
                    redact_secrets(item, depth=depth + 1, max_depth=max_depth)
                    for item in v
                ]
            else:
                out[key] = v
        return out
    if isinstance(value, list):
        return [redact_secrets(item, depth=depth + 1, max_depth=max_depth) for item in value]
    return value


def redact_strategy_row(row: dict | None) -> dict | None:
    """Mask credential-like fields before returning a strategy to an agent."""
    if not row:
        return row
    out = dict(row)
    for field in ("exchange_config", "trading_config", "notification_config", "ai_model_config"):
        if isinstance(out.get(field), dict):
            out[field] = redact_secrets(out[field])
    return out
