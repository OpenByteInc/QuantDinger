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


def test_uniform_llm_max_tokens_env_mapping(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "16384")
    _reset_config_cache()

    cfg = load_addon_config()

    assert cfg["llm"]["max_tokens"] == 16384
    assert LLMService().get_max_tokens() == 16384


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
    monkeypatch.setattr(service, "get_max_tokens", lambda: 16384)

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
    assert captured["json"]["max_tokens"] == 16384
    assert "response_format" not in captured["json"]


def test_google_gemini_uses_uniform_max_tokens(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "{\"ok\": true}"}]}}
                ]
            }

    service = LLMService(provider="google")
    monkeypatch.setattr(service, "get_max_tokens", lambda: 16384)

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(service, "_llm_post", fake_post)

    out = service._call_google_gemini(
        [{"role": "user", "content": "hello"}],
        "gemini-1.5-flash",
        0.7,
        "google-key",
        "https://generativelanguage.googleapis.com/v1beta",
        30,
    )

    assert out == "{\"ok\": true}"
    assert captured["json_payload"]["generationConfig"]["maxOutputTokens"] == 16384


@pytest.mark.parametrize(
    ("finish_reason", "expected_type", "retryable"),
    [
        ("MAX_TOKENS", "max_tokens_exceeded", False),
        ("SAFETY", "content_policy_violation", False),
        ("OTHER", "provider_unavailable", True),
    ],
)
def test_google_gemini_classifies_finish_reason(
    monkeypatch,
    finish_reason,
    expected_type,
    retryable,
):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {"parts": [{"text": "partial"}]},
                    "finishReason": finish_reason,
                }]
            }

    service = LLMService(provider="google")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(LLMAPIError) as exc_info:
        service._call_google_gemini(
            [{"role": "user", "content": "hello"}],
            "gemini-1.5-flash",
            0.7,
            "google-key",
            "https://generativelanguage.googleapis.com/v1beta",
            30,
        )

    assert exc_info.value.error_type == expected_type
    assert exc_info.value.retryable is retryable


