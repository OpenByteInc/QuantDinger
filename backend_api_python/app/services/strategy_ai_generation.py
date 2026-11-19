"""Prompt selection and post-generation enforcement for strategy AI."""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable

from app.services.ai_generation_contracts import (
    CTA_STRATEGY_SYSTEM_PROMPT,
    INDICATOR_TO_STRATEGY_SYSTEM_PROMPT,
    PORTFOLIO_STRATEGY_SYSTEM_PROMPT,
)
from app.services.strategy_ai_capabilities import (
    StrategyAIGenerationIntent,
    render_strategy_capability_contract,
    resolve_strategy_generation_intent,
    validate_strategy_ai_semantics,
)
from app.services.strategy_ai_workspace import normalize_asset_type
from app.services.strategy_v2 import StrategyV2ContractError, inspect_strategy_v2
from app.services.strategy_v2.instruments import normalize_frequency, parse_instrument


_TIMEFRAME_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|1w)(?![a-z0-9])",
    re.IGNORECASE,
)
_TIMEFRAME_CHANGE_RE = re.compile(
    r"(?:周期|频率|timeframe|frequency|订阅).{0,24}(?:改|调整|切换|设为|change|switch|set)"
    r"|(?:改成|改为|调整为|切换到|change\s+to|switch\s+to|set\s+to).{0,12}"
    r"(?:1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|1w)",
    re.IGNORECASE,
)
_REFERENTIAL_EDIT_RE = re.compile(
    r"(?:我让你|按(?:上面|刚才)|不要(?:再)?.{0,8}解释|直接).{0,24}(?:改|修改|写入|应用|执行)",
    re.IGNORECASE,
)

_SOURCE_PRESERVATION_CAPABILITIES = {
    "crypto_swap",
    "bidirectional",
    "one_way_reversal",
    "supertrend",
    "technical_factors",
}
_OTHER_EDIT_RE = re.compile(
    r"(?:止损|止盈|仓位|杠杆|标的|多空|做多|做空|long|short|indicator|指标|信号|参数)",
    re.IGNORECASE,
)


def _timeframe_change_target(text: str) -> str:
    value = str(text or "")
    if not _TIMEFRAME_CHANGE_RE.search(value) or _OTHER_EDIT_RE.search(value):
        return ""
    matches = _TIMEFRAME_TOKEN_RE.findall(value)
    return normalize_frequency(matches[-1]) if matches else ""


def _line_char_offset(line: str, byte_offset: int) -> int:
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore"))


