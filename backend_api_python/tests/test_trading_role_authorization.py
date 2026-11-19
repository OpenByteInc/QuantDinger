"""Regression tests for human trading-role authorization."""
from __future__ import annotations

import pytest

from app.routes import alpaca, ibkr, openapi_brokers
from app.utils import auth


@pytest.fixture
def viewer_authed(monkeypatch):
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda _raw: {
            "sub": "read-only-user",
            "user_id": 77,
            "role": "admin",
            "_verified_username": "read-only-user",
            "_verified_user_role": "viewer",
        },
    )
    monkeypatch.setattr(ibkr, "is_trading_venue_enabled", lambda _venue: True)
    monkeypatch.setattr(alpaca, "is_trading_venue_enabled", lambda _venue: True)
    monkeypatch.setattr(openapi_brokers, "is_trading_venue_enabled", lambda _venue: True)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/ibkr/connect"),
        ("post", "/api/ibkr/disconnect"),
        ("post", "/api/ibkr/order"),
        ("delete", "/api/ibkr/order/123"),
        ("post", "/api/alpaca/connect"),
        ("post", "/api/alpaca/disconnect"),
        ("post", "/api/alpaca/order"),
        ("delete", "/api/alpaca/order/example"),
        ("post", "/api/tiger/connect"),
        ("post", "/api/tiger/disconnect"),
        ("post", "/api/tiger/order"),
        ("delete", "/api/tiger/order/example"),
        ("post", "/api/webull/connect"),
        ("post", "/api/webull/order"),
        ("post", "/api/quick-trade/place-order"),
        ("post", "/api/quick-trade/close-position"),
        ("post", "/api/quick-trade/cancel-order"),
        ("post", "/api/quick-trade/position-protection"),
    ],
)
def test_viewer_cannot_mutate_trading_state(client, viewer_authed, method, path):
    response = client.open(
        path,
        method=method.upper(),
        headers={"Authorization": "Bearer database-backed-viewer"},
        json={},
    )

    assert response.status_code == 403
    body = response.get_json()
    assert body["msg"] == "Trading access required"
