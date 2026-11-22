"""Deterministic market-specific equity features.

LLMs may explain these features later, but they never create the underlying
numbers.  Every feature keeps references to the evidence observations used to
derive it.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.research.fundamentals import finite_number, normalize_fundamentals


_COMMON_METRICS = (
    "pe_ratio",
    "pb_ratio",
    "market_cap",
    "roe",
    "revenue_growth",
    "profit_margin",
    "debt_to_equity",
    "current_ratio",
    "free_cash_flow",
)


def _safe_float(value: Any) -> float | None:
    return finite_number(value)


def _evidence_map(evidence: list[Mapping[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in evidence:
        metric = str(item.get("metric") or "")
        evidence_id = str(item.get("evidence_id") or "")
        if metric and evidence_id and metric not in result:
            result[metric] = evidence_id
    return result


def _metric_feature(
    fundamental: Mapping[str, Any],
    evidence_ids: Mapping[str, str],
    key: str,
) -> dict[str, Any] | None:
    value = _safe_float(fundamental.get(key))
    if value is None:
        return None
    metadata = (fundamental.get("field_metadata") or {}).get(key) or {}
    return {
        "value": value,
        "unit": metadata.get("unit"),
        "period_type": metadata.get("period_type"),
        "period_end": metadata.get("period_end"),
        "source": metadata.get("source") or fundamental.get("source"),
        "evidence_refs": [evidence_ids[key]] if evidence_ids.get(key) else [],
    }


def build_equity_features(
    market: str,
    payload: Mapping[str, Any],
    evidence: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build common and market-specific equity feature coverage.

    Missing professional capabilities are explicit.  They are not silently
    converted into neutral scores, which would overstate report completeness.
    """
    fundamental = normalize_fundamentals(payload.get("fundamental"))
    evidence_ids = _evidence_map(evidence)
    metrics = {
        key: feature
        for key in _COMMON_METRICS
        if (feature := _metric_feature(fundamental, evidence_ids, key)) is not None
    }
    identity = fundamental.get("identity") or {}
    warnings: list[str] = []
    if identity and identity.get("verified") is False:
        warnings.append("instrument_identity_unverified")

    statements = fundamental.get("financial_statements") or {}
    latest_quarter = statements.get("latest_quarter") or {}
    ttm = statements.get("ttm") or {}
    latest_annual = statements.get("latest_annual") or {}
    latest_report = statements.get("latest_report") or {}
    applicable: dict[str, bool] = {}

    if market == "USStock":
        market_specific = {
            "filings": payload.get("sec_filings") or [],
            "analyst_expectations": payload.get("analyst_expectations") or {},
            "options": payload.get("options") or {},
            "short_interest": payload.get("short_interest") or {},
            "insider_activity": payload.get("insider_activity") or {},
        }
        missing = [
            key
            for key, value in market_specific.items()
            if not value
        ]
    elif market == "HKStock":
        security_profile = payload.get("hk_security_profile") or {}
        # A/H premium is meaningful only for an H-share with a corresponding
        # mainland listing. Treating it as mandatory for Tencent and every
        # other non-H-share incorrectly depresses report coverage.
        ah_premium_applicable = bool(
            security_profile.get("is_h_share")
            or payload.get("ah_pair")
            or payload.get("ah_premium")
        )
        market_specific = {
            "hkex_announcements": payload.get("hkex_announcements") or [],
            "southbound_flow": payload.get("southbound_flow") or {},
            "short_selling": payload.get("short_selling") or {},
            "ccass": payload.get("ccass") or {},
            "ah_premium": payload.get("ah_premium") or {},
            "analyst_expectations": payload.get("analyst_expectations") or {},
        }
        applicable = {
            "hkex_announcements": True,
            "southbound_flow": bool(
                security_profile.get("southbound_eligible_sh")
                or security_profile.get("southbound_eligible_sz")
                or not security_profile
            ),
            "short_selling": security_profile.get("short_selling_eligible") is not False,
            "ccass": True,
            "ah_premium": ah_premium_applicable,
            "analyst_expectations": True,
        }
        missing = [
            key for key, value in market_specific.items()
            if applicable.get(key, True) and not value
        ]
    elif market == "CNStock":
        market_specific = {
            "company_profile": payload.get("company") or {},
            "financial_statements": statements,
            "exchange_disclosures": payload.get("exchange_disclosures") or [],
            "northbound_flow": payload.get("northbound_flow") or {},
            "margin_financing": payload.get("margin_financing") or {},
            "analyst_expectations": payload.get("analyst_expectations") or {},
            "cn_macro": payload.get("cn_macro") or {},
            "company_news": [
                item for item in payload.get("news") or []
                if isinstance(item, Mapping) and item.get("asset_relevance") in {"direct", "material"}
            ],
        }
        missing = [
            key for key, value in market_specific.items()
            if not value
        ]
    else:
        raise ValueError(f"unsupported equity market: {market}")

    if not latest_quarter and not latest_annual and not latest_report and not ttm:
        warnings.append("financial_statements_unavailable")
    if missing:
        warnings.append("market_specific_data_incomplete")

    provider_status = (payload.get("_meta") or {}).get("research_provider_status") or (payload.get("_meta") or {}).get(
        {"USStock": "us_provider_status", "HKStock": "hk_provider_status", "CNStock": "cn_provider_status"}[market]
    ) or {}
    attempted = set(provider_status.get("attempted") or [])
    errors = provider_status.get("errors") or {}
    statuses = {}
    for key, value in market_specific.items():
        provider_key = "sec_filings" if key == "filings" else key
        statuses[key] = (
            "not_applicable" if not applicable.get(key, True) else
            "available" if value else
            "fetch_failed" if errors.get(provider_key) else
            "unavailable" if provider_key in attempted or key in {"company_profile", "financial_statements", "company_news"} else
            "not_integrated"
        )
    return {
        "version": "equity_features_v1",
        "market": market,
        "identity": identity,
        "valuation_and_quality": metrics,
        "financial_periods": {
            "latest_quarter": latest_quarter,
            "ttm": ttm,
            "latest_annual": latest_annual,
            "latest_report": latest_report,
        },
        "market_specific": market_specific,
        "supplementary": {
            key: payload.get(key) or {}
            for key in ("short_volume", "ownership", "earnings_transcript")
            if payload.get(key)
        } if market == "USStock" else {},
        "applicability": applicable,
        "capability_status": statuses,
        "missing_capabilities": missing,
        "warnings": warnings,
        "evidence_refs": sorted({ref for item in metrics.values() for ref in item["evidence_refs"]}),
    }


__all__ = ["build_equity_features"]
