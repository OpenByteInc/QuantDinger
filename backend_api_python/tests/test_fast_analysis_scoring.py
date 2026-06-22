from app.services.fast_analysis import FastAnalysisService


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