def test_openai_compatible_stream_uses_uniform_max_tokens(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            return [b"data: [DONE]"]

        def close(self):
            return None

    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_max_tokens", lambda: 16384)

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(service, "_llm_post", fake_post)

    chunks = list(
        service._stream_openai_compatible(
            [{"role": "user", "content": "hello"}],
            "openai/gpt-5.4",
            0.7,
            "openrouter-key",
            "https://openrouter.ai/api/v1",
            30,
        )
    )

    assert chunks == []
    assert captured["json_payload"]["max_tokens"] == 16384


def test_openai_compatible_stream_rejects_provider_error_frame(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {
            "x-request-id": "stream-request-42",
            "x-generation-id": "generation-42",
        }

        def iter_lines(self, decode_unicode=False):
            return [
                b'data: {"error":{"code":429,"message":"provider overloaded","metadata":{"error_type":"provider_overloaded"}}}',
            ]

        def close(self):
            return None

    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(LLMAPIError, match="provider overloaded") as exc_info:
        list(
            service._stream_openai_compatible(
                [{"role": "user", "content": "hello"}],
                "openai/gpt-5.4",
                0.7,
                "openrouter-key",
                "https://openrouter.ai/api/v1",
                30,
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.request_id == "stream-request-42"
    assert exc_info.value.generation_id == "generation-42"
    assert exc_info.value.error_type == "provider_overloaded"
    assert exc_info.value.retryable is True


def test_openai_compatible_stream_rejects_premature_eof(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

        def iter_lines(self, decode_unicode=False):
            return [
                b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
            ]

        def close(self):
            return None

    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())
    stream = service._stream_openai_compatible(
        [{"role": "user", "content": "hello"}],
        "openai/gpt-5.4",
        0.7,
        "openrouter-key",
        "https://openrouter.ai/api/v1",
        30,
    )

    assert next(stream) == "partial"
    with pytest.raises(LLMAPIError, match="before a terminal event") as exc_info:
        next(stream)

    assert exc_info.value.error_type == "premature_eof"
    assert exc_info.value.retryable is True


def test_openai_compatible_stream_accepts_finish_reason_stop_without_done(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

        def iter_lines(self, decode_unicode=False):
            return [
                b'data: {"choices":[{"delta":{"content":"complete"},"finish_reason":"stop"}]}',
            ]

        def close(self):
            return None

    service = LLMService(provider="openai")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())

    assert list(service._stream_openai_compatible(
        [{"role": "user", "content": "hello"}],
        "gpt-5.4",
        0.7,
        "openai-key",
        "https://api.openai.com/v1",
        30,
    )) == ["complete"]


@pytest.mark.parametrize(
    ("finish_reason", "expected_type", "retryable"),
    [
        ("length", "max_tokens_exceeded", False),
        ("content_filter", "content_policy_violation", False),
        ("insufficient_system_resource", "provider_unavailable", True),
    ],
)
def test_openai_compatible_stream_classifies_terminal_reasons(
    monkeypatch,
    finish_reason,
    expected_type,
    retryable,
):
    class FakeResponse:
        status_code = 200
        headers = {}

        def iter_lines(self, decode_unicode=False):
            payload = {
                "choices": [{
                    "delta": {"content": "partial"},
                    "finish_reason": finish_reason,
                }]
            }
            return [f"data: {__import__('json').dumps(payload)}".encode()]

        def close(self):
            return None

    service = LLMService(provider="deepseek")
    monkeypatch.setattr(service, "_llm_post", lambda *args, **kwargs: FakeResponse())
    stream = service._stream_openai_compatible(
        [{"role": "user", "content": "hello"}],
        "deepseek-chat",
        0.7,
        "deepseek-key",
        "https://api.deepseek.com/v1",
        30,
    )

    assert next(stream) == "partial"
    with pytest.raises(LLMAPIError) as exc_info:
        next(stream)

    assert exc_info.value.finish_reason == finish_reason
    assert exc_info.value.error_type == expected_type
    assert exc_info.value.retryable is retryable


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


def test_atlascloud_uses_configured_model_before_static_fallback(monkeypatch):
    attempted = []
    service = LLMService(provider="atlascloud")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "atlas-key")
    monkeypatch.setattr(
        service,
        "get_base_url",
        lambda provider=None: "https://api.atlascloud.ai/v1",
    )
    monkeypatch.setattr(
        service,
        "get_default_model",
        lambda provider=None: "openai/gpt-5.4",
    )

    def fake_call(messages, model, *args, **kwargs):
        attempted.append(model)
        if model == "openai/gpt-5.3-codex":
            raise LLMAPIError(
                "AtlasCloud API 400 (model=openai/gpt-5.3-codex): not found",
                status_code=400,
            )
        return "generated code"

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    result = service.call_llm_api(
        [{"role": "user", "content": "generate a strategy"}],
        model="openai/gpt-5.3-codex",
        provider=LLMProvider.ATLASCLOUD,
        use_json_mode=False,
        try_alternative_providers=False,
    )

    assert result == "generated code"
    assert attempted == ["openai/gpt-5.3-codex", "openai/gpt-5.4"]


def test_atlascloud_reports_every_failed_model_attempt(monkeypatch):
    service = LLMService(provider="atlascloud")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "atlas-key")
    monkeypatch.setattr(
        service,
        "get_base_url",
        lambda provider=None: "https://api.atlascloud.ai/v1",
    )
    monkeypatch.setattr(
        service,
        "get_default_model",
        lambda provider=None: "openai/gpt-5.4",
    )

    def fake_call(messages, model, *args, **kwargs):
        raise LLMAPIError(
            f"AtlasCloud API 400 (model={model}): not found",
            status_code=400,
            request_id="atlas-request-99",
        )

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    with pytest.raises(LLMAPIError) as exc_info:
        service.call_llm_api(
            [{"role": "user", "content": "generate a strategy"}],
            model="openai/gpt-5.3-codex",
            provider=LLMProvider.ATLASCLOUD,
            use_json_mode=False,
            try_alternative_providers=False,
        )

    message = str(exc_info.value)
    assert "All model calls failed for atlascloud" in message
    assert "openai/gpt-5.3-codex" in message
    assert "openai/gpt-5.4" in message
    assert "deepseek-v3" not in message


def test_explicit_atlascloud_provider_is_not_replaced_by_model_prefix(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "atlascloud")
    _reset_config_cache()
    service = LLMService()
    captured = {}

    monkeypatch.setattr(
        service,
        "get_api_key",
        lambda provider=None: "configured-key",
    )
    monkeypatch.setattr(
        service,
        "get_base_url",
        lambda provider=None: f"https://{(provider or service.provider).value}.example/v1",
    )
    monkeypatch.setattr(
        service,
        "get_default_model",
        lambda provider=None: "openai/gpt-5.4",
    )

    def fake_call(messages, model, temperature, api_key, base_url, timeout, use_json_mode=True):
        captured.update({"model": model, "api_key": api_key, "base_url": base_url})
        return "generated code"

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    result = service.call_llm_api(
        [{"role": "user", "content": "generate a strategy"}],
        model="deepseek/deepseek-v3",
        use_json_mode=False,
        try_alternative_providers=False,
    )

    assert result == "generated code"
    assert captured["model"] == "deepseek/deepseek-v3"
    assert captured["base_url"] == "https://atlascloud.example/v1"


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

    with pytest.raises(LLMAPIError, match="LiteLLM API error") as exc_info:
        service._call_litellm(
            [{"role": "user", "content": "hello"}],
            "openai/gpt-4o-mini",
            0.7,
            "",
            "",
            30,
            use_json_mode=False,
        )

    assert exc_info.value.retryable is True


def test_litellm_response_content(monkeypatch):
    captured = {}

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
            )

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    service = LLMService(provider="litellm")
    monkeypatch.setattr(service, "get_max_tokens", lambda: 16384)

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
    assert captured["max_tokens"] == 16384


class _FakeHttpGetResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_test_connection_requires_configured_key(monkeypatch):
    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "")

    ok, detail = service.test_connection()

    assert ok is False
    assert "not configured" in detail


