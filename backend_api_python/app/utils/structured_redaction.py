"""Structured secret detection, redaction, and masked-update helpers."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Collection


MASKED_SECRET = "***"

# Keys are stored in separator-free lowercase form so one entry covers snake_case,
# kebab-case, dotted, spaced, and camelCase spellings.
SENSITIVE_KEY_NAMES = frozenset({
    "apikey",
    "secretkey",
    "secret",
    "passphrase",
    "password",
    "privatekey",
    "accesstoken",
    "refreshtoken",
    "bottoken",
    "webhooksecret",
    "signingsecret",
    "clientsecret",
    "telegrambottoken",
    "telegramtoken",
    "webhooktoken",
    "webhooksigningsecret",
    "authorization",
    "proxyauthorization",
    "xapikey",
    "xauthtoken",
})
SENSITIVE_CONTAINER_KEY_NAMES = frozenset({"webhookheaders"})

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_sensitive_key(key: Any) -> str:
    """Normalize a structured key without losing camelCase equivalence."""
    return _NON_ALNUM_RE.sub("", str(key or "").lower())


def is_sensitive_key(key: Any, *, extra_keys: Collection[str] = ()) -> bool:
    normalized = normalize_sensitive_key(key)
    return normalized in SENSITIVE_KEY_NAMES or normalized in extra_keys


def is_masked_secret(value: Any) -> bool:
    return isinstance(value, str) and value.strip() in {MASKED_SECRET, "<redacted>"}


def redact_structured_secrets(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 8,
    mask: str = MASKED_SECRET,
    extra_keys: Collection[str] = (),
) -> Any:
    """Return a copy with credential fields masked, failing closed at depth."""
    if depth > max_depth:
        return mask if isinstance(value, (Mapping, list, tuple)) else value
    if isinstance(value, Mapping):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = normalize_sensitive_key(key)
            if is_sensitive_key(key, extra_keys=extra_keys) and item not in (None, "", False):
                output[key] = mask
            elif normalized_key in SENSITIVE_CONTAINER_KEY_NAMES and isinstance(item, str):
                try:
                    parsed = json.loads(item)
                except (TypeError, ValueError, json.JSONDecodeError):
                    output[key] = mask if item.strip() else item
                else:
                    if isinstance(parsed, Mapping):
                        redacted = redact_structured_secrets(
                            parsed,
                            depth=depth + 1,
                            max_depth=max_depth,
                            mask=mask,
                            extra_keys=extra_keys,
                        )
                        output[key] = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
                    else:
                        output[key] = mask
            else:
                output[key] = redact_structured_secrets(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    mask=mask,
                    extra_keys=extra_keys,
                )
        return output
    if isinstance(value, list):
        return [
            redact_structured_secrets(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                mask=mask,
                extra_keys=extra_keys,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_structured_secrets(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                mask=mask,
                extra_keys=extra_keys,
            )
            for item in value
        )
    return value


def contains_structured_secret(value: Any, *, extra_keys: Collection[str] = ()) -> bool:
    if isinstance(value, Mapping):
        return any(
            (is_sensitive_key(key, extra_keys=extra_keys) and item not in (None, "", False))
            or contains_structured_secret(item, extra_keys=extra_keys)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_structured_secret(item, extra_keys=extra_keys) for item in value)
    return False


def strip_structured_secrets(value: Any, *, extra_keys: Collection[str] = ()) -> Any:
    if isinstance(value, Mapping):
        return {
            key: strip_structured_secrets(item, extra_keys=extra_keys)
            for key, item in value.items()
            if not is_sensitive_key(key, extra_keys=extra_keys)
        }
    if isinstance(value, list):
        return [strip_structured_secrets(item, extra_keys=extra_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_structured_secrets(item, extra_keys=extra_keys) for item in value)
    return value


def preserve_masked_secrets(
    incoming: Any,
    existing: Any,
    *,
    extra_keys: Collection[str] = (),
) -> Any:
    """Keep stored secrets when an update omits or echoes a redacted value."""
    if not isinstance(incoming, Mapping) or not isinstance(existing, Mapping):
        return incoming

    output = dict(incoming)
    incoming_by_name = {normalize_sensitive_key(key): key for key in incoming}

    for existing_key, existing_value in existing.items():
        normalized = normalize_sensitive_key(existing_key)
        incoming_key = incoming_by_name.get(normalized)

        if normalized in SENSITIVE_CONTAINER_KEY_NAMES:
            if incoming_key is None:
                output[existing_key] = existing_value
                continue
            candidate = output[incoming_key]
            if candidate in (None, "", False) or is_masked_secret(candidate):
                output[incoming_key] = existing_value
                continue
            incoming_headers = _parse_mapping(candidate)
            existing_headers = _parse_mapping(existing_value)
            if incoming_headers is not None and existing_headers is not None:
                preserved = preserve_masked_secrets(
                    incoming_headers,
                    existing_headers,
                    extra_keys=extra_keys,
                )
                output[incoming_key] = (
                    json.dumps(preserved, ensure_ascii=False, separators=(",", ":"))
                    if isinstance(candidate, str)
                    else preserved
                )
            continue

        if is_sensitive_key(existing_key, extra_keys=extra_keys):
            if incoming_key is None:
                if existing_value not in (None, "", False):
                    output[existing_key] = existing_value
                continue
            candidate = output[incoming_key]
            if candidate in (None, "", False) or is_masked_secret(candidate):
                output[incoming_key] = existing_value
            continue

        if incoming_key is not None and isinstance(output[incoming_key], Mapping):
            output[incoming_key] = preserve_masked_secrets(
                output[incoming_key],
                existing_value,
                extra_keys=extra_keys,
            )
            continue

        if incoming_key is None and isinstance(existing_value, Mapping):
            preserved = preserve_masked_secrets({}, existing_value, extra_keys=extra_keys)
            if preserved:
                output[existing_key] = preserved

    return output


def _parse_mapping(value: Any) -> Mapping[Any, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None
