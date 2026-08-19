import pytest

import app.routes.ai_chat as ai_chat

from app.routes.ai_chat import (
    _agent_response_language_name,
    _build_system_prompt,
    _fallback_agent_intent,
    _format_kline_time_utc,
    _normalize_agent_intent,
    _stream_llm_with_recovery,
    _summarize_klines,
)
from app.services.llm import LLMAPIError


def test_kline_timestamp_is_exposed_as_readable_utc():
    bars = [
        {
            "time": 1_784_217_600 + index * 3600,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1000 + index,
        }
        for index in range(5)
    ]

    summary = _summarize_klines(bars, "1H")

    assert summary["latest_time"] == bars[-1]["time"]
    assert summary["latest_time_utc"].endswith("+00:00")
    assert summary["latest_time_utc"].startswith("2026-")


def test_kline_timestamp_formatter_accepts_milliseconds_and_iso_strings():
    assert _format_kline_time_utc(1_784_217_600_000) == "2026-07-16T16:00:00+00:00"
    assert _format_kline_time_utc("2026-07-16T16:00:00Z") == "2026-07-16T16:00:00+00:00"


def test_agent_response_language_follows_every_ui_locale():
    expected = {
        "ar-SA": "Arabic",
        "de-DE": "German",
        "en-US": "English",
        "fr-FR": "French",
        "ja-JP": "Japanese",
        "ko-KR": "Korean",
        "ru-RU": "Russian",
        "th-TH": "Thai",
        "vi-VN": "Vietnamese",
        "zh-CN": "Simplified Chinese",
        "zh-TW": "Traditional Chinese",
    }

    for locale, language_name in expected.items():
        assert _agent_response_language_name(locale) == language_name
        prompt = _build_system_prompt(locale, {}, "general", False, json_response=False)
        assert f"Reply in {language_name}." in prompt


@pytest.mark.parametrize(
    ("locale", "message"),
    [
        ("ja-JP", "この銘柄のチャートインジケーターを作成してください"),
        ("ko-KR", "이 종목에 실행 가능한 차트 지표를 만들어 주세요"),
        ("de-DE", "Erstelle einen ausführbaren Indikator für dieses Symbol"),
        ("fr-FR", "Crée un indicateur exécutable pour ce symbole"),
        ("ru-RU", "Создай исполняемый индикатор для этого инструмента"),
        ("vi-VN", "Tạo chỉ báo có thể chạy cho mã này"),
        ("th-TH", "สร้างอินดิเคเตอร์ที่ใช้งานได้สำหรับสินทรัพย์นี้"),
        ("ar-SA", "أنشئ مؤشرًا قابلاً للتنفيذ لهذا الرمز"),
    ],
)
def test_agent_fallback_executes_indicator_workflow_in_ui_languages(locale, message):
    plan = _fallback_agent_intent(
        message,
        False,
        {"market": "Crypto", "symbol": "BTC/USDT"},
        locale,
    )

    assert plan["intent"] == "strategy_build"
    assert plan["should_execute"] is True
    assert plan["target_type"] == "indicator"
    assert plan["workflow"] == "indicator_ide"


def test_agent_intent_preserves_strategy_instrument_context():
    context = {
        "market": "Crypto",
        "symbol": "SOL/USDT",
        "timeframe": "4H",
        "exchange_id": "okx",
        "market_type": "swap",
        "instrument_id": "SOL-USDT-SWAP",
    }
    fallback = _fallback_agent_intent(
        "Create a runnable Python strategy",
        False,
        context,
        "en-US",
    )
    normalized = _normalize_agent_intent(
        {
            "intent": "strategy_build",
            "target_type": "script",
            "workflow": "script_strategy",
            "should_execute": True,
            "entities": {},
        },
        "Create a runnable Python strategy",
        False,
        context,
        "en-US",
    )

    for plan in (fallback, normalized):
        assert plan["entities"]["timeframe"] == "4h"
        assert plan["entities"]["timeframes"] == ["4h"]
        assert plan["entities"]["exchange_id"] == "okx"
        assert plan["entities"]["market_type"] == "swap"
        assert plan["entities"]["instrument_id"] == "SOL-USDT-SWAP"


