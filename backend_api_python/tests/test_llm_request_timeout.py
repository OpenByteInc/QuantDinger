import json

import pytest
import requests

from app.routes import ai_chat
from app.services import llm
from app.services.llm import LLMAPIError, LLMProvider, LLMService


@pytest.mark.parametrize("timeout", [None, 20])
def test_safe_call_forwards_only_an_explicit_timeout(monkeypatch, timeout):
    service = LLMService()
    captured = {}

    def call(messages, **kwargs):
        captured.update(kwargs)
        return '{"ok": true}'

    monkeypatch.setattr(service, "call_llm_api", call)
    assert service.safe_call_llm("system", "user", {}, timeout_seconds=timeout) == {"ok": True}
    assert captured == {"model": None, "provider": None, **({"timeout_seconds": 20} if timeout else {})}


def configured_service(monkeypatch, provider_timeout):
    service = LLMService(provider="custom")
    monkeypatch.setattr(llm, "load_addon_config", lambda: {
        "llm": {"provider": "custom"}, "custom": {"timeout": provider_timeout}
    })
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "test-key")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "https://gateway.example/v1")
    monkeypatch.setattr(service, "get_default_model", lambda provider=None: "fallback-model")
    monkeypatch.setattr(service, "_record_model_use", lambda *args: None)
    return service


@pytest.mark.parametrize("provider_timeout, expected", [(120, 20), (5, 5)])
def test_timeout_caps_every_model_attempt_without_increasing_provider_limit(monkeypatch, provider_timeout, expected):
    service = configured_service(monkeypatch, provider_timeout)
    attempts = []

    def call(messages, model, temperature, api_key, base_url, timeout, **kwargs):
        attempts.append((model, timeout))
        if len(attempts) == 1:
            raise LLMAPIError("model unavailable", status_code=404)
        return '{"ok": true}'

    monkeypatch.setattr(service, "_call_openai_compatible", call)
    assert service.safe_call_llm("system", "user", {}, model="first-model", timeout_seconds=20) == {"ok": True}
    assert attempts == [("first-model", expected), ("fallback-model", expected)]


@pytest.mark.parametrize("failure", ["structured", "http"])
def test_alternative_provider_retains_timeout_cap(monkeypatch, failure):
    service = configured_service(monkeypatch, 120)
    monkeypatch.setattr(service, "_normalize_model_for_provider", lambda model, provider: "only-model")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "key" if provider in (None, LLMProvider.CUSTOM, LLMProvider.DEEPSEEK) else "")
    attempts = []

    def call(messages, model, temperature, api_key, base_url, timeout, **kwargs):
        attempts.append(timeout)
        if len(attempts) == 1:
            if failure == "structured":
                raise LLMAPIError("forbidden", status_code=403)
            response = requests.Response()
            response.status_code = 403
            raise requests.HTTPError("forbidden", response=response)
        return '{"ok": true}'

    monkeypatch.setattr(service, "_call_openai_compatible", call)
    assert service.safe_call_llm("system", "user", {}, timeout_seconds=20) == {"ok": True}
    assert attempts == [20, 20]


def test_intent_timeout_uses_existing_fallback_and_keeps_context(monkeypatch):
    service = LLMService()
    captured = []

    def call(messages, **kwargs):
        captured.append((json.loads(messages[1]["content"]), kwargs))
        raise requests.Timeout("intent request timed out")

    monkeypatch.setattr(service, "call_llm_api", call)
    monkeypatch.setattr(ai_chat, "LLMService", lambda: service)
    context = {"symbol": "BTC/USDT", "market": "Crypto", "timeframe": "1h"}
    expected = ai_chat._fallback_agent_intent("Analyze BTC trend", False, context, "en-US")
    actual = ai_chat._classify_agent_intent("Analyze BTC trend", [], context, "en-US")
    assert actual["intent"] == expected["intent"]
    assert actual["workflow"] == expected["workflow"]
    assert "intent request timed out" in actual["error"]
    assert captured[0][1]["timeout_seconds"] == 20
    assert captured[0][0]["selected_context"]["symbol"] == "BTC/USDT"