def test_test_connection_uses_authenticated_endpoint_for_openrouter(monkeypatch):
    captured = {}
    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "openrouter-key")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "https://openrouter.ai/api/v1")

    def fake_get(url, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _FakeHttpGetResponse(200, {"data": {}})

    monkeypatch.setattr("app.services.llm.requests.get", fake_get)

    ok, detail = service.test_connection()

    assert ok is True
    # /models is public on OpenRouter, so it cannot validate a key.
    assert captured["url"] == "https://openrouter.ai/api/v1/auth/key"
    assert captured["headers"]["Authorization"] == "Bearer openrouter-key"
    assert "reachable" in detail


def test_test_connection_flags_openrouter_provisioning_key(monkeypatch):
    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "provisioning-key")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "https://openrouter.ai/api/v1")
    monkeypatch.setattr(
        "app.services.llm.requests.get",
        lambda *args, **kwargs: _FakeHttpGetResponse(200, {"data": {"is_provisioning_key": True}}),
    )

    ok, detail = service.test_connection()

    assert ok is False
    # Such a key passes /auth/key but chat completions reject it with 401.
    assert "provisioning/management" in detail


def test_test_connection_reports_http_failure(monkeypatch):
    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "bad-key")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "https://openrouter.ai/api/v1")
    monkeypatch.setattr(
        "app.services.llm.requests.get",
        lambda *args, **kwargs: _FakeHttpGetResponse(401, None, '{"error":"unauthorized"}'),
    )

    ok, detail = service.test_connection()

    assert ok is False
    assert "HTTP 401" in detail


def test_test_connection_never_puts_google_key_in_url(monkeypatch):
    captured = {}
    service = LLMService(provider="google")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "google-key")
    monkeypatch.setattr(
        service,
        "get_base_url",
        lambda provider=None: "https://generativelanguage.googleapis.com/v1beta",
    )

    def fake_get(url, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers})
        return _FakeHttpGetResponse(200, {})

    monkeypatch.setattr("app.services.llm.requests.get", fake_get)

    ok, _ = service.test_connection()

    assert ok is True
    assert captured["headers"]["x-goog-api-key"] == "google-key"
    assert "google-key" not in captured["url"]


def _openrouter_service_with(monkeypatch, default_model="deepseek/deepseek-v4.1-flash"):
    service = LLMService(provider="openrouter")
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "openrouter-key")
    monkeypatch.setattr(
        service, "get_base_url", lambda provider=None: "https://openrouter.ai/api/v1"
    )
    monkeypatch.setattr(service, "get_default_model", lambda provider=None: default_model)
    return service


def test_model_fallback_is_recorded_when_primary_model_fails(monkeypatch):
    attempted = []
    service = _openrouter_service_with(monkeypatch)

    def fake_call(messages, model, *args, **kwargs):
        attempted.append(model)
        if model == "does/not-exist":
            raise LLMAPIError(
                "OpenRouter API 400 (model=does/not-exist): not a valid model ID",
                status_code=400,
            )
        return "generated code"

    monkeypatch.setattr(service, "_call_openai_compatible", fake_call)

    out = service.call_llm_api(
        [{"role": "user", "content": "hi"}],
        model="does/not-exist",
        use_json_mode=False,
        try_alternative_providers=False,
    )

    assert out == "generated code"
    assert attempted == ["does/not-exist", "deepseek/deepseek-v4.1-flash"]
    # The caller must be able to tell that a different model answered.
    assert service.last_model_used == "deepseek/deepseek-v4.1-flash"
    assert service.last_provider_used == "openrouter"
    assert service.model_fallback_used is True


def test_model_fallback_flag_is_false_when_primary_model_answers(monkeypatch):
    service = _openrouter_service_with(monkeypatch)
    monkeypatch.setattr(service, "_call_openai_compatible", lambda *args, **kwargs: "generated code")

    out = service.call_llm_api(
        [{"role": "user", "content": "hi"}],
        model="deepseek/deepseek-v4.1-flash",
        use_json_mode=False,
        try_alternative_providers=False,
    )

    assert out == "generated code"
    assert service.last_model_used == "deepseek/deepseek-v4.1-flash"
    assert service.model_fallback_used is False