def apply_deterministic_strategy_edit(
    existing_code: str,
    prompt: str,
    *,
    summary: dict | None = None,
    recent_messages: list[dict] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Apply safe, narrow source edits before asking a model to rewrite a file.

    Only a single-frequency literal subscription is handled here. Broader or
    ambiguous requests deliberately fall through to normal model generation.
    """
    source = str(existing_code or "")
    if not source.strip():
        return None
    target = _timeframe_change_target(prompt)
    source_request = str(prompt or "")
    if not target and _REFERENTIAL_EDIT_RE.search(source_request):
        for item in reversed(list(recent_messages or [])):
            if str(item.get("role") or "") != "user":
                continue
            prior = str(item.get("content") or "")
            target = _timeframe_change_target(prior)
            if target:
                source_request = prior
                break
        if not target:
            summary_timeframe = str((summary or {}).get("timeframe") or "")
            if _TIMEFRAME_TOKEN_RE.fullmatch(summary_timeframe):
                target = normalize_frequency(summary_timeframe)
                source_request = "workspace_summary"
    if not target:
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    subscription_literals: list[ast.Constant] = []
    for function in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if function.name != "initialize":
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "subscribe":
                continue
            frequency = next((item.value for item in node.keywords if item.arg == "frequency"), None)
            if isinstance(frequency, ast.Constant) and isinstance(frequency.value, str):
                subscription_literals.append(frequency)
    current = {normalize_frequency(node.value) for node in subscription_literals}
    if not subscription_literals or len(current) != 1:
        return None
    previous = next(iter(current))
    if previous == target:
        return source, {
            "executor": "deterministic",
            "operation": "set_single_timeframe",
            "from": previous,
            "to": target,
            "changed": False,
            "resolved_from": source_request,
        }

    literals = list(subscription_literals)
    seen_locations = {(node.lineno, node.col_offset) for node in literals}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        candidates = [
            item.value for item in node.keywords if item.arg == "frequency"
        ]
        if call_name == "get_history" and len(node.args) > 1:
            candidates.append(node.args[1])
        for candidate in candidates:
            if not isinstance(candidate, ast.Constant) or not isinstance(candidate.value, str):
                continue
            try:
                candidate_frequency = normalize_frequency(candidate.value)
            except ValueError:
                continue
            location = (candidate.lineno, candidate.col_offset)
            if candidate_frequency == previous and location not in seen_locations:
                literals.append(candidate)
                seen_locations.add(location)

    original_lines = source.splitlines(keepends=True)
    lines = list(original_lines)
    replacements: list[tuple[int, int, int, str]] = []
    for node in literals:
        line_index = int(node.lineno) - 1
        start = _line_char_offset(lines[line_index], int(node.col_offset))
        end = _line_char_offset(lines[line_index], int(node.end_col_offset))
        replacements.append((line_index, start, end, json.dumps(target)))
    for line_index, start, end, replacement in reversed(replacements):
        lines[line_index] = lines[line_index][:start] + replacement + lines[line_index][end:]
    edit_operations = []
    changed_line_indexes = sorted({line_index for line_index, _start, _end, _replacement in replacements})
    for line_index in changed_line_indexes:
        old_text = original_lines[line_index]
        if source.count(old_text) != 1:
            edit_operations = []
            break
        edit_operations.append({
            "oldText": old_text,
            "newText": lines[line_index],
            "startLine": line_index + 1,
            "endLine": line_index + 1,
        })
    edit_plan = {
        "executor": "deterministic",
        "operation": "set_single_timeframe",
        "from": previous,
        "to": target,
        "changed": True,
        "replacement_count": len(replacements),
        "resolved_from": source_request,
    }
    if edit_operations:
        edit_plan["operations"] = edit_operations
        edit_plan["operation_count"] = len(edit_operations)
    return "".join(lines), edit_plan


def resolve_strategy_validation_intent(
    *,
    prompt: str,
    existing_code: str = "",
    context: dict | None = None,
) -> StrategyAIGenerationIntent:
    """Enforce requested contracts without retroactively blocking unrelated edits."""
    source_intent = resolve_strategy_generation_intent(
        prompt=prompt,
        existing_code=existing_code,
        context=context,
    )
    requested_intent = resolve_strategy_generation_intent(
        prompt=prompt,
        context=context,
    )
    capabilities = {
        *requested_intent.capabilities,
        *(
            capability
            for capability in source_intent.capabilities
            if capability in _SOURCE_PRESERVATION_CAPABILITIES
        ),
    }
    return StrategyAIGenerationIntent(
        tuple(sorted(capabilities)),
        source_intent.requested_direction_mode,
        source_intent.factor_ids,
        requested_intent.required_factor_ids,
    )


def select_strategy_system_prompt(asset_type: str, generation_mode: str = "authoring") -> str:
    normalized_type = normalize_asset_type(asset_type)
    mode = str(generation_mode or "authoring").strip().lower()
    if mode == "indicator_conversion":
        if normalized_type != "script":
            raise ValueError("strategyV2.indicatorConversionCtaOnly")
        return INDICATOR_TO_STRATEGY_SYSTEM_PROMPT
    if normalized_type == "portfolio_strategy":
        return PORTFOLIO_STRATEGY_SYSTEM_PROMPT
    return CTA_STRATEGY_SYSTEM_PROMPT


def build_strategy_system_prompt(
    *,
    prompt: str,
    asset_type: str,
    existing_code: str = "",
    generation_mode: str = "authoring",
    context: dict | None = None,
) -> tuple[str, StrategyAIGenerationIntent]:
    """Build a compact base prompt plus only the relevant capability packs."""
    base = select_strategy_system_prompt(asset_type, generation_mode)
    intent = resolve_strategy_generation_intent(
        prompt=prompt,
        existing_code=existing_code,
        context=context,
    )
    capability_contract = render_strategy_capability_contract(intent)
    return (f"{base}\n\n{capability_contract}" if capability_contract else base), intent


def _canonical_instrument(value: Any) -> str:
    if not str(value or "").strip():
        return ""
    return parse_instrument(value).key


def build_strategy_generation_request(
    *,
    prompt: str,
    asset_type: str,
    existing_code: str = "",
    generation_mode: str = "authoring",
    context: dict | None = None,
    response_mode: str = "full",
) -> str:
    normalized_type = normalize_asset_type(asset_type)
    mode = str(generation_mode or "authoring").strip().lower()
    context = dict(context or {})
    expected_manifest = "portfolio" if normalized_type == "portfolio_strategy" else "cta"
    instrument = _canonical_instrument(context.get("instrument") or context.get("sourceInstrument"))
    timeframe_raw = context.get("timeframe") or context.get("sourceTimeframe")
    timeframe = normalize_frequency(timeframe_raw) if str(timeframe_raw or "").strip() else ""
    intent = resolve_strategy_generation_intent(
        prompt=prompt,
        existing_code=existing_code,
        context=context,
    )
    constraints = {
        "workspace_asset_type": normalized_type,
        "required_manifest_strategy_type": expected_manifest,
        "generation_mode": mode,
        "required_instrument": instrument,
        "required_timeframe": timeframe,
        "current_source_is_truth": bool(str(existing_code or "").strip()),
        "requested_capabilities": list(intent.capabilities),
        "requested_direction_mode": intent.requested_direction_mode,
        "active_factor_ids": list(intent.factor_ids),
        "requested_factor_ids": list(intent.required_factor_ids),
    }
    parts = [
        "# Structured IDE constraints (machine-enforced after generation)",
        json.dumps(constraints, ensure_ascii=False, sort_keys=True),
        "",
        "# User request",
        str(prompt or "").strip(),
    ]
    if existing_code:
        parts.extend([
            "",
            "# Current Strategy API V2 source (source of truth)",
            str(existing_code).strip(),
            "",
            (
                "Return minimal exact replacements under the source edit response contract. "
                "Preserve behavior not explicitly changed by the user."
                if str(response_mode or "full").strip().lower() == "patch"
                else "Return one complete replacement candidate. Preserve behavior not explicitly changed by the user."
            ),
        ])
    else:
        parts.extend(["", "Return one complete new Strategy API V2 candidate."])
    return "\n".join(parts)


def validate_generated_strategy(
    code: str,
    *,
    asset_type: str,
    generation_mode: str = "authoring",
    context: dict | None = None,
    prompt: str = "",
    existing_code: str = "",
    intent: StrategyAIGenerationIntent | None = None,
    compiler: Callable[[str], Any] = inspect_strategy_v2,
):
    normalized_type = normalize_asset_type(asset_type)
    context = dict(context or {})
    program = compiler(code)
    manifest = program.manifest
    expected_type = "portfolio" if normalized_type == "portfolio_strategy" else "cta"
    if manifest.strategy_type != expected_type:
        raise StrategyV2ContractError(
            f"strategyV2.aiManifestTypeMismatch:{expected_type}:{manifest.strategy_type}"
        )

    expected_instrument = _canonical_instrument(
        context.get("instrument") or context.get("sourceInstrument")
    )
    if expected_instrument:
        actual = [item.key for item in manifest.universe.instruments]
        if actual != [expected_instrument]:
            raise StrategyV2ContractError(
                f"strategyV2.aiInstrumentMismatch:{expected_instrument}"
            )

    timeframe_raw = context.get("timeframe") or context.get("sourceTimeframe")
    if str(timeframe_raw or "").strip():
        expected_timeframe = normalize_frequency(timeframe_raw)
        if expected_timeframe not in manifest.frequencies:
            raise StrategyV2ContractError(
                f"strategyV2.aiTimeframeMismatch:{expected_timeframe}"
            )

    mode = str(generation_mode or "authoring").strip().lower()
    if mode == "indicator_conversion" and manifest.strategy_type != "cta":
        raise StrategyV2ContractError("strategyV2.indicatorConversionCtaOnly")
    resolved_intent = intent or resolve_strategy_generation_intent(
        prompt=prompt,
        existing_code=existing_code,
        context=context,
    )
    validate_strategy_ai_semantics(code, manifest, resolved_intent)
    return program
