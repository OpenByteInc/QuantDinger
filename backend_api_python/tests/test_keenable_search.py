"""Keenable search provider: keyless request shape, optional key, and opt-in registration."""
from types import SimpleNamespace

import pytest

from app.services import search as service
from app.utils import config_loader


SEARCH_ENV = (
    "SEARCH_PROVIDER", "KEENABLE_API_KEY", "TAVILY_API_KEYS", "SERPAPI_KEYS", "ALPHA_VANTAGE_API_KEY",
    "SEARCH_GOOGLE_API_KEY", "SEARCH_GOOGLE_CX", "SEARCH_BING_API_KEY", "SEARCH_SEARXNG_BASE_URL",
)


def _response(status=200, payload=None, text=""):
    return SimpleNamespace(status_code=status, json=lambda: payload or {}, text=text)


def _capture_post(monkeypatch, response):
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(service.requests, "post", post)
    return calls


def _service(monkeypatch, search_config=None, env=None):
    for name in SEARCH_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    config = {"search": search_config or {}}
    monkeypatch.setattr(service, "load_addon_config", lambda: config)
    monkeypatch.setattr(config_loader, "load_addon_config", lambda: config)
    return service.SearchService()


def test_keyless_search_uses_public_endpoint_and_maps_snippet(monkeypatch):
    calls = _capture_post(monkeypatch, _response(payload={"results": [
        {"title": "NVDA earnings", "url": "https://www.example.com/nvda", "description": "",
         "snippet": "Nvidia reported\n\nrecord revenue " + "x" * 600, "published_at": "2026-11-02T10:00:00Z"},
        {"title": "No URL", "url": "", "snippet": "dropped"},
        {"title": "Second", "url": "https://news.example.org/a", "snippet": "Second result"},
    ]}))

    result = service.KeenableSearchProvider().search("NVDA earnings", max_results=5, days=3)

    assert result.success
    assert calls[0]["url"] == "https://api.keenable.ai/v1/search/public"
    assert calls[0]["headers"]["X-Keenable-Title"] == "QuantDinger"
    assert "X-API-Key" not in calls[0]["headers"]
    assert calls[0]["json"]["query"] == "NVDA earnings"
    assert calls[0]["json"]["max_results"] == 5
    assert len(calls[0]["json"]["published_after"]) == 10
    first = result.results[0]
    assert first.snippet.startswith("Nvidia reported record revenue")
    assert len(first.snippet) == 500
    assert first.source == "example.com"
    assert first.published_date == "2026-11-02T10:00:00Z"
    assert [item.url for item in result.results] == ["https://www.example.com/nvda", "https://news.example.org/a"]


def test_api_key_uses_keyed_endpoint_and_long_windows_skip_date_filter(monkeypatch):
    calls = _capture_post(monkeypatch, _response(payload={"results": [
        {"title": "10-K", "url": "https://www.sec.gov/report.htm", "snippet": "Annual report"},
    ]}))

    result = service.KeenableSearchProvider("keen_test").search("Apple 10-K", max_results=80, days=3650)

    assert result.success
    assert calls[0]["url"] == "https://api.keenable.ai/v1/search"
    assert calls[0]["headers"]["X-API-Key"] == "keen_test"
    assert calls[0]["json"]["max_results"] == 50
    assert "published_after" not in calls[0]["json"]


@pytest.mark.parametrize("response", [
    _response(status=429, text="Too Many Requests"),
    _response(payload={"results": []}),
])
def test_errors_and_empty_results_let_the_chain_fall_through(monkeypatch, response):
    _capture_post(monkeypatch, response)
    result = service.KeenableSearchProvider().search("query", max_results=5)
    assert not result.success
    assert result.results == []


def test_keenable_is_not_registered_unless_selected(monkeypatch):
    search = _service(monkeypatch)

    names = [provider.name for provider in search._providers]
    assert "Keenable" not in names
    assert names == ["GDELT", "DuckDuckGo"]
    status = {row["provider"]: row for row in search.provider_status()}
    assert status["Keenable"]["configured"] is False
    assert "SEARCH_PROVIDER=keenable" in status["Keenable"]["note"]


def test_selecting_keenable_puts_it_first_without_reordering_the_rest(monkeypatch):
    search = _service(monkeypatch, search_config={"provider": "keenable"})

    assert [provider.name for provider in search._providers] == ["Keenable", "GDELT", "DuckDuckGo"]
    status = {row["provider"]: row for row in search.provider_status()}
    assert status["Keenable"]["configured"] is True
    assert status["Keenable"]["available"] is True


def test_api_key_alone_registers_keenable_in_the_fallback_chain(monkeypatch):
    search = _service(monkeypatch, env={"KEENABLE_API_KEY": "keen_test", "TAVILY_API_KEYS": "tvly-test"})

    assert [provider.name for provider in search._providers] == ["Tavily", "GDELT", "Keenable", "DuckDuckGo"]
