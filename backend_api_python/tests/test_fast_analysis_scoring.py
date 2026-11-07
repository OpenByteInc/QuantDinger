from app.services.fast_analysis import FastAnalysisService
from app.services.fast_analysis_formatters import (
    build_crypto_factor_summary_code,
    build_trend_outlook_summary,
)
from app.services.fast_analysis_policy import should_override_with_consensus


def _service():
    svc = FastAnalysisService()
    svc._get_ai_calibration = lambda market="Crypto": {}
    return svc


def test_oversold_only_does_not_expand_to_full_bullish_score():
    svc = _service()

    score = svc._calculate_technical_score(
        {"rsi": {"value": 23.0}},
        {"price": 100.0, "changePercent": 0.0},
    )

    assert 0 < score < 20


def test_bearish_breakdown_suppresses_oversold_buy_bias():
    svc = _service()
    indicators = {
        "rsi": {"value": 23.0},
        "macd": {"signal": "bearish"},
        "moving_averages": {"trend": "strong_downtrend"},
        "price_position": 9.2,
        "volume_ratio": 0.58,
        "bollinger": {
            "BB_upper": 81676.6,
            "BB_lower": 68373.55,
        },
        "current_price": 66909.99,
        "volatility": {"pct": 3.23},
    }
    price = {"price": 66909.99, "changePercent": -5.21}

    risk = svc._technical_risk_context(indicators, price)
    score = svc._calculate_technical_score(indicators, price)

    assert risk["panic_breakdown"] is True
    assert score <= -20


def test_neutral_consensus_never_overrides_directional_model_judgment():
    svc = _service()

    assert svc._score_to_decision(17.6, market="USStock") == "HOLD"
    assert should_override_with_consensus("HOLD", 17.6, 15.0) is False
    assert should_override_with_consensus("BUY", 20.0, 15.0) is True


def test_low_confidence_direction_survives_when_consensus_confirms_it():
    svc = _service()
    analysis = {
        "decision": "BUY",
        "confidence": 56,
        "consensus": {"consensus_decision": "BUY", "consensus_score": 22.0},
        "objective_scores_by_timeframe": {"4H": {"decision": "BUY"}, "1D": {"decision": "BUY"}},
    }
    indicators = {
        "rsi": {"value": 55},
        "macd": {"signal": "bullish"},
        "moving_averages": {"trend": "uptrend", "ma20": 95},
        "current_price": 100,
        "price_position": 60,
        "volume_ratio": 1.0,
    }

    result = svc._validate_decision_against_indicators(analysis, indicators, 56)

    assert result["decision"] == "BUY"
    assert result.get("decision_guard") is None


def test_low_confidence_direction_without_consensus_is_still_held():
    svc = _service()
    analysis = {
        "decision": "BUY",
        "confidence": 56,
        "consensus": {"consensus_decision": "HOLD", "consensus_score": 17.6},
    }

    result = svc._validate_decision_against_indicators(analysis, {}, 56)

    assert result["decision"] == "HOLD"
    assert result["decision_guard"] == "low_confidence_without_consensus"


def test_mild_direction_is_presented_without_changing_hold_action():
    svc = _service()
    outlook = {
        "next_24h": {"trend": "HOLD", "score": 17.6, "strength": "neutral"},
        "next_3d": {},
        "next_1w": {},
        "next_1m": {},
    }

    summary = build_trend_outlook_summary(outlook, "zh-CN")

    assert "轻微利多·观望" in summary


def test_crypto_factor_summary_code_is_language_neutral():
    summary = build_crypto_factor_summary_code({
        "volume_change_24h": 12.5,
        "signals": {
            "derivatives_bias": "neutral",
            "flow_bias": "bullish",
            "squeeze_risk": "low",
        },
    })

    assert summary == {
        "volume_state": "expanding",
        "bias": "bullish",
        "crowding": "low",
    }
