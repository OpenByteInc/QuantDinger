"""Structured source-edit protocol shared by AI authoring workspaces."""

from __future__ import annotations

import json
from typing import Any


class CodeEditError(ValueError):
    """Raised when a model edit response cannot be applied safely."""


CODE_EDIT_SYSTEM_SUFFIX = """

# Source edit response contract
The current source already exists. Return JSON only, with this exact shape:
{"operations":[{"old_text":"exact text copied from the current source","new_text":"replacement text"}]}

Rules:
- Return only the smallest independent replacements needed for the requested change.
- Every old_text must be copied exactly from the supplied current source and must occur exactly once.
- Include enough unchanged surrounding text in old_text to make it unique.
- Preserve all behavior that the user did not ask to change.
- Do not return the complete source file, markdown fences, prose, line numbers, or a unified diff.
- Insertions must replace a unique nearby anchor with that same anchor plus the inserted text.
- Deletions use an empty new_text.
""".strip()


def code_edit_user_instruction() -> str:
    return (
        "Return a minimal structured edit response using the source edit response contract. "
        "Do not return a complete replacement file."
    )


def _parse_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    # Reasoning models prepend <think>...</think> prose; json.loads fails on it.
    from app.services.llm import strip_think_blocks

    raw = strip_think_blocks(str(response or ""))
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    try:
        payload = json.loads(raw.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise CodeEditError("invalid_edit_json") from exc
    if not isinstance(payload, dict):
        raise CodeEditError("invalid_edit_payload")
    return payload


def apply_model_code_edits(source: str, response: Any) -> tuple[str, dict[str, Any]]:
    """Apply non-overlapping exact replacements to one immutable source snapshot."""
    base = str(source or "")
    payload = _parse_payload(response)
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 64:
        raise CodeEditError("invalid_edit_operations")

    resolved: list[dict[str, Any]] = []
    for index, item in enumerate(operations):
        if not isinstance(item, dict):
            raise CodeEditError(f"invalid_edit_operation:{index}")
        old_text = item.get("old_text")
        new_text = item.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise CodeEditError(f"empty_edit_anchor:{index}")
        if not isinstance(new_text, str):
            raise CodeEditError(f"invalid_edit_replacement:{index}")
        if old_text == new_text:
            raise CodeEditError(f"no_op_edit:{index}")
        first = base.find(old_text)
        if first < 0:
            raise CodeEditError(f"edit_anchor_not_found:{index}")
        if base.find(old_text, first + 1) >= 0:
            raise CodeEditError(f"edit_anchor_ambiguous:{index}")
        resolved.append({
            "start": first,
            "end": first + len(old_text),
            "old_text": old_text,
            "new_text": new_text,
        })

    ordered = sorted(resolved, key=lambda item: int(item["start"]))
    for previous, current in zip(ordered, ordered[1:]):
        if int(current["start"]) < int(previous["end"]):
            raise CodeEditError("overlapping_edit_operations")

    candidate = base
    for item in reversed(ordered):
        start = int(item["start"])
        end = int(item["end"])
        candidate = candidate[:start] + str(item["new_text"]) + candidate[end:]

    public_operations = [
        {
            "oldText": item["old_text"],
            "newText": item["new_text"],
            "startLine": base.count("\n", 0, int(item["start"])) + 1,
            "endLine": base.count("\n", 0, int(item["end"])) + 1,
        }
        for item in ordered
    ]
    return candidate, {
        "executor": "model_patch",
        "operation": "exact_replacements",
        "operation_count": len(public_operations),
        "operations": public_operations,
    }
