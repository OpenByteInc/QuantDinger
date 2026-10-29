from app.services.fast_analysis import FastAnalysisService


def test_failed_llm_narrative_uses_deterministic_analysis():
    analysis = {
        "summary": "Analysis failed",
        "report": "Analysis failed: provider unavailable",
        "key_reasons": ["Unable to analyze"],
        "risks": ["Analysis error"],
    }

    replaced = FastAnalysisService._replace_failed_llm_narrative(
        analysis,
        "24h HOLD; 3d SELL",
        "Derivatives positioning is cautious.",
    )

    assert replaced is True
    assert analysis["summary"] == "24h HOLD; 3d SELL"
    assert analysis["key_reasons"] == [
        "24h HOLD; 3d SELL",
        "Derivatives positioning is cautious.",
    ]
    assert analysis["risks"] == []
    assert analysis["_llm_fallback_used"] is True


def test_successful_llm_narrative_is_preserved():
    analysis = {"summary": "Momentum is improving.", "report": ""}

    replaced = FastAnalysisService._replace_failed_llm_narrative(
        analysis,
        "24h HOLD",
    )

    assert replaced is False
    assert analysis == {"summary": "Momentum is improving.", "report": ""}


def test_analysis_market_is_normalized_for_report_contracts():
    assert FastAnalysisService._normalize_analysis_market("FUTURES") == "Futures"
    assert FastAnalysisService._normalize_analysis_market("FOREX") == "Forex"
    assert FastAnalysisService._normalize_analysis_market("HKSTOCK") == "HKStock"
