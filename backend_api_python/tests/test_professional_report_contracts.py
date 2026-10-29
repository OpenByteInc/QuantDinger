from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.professional_report.contracts import (
    CandidateSetup,
    DataQualitySummary,
    DecisionProfile,
    EvidenceClaim,
    EvidenceObservation,
    EvidenceSnapshotV1,
    InstrumentIdentity,
    ProfessionalReportV1,
    RiskPlan,
    ScenarioCase,
)
from app.professional_report.quality import apply_quality_gate, assess_snapshot_quality


NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)


def instrument(market: str = "USStock") -> InstrumentIdentity:
    symbols = {
        "USStock": "aapl",
        "HKStock": "00700",
        "Crypto": "btc/usdt",
        "Forex": "eurusd",
        "Futures": "gc",
    }
    currencies = {
        "USStock": "usd",
        "HKStock": "hkd",
        "Crypto": "usdt",
        "Forex": "usd",
        "Futures": "usd",
    }
    return InstrumentIdentity(
        market=market,
        symbol=symbols[market],
        canonical_symbol=symbols[market],
        exchange="nasdaq" if market == "USStock" else "binance",
        quote_currency=currencies[market],
        timezone="America/New_York" if market == "USStock" else "UTC",
    )


def observation(
    evidence_id: str,
    metric: str,
    value: object,
    *,
    age: timedelta = timedelta(minutes=5),
    source: str = "primary",
    flags: list[str] | None = None,
) -> EvidenceObservation:
    return EvidenceObservation(
        evidence_id=evidence_id,
        metric=metric,
        value=value,
        source=source,
        as_of=NOW - age,
        retrieved_at=NOW,
        unit="price" if metric == "price" else None,
        currency="usd" if metric == "price" else None,
        period="instant",
        source_url=f"https://data.example/{evidence_id}",
        quality_flags=flags or [],
        freshness_limit_seconds=3600,
    )


def snapshot(
    observations: list[EvidenceObservation],
    required_metrics: list[str],
    market: str = "USStock",
) -> EvidenceSnapshotV1:
    return EvidenceSnapshotV1(
        snapshot_id="snap-1",
        instrument=instrument(market),
        as_of=NOW - timedelta(minutes=5),
        retrieved_at=NOW,
        observations=observations,
        required_metrics=required_metrics,
    )


def test_observation_requires_aware_chronological_timestamps_and_normalises_metadata():
    item = observation("px-1", "price", 229.4, flags=[" Estimated ", "estimated"])

    assert item.currency == "USD"
    assert item.quality_flags == ["estimated"]
    assert item.source_url == "https://data.example/px-1"

    with pytest.raises(ValidationError, match="timezone"):
        EvidenceObservation(
            evidence_id="bad",
            metric="price",
            value=1,
            source="vendor",
            as_of=datetime(2026, 9, 7, 9),
            retrieved_at=NOW,
        )

    with pytest.raises(ValidationError, match="retrieved_at"):
        EvidenceObservation(
            evidence_id="future",
            metric="price",
            value=1,
            source="vendor",
            as_of=NOW,
            retrieved_at=NOW - timedelta(seconds=1),
        )


def test_snapshot_rejects_duplicate_evidence_ids():
    with pytest.raises(ValidationError, match="evidence_id must be unique"):
        snapshot(
            [observation("same", "price", 10), observation("same", "volume", 20)],
            ["price", "volume"],
        )


@pytest.mark.parametrize("market", ["USStock", "HKStock", "Crypto", "Forex", "Futures"])
def test_contract_supports_professional_report_markets(market: str):
    identity = instrument(market)
    assert identity.market == market
    assert identity.quote_currency in {"USD", "HKD", "USDT"}


def test_candidate_setup_is_explicitly_watch_only():
    setup = CandidateSetup(
        direction="BUY",
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        stop_distance_pct=5,
        gross_risk_reward=2,
        net_risk_reward=1.9,
        estimated_roundtrip_cost_bps=10,
    )

    assert setup.status == "watch_only"
    with pytest.raises(ValidationError):
        CandidateSetup(**{**setup.model_dump(), "status": "actionable"})
    with pytest.raises(ValidationError, match="geometry"):
        CandidateSetup(**{**setup.model_dump(), "stop_loss": 105})


def test_complete_fresh_consistent_snapshot_allows_strong_conclusion():
    result = assess_snapshot_quality(
        snapshot(
            [
                observation("px-a", "price", 100.0, source="vendor-a"),
                observation("px-b", "price", 101.0, source="vendor-b"),
                observation("vol", "volume", 1_000_000),
                observation("fund", "fundamentals", "available"),
            ],
            ["price", "volume", "fundamentals"],
        )
    )

    assert result.coverage_ratio == 1
    assert result.freshness_ratio == 1
    assert result.conflict_ratio == 0
    assert result.max_conclusion_strength == "high"
    assert result.directional_conclusion_allowed is True


