"""Scrub credentials out of strings before they reach logs or API responses.

Providers embed the request URL in exception messages, and some APIs carry the
key as a query parameter, so anything derived from an exception must be scrubbed
before being logged, re-raised, or returned to a client.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_QUERY_PATTERN = re.compile(
    r"(?i)\b([a-z0-9_]*(?:api[_-]?key|apikey|key|access[_-]?token|token|secret|password))\s*=\s*([^&\s'\"]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")


def redact_secrets(text: Any) -> str:
    """Return ``text`` with credentials stripped (query params and bearer tokens).

    Non-secret context (hosts, paths, causes) is preserved so failures stay
    debuggable.
    """
    if text is None:
        return ""
    scrubbed = _SECRET_QUERY_PATTERN.sub(r"\1=<redacted>", str(text))
    return _BEARER_PATTERN.sub(r"\1<redacted>", scrubbed)
