import pytest

from app.services.ai_report_pdf import (
    _format_evidence_observation,
    _format_evidence_provider,
    _professional_pdf_projection,
    _report_pdf_labels,
    build_ai_report_pdf,
)


def test_pdf_evidence_values_use_financial_units_without_metadata_currency_leaks():
    assert _format_evidence_observation({
        "metric": "financial.latest_quarter.balance_sheet.total_assets",
        "value": 148_524_000_000,
        "unit": "USD",
        "currency": "USD",
    }) == "148.52B USD"
    assert _format_evidence_observation({
        "metric": "financial.profit_margin", "value": 29.25, "unit": "percent",
    }) == "29.25%"
    assert _format_evidence_observation({
        "metric": "crypto.funding_rate", "value": 0.005, "unit": "percent",
    }) == "0.005%"
    assert _format_evidence_observation({
        "metric": "financial.eps", "value": 21.995,
        "unit": "currency_per_share", "currency": "USD",
    }, is_zh=True) == "21.995 USD/股"
    assert _format_evidence_observation({
        "metric": "financial.latest_date", "value": "2026-06-30",
        "unit": "USD", "currency": "USD",
    }) == "2026-06-30"
    assert _format_evidence_observation({
        "metric": "indicator.rsi.value", "value": 43.191, "unit": "percent",
    }) == "43.191"
    assert _format_evidence_observation({
        "metric": "financial.inventory", "value": 1_250_000_000,
        "unit": "currency", "currency": "USD",
    }) == "1.25B USD"
    assert _format_evidence_observation({
        "metric": "financial.currency", "value": "USD", "unit": "USD", "currency": "USD",
    }) == "USD"
    assert _format_evidence_provider("finnhub+yfinance+yfinance statements") == "Finnhub + Yahoo Finance statements"


def test_professional_report_v1_is_projected_for_pdf_without_legacy_input():
    projection = _professional_pdf_projection({
        "schema_version": "professional_report_v1",
        "instrument": {"market": "HKStock", "symbol": "00700", "canonical_symbol": "00700"},
        "decision_profile": {"decision": "HOLD", "confidence": 35},
        "executive_summary": "数据质量不足，保持观望。",
        "risk_plan": {
            "net_risk_reward": None,
            "warnings": ["no_directional_position"],
            "candidate_setup": {
                "direction": "SELL",
                "entry_price": 438.4,
                "stop_loss": 443.0,
                "take_profit": 429.6,
                "net_risk_reward": 1.8,
                "warnings": [],
            },
        },
        "dimensions": [{"key": "technical", "score": 42, "narrative": "趋势偏弱"}],
        "claims": [{"kind": "risk", "text": "关键数据缺失"}],
        "scenarios": [],
        "evidence_snapshot": {
            "observations": [{"metric": "quote.price", "value": 438.4, "source": "provider", "as_of": "2026-09-07T00:00:00Z"}],
        },
    })

    assert projection["market"] == "HKStock"
    assert projection["symbol"] == "00700"
    assert projection["market_data"]["current_price"] == 438.4
    assert projection["detailed_analysis"]["technical"] == "趋势偏弱"
    assert projection["risks"] == ["关键数据缺失"]
    assert projection["market_bias"] == "BEARISH"
    assert projection["trading_plan"]["entry_price"] == 438.4
    assert projection["trading_plan"]["candidate_setup"] is True


def test_professional_pdf_renders_bias_action_and_watch_only_candidate():
    pdf = build_ai_report_pdf(
        {
            "schema_version": "professional_report_v1",
            "report_id": "report-hold-bearish",
            "instrument": {
                "market": "HKStock",
                "symbol": "00700",
                "canonical_symbol": "00700",
                "name": "Tencent",
            },
            "decision_profile": {
                "decision": "HOLD",
                "market_bias": "BEARISH",
                "market_bias_score": -16,
                "confidence": 68,
            },
            "executive_summary": "技术结构偏空，但当前赔率不足，等待确认。",
            "risk_plan": {
                "decision": "HOLD",
                "recommended_position_pct": 0,
                "risk_budget_pct": 1,
                "warnings": ["no_directional_position"],
                "candidate_setup": {
                    "direction": "SELL",
                    "entry_price": 443.5,
                    "stop_loss": 454.5,
                    "take_profit": 429.6,
                    "net_risk_reward": 1.18,
                    "warnings": [],
                },
            },
            "data_quality": {"overall_score": 93},
            "dimensions": [],
            "scenarios": [],
            "claims": [],
            "evidence_snapshot": {"observations": []},
        },
        language="zh-CN",
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1_000


SUPPORTED_LANGUAGES = (
    "en-US",
    "zh-CN",
    "zh-TW",
    "ja-JP",
    "ko-KR",
    "de-DE",
    "fr-FR",
    "ru-RU",
    "ar-SA",
    "th-TH",
    "vi-VN",
    "pt-BR",
)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_report_pdf_has_complete_labels_for_every_supported_language(language):
    labels = _report_pdf_labels(language)

    required = {
        "title",
        "subtitle",
        "target",
        "generated",
        "decision",
        "confidence",
        "summary",
        "plan",
        "scores",
        "trend",
        "crypto",
        "details",
        "reasons",
        "risks",
        "indicators",
        "rr_warning",
        "rr_warning_text",
        "disclaimer",
        "field_trend",
        "field_direction",
        "field_score",
        "field_strength",
        "field_summary",
        "field_value",
        "field_signal",
        "current_price",
        "change_24h",
        "entry",
        "stop_loss",
        "take_profit",
        "risk_reward",
        "horizon",
        "outlook",
    }

    assert required <= labels.keys()
    assert all(str(labels[key]).strip() for key in required)
    if language != "en-US":
        assert labels["title"] != _report_pdf_labels("en-US")["title"]
        assert labels["rr_warning"] != _report_pdf_labels("en-US")["rr_warning"]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_report_pdf_renders_for_every_supported_language(language):
    pdf = build_ai_report_pdf(
        {
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "decision": "BUY",
            "confidence": 70,
            "summary": "Test summary",
            "trend_outlook": {"trend": "up", "strength": "moderate"},
            "trading_plan": {
                "entry_price": 100,
                "stop_loss": 92,
                "take_profit": 104,
                "risk_reward_ratio": 0.5,
                "rr_warning": {"code": "risk_reward_below_one"},
            },
        },
        language=language,
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1_000


def test_pt_br_request_language_and_alerts_are_supported():
    from app.services.portfolio_monitor_i18n import get_alert_title
    from app.utils.language import _normalize_lang

    assert _normalize_lang("pt-BR,pt;q=0.9") == "pt-BR"
    assert _normalize_lang("pt") == "pt-BR"
    assert get_alert_title("pt-BR") == "Alerta de Preço/L/P"
