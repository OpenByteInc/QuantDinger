"""Versioned contracts for evidence-backed professional analysis reports.

The models in this module deliberately reject unknown fields.  They form the
stable boundary between data adapters, deterministic scoring and report
rendering; source-specific payloads should be normalised before reaching here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import AliasChoices, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


Market = Literal["USStock", "HKStock", "Crypto", "Forex", "Futures"]
Decision = Literal["BUY", "SELL", "HOLD"]
MarketBias = Literal["BULLISH", "BEARISH", "NEUTRAL"]
ConclusionStrength = Literal["none", "low", "medium", "high"]
ScenarioName = Literal["bull", "base", "bear"]


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class InstrumentIdentity(_ContractModel):
    """Canonical identity shared by all providers for one instrument."""

    market: Market
    symbol: str = Field(min_length=1)
    canonical_symbol: str = Field(min_length=1)
    name: str | None = None
    asset_type: str | None = None
    exchange: str | None = None
    venue: str | None = None
    product_type: str | None = None
    base_currency: str | None = None
    quote_currency: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    identity_verified: bool | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)

    @field_validator("market", mode="before")
    @classmethod
    def normalise_market(cls, value: str) -> str:
        aliases = {
            "us_equity": "USStock",
            "hk_equity": "HKStock",
            "crypto": "Crypto",
            "forex": "Forex",
            "fx": "Forex",
            "futures": "Futures",
        }
        return aliases.get(str(value), str(value))

    @field_validator("symbol", "canonical_symbol", "exchange", "base_currency", "quote_currency")
    @classmethod
    def normalise_market_codes(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class EvidenceObservation(_ContractModel):
    """One atomic, attributable observation used by scoring or a claim."""

    evidence_id: str = Field(min_length=1)
    category: str = "other"
    metric: str = Field(min_length=1)
    value: Any = None
    source: str = Field(min_length=1)
    as_of: AwareDatetime
    retrieved_at: AwareDatetime
    unit: str | None = None
    currency: str | None = None
    period: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    source_url: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    required: bool = Field(default=False, validation_alias=AliasChoices("required", "is_required"))
    freshness_limit_seconds: int = Field(default=86_400, gt=0)

    @property
    def is_required(self) -> bool:
        """Compatibility spelling used by early quality-policy callers."""
        return self.required

    @field_validator("as_of", "retrieved_at", mode="before")
    @classmethod
    def expand_date_only_timestamp(cls, value: Any) -> Any:
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
            return f"{value.strip()}T00:00:00Z"
        return value

    @field_validator("currency")
    @classmethod
    def normalise_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("quality_flags")
    @classmethod
    def unique_quality_flags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(flag.strip().lower() for flag in value if flag.strip()))

    @model_validator(mode="after")
    def validate_timestamps(self) -> "EvidenceObservation":
        if self.retrieved_at < self.as_of:
            raise ValueError("retrieved_at must be greater than or equal to as_of")
        return self


class EvidenceSnapshotV1(_ContractModel):
    """Immutable evidence set captured for one report run."""

    schema_version: Literal["evidence_snapshot_v1", "1.0"] = Field(
        default="evidence_snapshot_v1",
        validation_alias=AliasChoices("schema_version", "version"),
    )
    snapshot_id: str = Field(default="", min_length=1)
    instrument: InstrumentIdentity
    as_of: AwareDatetime
    retrieved_at: AwareDatetime
    timeframe: str | None = None
    observations: list[EvidenceObservation] = Field(default_factory=list)
    required_metrics: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    collection: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def derive_snapshot_id(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("snapshot_id"):
            return value
        payload = dict(value)
        instrument = payload.get("instrument") or {}
        identity = instrument.get("canonical_symbol") or instrument.get("symbol") or "unknown"
        fingerprint = json.dumps(
            [identity, payload.get("as_of"), payload.get("retrieved_at")],
            default=str,
            separators=(",", ":"),
        )
        payload["snapshot_id"] = "snap_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
        return payload

    @field_validator("required_metrics", "quality_flags")
    @classmethod
    def unique_strings(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def validate_snapshot(self) -> "EvidenceSnapshotV1":
        if self.retrieved_at < self.as_of:
            raise ValueError("retrieved_at must be greater than or equal to as_of")

        evidence_ids = [item.evidence_id for item in self.observations]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id must be unique within a snapshot")

        if any(item.retrieved_at > self.retrieved_at for item in self.observations):
            raise ValueError("observation retrieved_at cannot be later than snapshot retrieved_at")
        return self


class DataQualitySummary(_ContractModel):
    """Deterministic quality result consumed by the conclusion gate."""

    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: AwareDatetime
    coverage_ratio: float = Field(ge=0, le=1)
    freshness_ratio: float = Field(ge=0, le=1)
    conflict_ratio: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    overall_score: float | None = Field(default=None, ge=0, le=100)
    required_metrics: list[str] = Field(default_factory=list)
    covered_metrics: list[str] = Field(default_factory=list)
    missing_metrics: list[str] = Field(default_factory=list)
    stale_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_metrics: list[str] = Field(default_factory=list)
    invalid_evidence_ids: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    max_conclusion_strength: ConclusionStrength
    directional_conclusion_allowed: bool

    @model_validator(mode="after")
    def validate_gate_consistency(self) -> "DataQualitySummary":
        if self.max_conclusion_strength == "none" and self.directional_conclusion_allowed:
            raise ValueError("directional conclusions cannot be allowed when maximum strength is none")
        if set(self.covered_metrics) & set(self.missing_metrics):
            raise ValueError("a metric cannot be both covered and missing")
        return self


class DecisionProfile(_ContractModel):
    decision: Decision
    raw_decision: Decision | None = None
    market_bias: MarketBias = "NEUTRAL"
    market_bias_score: float = Field(default=0, ge=-100, le=100)
    market_bias_basis: Literal["technical_score"] = "technical_score"
    confidence: float = Field(ge=0, le=100)
    raw_confidence: float | None = Field(default=None, ge=0, le=100)
    confidence_kind: Literal["model_strength", "calibrated_probability"] = "model_strength"
    quality_gate_reasons: list[str] = Field(default_factory=list)
    conclusion_strength: ConclusionStrength = "none"
    rationale: str = ""
    horizon: str | None = None
    score: float | None = Field(default=None, ge=-100, le=100)
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceClaim(_ContractModel):
    claim_id: str | None = None
    kind: str = "thesis"
    text: str = Field(min_length=1, validation_alias=AliasChoices("text", "statement"))
    evidence_refs: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("evidence_refs", "evidence_ids"),
    )
    opposing_evidence_refs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("opposing_evidence_refs", "opposing_evidence_ids"),
    )
    strength: Literal["low", "medium", "high"] = "medium"
    quality_flags: list[str] = Field(default_factory=list)


class ScenarioCase(_ContractModel):
    case: ScenarioName = Field(validation_alias=AliasChoices("case", "name"))
    probability: float | None = Field(default=None, ge=0, le=1)
    thesis: str | None = None
    trigger: str | None = None
    triggers: list[str] = Field(default_factory=list)
    target_price: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("target_price", "price_target"),
    )
    invalidation: float | str | None = None
    currency: str | None = None
    evidence_refs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_refs", "evidence_ids"),
    )


class CandidateSetup(_ContractModel):
    """Non-actionable price geometry retained when the trade action is HOLD."""

    direction: Literal["BUY", "SELL"]
    status: Literal["watch_only"] = "watch_only"
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    stop_distance_pct: float = Field(ge=0)
    gross_risk_reward: float = Field(ge=0)
    net_risk_reward: float = Field(ge=0)
    estimated_roundtrip_cost_bps: float = Field(ge=0)
    source: str = "technical_levels"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_price_geometry(self) -> "CandidateSetup":
        valid = (
            (self.direction == "BUY" and self.stop_loss < self.entry_price < self.take_profit)
            or (self.direction == "SELL" and self.take_profit < self.entry_price < self.stop_loss)
        )
        if not valid:
            raise ValueError("candidate entry, stop and target geometry is invalid")
        return self


class RiskPlan(_ContractModel):
    decision: Decision | None = None
    entry_price: float | None = Field(default=None, ge=0)
    invalidation_conditions: list[str] = Field(default_factory=list)
    stop_loss: float | None = Field(default=None, ge=0)
    take_profit: float | None = Field(default=None, ge=0)
    stop_distance_pct: float | None = Field(default=None, ge=0)
    gross_risk_reward: float | None = Field(default=None, ge=0)
    net_risk_reward: float | None = Field(default=None, ge=0)
    risk_budget_pct: float | None = Field(default=None, ge=0, le=100)
    max_position_pct: float | None = Field(default=None, ge=0, le=100)
    recommended_position_pct: float | None = Field(default=None, ge=0, le=100)
    max_loss_pct: float | None = Field(default=None, ge=0, le=100)
    estimated_roundtrip_cost_bps: float | None = Field(default=None, ge=0)
    valid: bool = True
    warnings: list[str] = Field(default_factory=list)
    candidate_setup: CandidateSetup | None = None
    horizon: str | None = None
    evidence_refs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_refs", "evidence_ids"),
    )


class _AnalysisDimension(_ContractModel):
    key: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    score_kind: str
    status: Literal["available", "insufficient_data"]
    narrative: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class PriceBar(_ContractModel):
    """One report-safe OHLCV observation used by the compact price chart."""

    timestamp: str = Field(min_length=1)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ohlc_geometry(self) -> "PriceBar":
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar high/low must contain open and close")
        if self.high < self.low:
            raise ValueError("bar high must be greater than or equal to bar low")
        return self


class PriceChart(_ContractModel):
    """Bounded chart payload derived from the same candles used by the report."""

    timeframe: str = "1D"
    bars: list[PriceBar] = Field(default_factory=list, max_length=60)
    moving_average_periods: list[int] = Field(default_factory=lambda: [5, 10, 20])
    support: float | None = Field(default=None, gt=0)
    resistance: float | None = Field(default=None, gt=0)


class ProfessionalReportV1(_ContractModel):
    """Top-level V1 report artifact with fully traceable evidence references."""

    schema_version: Literal["professional_report_v1", "1.0"] = "professional_report_v1"
    report_id: str = Field(min_length=1)
    generated_at: AwareDatetime
    as_of: AwareDatetime
    language: str = "en-US"
    data_tier: Literal["community", "professional"] = "community"
    instrument: InstrumentIdentity
    evidence_snapshot: EvidenceSnapshotV1
    data_quality: DataQualitySummary
    decision_profile: DecisionProfile = Field(validation_alias=AliasChoices("decision_profile", "decision"))
    executive_summary: str = ""
    dimensions: list[_AnalysisDimension] = Field(default_factory=list)
    claims: list[EvidenceClaim] = Field(default_factory=list)
    scenarios: list[ScenarioCase] = Field(default_factory=list)
    risk_plan: RiskPlan | None = None
    price_chart: PriceChart | None = None
    market_features: dict[str, Any] = Field(default_factory=dict)
    research_intelligence: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    methodology: dict[str, Any] = Field(default_factory=dict)
    model_version: str | None = None
    prompt_version: str | None = None
    scoring_version: str | None = None

    @model_validator(mode="after")
    def validate_report_links(self) -> "ProfessionalReportV1":
        if self.instrument != self.evidence_snapshot.instrument:
            raise ValueError("report instrument must match evidence snapshot instrument")

        available = {item.evidence_id for item in self.evidence_snapshot.observations}
        references: set[str] = set(self.decision_profile.evidence_ids)
        for dimension in self.dimensions:
            references.update(dimension.evidence_refs)
        for claim in self.claims:
            references.update(claim.evidence_refs)
            references.update(claim.opposing_evidence_refs)
        for scenario in self.scenarios:
            references.update(scenario.evidence_refs)
        if self.risk_plan:
            references.update(self.risk_plan.evidence_refs)
        unknown = references - available
        if unknown:
            raise ValueError(f"report references unknown evidence ids: {sorted(unknown)}")

        scenario_names = [case.case for case in self.scenarios]
        if len(scenario_names) != len(set(scenario_names)):
            raise ValueError("scenario names must be unique")
        probabilities = [case.probability for case in self.scenarios]
        if probabilities and all(item is not None for item in probabilities) and abs(sum(probabilities) - 1.0) > 1e-6:
            raise ValueError("scenario probabilities must sum to 1")
        return self


__all__ = [
    "CandidateSetup",
    "DataQualitySummary",
    "DecisionProfile",
    "EvidenceClaim",
    "EvidenceObservation",
    "EvidenceSnapshotV1",
    "InstrumentIdentity",
    "MarketBias",
    "ProfessionalReportV1",
    "RiskPlan",
    "ScenarioCase",
]
