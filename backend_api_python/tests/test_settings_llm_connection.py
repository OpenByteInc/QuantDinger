from flask import Flask


def _unwrap_handler(handler):
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    return handler


def test_openrouter_connection_route_pins_the_requested_provider(monkeypatch):
    import app.routes.settings as settings_route
    import app.services.llm as llm_module

    calls = []

    class FakeLLMService:
        def __init__(self, provider=None):
            calls.append(("init", provider))

        def test_connection(self, provider=None):
            calls.append(("test", provider))
            return True, "ok"

    monkeypatch.setattr(llm_module, "LLMService", FakeLLMService)
    app = Flask(__name__)
    handler = _unwrap_handler(settings_route.test_connection)

    with app.test_request_context(json={"service": "openrouter"}):
        response = handler()

    assert response.get_json()["code"] == 1
    assert calls == [
        ("init", llm_module.LLMProvider.OPENROUTER),
        ("test", llm_module.LLMProvider.OPENROUTER),
    ]
