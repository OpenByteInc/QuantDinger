"""TypeSafe Jev System One adapter."""

from __future__ import annotations

from typing import Any, Callable, Mapping

import requests

from app.services.decision_providers.base import ChoiceAnswer, DecisionProviderError, question_options


DEFAULT_JEV_MODEL = "jev-latest"


class JevDecisionProvider:
    """Send typed choice questions to Jev ``/systemone`` and validate the answers."""

    name = "jev"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = DEFAULT_JEV_MODEL,
        timeout_seconds: Any = 8,
        post: Callable[..., Any] | None = None,
    ) -> None:
        base = str(base_url or "").strip().rstrip("/")
        self.url = base if base.endswith("/systemone") else f"{base}/systemone"
        self.api_key = str(api_key or "")
        self.model = str(model or "").strip() or DEFAULT_JEV_MODEL
        self.timeout_seconds = max(1.0, min(float(timeout_seconds or 8), 30.0))
        self._post = post or requests.post

    def evaluate(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, ChoiceAnswer]:
        response = self._post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "state": state,
                "questions": questions,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers") or payload.get("result") or payload.get("data") or {}
        return {
            name: self.normalize_answer(self._answer(answers, name), question=name, options=question_options(question))
            for name, question in questions.items()
        }

    @staticmethod
    def normalize_answer(answer: Mapping[str, Any], *, question: str, options: set[str]) -> ChoiceAnswer:
        """Validate one Jev typed choice answer against the question's options."""
        choice = str(answer.get("choice") or answer.get("selected") or "").strip().lower()
        if choice not in options:
            raise DecisionProviderError(f"Jev response did not contain a valid {question}")
        raw_probabilities = answer.get("probabilities") or answer.get("probability") or {}
        raw_probabilities = dict(raw_probabilities) if isinstance(raw_probabilities, Mapping) else {}
        if set(raw_probabilities) != options:
            raise DecisionProviderError(f"Jev response probabilities were incomplete for {question}")
        probabilities: dict[str, float] = {}
        for option, value in raw_probabilities.items():
            try:
                probability = float(value)
            except (TypeError, ValueError) as exc:
                raise DecisionProviderError(f"Jev response probability was invalid for {question}") from exc
            if probability < 0 or probability > 1:
                raise DecisionProviderError(f"Jev response probability was out of range for {question}")
            probabilities[str(option)] = probability
        if abs(sum(probabilities.values()) - 1.0) > 0.001:
            raise DecisionProviderError(f"Jev response probabilities did not sum to one for {question}")
        if probabilities.get(choice) != max(probabilities.values()):
            raise DecisionProviderError(f"Jev response choice was not the highest probability for {question}")
        confidence = None
        if "confidence" in answer:
            try:
                confidence = float(answer["confidence"])
            except (TypeError, ValueError) as exc:
                raise DecisionProviderError(f"Jev response confidence was invalid for {question}") from exc
        return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence)

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
