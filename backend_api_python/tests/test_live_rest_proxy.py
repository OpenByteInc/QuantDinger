"""Live-trading REST proxy resolution.

Regression coverage for the live-trading funnel honoring ``PROXY_URL``:
market data (CCXT) already honored it while ``BaseRestClient._request``
sent exchange REST traffic directly, so deployments that rely on
``PROXY_URL`` for exchange reachability broke only on live trading.
"""

import pytest

from app.services.live_trading import base as rest_base
from app.services.live_trading.base import BaseRestClient


@pytest.fixture(autouse=True)
def _reset_proxy_cache():
    rest_base._proxies_resolved = False
    rest_base._proxies_value = None
    yield
    rest_base._proxies_resolved = False
    rest_base._proxies_value = None


def _capture_request(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"] = method
        calls["url"] = url
        calls.update(kwargs)

        class _Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _Resp()

    monkeypatch.setattr(rest_base.requests, "request", fake_request)
    return calls


def test_explicit_proxy_url_is_applied(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://infra-01:20171")
    calls = _capture_request(monkeypatch)
    BaseRestClient("https://example.com")._request("GET", "/time")
    assert calls["proxies"] == {
        "http": "http://infra-01:20171",
        "https": "http://infra-01:20171",
    }


def test_socks5h_proxy_url_is_applied(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "socks5h://127.0.0.1:10808")
    calls = _capture_request(monkeypatch)
    BaseRestClient("https://example.com")._request("GET", "/time")
    assert calls["proxies"] == {
        "http": "socks5h://127.0.0.1:10808",
        "https": "socks5h://127.0.0.1:10808",
    }


def test_unset_proxy_url_forces_nothing(monkeypatch):
    monkeypatch.delenv("PROXY_URL", raising=False)
    calls = _capture_request(monkeypatch)
    BaseRestClient("https://example.com")._request("GET", "/time")
    assert calls["proxies"] is None  # trust_env (standard vars) stays in charge


def test_resolution_is_cached_per_process(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://first:1")
    assert rest_base._get_proxies()["http"] == "http://first:1"
    monkeypatch.setenv("PROXY_URL", "http://second:2")
    assert rest_base._get_proxies()["http"] == "http://first:1"


def test_funnel_is_single_entrypoint(monkeypatch):
    """Guard: _request must keep forwarding the resolved proxies kwarg."""
    monkeypatch.setenv("PROXY_URL", "http://infra-01:20171")
    calls = _capture_request(monkeypatch)
    client = BaseRestClient("https://example.com")
    client._request("POST", "/order", json_body={"x": 1})
    assert calls["method"] == "POST"
    assert calls["proxies"] is not None