def test_missing_coverage_blocks_directional_decision_and_caps_confidence():
    quality = assess_snapshot_quality(
        snapshot([observation("px", "price", 100)], ["price", "volume", "fundamentals"])
    )

    decision, confidence, reasons = apply_quality_gate("buy", 92, quality)

    assert quality.coverage_ratio == pytest.approx(1 / 3)
    assert quality.missing_metrics == ["fundamentals", "volume"]
    assert decision == "HOLD"
    assert confidence == 35
    assert "directional_decision_blocked" in reasons
    assert "insufficient_coverage" in reasons


def test_stale_observation_lowers_freshness_and_blocks_conclusion():
    quality = assess_snapshot_quality(
        snapshot(
            [
                observation("px", "price", 100, age=timedelta(hours=2)),
                observation("vol", "volume", 10, age=timedelta(hours=2)),
            ],
            ["price", "volume"],
        )
    )

    assert quality.coverage_ratio == 1
    assert quality.freshness_ratio == 0
    assert quality.stale_evidence_ids == ["px", "vol"]
    assert quality.max_conclusion_strength == "none"


def test_conflicting_sources_are_detected_and_gate_directional_conclusion():
    quality = assess_snapshot_quality(
        snapshot(
            [
                observation("px-a", "price", 100, source="vendor-a"),
                observation("px-b", "price", 120, source="vendor-b"),
                observation("vol", "volume", 10),
                observation("fund", "fundamentals", "available"),
            ],
            ["price", "volume", "fundamentals"],
        )
    )

    assert quality.conflicting_metrics == ["price"]
    assert quality.conflict_ratio == pytest.approx(1 / 3)
    assert apply_quality_gate("SELL", 88, quality)[0] == "HOLD"


def test_repeated_observations_from_one_source_are_not_cross_source_conflicts():
    quality = assess_snapshot_quality(
        snapshot(
            [
                observation("px-old", "price", 100, source="vendor-a"),
                observation("px-new", "price", 120, source="vendor-a"),
            ],
            ["price"],
        )
    )

    assert quality.conflict_ratio == 0
    assert quality.conflicting_metrics == []


def test_professional_report_validates_evidence_links_and_scenario_probabilities():
    snap = snapshot(
        [observation("px", "price", 100), observation("risk", "atr", 2.5)],
        ["price", "atr"],
    )
    quality = assess_snapshot_quality(snap)
    report = ProfessionalReportV1(
        report_id="report-1",
        generated_at=NOW,
        instrument=snap.instrument,
        evidence_snapshot=snap,
        data_quality=quality,
        as_of=NOW,
        decision_profile=DecisionProfile(
            decision="BUY",
            confidence=70,
            conclusion_strength="high",
            evidence_ids=["px"],
        ),
        claims=[EvidenceClaim(claim_id="c1", text="Price momentum is positive", evidence_refs=["px"])],
        scenarios=[
            ScenarioCase(case="bull", probability=0.3, thesis="Upside breakout", evidence_refs=["px"]),
            ScenarioCase(case="base", probability=0.5, thesis="Range continuation", evidence_refs=["px"]),
            ScenarioCase(case="bear", probability=0.2, thesis="Momentum failure", evidence_refs=["risk"]),
        ],
        risk_plan=RiskPlan(stop_loss=95, max_position_pct=5, evidence_refs=["risk"]),
        model_version="model-1",
        prompt_version="prompt-1",
        scoring_version="score-1",
    )

    assert report.model_dump(mode="json")["schema_version"] == "professional_report_v1"

    payload = report.model_dump()
    payload["claims"] = [{"claim_id": "bad", "statement": "Unsupported", "evidence_ids": ["missing"]}]
    with pytest.raises(ValidationError, match="unknown evidence ids"):
        ProfessionalReportV1.model_validate(payload)


def test_quality_contract_rejects_inconsistent_gate_state():
    with pytest.raises(ValidationError, match="cannot be allowed"):
        DataQualitySummary(
            evaluated_at=NOW,
            coverage_ratio=0,
            freshness_ratio=0,
            conflict_ratio=0,
            quality_score=0,
            overall_score=0,
            max_conclusion_strength="none",
            directional_conclusion_allowed=True,
        )


def test_unknown_contract_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        instrument().model_copy(update={"unsupported": "value"}).model_validate(
            {**instrument().model_dump(), "unsupported": "value"}
        )
