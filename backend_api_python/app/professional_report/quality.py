"""Deterministic data-quality assessment and report conclusion gates."""

from __future__ import annotations

import math
from typing import Any

from .contracts import DataQualitySummary, EvidenceObservation, EvidenceSnapshotV1


_INVALID_FLAGS = frozenset({"invalid", "missing", "error", "unavailable"})
_STALE_FLAGS = frozenset({"stale", "expired"})
_CONFLICT_FLAGS = frozenset({"conflict", "conflicting"})
_CONFIDENCE_CAPS = {"none": 35.0, "low": 55.0, "medium": 75.0, "high": 100.0}


def _flags(observation: EvidenceObservation) -> set[str]:
    return {flag.strip().lower() for flag in observation.quality_flags}


def _is_valid(observation: EvidenceObservation) -> bool:
    if observation.value is None or _flags(observation) & _INVALID_FLAGS:
        return False
    if isinstance(observation.value, float) and not math.isfinite(observation.value):
        return False
    return True


def _is_fresh(observation: EvidenceObservation, snapshot: EvidenceSnapshotV1) -> bool:
    if _flags(observation) & _STALE_FLAGS:
        return False
    age_seconds = (snapshot.retrieved_at - observation.as_of).total_seconds()
    return age_seconds <= observation.freshness_limit_seconds


def _normalised_value(value: Any) -> tuple[str, Any]:
    if isinstance(value, bool):
        return ("text", str(value).lower())
    if isinstance(value, (int, float)):
        return ("number", float(value))
    if isinstance(value, str):
        return ("text", value.strip().casefold())
    return ("text", repr(value))


def _has_conflict(observations: list[EvidenceObservation], tolerance: float = 0.02) -> bool:
    if any(_flags(item) & _CONFLICT_FLAGS for item in observations):
        return True
    valid_items = [item for item in observations if _is_valid(item)]
    if len({item.source.casefold() for item in valid_items}) < 2:
        return False
    values = [_normalised_value(item.value) for item in valid_items]
    if len(values) < 2:
        return False
    if len({kind for kind, _ in values}) > 1:
        return True
    if values[0][0] == "text":
        return len({value for _, value in values}) > 1

    numbers = [value for _, value in values]
    low, high = min(numbers), max(numbers)
    denominator = max(abs(low), abs(high), 1e-12)
    return abs(high - low) / denominator > tolerance


def assess_snapshot_quality(snapshot: EvidenceSnapshotV1) -> DataQualitySummary:
    """Measure coverage, freshness and conflicts for a normalised snapshot.

    Required metrics come from ``snapshot.required_metrics``.  When that list is
    empty, observations marked ``is_required`` are used; if none are marked, all
    observed metrics become required.  This prevents an empty policy from
    accidentally producing a perfect quality score.
    """

    by_metric: dict[str, list[EvidenceObservation]] = {}
    for observation in snapshot.observations:
        by_metric.setdefault(observation.metric, []).append(observation)

    required = list(snapshot.required_metrics)
    if not required:
        required = list(
            dict.fromkeys(item.metric for item in snapshot.observations if item.required)
        )
    if not required:
        required = list(dict.fromkeys(item.metric for item in snapshot.observations))

    invalid_ids = sorted(
        item.evidence_id for item in snapshot.observations if not _is_valid(item)
    )
    covered = sorted(
        metric for metric in required if any(_is_valid(item) for item in by_metric.get(metric, []))
    )
    missing = sorted(set(required) - set(covered))

    fresh_metrics: list[str] = []
    stale_ids: list[str] = []
    for metric in covered:
        valid_items = [item for item in by_metric[metric] if _is_valid(item)]
        fresh_items = [item for item in valid_items if _is_fresh(item, snapshot)]
        if fresh_items:
            fresh_metrics.append(metric)
        stale_ids.extend(item.evidence_id for item in valid_items if not _is_fresh(item, snapshot))

    conflicting = sorted(
        metric for metric in required if _has_conflict(by_metric.get(metric, []))
    )

    denominator = len(required)
    coverage_ratio = len(covered) / denominator if denominator else 0.0
    freshness_ratio = len(fresh_metrics) / len(covered) if covered else 0.0
    conflict_ratio = len(conflicting) / denominator if denominator else 0.0
    quality_score = max(
        0.0,
        min(1.0, 0.50 * coverage_ratio + 0.35 * freshness_ratio + 0.15 * (1 - conflict_ratio)),
    )

    flags = {flag.strip().lower() for flag in snapshot.quality_flags if flag.strip()}
    if not required:
        flags.add("no_required_metrics")
    if missing:
        flags.add("insufficient_coverage")
    if stale_ids:
        flags.add("stale_data")
    if conflicting:
        flags.add("conflicting_evidence")
    if invalid_ids:
        flags.add("invalid_evidence")

    if not required or coverage_ratio < 0.60 or freshness_ratio < 0.50 or conflict_ratio > 0.25:
        strength = "none"
        directional_allowed = False
    elif coverage_ratio < 0.80 or freshness_ratio < 0.70 or conflict_ratio > 0.10:
        strength = "low"
        directional_allowed = True
    elif coverage_ratio < 0.95 or freshness_ratio < 0.90 or conflict_ratio > 0:
        strength = "medium"
        directional_allowed = True
    else:
        strength = "high"
        directional_allowed = True

    core_score = quality_score * 100
    news_status = (snapshot.collection.get("news") or {}).get("status", "not_checked")
    if news_status == "fetch_failed":
        flags.add("news_collection_failed")
        quality_score = min(quality_score, 0.80)
        if strength == "high":
            strength = "medium"
    elif news_status == "headline_only":
        flags.add("news_content_limited")
        quality_score = min(quality_score, 0.90)
        if strength == "high":
            strength = "medium"

    return DataQualitySummary(
        evaluated_at=snapshot.retrieved_at,
        coverage_ratio=coverage_ratio,
        freshness_ratio=freshness_ratio,
        conflict_ratio=conflict_ratio,
        quality_score=quality_score,
        overall_score=quality_score * 100,
        required_metrics=required,
        covered_metrics=covered,
        missing_metrics=missing,
        stale_evidence_ids=sorted(set(stale_ids)),
        conflicting_metrics=conflicting,
        invalid_evidence_ids=invalid_ids,
        quality_flags=sorted(flags),
        warnings=sorted(flags),
        max_conclusion_strength=strength,
        directional_conclusion_allowed=directional_allowed,
        core_quality_score=core_score,
        news_status=news_status,
    )


def apply_quality_gate(
    decision: str,
    confidence: float,
    quality: DataQualitySummary,
) -> tuple[str, float, list[str]]:
    """Downgrade a directional decision and cap confidence from data quality."""

    normalised_decision = decision.strip().upper()
    if normalised_decision not in {"BUY", "SELL", "HOLD"}:
        raise ValueError("decision must be BUY, SELL, or HOLD")
    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        raise ValueError("confidence must be a finite number between 0 and 100")

    gated_decision = normalised_decision
    reasons: list[str] = []
    if normalised_decision != "HOLD" and not quality.directional_conclusion_allowed:
        gated_decision = "HOLD"
        reasons.append("directional_decision_blocked")

    cap = _CONFIDENCE_CAPS[quality.max_conclusion_strength]
    gated_confidence = min(float(confidence), cap)
    if gated_confidence < confidence:
        reasons.append(f"confidence_capped:{quality.max_conclusion_strength}")
    reasons.extend(flag for flag in quality.quality_flags if flag not in reasons)
    return gated_decision, gated_confidence, reasons


__all__ = ["apply_quality_gate", "assess_snapshot_quality"]
