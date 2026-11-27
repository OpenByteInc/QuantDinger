"""Provider contract for typed-choice AI decisions.

A decision provider receives a point-in-time ``state`` and a mapping of typed
``choice`` questions, and returns one normalized answer per question. Providers
own transport, authentication, and response normalization only. Questions,
confidence thresholds, decision rules, billing, fallback, and audit records stay
in the calling services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class DecisionProviderError(ValueError):
    """Raised when a provider response cannot be normalized into valid answers."""


@dataclass(frozen=True)
class ChoiceAnswer:
    """One validated answer to a typed choice question.

    ``probabilities`` covers every option of the question, sums to one, and
    ``choice`` holds the highest probability. ``confidence`` is the provider's
    own confidence value, unclamped, or ``None`` when the provider omitted it.
    Callers decide how to treat a missing confidence and which threshold
    applies, because confidence semantics differ by provider.
    """

    choice: str
    probabilities: dict[str, float]
    confidence: float | None


class DecisionProvider(Protocol):
    """Evaluate a state against typed choice questions."""

    name: str
    model: str

    def evaluate(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, ChoiceAnswer]:
        """Return one answer per question, or raise on transport or validation failure."""
        ...


def question_options(question: Mapping[str, Any]) -> set[str]:
    """Return the allowed options of a typed choice question."""
    criteria = question.get("criteria")
    return {str(option) for option in criteria} if isinstance(criteria, Mapping) else set()
