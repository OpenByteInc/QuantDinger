"""Indicator code-candidate generation outside the HTTP route."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Mapping

from app.services.ai_code_edits import (
    CODE_EDIT_SYSTEM_SUFFIX,
    CodeEditError,
    apply_model_code_edits,
    code_edit_user_instruction,
)
from app.services.ai_copilot_context import fit_messages_to_budget


def _context_block(context: Mapping[str, Any]) -> str:
    lines: List[str] = []
    market = str(context.get("market") or "").strip()
    symbol = str(context.get("symbol") or "").strip()
    timeframe = str(context.get("timeframe") or "").strip()
    indicator_name = str(context.get("indicatorName") or "").strip()
    indicator_description = str(context.get("indicatorDescription") or "").strip()
    param_defaults = context.get("paramDefaults")
    if market or symbol or timeframe:
        lines.append(
            f"- Current chart: market={market or 'unknown'}, "
            f"symbol={symbol or 'unknown'}, timeframe={timeframe or 'unknown'}"
        )
    if indicator_name:
        lines.append(f"- Current indicator name: {indicator_name}")
    if indicator_description:
        lines.append(f"- Current indicator description: {indicator_description[:300]}")
    if isinstance(param_defaults, dict) and param_defaults:
        lines.append(
            "- Existing @param defaults: "
            + json.dumps(param_defaults, ensure_ascii=False, default=str)[:1200]
        )
    if not lines:
        return ""
    return (
        "\n\n# Current IDE context (for intent only; do not hardcode "
        "symbol/timeframe/account settings)\n"
        + "\n".join(lines)
    )


def _strip_code_fences(content: Any) -> str:
    text = str(content or "").strip()
    if text.startswith("```python"):
        text = text[9:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def generate_indicator_code_candidate(
    *,
    prompt: str,
    existing: str,
    context: Mapping[str, Any],
    system_prompt: str,
    workspace_context: Mapping[str, Any] | None,
    template_factory: Callable[[], str],
    logger: Any,
) -> tuple[str, Dict[str, Any]]:
    """Generate a full candidate or apply bounded model edit operations."""
    from app.services.llm import LLMService

    llm = LLMService()
    current_provider = llm.provider
    current_model = llm.get_code_generation_model()
    current_api_key = llm.get_api_key()
    base_url = llm.get_base_url()
    logger.info(
        "AI Code Generation - Provider: %s, Model: %s, Base URL: %s, "
        "API Key configured: %s",
        current_provider.value,
        current_model,
        base_url,
        bool(current_api_key),
    )

    if not current_api_key:
        logger.warning("No LLM API key configured, using template code")
        return template_factory(), {"executor": "template", "operation": "generate_candidate"}

    context_text = _context_block(context)
    user_prompt = prompt + context_text
    use_patch_response = bool(existing.strip())
    if existing:
        user_prompt = (
            "# Existing QuantDinger indicator code (source of truth):\n\n```python\n"
            + existing.strip()
            + "\n```\n\n# Change request:\n\n"
            + prompt
            + context_text
            + "\n\nPreserve my_indicator_name/description, df = df.copy(), declared @param "
            "values read via params.get(...), output dict with layers defaulting to [], and list "
            "lengths == len(df). Do not emit execution columns, # @strategy, risk, sizing, "
            "timeframe, or trade-direction settings. For visual signals, output one-bar event "
            "markers by default; do not repeat markers on every bar while a condition remains "
            "true. For every declared @param, the params.get fallback default must exactly "
            "match the declared default. "
            + code_edit_user_instruction()
        )

    generation_system_prompt = (
        f"{system_prompt}\n\n{CODE_EDIT_SYSTEM_SUFFIX}"
        if use_patch_response
        else system_prompt
    )
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": generation_system_prompt}
    ]
    if workspace_context:
        summary_text = json.dumps(
            workspace_context.get("summary") or {},
            ensure_ascii=False,
            default=str,
        )
        messages.append({
            "role": "system",
            "content": (
                "# Indicator authoring memory\n"
                "Use this bounded memory only to preserve the user's intent and prior constraints. "
                "The current code below is always the source of truth.\n"
                + summary_text[:5000]
            ),
        })
        for item in workspace_context.get("recent_messages") or []:
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            if role == "assistant" and str(item.get("message_type") or "") == "discussion":
                continue
            content_text = str(item.get("content") or "").strip()
            if content_text:
                messages.append({"role": role, "content": content_text[:2400]})
    messages.append({"role": "user", "content": user_prompt})
    messages, budget_debug = fit_messages_to_budget(messages, max_tokens=48000)
    logger.info(
        "indicator ai context budget=%s",
        json.dumps(budget_debug, ensure_ascii=False, default=str),
    )

    temperature = float(os.getenv("OPENROUTER_TEMPERATURE", "0.7") or 0.7)
    content = llm.call_llm_api(
        messages=messages,
        model=current_model,
        temperature=0.2 if use_patch_response else temperature,
        use_json_mode=use_patch_response,
    )
    if use_patch_response:
        try:
            code, plan = apply_model_code_edits(existing, content)
        except CodeEditError as exc:
            logger.warning("indicator model patch rejected, retrying full candidate: %s", exc)
            fallback_prompt = (
                "# Existing QuantDinger indicator code (source of truth):\n\n```python\n"
                + existing.strip()
                + "\n```\n\n# Change request:\n\n"
                + prompt
                + context_text
                + "\n\nReturn one complete replacement indicator source. Preserve behavior not "
                "explicitly changed. Python only, without markdown or prose."
            )
            content = llm.call_llm_api(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": fallback_prompt},
                ],
                model=current_model,
                temperature=0.25,
                use_json_mode=False,
            )
            plan = {
                "executor": "model_full_fallback",
                "operation": "generate_candidate",
                "patch_error": str(exc),
            }
            code = _strip_code_fences(content) or template_factory()
    else:
        plan = {"executor": "model", "operation": "generate_candidate"}
        code = _strip_code_fences(content) or template_factory()

    # A model-level fallback is otherwise only visible in the provider dashboard.
    plan["model"] = llm.last_model_used
    plan["provider"] = llm.last_provider_used
    plan["model_fallback"] = llm.model_fallback_used
    return code, plan
