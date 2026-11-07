"""Validated contracts for strategy evolution studies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_METHODS = frozenset({"random", "grid", "tpe"})


@dataclass(frozen=True)
class SearchParameter:
    name: str
    kind: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    default: Any = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SearchParameter":
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("strategyEvolution.parameterNameRequired")
        kind = str(payload.get("type") or payload.get("kind") or "number").lower()
        kind = {
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "select": "enum",
            "text": "string",
        }.get(kind, kind)
        if kind not in {"integer", "number", "percent", "boolean", "enum", "string"}:
            raise ValueError("strategyEvolution.parameterTypeUnsupported")
        choices = tuple(payload.get("choices") or payload.get("options") or payload.get("values") or ())
        minimum = _optional_float(payload.get("min"))
        maximum = _optional_float(payload.get("max"))
        step = _optional_float(payload.get("step"))
        if kind in {"integer", "number", "percent"}:
            if minimum is None or maximum is None or minimum > maximum:
                raise ValueError("strategyEvolution.parameterRangeInvalid")
            if step is not None and step <= 0:
                raise ValueError("strategyEvolution.parameterStepInvalid")
        if kind in {"enum", "string"} and not choices:
            raise ValueError("strategyEvolution.parameterChoicesRequired")
        return cls(
            name=name,
            kind=kind,
            minimum=minimum,
            maximum=maximum,
            step=step,
            choices=choices,
            default=payload.get("default"),
        )


@dataclass(frozen=True)
class EvolutionConfig:
    method: str = "tpe"
    trials: int = 64
    folds: int = 1
    train_ratio: float = 0.7
    blind_ratio: float = 0.15
    embargo_bars: int = 0
    seed: int = 42
    auto_prune: bool = False
    monte_carlo_paths: int = 1000
    block_size: int = 5
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0)
    top_candidates: int = 5
    weights: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "EvolutionConfig":
        values = dict(payload or {})
        method = str(values.get("method") or "tpe").lower()
        if method not in SUPPORTED_METHODS:
            raise ValueError("strategyEvolution.methodUnsupported")
        default_trials = 64
        minimum_trials = 24 if method == "tpe" else 1
        maximum_trials = 5000 if method == "grid" else 500
        trials = max(
            minimum_trials,
            min(maximum_trials, int(values.get("trials") or default_trials)),
        )
        weights = _normalized_weights(values.get("weights"))
        multiplier_values = values.get("costMultipliers") or (1, 1.5, 2, 3)
        multipliers = tuple(sorted({max(0.0, min(10.0, float(item))) for item in multiplier_values}))
        return cls(
            method=method,
            trials=trials,
            folds=1,
            train_ratio=max(0.55, min(0.85, float(values.get("trainRatio") or 0.7))),
            blind_ratio=max(0.0, min(0.3, float(values.get("blindRatio") if values.get("blindRatio") is not None else 0.15))),
            embargo_bars=max(
                0,
                min(
                    10_000,
                    int(
                        values.get("embargoBars")
                        if values.get("embargoBars") is not None
                        else values.get("embargoDays")
                        if values.get("embargoDays") is not None
                        else 0
                    ),
                ),
            ),
            seed=int(values.get("seed") or 42),
            auto_prune=bool(values.get("autoPrune", False)),
            monte_carlo_paths=max(100, min(5000, int(values.get("monteCarloPaths") or 1000))),
            block_size=max(1, min(60, int(values.get("blockSize") or 5))),
            cost_multipliers=multipliers or (1.0,),
            top_candidates=max(1, min(10, int(values.get("topCandidates") or 5))),
            weights=weights,
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _normalized_weights(raw: Any) -> dict[str, float]:
    defaults = {"return": 0.25, "sharpe": 0.3, "drawdown": 0.2, "stability": 0.15, "decay": 0.1}
    if not isinstance(raw, dict):
        return defaults
    values = {key: max(0.0, float(raw.get(key, default))) for key, default in defaults.items()}
    total = sum(values.values())
    if total <= 0:
        return defaults
    return {key: value / total for key, value in values.items()}
