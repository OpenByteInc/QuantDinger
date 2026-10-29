"""Deterministic FX and futures feature normalization."""

from __future__ import annotations

from typing import Any, Mapping


def _refs(evidence: list[Mapping[str, Any]], prefix: str) -> list[str]:
    return [
        str(item.get("evidence_id"))
        for item in evidence
        if str(item.get("metric") or "").startswith(prefix) and item.get("evidence_id")
    ]


def build_forex_features(
    payload: Mapping[str, Any], evidence: list[Mapping[str, Any]]
) -> dict[str, Any]:
    drivers = payload.get("forex_drivers") or {}
    missing = []
    if not drivers.get("policy_rates"):
        missing.append("policy_rates")
    if drivers.get("policy_rate_differential_pct") is None:
        missing.append("policy_rate_differential")
    return {
        "version": "forex_features_v1",
        "market": "Forex",
        "pair": {
            "base_currency": drivers.get("base_currency"),
            "quote_currency": drivers.get("quote_currency"),
        },
        "policy_rates": drivers.get("policy_rates") or {},
        "policy_rate_differential_pct": drivers.get("policy_rate_differential_pct"),
        "missing_capabilities": missing,
        "warnings": ["forex_macro_data_incomplete"] if missing else [],
        "evidence_refs": _refs(evidence, "forex."),
    }


def build_futures_features(
    payload: Mapping[str, Any], evidence: list[Mapping[str, Any]]
) -> dict[str, Any]:
    curve = payload.get("futures_curve") or {}
    positioning = payload.get("futures_positioning") or {}
    missing = []
    if not curve:
        missing.append("continuous_contract_context")
    if not positioning:
        missing.append("cftc_positioning")
    if str(curve.get("scope") or "") == "continuous_front_contract_history_not_expiry_term_structure":
        missing.append("expiry_term_structure")
    return {
        "version": "futures_features_v1",
        "market": "Futures",
        "contract_context": curve,
        "positioning": positioning,
        "missing_capabilities": list(dict.fromkeys(missing)),
        "warnings": ["futures_research_data_incomplete"] if missing else [],
        "evidence_refs": _refs(evidence, "futures."),
    }


__all__ = ["build_forex_features", "build_futures_features"]
