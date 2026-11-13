"""Recognize explicit gateway rejections of the JSON response-format parameter."""

import re


def rejects_response_format(response) -> bool:
    """Retry only validation errors that identify an unsupported parameter."""
    if response.status_code not in (400, 422):
        return False
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return False
    parameter = str(error.get("param") or "").strip().lower()
    code = str(error.get("code") or error.get("type") or "").strip().lower()
    message = str(error.get("message") or "").lower()
    if parameter == "response_format":
        return code in {"unsupported_parameter", "unknown_parameter", "unrecognized_parameter", "extra_forbidden"} or bool(
            re.search(r"\b(?:unsupported|not supported|not allowed|not permitted|unrecognized|unknown parameter)\b", message)
        )
    parameter_name = r"[\"'`]?response_format\b[\"'`]?"
    return any(re.search(pattern, message) for pattern in (
        rf"\b(?:unsupported|unknown|unrecognized|unexpected)\s+(?:(?:request|body)\s+)?"
        rf"(?:parameter|argument|field|keyword)\s*[:=]?\s*{parameter_name}",
        rf"{parameter_name}\s+(?:parameter\s+)?(?:is\s+)?"
        r"(?:unsupported|not supported|not allowed|not permitted)\b",
        rf"\bdoes not support\s+(?:the\s+)?(?:parameter\s+)?{parameter_name}",
    ))