def test_agent_intent_preserves_every_requested_strategy_timeframe():
    message = "Create a BTC strategy using 1d, 4h and 15m signals"
    plan = _normalize_agent_intent(
        {
            "intent": "strategy_build",
            "target_type": "script",
            "workflow": "script_strategy",
            "should_execute": True,
            "entities": {"symbol": "BTC/USDT", "timeframes": ["1d", "4h", "15m"]},
        },
        message,
        False,
        {"market": "Crypto", "symbol": "BTC/USDT"},
        "en-US",
    )

    assert plan["entities"]["timeframes"] == ["1d", "4h", "15m"]
    assert plan["entities"]["timeframe"] == "15m"


def test_explicit_single_timeframe_does_not_inherit_selected_chart_timeframe():
    message = "Create a BTC strategy using a 1m golden cross"
    context = {
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "timeframe": "4h",
        "timeframes": ["4h"],
    }
    fallback = _fallback_agent_intent(message, False, context, "en-US")
    normalized = _normalize_agent_intent(
        {
            "intent": "strategy_build",
            "target_type": "script",
            "workflow": "script_strategy",
            "should_execute": True,
            "entities": {"symbol": "BTC/USDT", "timeframe": "1m"},
        },
        message,
        False,
        context,
        "en-US",
    )

    for plan in (fallback, normalized):
        assert plan["entities"]["timeframes"] == ["1m"]
        assert plan["entities"]["timeframe"] == "1m"


def test_stream_recovery_replaces_partial_provider_output(monkeypatch):
    class FakeLLMService:
        def stream_llm_api(self, messages, temperature):
            yield "partial"
            raise LLMAPIError(
                "upstream stream closed",
                status_code=502,
                error_type="provider_unavailable",
            )

        def call_llm_api(self, messages, temperature, use_json_mode):
            return "complete recovered answer"

    monkeypatch.setattr(ai_chat, "LLMService", FakeLLMService)

    events = list(_stream_llm_with_recovery([{"role": "user", "content": "hello"}]))

    assert events == [
        ("delta", {"text": "partial"}),
        ("replace", {"text": "complete recovered answer", "recovered": True}),
    ]


def test_stream_output_limit_keeps_partial_without_regeneration(monkeypatch):
    calls = {"fixed": 0}

    class FakeLLMService:
        def stream_llm_api(self, messages, temperature):
            yield "partial"
            raise LLMAPIError(
                "output limit",
                status_code=400,
                error_type="max_tokens_exceeded",
                finish_reason="length",
                retryable=False,
            )

        def call_llm_api(self, *args, **kwargs):
            calls["fixed"] += 1
            return "must not run"

    monkeypatch.setattr(ai_chat, "LLMService", FakeLLMService)

    events = list(_stream_llm_with_recovery([{"role": "user", "content": "hello"}]))

    assert events[0] == ("delta", {"text": "partial"})
    assert events[1][0] == "warning"
    assert events[1][1]["truncated"] is True
    assert calls["fixed"] == 0


def test_stream_non_retryable_error_does_not_regenerate(monkeypatch):
    calls = {"fixed": 0}

    class FakeLLMService:
        def stream_llm_api(self, messages, temperature):
            raise LLMAPIError(
                "content blocked",
                status_code=400,
                error_type="content_policy_violation",
                finish_reason="content_filter",
                retryable=False,
            )
            yield  # pragma: no cover

        def call_llm_api(self, *args, **kwargs):
            calls["fixed"] += 1
            return "must not run"

    monkeypatch.setattr(ai_chat, "LLMService", FakeLLMService)

    with pytest.raises(LLMAPIError, match="content blocked"):
        list(_stream_llm_with_recovery([{"role": "user", "content": "hello"}]))

    assert calls["fixed"] == 0
