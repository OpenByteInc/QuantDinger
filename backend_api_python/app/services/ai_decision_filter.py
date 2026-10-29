"""Fail-open AI decision filter for live entry orders."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import requests

from app.services.llm import LLMService
from app.utils.db import get_db_connection
from app.utils.logger import get_logger


logger = get_logger(__name__)

ENTRY_ACTIONS = {"open_long", "open_short", "add_long", "add_short", "buy", "sell"}
EXCLUDED_STRATEGY_TYPES = {"grid", "dca", "martingale", "layered_martingale"}

JEV_QUESTIONS = {
    "data_quality": {
        "type": "choice",
        "instructions": "Assess whether the supplied point-in-time evidence is sufficient for a pre-trade decision.",
        "criteria": {
            "sufficient": "Market, signal, portfolio, and execution evidence are current and usable.",
            "partial": "Some evidence is missing or stale, but concrete risk checks remain possible.",
            "insufficient": "The state lacks enough current evidence for a directional or risk judgement.",
        },
    },
    "signal_alignment": {
        "type": "choice",
        "instructions": (
            "Compare the requested action and signal reason with every available timeframe in "
            "context.market_evidence. Treat missing evidence as insufficient rather than conflict."
        ),
        "criteria": {
            "aligned": "Price trend, momentum, and volatility evidence support the requested direction.",
            "mixed": "Evidence is usable but timeframes or indicators disagree without a strong contradiction.",
            "conflict": "Current evidence materially contradicts the requested direction or signal reason.",
            "insufficient": "There is not enough current market evidence to judge alignment.",
        },
    },
    "market_regime": {
        "type": "choice",
        "instructions": "Judge whether the current multi-timeframe market regime is suitable for this requested entry.",
        "criteria": {
            "favorable": "Trend, momentum, volatility, and volume are reasonably supportive of this entry.",
            "neutral": "The regime is mixed or range-bound but does not materially oppose the entry.",
            "adverse": "The regime materially opposes the entry or shows unstable conditions for it.",
            "insufficient": "The supplied market evidence is unavailable or too stale to judge the regime.",
        },
    },
    "risk_check": {
        "type": "choice",
        "instructions": (
            "Assess position sizing, leverage, existing exposure, drawdown, recent realized performance, "
            "protection, and the deterministic order budget."
        ),
        "criteria": {
            "clear": "The new exposure is proportionate and no material account or portfolio risk is visible.",
            "caution": "Risk is elevated but remains within the supplied limits and does not require blocking.",
            "block": "A concrete sizing, leverage, concentration, drawdown, loss-streak, or protection risk requires blocking.",
            "insufficient": "Account evidence is incomplete and no concrete blocking risk can be established.",
        },
    },
    "execution_quality": {
        "type": "choice",
        "instructions": (
            "Assess price freshness, reference-price deviation, order type, market type, protection, "
            "and any supplied execution constraints."
        ),
        "criteria": {
            "clear": "The order can be submitted with current data and no material execution concern.",
            "caution": "Execution conditions are imperfect but do not justify blocking.",
            "block": "Stale or contradictory pricing, invalid protection, or another concrete execution issue requires blocking.",
            "insufficient": "Execution evidence is incomplete and no concrete blocking issue can be established.",
        },
    },
}

JEV_CHECK_OPTIONS = {
    "data_quality": {"sufficient", "partial", "insufficient"},
    "signal_alignment": {"aligned", "mixed", "conflict", "insufficient"},
    "market_regime": {"favorable", "neutral", "adverse", "insufficient"},
    "risk_check": {"clear", "caution", "block", "insufficient"},
    "execution_quality": {"clear", "caution", "block", "insufficient"},
}


@dataclass(frozen=True)
class AIDecisionRequest:
    user_id: int
    source_type: str
    symbol: str
    action: str
    source_id: int = 0
    market_type: str = ""
    order_type: str = "market"
    quantity: float = 0.0
    reference_price: float = 0.0
    leverage: float = 1.0
    reason: str = ""
    strategy_id: int = 0
    strategy_run_id: int = 0
    order_intent_id: int = 0
    strategy_type: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIDecisionResult:
    allowed: bool
    decision: str
    provider: str
    reason: str
    decision_id: str
    model: str = ""
    confidence: float | None = None
    probabilities: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    fallback_reason: str = ""
    billing: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class AIDecisionFilter:
    """Evaluate entry orders with Jev first and an LLM fallback."""

    def evaluate(self, request: AIDecisionRequest, *, enabled: bool) -> AIDecisionResult:
        decision_id = str(uuid.uuid4())
        started = time.perf_counter()
        if not enabled:
            return self._result(True, "skipped", "none", "filter_disabled", decision_id, started)
        action = str(request.action or "").strip().lower()
        strategy_type = str(request.strategy_type or "").strip().lower()
        if action not in ENTRY_ACTIONS:
            result = self._result(True, "skipped", "none", "exit_orders_are_not_filtered", decision_id, started)
            self._persist(request, result)
            return result
        if strategy_type in EXCLUDED_STRATEGY_TYPES:
            result = self._result(True, "skipped", "none", "strategy_type_not_supported", decision_id, started)
            self._persist(request, result)
            return result

        failures: list[str] = []
        jev_config = self._jev_config()
        llm: LLMService | None = None
        llm_configured = False
        if not jev_config["api_key"]:
            try:
                llm = LLMService()
                llm_configured = llm.is_configured()
            except Exception as exc:
                failures.append(f"llm:{self._safe_error(exc)}")
                logger.warning("LLM configuration check failed open: %s", exc)
            if not llm_configured:
                reason = "ai_provider_unavailable" if failures else "ai_not_configured"
                result = self._result(
                    True,
                    "skipped",
                    "none",
                    reason,
                    decision_id,
                    started,
                    fallback_reason="; ".join(failures),
                )
                self._persist(request, result)
                return result

        billing = self._consume_credits(request.user_id, decision_id)
        if not billing.get("accepted"):
            status = str(billing.get("message") or "")
            reason = "billing_insufficient_credits" if status.startswith("insufficient_credits") else "billing_unavailable"
            result = self._result(
                True,
                "skipped",
                "none",
                reason,
                decision_id,
                started,
                fallback_reason=status,
                billing=billing,
            )
            self._persist(request, result)
            return result

        if jev_config["api_key"]:
            try:
                result = replace(
                    self._evaluate_jev(request, decision_id, started, jev_config),
                    billing=billing,
                )
                self._persist(request, result)
                return result
            except Exception as exc:
                failures.append(f"jev:{self._safe_error(exc)}")
                logger.warning("Jev decision failed open: %s", exc)

        if llm is None:
            try:
                llm = LLMService()
                llm_configured = llm.is_configured()
            except Exception as exc:
                failures.append(f"llm:{self._safe_error(exc)}")
                logger.warning("LLM configuration check failed open: %s", exc)
        if llm_configured and llm is not None:
            try:
                result = replace(
                    self._evaluate_llm(request, decision_id, started, failures, service=llm),
                    billing=billing,
                )
                self._persist(request, result)
                return result
            except Exception as exc:
                failures.append(f"llm:{self._safe_error(exc)}")
                logger.warning("LLM decision failed open: %s", exc)

        billing = self._refund_credits(request.user_id, billing)
        reason = "ai_not_configured" if not failures else "ai_provider_unavailable"
        result = self._result(
            True,
            "error_allowed" if failures else "skipped",
            "none",
            reason,
            decision_id,
            started,
            fallback_reason="; ".join(failures),
            billing=billing,
        )
        self._persist(request, result)
        return result

    def _evaluate_jev(
        self,
        request: AIDecisionRequest,
        decision_id: str,
        started: float,
        config: dict[str, str],
    ) -> AIDecisionResult:
        base_url = config["base_url"].strip().rstrip("/")
        url = base_url if base_url.endswith("/systemone") else f"{base_url}/systemone"
        model = config["model"].strip() or "jev-latest"
        timeout = max(1.0, min(float(config["timeout_seconds"] or 8), 30.0))
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "state": self._state_payload(request),
                "questions": JEV_QUESTIONS,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers") or payload.get("result") or payload.get("data") or {}
        results: dict[str, tuple[str, dict[str, float], float | None]] = {}
        checks: list[dict[str, Any]] = []
        for name, options in JEV_CHECK_OPTIONS.items():
            answer = self._answer(answers, name)
            choice, probabilities = self._validate_choice_answer(
                answer,
                question=name,
                options=options,
            )
            confidence = self._confidence(answer, probabilities, choice)
            results[name] = (choice, probabilities, confidence)
            checks.append({
                "name": name,
                "result": choice,
                "confidence": confidence,
                "probabilities": probabilities,
            })

        min_confidence = max(0.0, min(float(config.get("min_confidence") or 0.55), 1.0))
        for name in ("risk_check", "execution_quality"):
            confidence = results[name][2]
            if confidence is None or confidence < min_confidence:
                confidence_text = "missing" if confidence is None else f"{confidence:.3f}"
                raise ValueError(
                    f"Jev confidence below threshold for {name} "
                    f"(confidence={confidence_text}, threshold={min_confidence:.3f})"
                )

        risk_choice, _, risk_confidence = results["risk_check"]
        execution_choice, _, execution_confidence = results["execution_quality"]
        signal_choice, _, signal_confidence = results["signal_alignment"]
        regime_choice, _, regime_confidence = results["market_regime"]
        directional_block = (
            signal_choice == "conflict"
            and regime_choice == "adverse"
            and float(signal_confidence or 0) >= min_confidence
            and float(regime_confidence or 0) >= min_confidence
        )
        allowed = (
            risk_choice != "block"
            and execution_choice != "block"
            and not directional_block
        )
        final_choice = "pass" if allowed else "reject"
        if allowed:
            reason = "jev_entry_approved"
        elif risk_choice == "block":
            reason = "jev_entry_rejected:risk_block"
        elif execution_choice == "block":
            reason = "jev_entry_rejected:execution_block"
        elif directional_block:
            reason = "jev_entry_rejected:signal_conflict"
        else:
            reason = "jev_entry_rejected"
        if risk_choice == "block":
            confidence = risk_confidence
        elif execution_choice == "block":
            confidence = execution_confidence
        elif directional_block:
            confidence = min(float(signal_confidence or 0), float(regime_confidence or 0))
        else:
            confidence = min(float(risk_confidence or 0), float(execution_confidence or 0))
        return self._result(
            allowed,
            final_choice,
            "jev",
            reason,
            decision_id,
            started,
            model=model,
            confidence=confidence,
            checks=checks,
        )

    def _evaluate_llm(
        self,
        request: AIDecisionRequest,
        decision_id: str,
        started: float,
        failures: list[str],
        *,
        service: LLMService | None = None,
    ) -> AIDecisionResult:
        service = service or LLMService()
        model = service.get_default_model()
        content = service.call_llm_api(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative evidence-based pre-trade entry filter. Inspect the supplied "
                        "multi-timeframe market evidence, strategy signal, portfolio risk, recent performance, "
                        "protection, and execution conditions. Missing data alone is not a rejection. Reject only "
                        "for a concrete contradiction or material risk. Return strict JSON with keys decision "
                        "(pass or reject), confidence (0 to 1), reason, and checks (array). Each check must include "
                        "name, result, and concise evidence from the supplied state."
                    ),
                },
                {"role": "user", "content": self._state_text(request)},
            ],
            model=model,
            temperature=0,
            use_fallback=True,
            use_json_mode=True,
            try_alternative_providers=True,
            timeout_seconds=max(1.0, min(float(os.getenv("AI_DECISION_TIMEOUT_SECONDS", "10") or 10), 30.0)),
        )
        payload = self._json_object(content)
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"pass", "reject"}:
            raise ValueError("LLM response did not contain a valid decision")
        confidence = self._bounded_float(payload.get("confidence"))
        checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
        return self._result(
            decision == "pass",
            decision,
            "llm",
            str(payload.get("reason") or "llm_decision"),
            decision_id,
            started,
            model=model,
            confidence=confidence,
            checks=[item for item in checks if isinstance(item, dict)],
            fallback_reason="; ".join(failures),
        )

    @staticmethod
    def _state_payload(request: AIDecisionRequest) -> dict[str, Any]:
        state = {
            "source_type": request.source_type,
            "symbol": request.symbol,
            "action": request.action,
            "market_type": request.market_type,
            "order_type": request.order_type,
            "quantity": request.quantity,
            "reference_price": request.reference_price,
            "notional": request.quantity * request.reference_price,
            "leverage": request.leverage,
            "strategy_type": request.strategy_type,
            "signal_reason": request.reason,
            "context": request.context,
        }
        return json.loads(json.dumps(state, ensure_ascii=False, default=str))

    @staticmethod
    def _state_text(request: AIDecisionRequest) -> str:
        return json.dumps(
            AIDecisionFilter._state_payload(request),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _answer(answers: Any, key: str) -> dict[str, Any]:
        if isinstance(answers, dict):
            value = answers.get(key)
            if isinstance(value, dict):
                return value
            nested = answers.get("answers")
            if isinstance(nested, dict) and isinstance(nested.get(key), dict):
                return nested[key]
        return {}

    @staticmethod
    def _probabilities(answer: dict[str, Any]) -> dict[str, Any]:
        value = answer.get("probabilities") or answer.get("probability") or {}
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _validate_choice_answer(
        cls,
        answer: dict[str, Any],
        *,
        question: str,
        options: set[str],
    ) -> tuple[str, dict[str, float]]:
        choice = str(answer.get("choice") or answer.get("selected") or "").strip().lower()
        if choice not in options:
            raise ValueError(f"Jev response did not contain a valid {question}")
        raw_probabilities = cls._probabilities(answer)
        if set(raw_probabilities) != options:
            raise ValueError(f"Jev response probabilities were incomplete for {question}")
        probabilities: dict[str, float] = {}
        for option, value in raw_probabilities.items():
            try:
                probability = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Jev response probability was invalid for {question}") from exc
            if probability < 0 or probability > 1:
                raise ValueError(f"Jev response probability was out of range for {question}")
            probabilities[str(option)] = probability
        if abs(sum(probabilities.values()) - 1.0) > 0.001:
            raise ValueError(f"Jev response probabilities did not sum to one for {question}")
        maximum = max(probabilities.values())
        if probabilities.get(choice) != maximum:
            raise ValueError(f"Jev response choice was not the highest probability for {question}")
        confidence = cls._bounded_float(answer.get("confidence"))
        if confidence is None:
            raise ValueError(f"Jev response confidence was invalid for {question}")
        return choice, probabilities

    @classmethod
    def _confidence(cls, answer: dict[str, Any], probabilities: dict[str, Any], choice: str) -> float | None:
        direct = cls._bounded_float(answer.get("confidence"))
        if direct is not None:
            return direct
        return cls._bounded_float(probabilities.get(choice))

    @staticmethod
    def _bounded_float(value: Any) -> float | None:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        text = str(value or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:].lstrip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("AI decision response must be a JSON object")
        return parsed

    @staticmethod
    def _jev_config() -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            from app.services.settings.store import read_settings

            values = read_settings()
        except Exception as exc:
            logger.debug("Jev settings file refresh skipped: %s", exc)

        def setting(key: str, default: str = "") -> str:
            if key in values:
                return str(values.get(key) or "").strip()
            return str(os.getenv(key, default) or default).strip()

        return {
            "api_key": setting("JEV_API_KEY"),
            "base_url": setting("JEV_BASE_URL", "https://api.typesafe.ai/v1"),
            "model": setting("JEV_MODEL", "jev-latest"),
            "timeout_seconds": setting("JEV_TIMEOUT_SECONDS", "8"),
            "min_confidence": setting("JEV_MIN_CONFIDENCE", "0.55"),
        }

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return " ".join(str(exc or exc.__class__.__name__).split())[:300]

    @staticmethod
    def _consume_credits(user_id: int, decision_id: str) -> dict[str, Any]:
        reference_id = f"ai-decision:{decision_id}"
        receipt: dict[str, Any] = {
            "feature": "ai_decision_filter",
            "reference_id": reference_id,
            "accepted": False,
            "charged": 0,
            "refunded": 0,
            "status": "unavailable",
            "message": "",
        }
        try:
            from app.services.billing_service import get_billing_service

            billing = get_billing_service()
            cost = max(0, int(billing.get_feature_cost("ai_decision_filter") or 0))
            accepted, message = billing.check_and_consume(
                int(user_id or 0),
                "ai_decision_filter",
                reference_id,
            )
            status = "charged" if message == "consumed" else "free"
            return {
                **receipt,
                "accepted": bool(accepted),
                "cost": cost,
                "charged": cost if accepted and message == "consumed" else 0,
                "status": status if accepted else "rejected",
                "message": str(message or ""),
            }
        except Exception as exc:
            logger.warning("AI decision billing failed open: %s", exc)
            return {**receipt, "message": AIDecisionFilter._safe_error(exc)}

    @staticmethod
    def _refund_credits(user_id: int, receipt: dict[str, Any]) -> dict[str, Any]:
        charged = max(0, int(receipt.get("charged") or 0))
        if charged <= 0:
            return receipt
        try:
            from app.services.billing_service import get_billing_service

            refunded, message = get_billing_service().add_credits(
                user_id=int(user_id or 0),
                amount=charged,
                action="refund",
                remark="ai_decision_provider_unavailable",
                reference_id=str(receipt.get("reference_id") or ""),
            )
            if refunded:
                return {
                    **receipt,
                    "refunded": charged,
                    "status": "refunded",
                    "refund_message": str(message or ""),
                }
            return {**receipt, "status": "refund_failed", "refund_message": str(message or "")}
        except Exception as exc:
            logger.warning("AI decision billing refund failed: %s", exc)
            return {
                **receipt,
                "status": "refund_failed",
                "refund_message": AIDecisionFilter._safe_error(exc),
            }

    @staticmethod
    def _result(
        allowed: bool,
        decision: str,
        provider: str,
        reason: str,
        decision_id: str,
        started: float,
        *,
        model: str = "",
        confidence: float | None = None,
        probabilities: dict[str, Any] | None = None,
        checks: list[dict[str, Any]] | None = None,
        fallback_reason: str = "",
        billing: dict[str, Any] | None = None,
    ) -> AIDecisionResult:
        return AIDecisionResult(
            allowed=allowed,
            decision=decision,
            provider=provider,
            reason=reason,
            decision_id=decision_id,
            model=model,
            confidence=confidence,
            probabilities=probabilities or {},
            checks=checks or [],
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            fallback_reason=fallback_reason,
            billing=billing or {},
        )

    @staticmethod
    def _persist(request: AIDecisionRequest, result: AIDecisionResult) -> None:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'qd_ai_decisions'
                          AND column_name = 'billing_json'
                    ) AS present
                    """
                )
                column_row = cur.fetchone()
                has_billing_column = bool(
                    column_row.get("present") if isinstance(column_row, dict) else column_row and column_row[0]
                )
                values = (
                    result.decision_id,
                    int(request.user_id or 0),
                    str(request.source_type or ""),
                    int(request.source_id or request.strategy_id or 0),
                    int(request.strategy_run_id or 0),
                    int(request.order_intent_id or 0),
                    str(request.symbol or ""),
                    str(request.action or ""),
                    str(request.market_type or ""),
                    result.provider,
                    result.model,
                    result.decision,
                    bool(result.allowed),
                    result.confidence,
                    result.reason,
                    result.fallback_reason,
                    json.dumps(result.probabilities, ensure_ascii=False, default=str),
                    json.dumps(result.checks, ensure_ascii=False, default=str),
                    AIDecisionFilter._state_text(request),
                )
                if has_billing_column:
                    cur.execute(
                        """
                        INSERT INTO qd_ai_decisions
                          (decision_uid, user_id, source_type, source_id, strategy_run_id,
                           order_intent_id, symbol, action, market_type, provider, model,
                           decision, allowed, confidence, reason, fallback_reason,
                           probabilities_json, checks_json, request_snapshot, billing_json,
                           latency_ms, created_at)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (decision_uid) DO NOTHING
                        """,
                        values + (
                            json.dumps(result.billing, ensure_ascii=False, default=str),
                            int(result.latency_ms),
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO qd_ai_decisions
                          (decision_uid, user_id, source_type, source_id, strategy_run_id,
                           order_intent_id, symbol, action, market_type, provider, model,
                           decision, allowed, confidence, reason, fallback_reason,
                           probabilities_json, checks_json, request_snapshot,
                           latency_ms, created_at)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (decision_uid) DO NOTHING
                        """,
                        values + (int(result.latency_ms),),
                    )
                db.commit()
                cur.close()
        except Exception as exc:
            logger.warning("AI decision audit persistence skipped: %s", exc)


def list_ai_decisions(
    *,
    user_id: int,
    source_type: str,
    source_id: int = 0,
    symbol: str = "",
    market_type: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 500))
    clauses = ["user_id = %s", "source_type = %s"]
    params: list[Any] = [int(user_id), str(source_type)]
    if source_id:
        clauses.append("source_id = %s")
        params.append(int(source_id))
    if symbol:
        clauses.append("UPPER(symbol) = UPPER(%s)")
        params.append(str(symbol))
    if market_type:
        clauses.append("LOWER(market_type) = LOWER(%s)")
        params.append(str(market_type))
    params.append(limit)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT decision_uid, source_type, source_id, strategy_run_id, symbol, action,
                   market_type, provider, model, decision, allowed, confidence, reason,
                   fallback_reason, probabilities_json, checks_json,
                   COALESCE(to_jsonb(qd_ai_decisions) -> 'billing_json', '{{}}'::jsonb) AS billing_json,
                   latency_ms, created_at
            FROM qd_ai_decisions
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
        cur.close()
    return [dict(row) for row in rows if isinstance(row, dict)]
