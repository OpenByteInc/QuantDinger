import sys
from types import SimpleNamespace

import pytest

from app.services.llm import LLMProvider, LLMService
from app.utils.config_loader import clear_config_cache, load_addon_config


def _reset_config_cache():
    clear_config_cache()


def test_litellm_env_mapping(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "litellm-key")
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-20250514")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example/v1")
    _reset_config_cache()

    cfg = load_addon_config()

    assert cfg["litellm"]["api_key"] == "litellm-key"
    assert cfg["litellm"]["model"] == "anthropic/claude-sonnet-4-20250514"
    assert cfg["litellm"]["base_url"] == "https://litellm.example/v1"


def test_litellm_keeps_provider_prefixed_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "litellm")
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-20250514")
    _reset_config_cache()

    service = LLMService()

    assert service.provider == LLMProvider.LITELLM
    assert service.get_default_model() == "anthropic/claude-sonnet-4-20250514"
    assert (
        service._normalize_model_for_provider("anthropic/claude-sonnet-4-20250514", LLMProvider.LITELLM)
        == "anthropic/claude-sonnet-4-20250514"
    )


def test_litellm_provider_can_call_without_litellm_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "litellm")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    _reset_config_cache()

    captured = {}

    def fake_call(messages, model, temperature, api_key, base_url, timeout, use_json_mode=True):
        captured.update({"model": model, "api_key": api_key, "base_url": base_url})
        return "ok"

    service = LLMService()
    monkeypatch.setattr(service, "_call_litellm", fake_call)

    out = service.call_llm_api(
        [{"role": "user", "content": "hello"}],
        model="openai/gpt-4o-mini",
        try_alternative_providers=False,
        use_json_mode=False,
    )

    assert out == "ok"
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["api_key"] == ""


def test_litellm_sdk_error_is_wrapped(monkeypatch):
    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    service = LLMService(provider="litellm")

    with pytest.raises(ValueError, match="LiteLLM API error"):
        service._call_litellm(
            [{"role": "user", "content": "hello"}],
            "openai/gpt-4o-mini",
            0.7,
            "",
            "",
            30,
            use_json_mode=False,
        )


def test_litellm_response_content(monkeypatch):
    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
            )

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    service = LLMService(provider="litellm")

    out = service._call_litellm(
        [{"role": "user", "content": "hello"}],
        "openai/gpt-4o-mini",
        0.7,
        "",
        "",
        30,
        use_json_mode=False,
    )

    assert out == "hello"
