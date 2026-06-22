"""Client-side safety rails for the QuantDinger MCP server."""
from __future__ import annotations

import re
import time
from typing import Any, Mapping

import httpx

# Keep in sync with backend `agent_v1/_security.py`.
MAX_INDICATOR_CODE_BYTES = 512 * 1024

_SECRET_KEYS = frozenset({
    "api_key", "secret_key", "passphrase", "apiKey", "secret", "password",
    "private_key", "access_token", "refresh_token", "bot_token",
    "webhook_secret", "signing_secret", "client_secret",
})

_SSE_EVENT_RE = re.compile(r"^event:\s*(\S+)\s*$", re.MULTILINE)
_SSE_DATA_RE = re.compile(r"^data:\s*(.+)$", re.MULTILINE)


def assert_indicator_code_size(code: str) -> None:
    if len((code or "").encode("utf-8")) > MAX_INDICATOR_CODE_BYTES:
        raise ValueError(
            f"Indicator code exceeds {MAX_INDICATOR_CODE_BYTES // 1024} KiB MCP limit"
        )


def assert_json_dict(name: str, value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = 6) -> Any:
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


def parse_sse_chunk(text: str) -> list[tuple[str, Any]]:
    """Parse one or more SSE frames from a text chunk."""
    import json

    frames: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_m = _SSE_EVENT_RE.search(block)
        data_m = _SSE_DATA_RE.search(block)
        if not event_m or not data_m:
            continue
        event = event_m.group(1)
        try:
            payload = json.loads(data_m.group(1))
        except Exception:
            payload = data_m.group(1)
        frames.append((event, payload))
    return frames


def consume_job_stream(
    client: httpx.Client,
    path: str,
    *,
    since_seq: int = 0,
    max_events: int = 200,
    max_seconds: float = 300.0,
) -> dict[str, Any]:
    """Read job SSE until `result` or safety limits are hit."""
    import json

    params = {"since": int(since_seq)} if since_seq else None
    events: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    truncated = False
    started = time.monotonic()

    with client.stream("GET", path, params=params) as resp:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:2000]
            return {
                "error": True,
                "status": resp.status_code,
                "body": body,
                "events": events,
            }

        buffer = ""
        for chunk in resp.iter_text():
            if time.monotonic() - started > max_seconds:
                truncated = True
                break
            buffer += chunk
            while "\n\n" in buffer:
                part, buffer = buffer.split("\n\n", 1)
                for event, payload in parse_sse_chunk(part + "\n\n"):
                    events.append({"event": event, "data": payload})
                    if len(events) > max_events:
                        truncated = True
                        break
                    if event == "result":
                        result = payload if isinstance(payload, dict) else {"value": payload}
                if truncated or result is not None:
                    break
            if truncated or result is not None:
                break

    return {
        "events": events,
        "result": result,
        "truncated": truncated,
        "event_count": len(events),
    }


def poll_job_until_terminal(
    get_job_fn,
    job_id: str,
    *,
    timeout_s: float = 300.0,
    interval_s: float = 2.0,
) -> dict[str, Any]:
    """Poll job snapshot until terminal status or timeout."""
    deadline = time.monotonic() + max(1.0, timeout_s)
    last: Any = None
    while time.monotonic() < deadline:
        last = get_job_fn(job_id)
        if isinstance(last, dict) and last.get("error"):
            return last
        status = (last or {}).get("status") if isinstance(last, dict) else None
        if status in ("succeeded", "failed", "cancelled"):
            return {"job": last, "timed_out": False}
        time.sleep(max(0.5, interval_s))
    return {"job": last, "timed_out": True}
