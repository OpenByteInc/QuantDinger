"""
Normalize experiment override maps for the Indicator IDE client.

Structured tuning stores indicator sweeps as flat keys such as
``indicator_params.sma_short``. The SPA apply-params flow expects a nested
``indicatorParams`` object on each candidate's ``overrides`` (see
``applyExperimentOverridesToCode`` in QuantDinger-Vue).
"""

from __future__ import annotations

from typing import Any, Dict


def enrich_experiment_overrides(overrides: Dict[str, Any] | None) -> Dict[str, Any]:
    """
    Add ``indicatorParams`` / ``riskParams`` nests expected by the frontend.

    Flat keys are preserved for backward compatibility.
    """
    if not overrides or not isinstance(overrides, dict):
        return overrides or {}

    out = dict(overrides)
    ind: Dict[str, Any] = dict(out.get("indicatorParams") or {})
    risk: Dict[str, Any] = dict(out.get("riskParams") or {})

    for key, value in list(out.items()):
        k = str(key or "")
        if k.startswith("indicator_params."):
            name = k.split(".", 1)[1]
            if name:
                ind[name] = value
        elif k.startswith("strategy_config.risk.") or k.startswith("strategyConfig.risk."):
            sub = k.split(".")[-1]
            if sub in ("stopLossPct", "takeProfitPct", "trailingEnabled", "trailingStopPct", "trailingActivationPct"):
                risk[sub] = value
            elif sub == "trailing" and isinstance(value, dict):
                risk.setdefault("trailingStop", value)
        elif k.startswith("strategy_config.position.") or k.startswith("strategyConfig.position."):
            sub = k.split(".")[-1]
            if sub == "entryPct":
                risk["entryPct"] = value

    if ind:
        out["indicatorParams"] = ind
    if risk:
        out["riskParams"] = risk
    return out


def enrich_experiment_candidate(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Enrich overrides on a ranked strategy / bestStrategyOutput candidate."""
    if not candidate or not isinstance(candidate, dict):
        return candidate
    c = dict(candidate)
    if c.get("overrides"):
        c["overrides"] = enrich_experiment_overrides(c.get("overrides"))
    return c
