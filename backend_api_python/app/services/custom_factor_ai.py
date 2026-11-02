"""Generate validated formula candidates without saving or executing model code."""

from __future__ import annotations

import json
import re
from uuid import uuid4

from app.services.custom_factors import CustomFactorError, analyze_custom_factor
from app.services.factors import list_factor_catalog
from app.utils.logger import get_logger

logger = get_logger(__name__)
FEATURE = "ai_code_gen"
CATEGORIES = {"momentum", "trend", "volatility", "risk", "liquidity", "custom"}
DIRECTIONS = {"neutral", "higher_is_bullish", "lower_is_bullish"}
LANGUAGES = {"en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "de-DE", "fr-FR", "ru-RU", "ar-SA", "th-TH", "vi-VN"}


class FactorGenerationError(Exception):
    def __init__(self, code: str, status: int = 400, data: dict | None = None):
        super().__init__(code)
        self.code, self.status, self.data = code, status, data or {}


def build_factor_prompt() -> str:
    catalog = list_factor_catalog(include_feature_families=True)
    functions, qlib_ids = [], []
    for item in catalog:
        factor_id = item["factor_id"]
        if factor_id.startswith("qlib:"):
            qlib_ids.append(factor_id)
        else:
            functions.append({
                "id": factor_id,
                "description": item.get("description", ""),
                "defaults": item.get("default_params", {}),
                "parameters": item.get("parameter_schema", {}),
                "fields": item.get("required_fields", []),
            })
    return """You author QuantDinger custom formula factors, NOT Python, Pine or trading strategies.
Return ONLY one JSON object with exactly these keys:
name (nonempty, <=120 characters), description (<=2000 characters), category
(momentum/trend/volatility/risk/liquidity/custom), directionHint
(higher_is_bullish/lower_is_bullish/neutral), expression, defaultParams, explanation (<=3000 characters).
Write human-readable fields in the requested locale. Keep API identifiers unchanged.
Preserve the user's signal intent. If it cannot be expressed with the contract/catalog,
return {"unsupported":true} instead of substituting an unrelated factor or approximating silently.
Never claim profitability, verified market data, or a completed backtest.

Contract:
- One expression, <=2000 characters and <=120 AST nodes. No statements, assignments,
  imports, loops, attributes, indexing, files, networking, arrays, orders or drawing.
- Raw latest-bar scalar fields: open, high, low, close, volume, amount, market_cap,
  net_income, shareholder_equity, revenue_growth, total_debt, free_cash_flow,
  pe_ratio, pb_ratio, return_on_equity, debt_to_equity. Fundamentals need point-in-time data.
- Operators: + - * / % **, comparisons, and/or/not, a if condition else b.
- Helpers: abs(x), min(a,b,...), max(a,b,...), log(x), log1p(x), sqrt(x), clip(x,low,high).
- History is accessible ONLY through registered factors. No close[1], pandas or rolling().
- Native call: momentum(period=lookback). Library call: factor("talib:RSI", timeperiod=period).
  Use named arguments. factor() accepts exactly one positional factor ID, never symbol.
- defaultParams is a JSON object of finite numbers or booleans ONLY. Names must be valid
  identifiers and cannot shadow fields/functions. Refer directly to lookback, NOT params.get().
  Call argument values must be literals or these named parameters, not computed expressions.
- Qlib windows are encoded in IDs; Qlib calls accept NO keywords.
  TA-Lib multi-output selection uses output="..." from the declared output enum.
- No custom:<id> references. Use only catalog IDs/parameters below. Do not invent functions.
- Result is one finite scalar; booleans become 0/1. Protect zero denominators and log/sqrt domains.
- Analysis validates structure and estimates warmup, NOT data availability or semantic accuracy.
Example: {"name":"Risk-adjusted momentum","description":"Momentum relative to volatility",
"category":"momentum","directionHint":"higher_is_bullish",
"expression":"momentum(period=lookback) / max(realized_volatility(period=vol_window), floor)",
"defaultParams":{"lookback":20,"vol_window":20,"floor":0.0001},
"explanation":"Higher values favor positive momentum with lower volatility. Test on historical data."}

Available native/TA-Lib factors:
""" + json.dumps(functions, ensure_ascii=False, separators=(",", ":")) + "\nQlib IDs (no parameters):\n" + ",".join(qlib_ids)


def parse_factor_candidate(content: str) -> dict:
    if not isinstance(content, str) or len(content) > 24000:
        raise FactorGenerationError("factor.aiInvalid")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    try:
        candidate = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise FactorGenerationError("factor.aiInvalid") from exc
    if not isinstance(candidate, dict):
        raise FactorGenerationError("factor.aiInvalid")
    if candidate.get("unsupported") is True:
        raise FactorGenerationError("factor.aiUnsupported")
    required = {"name", "description", "category", "directionHint", "expression", "defaultParams", "explanation"}
    if set(candidate) != required:
        raise FactorGenerationError("factor.aiInvalid")
    for key, limit in (("name", 120), ("description", 2000), ("expression", 2000), ("explanation", 3000)):
        value = candidate[key]
        if not isinstance(value, str) or len(value) > limit or (key in {"name", "expression"} and not value.strip()):
            raise FactorGenerationError("factor.aiInvalid")
        candidate[key] = value.strip()
    if (
        not isinstance(candidate["category"], str) or candidate["category"] not in CATEGORIES
        or not isinstance(candidate["directionHint"], str) or candidate["directionHint"] not in DIRECTIONS
    ):
        raise FactorGenerationError("factor.aiInvalid")
    params = candidate["defaultParams"]
    if not isinstance(params, dict) or len(params) > 40:
        raise FactorGenerationError("factor.aiInvalid")
    analysis = analyze_custom_factor(candidate["expression"], params)
    candidate["validation"] = {
        "requiredFields": list(analysis.required_fields),
        "dependencies": list(analysis.dependencies),
        "warmupBars": analysis.warmup_bars,
    }
    return candidate


def generate_factor_candidate(user_id: int, payload: dict, *, llm=None, billing=None) -> dict:
    if not isinstance(payload, dict):
        raise FactorGenerationError("factor.aiPromptRequired")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise FactorGenerationError("factor.aiPromptRequired")
    if len(prompt) > 4000:
        raise FactorGenerationError("factor.aiPromptTooLong")
    locale = payload.get("locale")
    locale = locale if isinstance(locale, str) and locale in LANGUAGES else "en-US"
    if llm is None:
        from app.services.llm import LLMService
        llm = LLMService()
    if not llm.is_configured():
        raise FactorGenerationError("factor.aiNotConfigured", 503)
    system_prompt = build_factor_prompt()
    if billing is None:
        from app.services.billing_service import get_billing_service
        billing = get_billing_service()
    cost = int(billing.get_feature_cost(FEATURE) or 0)
    accepted, status = billing.check_and_consume(
        user_id=int(user_id), feature=FEATURE,
        reference_id=f"factor_generate_{user_id}_{uuid4().hex}",
    )
    if not accepted:
        if str(status).startswith("insufficient_credits:"):
            raise FactorGenerationError("factor.aiInsufficientCredits", 402)
        raise FactorGenerationError("factor.aiBillingUnavailable", 503)
    billing_meta = {"feature": FEATURE, "credits_charged": cost if status == "consumed" else 0}
    try:
        billing_meta["remaining_credits"] = float(billing.get_user_credits(int(user_id)))
    except Exception:
        logger.warning("factor generation balance refresh unavailable")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({"locale": locale, "idea": prompt.strip()}, ensure_ascii=False)},
    ]
    try:
        for attempt in range(2):
            content = llm.call_llm_api(
                messages=messages, model=llm.get_code_generation_model(),
                temperature=0.2, use_json_mode=True,
            )
            try:
                candidate = parse_factor_candidate(content)
                return {"candidate": candidate, "billing": billing_meta}
            except (CustomFactorError, FactorGenerationError) as exc:
                if getattr(exc, "code", "") == "factor.aiUnsupported":
                    raise
                if attempt:
                    raise FactorGenerationError("factor.aiInvalid") from exc
                messages.extend([
                    {"role": "assistant", "content": str(content or "")[:24000]},
                    {"role": "user", "content": "Repair the JSON candidate without changing the idea. Contract validation failed: " + exc.code},
                ])
    except FactorGenerationError as exc:
        exc.data = {"billing": billing_meta}
        raise
    except Exception as exc:
        logger.warning("factor AI generation failed: %s", type(exc).__name__)
        raise FactorGenerationError("factor.aiFailed", 502, {"billing": billing_meta}) from exc
    raise FactorGenerationError("factor.aiInvalid", data={"billing": billing_meta})
