"""Small workflow registry that adds research depth without replacing orchestration."""

from __future__ import annotations

from typing import Any


_WORKFLOWS: dict[str, dict[str, Any]] = {
    "USStock": {
        "workflow_id": "equity_deep_dive_v1",
        "artifact_type": "equity_research",
        "required_capabilities": ["price_history", "financials", "filings"],
        "optional_capabilities": [
            "analyst_expectations", "options_surface", "short_interest", "short_volume",
            "ownership", "earnings_transcript",
        ],
    },
    "HKStock": {
        "workflow_id": "hk_equity_deep_dive_v1",
        "artifact_type": "equity_research",
        "required_capabilities": ["price_history", "financials", "issuer_disclosures"],
        "optional_capabilities": [
            "southbound_holdings", "short_selling", "ccass", "analyst_expectations",
        ],
    },
    "Crypto": {
        "workflow_id": "crypto_market_structure_v1",
        "artifact_type": "crypto_research",
        "required_capabilities": ["price_history", "spot_market"],
        "optional_capabilities": [
            "funding", "open_interest", "liquidations", "basis", "onchain_flows",
        ],
    },
    "Forex": {
        "workflow_id": "fx_macro_relative_value_v1",
        "artifact_type": "fx_research",
        "required_capabilities": ["price_history", "pair_identity"],
        "optional_capabilities": [
            "policy_rates", "yield_differential", "positioning", "event_calendar",
        ],
    },
    "Futures": {
        "workflow_id": "futures_curve_positioning_v1",
        "artifact_type": "futures_research",
        "required_capabilities": ["price_history", "contract_identity"],
        "optional_capabilities": [
            "term_structure", "cftc_positioning", "inventory", "seasonality", "basis",
        ],
    },
}

_MODE_CONFIG: dict[str, dict[str, list[str]]] = {
    "research": {
        "focus_sections": ["thesis", "variant_view", "valuation", "catalysts", "risks"],
        "evidence_priority": ["fundamental", "market", "macro", "news"],
    },
    "diagnosis": {
        "focus_sections": ["observed_move", "competing_explanations", "evidence_for_against", "next_checks"],
        "evidence_priority": ["market", "news", "macro", "positioning"],
    },
    "technical": {
        "focus_sections": ["trend", "momentum", "volatility", "levels", "invalidation"],
        "evidence_priority": ["technical", "market"],
    },
    "plan": {
        "focus_sections": ["bull_trigger", "bear_trigger", "stay_out", "risk_budget", "invalidation"],
        "evidence_priority": ["technical", "market", "event"],
    },
    "news": {
        "focus_sections": ["event_timeline", "confirmed_facts", "transmission_mechanism", "priced_in_test"],
        "evidence_priority": ["news", "filings", "market"],
    },
    "macro": {
        "focus_sections": ["macro_regime", "transmission_mechanism", "relative_value", "event_risk"],
        "evidence_priority": ["macro", "market", "positioning"],
    },
}


def resolve_workflow(market: str, research_mode: str | None = None) -> dict[str, Any]:
    """Return a stable workflow descriptor for an asset and optional UI mode."""
    base = dict(_WORKFLOWS.get(str(market), {
        "workflow_id": "cross_asset_research_v1",
        "artifact_type": "market_research",
        "required_capabilities": ["price_history"],
        "optional_capabilities": ["macro", "news"],
    }))
    mode = str(research_mode or "research").strip().lower()
    supported_modes = {"research", "diagnosis", "technical", "plan", "news", "macro"}
    base["research_mode"] = mode if mode in supported_modes else "research"
    base.update(_MODE_CONFIG[base["research_mode"]])
    return base


__all__ = ["resolve_workflow"]
