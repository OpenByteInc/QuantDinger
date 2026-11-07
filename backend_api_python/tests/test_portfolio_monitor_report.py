from app.services.portfolio_monitor_report import (
    collect_professional_reports,
    render_professional_html,
    render_professional_telegram,
)


def _report():
    return {
        "schema_version": "professional_report_v1",
        "report_id": "report-monitor-1",
        "instrument": {"market": "USStock", "symbol": "SKHY", "canonical_symbol": "SKHY"},
        "decision_profile": {
            "decision": "HOLD",
            "market_bias": "BEARISH",
            "confidence": 42,
            "rationale": "Wait for confirmation.",
        },
        "executive_summary": "Price remains below the trend filter.",
        "data_quality": {
            "quality_score": 0.76,
            "coverage_ratio": 0.8,
            "freshness_ratio": 0.9,
            "max_conclusion_strength": "moderate",
        },
        "dimensions": [
            {"key": "technical", "score": 38, "narrative": "Momentum is weak."},
        ],
        "scenarios": [
            {"case": "base", "target_price": 145, "thesis": "Range trading."},
        ],
        "risk_plan": {
            "candidate_setup": {
                "entry_price": 144,
                "stop_loss": 138,
                "take_profit": 156,
                "net_risk_reward": 1.8,
            },
            "recommended_position_pct": 0,
            "estimated_roundtrip_cost_bps": 8,
        },
    }


def test_professional_report_is_collected_from_monitor_analysis():
    report = _report()
    analyses = [
        {"professional_report": report},
        {"professional_report": {"schema_version": "legacy"}},
        {},
    ]

    assert collect_professional_reports(analyses) == [report]


def test_email_renderer_uses_current_contract_and_neutral_dark_palette():
    html = render_professional_html(
        _report(),
        {
            "professional_report": "Professional Analysis Report",
            "ai_decision": "AI Outlook",
            "confidence": "Confidence",
        },
    )

    assert 'data-report-version="professional_report_v1"' in html
    assert "Price remains below the trend filter." in html
    assert "Momentum is weak." in html
    assert "prefers-color-scheme: dark" in html
    assert "background: #171717" in html
    assert "#0f1218" not in html


def test_telegram_renderer_uses_professional_summary_and_risk_plan():
    lines = render_professional_telegram(
        _report(),
        {
            "ai_decision": "AI Outlook",
            "confidence": "Confidence",
            "data_quality": "Data quality",
            "entry_price": "Entry",
            "stop_loss": "Stop",
            "take_profit": "Target",
            "hold": "Neutral",
        },
    )

    text = "\n".join(lines)
    assert "USStock:SKHY" in text
    assert "Price remains below the trend filter." in text
    assert "Entry $144" in text
    assert "Stop $138" in text
    assert "Target $156" in text
