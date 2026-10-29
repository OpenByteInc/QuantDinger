"""Auditable, deterministic research calculations over collected evidence."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .workflows import resolve_workflow


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _closes(payload: Mapping[str, Any]) -> list[float]:
    values: list[float] = []
    for row in payload.get("kline") or []:
        if not isinstance(row, Mapping):
            continue
        value = _number(row.get("close"))
        if value is not None and value > 0:
            values.append(value)
    return values


def _returns(values: list[float]) -> list[float]:
    return [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]


def _performance(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = _closes(payload)
    audit: list[dict[str, Any]] = []
    if len(values) < 2:
        return {}, audit
    returns = _returns(values)
    metrics: dict[str, Any] = {
        "sample_bars": len(values),
        "period_return_pct": round((values[-1] / values[0] - 1) * 100, 4),
    }
    audit.append({
        "metric": "period_return_pct",
        "formula": "(last_close / first_close - 1) * 100",
        "inputs": {"first_close": values[0], "last_close": values[-1]},
    })
    if returns:
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / max(1, len(returns) - 1)
        metrics["realized_volatility_per_bar_pct"] = round(math.sqrt(variance) * 100, 4)
        audit.append({
            "metric": "realized_volatility_per_bar_pct",
            "formula": "sample_std(close_to_close_returns) * 100",
            "inputs": {"return_count": len(returns)},
        })
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    metrics["max_drawdown_pct"] = round(max_drawdown * 100, 4)
    audit.append({
        "metric": "max_drawdown_pct",
        "formula": "min(close / running_peak - 1) * 100",
        "inputs": {"bar_count": len(values)},
    })
    return metrics, audit


def _available_capabilities(market: str, payload: Mapping[str, Any]) -> set[str]:
    available = {"price_history"} if payload.get("kline") else set()
    if payload.get("fundamental"):
        available.add("financials")
    mapping = {
        "sec_filings": "filings",
        "hkex_announcements": "issuer_disclosures",
        "analyst_expectations": "analyst_expectations",
        "options": "options_surface",
        "short_interest": "short_interest",
        "short_volume": "short_volume",
        "ownership": "ownership",
        "earnings_transcript": "earnings_transcript",
        "southbound_flow": "southbound_holdings",
        "short_selling": "short_selling",
        "ccass": "ccass",
        "crypto_factors": "spot_market",
        "futures_curve": "term_structure",
        "futures_positioning": "cftc_positioning",
        "forex_drivers": "pair_identity",
    }
    for key, capability in mapping.items():
        if payload.get(key):
            available.add(capability)
    if market == "Futures" and payload.get("symbol"):
        available.add("contract_identity")
    return available


def _brief_claim(
    claims: Sequence[Mapping[str, Any]],
    *kinds: str,
) -> dict[str, Any] | None:
    for kind in kinds:
        for claim in claims:
            if str(claim.get("kind") or "") != kind:
                continue
            text = str(claim.get("text") or "").strip()
            if text:
                return {
                    "kind": kind,
                    "text": text,
                    "evidence_refs": list(dict.fromkeys(
                        str(item) for item in claim.get("evidence_refs") or [] if str(item)
                    )),
                }
    return None


def _research_brief(
    *,
    summary: str,
    claims: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    risk_plan: Mapping[str, Any],
) -> dict[str, Any]:
    core_thesis = _brief_claim(claims, "thesis", "catalyst")
    if core_thesis is None and summary.strip():
        core_thesis = {"kind": "summary", "text": summary.strip(), "evidence_refs": []}
    variant_view = _brief_claim(claims, "counter_argument", "risk")
    catalysts = [
        {
            "kind": "catalyst",
            "text": str(claim.get("text") or "").strip(),
            "evidence_refs": list(dict.fromkeys(
                str(item) for item in claim.get("evidence_refs") or [] if str(item)
            )),
        }
        for claim in claims
        if str(claim.get("kind") or "") == "catalyst" and str(claim.get("text") or "").strip()
    ]
    watch_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for scenario in scenarios:
        text = str(scenario.get("trigger") or "").strip()
        key = (str(scenario.get("case") or "base"), text)
        if text and key not in seen:
            seen.add(key)
            watch_items.append({
                "kind": "scenario",
                "case": key[0],
                "text": text,
                "invalidation": scenario.get("invalidation"),
                "evidence_refs": list(dict.fromkeys(
                    str(item) for item in scenario.get("evidence_refs") or [] if str(item)
                )),
            })
    for condition in risk_plan.get("invalidation_conditions") or []:
        text = str(condition or "").strip()
        key = ("invalidation", text)
        if text and key not in seen:
            seen.add(key)
            watch_items.append({
                "kind": "invalidation",
                "case": None,
                "text": text,
                "invalidation": None,
                "evidence_refs": [],
            })
    balance = {kind: 0 for kind in ("thesis", "catalyst", "risk", "counter_argument")}
    for claim in claims:
        kind = str(claim.get("kind") or "")
        if kind in balance:
            balance[kind] += 1
    return {
        "core_thesis": core_thesis,
        "variant_view": variant_view,
        "catalysts": catalysts,
        "watch_items": watch_items[:8],
        "claim_balance": balance,
    }


def build_research_intelligence(
    payload: Mapping[str, Any],
    *,
    quality: Mapping[str, Any] | None = None,
    summary: str = "",
    claims: Sequence[Mapping[str, Any]] = (),
    scenarios: Sequence[Mapping[str, Any]] = (),
    risk_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build research metadata, readiness and auditable calculations."""
    market = str(payload.get("market") or "")
    workflow = resolve_workflow(market, str(payload.get("research_mode") or "research"))
    available = _available_capabilities(market, payload)
    required = list(workflow["required_capabilities"])
    optional = list(workflow["optional_capabilities"])
    missing_required = [item for item in required if item not in available]
    missing_optional = [item for item in optional if item not in available]
    performance, audit = _performance(payload)
    quality_score = _number((quality or {}).get("quality_score")) or 0.0
    if missing_required or quality_score < 0.45:
        posture = "insufficient"
    elif missing_optional or quality_score < 0.75:
        posture = "conditional"
    else:
        posture = "decision_ready"
    return {
        **workflow,
        "readiness_posture": posture,
        "available_capabilities": sorted(available),
        "missing_required_capabilities": missing_required,
        "missing_optional_capabilities": missing_optional,
        "performance": performance,
        "calculation_audit": audit,
        "data_source_status": (payload.get("_meta") or {}).get("research_provider_status") or {},
        "research_brief": _research_brief(
            summary=summary,
            claims=claims,
            scenarios=scenarios,
            risk_plan=risk_plan or {},
        ),
    }


__all__ = ["build_research_intelligence"]
