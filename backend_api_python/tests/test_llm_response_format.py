import json

import pytest
import requests

from app.services import llm
from app.services.llm import LLMAPIError, LLMService


def response(status=200, payload=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload if payload is not None else {
        "choices": [{"message": {"content": '{"ok": true}'}}]
    }).encode()
    result.headers["x-request-id"] = "synthetic-request-id"
    return result


@pytest.fixture
def gateway(monkeypatch):
    service = LLMService(provider="custom")
    monkeypatch.setattr(llm, "load_addon_config", lambda: {"llm": {"provider": "custom"}})
    monkeypatch.setattr(service, "get_api_key", lambda provider=None: "synthetic-key")
    monkeypatch.setattr(service, "get_base_url", lambda provider=None: "https://gateway.example/v1")
    monkeypatch.setattr(service, "get_default_model", lambda provider=None: "custom-model")
    monkeypatch.setattr(service, "get_max_tokens", lambda: 1000)
    monkeypatch.setattr(service, "_record_model_use", lambda *args: None)
    return service


def transport(monkeypatch, gateway, replies):
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return replies[len(calls) - 1]

    monkeypatch.setattr(gateway, "_llm_post", post)
    return calls


@pytest.mark.parametrize("error", [
    {"param": "response_format", "code": "unsupported_parameter", "message": "Unsupported parameter"},
    {"param": "response_format", "message": "This parameter is not supported"},
    {"message": "Unsupported parameter: 'response_format'."},
    {"message": "Unknown argument: response_format"},
    {"message": "'response_format' is not supported by this model"},
    {"message": "This endpoint does not support response_format"},
])
@pytest.mark.parametrize("status", [400, 422])
def test_explicit_rejection_retries_same_custom_request_once(monkeypatch, gateway, error, status):
    calls = transport(monkeypatch, gateway, [response(status, {"error": error}), response()])
    assert gateway.safe_call_llm("Return JSON", "hello", {}) == {"ok": True}
    assert len(calls) == 2
    first, second = calls
    assert first["json_payload"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in second["json_payload"]
    assert {key: value for key, value in first["json_payload"].items() if key != "response_format"} == second["json_payload"]
    assert {key: value for key, value in first.items() if key != "json_payload"} == {
        key: value for key, value in second.items() if key != "json_payload"
    }


@pytest.mark.parametrize("json_mode", [True, False])
def test_supporting_gateway_keeps_requested_json_mode(monkeypatch, gateway, json_mode):
    calls = transport(monkeypatch, gateway, [response()])
    gateway.call_llm_api([], use_fallback=False, use_json_mode=json_mode)
    assert len(calls) == 1
    assert ("response_format" in calls[0]["json_payload"]) is json_mode


@pytest.mark.parametrize("status, payload", [
    (400, {"error": {"param": "temperature", "code": "unsupported_parameter"}}),
    (400, {"error": {"param": "response_format", "message": "JSON must appear in messages"}}),
    (400, {"error": {"message": "Invalid model"}}),
    (400, {"error": {"message": "The response_format value must be an object"}}),
    (401, {"error": {"param": "response_format", "code": "unsupported_parameter"}}),
    (429, {"error": {"param": "response_format", "code": "unsupported_parameter"}}),
    (500, {"error": {"param": "response_format", "code": "unsupported_parameter"}}),
    (400, ["unsupported response_format"]),
    (400, {"error": "unsupported response_format"}),
])
def test_unrelated_failures_do_not_trigger_compatibility_retry(monkeypatch, gateway, status, payload):
    calls = transport(monkeypatch, gateway, [response(status, payload)])
    with pytest.raises(LLMAPIError):
        gateway.call_llm_api([], use_fallback=False, try_alternative_providers=False)
    assert len(calls) == 1


def test_compatibility_retry_is_bounded_and_retains_error_metadata(monkeypatch, gateway):
    rejected = {"error": {"param": "response_format", "code": "unsupported_parameter"}}
    calls = transport(monkeypatch, gateway, [response(400, rejected), response(400, rejected)])
    with pytest.raises(LLMAPIError) as caught:
        gateway.call_llm_api([], use_fallback=False)
    assert len(calls) == 2
    assert caught.value.status_code == 400
    assert caught.value.request_id == "synthetic-request-id"


def test_successful_non_json_output_uses_existing_parse_fallback(monkeypatch, gateway):
    calls = transport(monkeypatch, gateway, [response(payload={"choices": [{"message": {"content": "invalid JSON"}}]})])
    result = gateway.safe_call_llm("Return JSON", "hello", {"ok": False})
    assert result["ok"] is False
    assert result["report"].startswith("Failed to parse analysis result JSON")
    assert len(calls) == 1


def test_other_providers_do_not_enable_custom_gateway_retry(monkeypatch, gateway):
    calls = transport(monkeypatch, gateway, [response(400, {
        "error": {"param": "response_format", "code": "unsupported_parameter"}
    })])
    with pytest.raises(LLMAPIError):
        gateway._call_openai_compatible([], "model", 0.7, "key", "https://api.example/v1", 20)
    assert len(calls) == 1
