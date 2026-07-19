import sys
from types import SimpleNamespace

import pytest

from app.services.llm import LLMAPIError, LLMProvider, LLMService
import app.utils.config_loader as config_loader
from app.utils.config_loader import clear_config_cache, load_addon_config


def _reset_config_cache():
    clear_config_cache()
    config_loader._env_loaded = True


def test_litellm_env_mapping(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "litellm-key")
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-20250514")
    monkeypatch.setenv("LITELLM_BASE_URL", "https://litellm.example/v1")
    _reset_config_cache()

    cfg = load_addon_config()

    assert cfg["litellm"]["api_key"] == "litellm-key"
    assert cfg["litellm"]["model"] == "anthropic/claude-sonnet-4-20250514"
    assert cfg["litellm"]["base_url"] == "https://litellm.example/v1"


def test_atlascloud_env_mapping(monkeypatch):
    monkeypatch.setenv("ATLASCLOUD_API_KEY", "atlas-key")
    monkeypatch.setenv("ATLASCLOUD_MODEL", "openai/gpt-5.4")
    monkeypatch.setenv("ATLASCLOUD_BASE_URL", "https://api.atlascloud.ai/v1")
    _reset_config_cache()

    cfg = load_addon_config()

    assert cfg["atlascloud"]["api_key"] == "atlas-key"
    assert cfg["atlascloud"]["model"] == "openai/gpt-5.4"
    assert cfg["atlascloud"]["base_url"] == "https://api.atlascloud.ai/v1"


def test_atlascloud_provider_defaults_and_model_prefix(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "atlascloud")
    monkeypatch.setenv("ATLASCLOUD_MODEL", "openai/gpt-5.4")
    _reset_config_cache()

    service = LLMService()

    assert service.provider == LLMProvider.ATLASCLOUD
    assert service.get_default_model() == "openai/gpt-5.4"
    assert service.get_base_url() == "https://api.atlascloud.ai/v1"
    assert (
        service._normalize_model_for_provider("atlascloud/deepseek-v3", LLMProvider.ATLASCLOUD)
        == "deepseek-v3"
    )
    assert (
        service._normalize_model_for_provider("openai/gpt-5.4", LLMProvider.ATLASCLOUD)
        == "openai/gpt-5.4"
    )
    assert service._detect_provider_from_model("atlascloud/deepseek-v3") == LLMProvider.ATLASCLOUD


def test_atlascloud_openai_compatible_call_skips_response_format(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "{\"ok\": true}"}}]}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("app.services.llm.requests.post", fake_post)
    service = LLMService(provider="atlascloud")

    out = service._call_openai_compatible(
        [{"role": "user", "content": "hello"}],
        "deepseek-v3",
        0.7,
        "atlas-key",
        "https://api.atlascloud.ai/v1",
        30,
        use_json_mode=True,
    )

    assert out == "{\"ok\": true}"
    assert captured["url"] == "https://api.atlascloud.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer atlas-key"
    assert captured["json"]["model"] == "deepseek-v3"
    assert "response_format" not in captured["json"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"message": "Unsupported parameter: temperature"}, "Unsupported parameter: temperature"),
        ({"error": {"message": "Model access denied"}}, "Model access denied"),
        (
            {
                "detail": [
                    {
                        "loc": ["body", "messages", 0, "content"],
                        "msg": "Value is not valid",
                    }
                ]
            },
            "body.messages.0.content: Value is not valid",
        ),
    ],
)
def test_atlascloud_http_error_preserves_provider_detail(monkeypatch, payload, expected):
    class FakeResponse:
        status_code = 400
        headers = {"x-request-id": "atlas-request-42"}
        text = ""

        def json(self):
            return payload

    service = LLMService(provider="atlascloud")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(LLMAPIError) as exc_info:
        service._call_openai_compatible(
            [{"role": "user", "content": "hello"}],
            "openai/gpt-5.4",
            0.4,
            "atlas-key",
            "https://api.atlascloud.ai/v1",
            30,
            use_json_mode=False,
        )

    message = str(exc_info.value)
    assert "AtlasCloud API 400" in message
    assert "model=openai/gpt-5.4" in message
    assert "request_id=atlas-request-42" in message
    assert expected in message
    assert exc_info.value.status_code == 400


def test_atlascloud_http_error_falls_back_to_plain_response_text(monkeypatch):
    class FakeResponse:
        status_code = 400
        headers = {}
        text = "invalid request body"

        def json(self):
            raise ValueError("not json")

    service = LLMService(provider="atlascloud")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(LLMAPIError, match="invalid request body"):
        service._call_openai_compatible(
            [{"role": "user", "content": "hello"}],
            "deepseek-v3",
            0.4,
            "atlas-key",
            "https://api.atlascloud.ai/v1",
            30,
            use_json_mode=False,
        )


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


@pytest.mark.parametrize(
    ("provider", "api_key", "base_url", "expected"),
    [
        ("openai", "openai-key", "https://api.openai.com/v1", True),
        ("openai", "", "https://api.openai.com/v1", False),
        ("custom", "", "http://127.0.0.1:11434/v1", True),
        ("custom", "", "", False),
        ("litellm", "", "", True),
    ],
)
def test_llm_configuration_readiness(monkeypatch, provider, api_key, base_url, expected):
    service = LLMService(provider=provider)
    monkeypatch.setattr(service, "get_api_key", lambda selected=None: api_key)
    monkeypatch.setattr(service, "get_base_url", lambda selected=None: base_url)

    assert service.is_configured() is expected


def test_custom_provider_can_call_without_api_key_when_base_url_is_configured(monkeypatch):
    captured = {}
    service = LLMService(provider="custom")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "http://127.0.0.1:11434/v1")

    def fake_call(messages, model, temperature, api_key, base_url, timeout, use_json_mode=True):
        captured.update({"model": model, "api_key": api_key, "base_url": base_url})
        return "ok"

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    out = service.call_llm_api(
        [{"role": "user", "content": "hello"}],
        model="local-model",
        try_alternative_providers=False,
        use_json_mode=False,
    )

    assert out == "ok"
    assert captured == {
        "model": "local-model",
        "api_key": "",
        "base_url": "http://127.0.0.1:11434/v1",
    }


def test_litellm_stream_can_run_without_litellm_api_key(monkeypatch):
    service = LLMService(provider="litellm")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "")
    monkeypatch.setattr(service, "call_llm_api", lambda *args, **kwargs: "streamed")

    assert list(service.stream_llm_api([{"role": "user", "content": "hello"}])) == ["streamed"]


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
