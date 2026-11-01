"""Process-local opaque fingerprints for sensitive cache key material."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any


_PROCESS_FINGERPRINT_KEY = secrets.token_bytes(32)


def opaque_fingerprint(*parts: Any, namespace: str) -> str:
    """Return a stable process-local HMAC without exposing the source values."""
    payload = bytearray()
    for part in parts:
        encoded = str(part or "").encode("utf-8")
        payload.extend(len(encoded).to_bytes(8, "big"))
        payload.extend(encoded)
    return hmac.new(
        _PROCESS_FINGERPRINT_KEY,
        namespace.encode("utf-8") + b"\x00" + bytes(payload),
        hashlib.sha256,
    ).hexdigest()
