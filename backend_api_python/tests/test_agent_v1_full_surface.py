"""Focused contracts for the expanded Agent Gateway and safety middleware."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from flask import g

from app.routes.agent_v1 import quick_trade, research, trading_data
from app.utils import agent_auth


def _token(scopes: str = "R,W,B,N,T") -> dict:
    return {
        "id": 501,
        "user_id": 7,
        "name": "full-surface-agent",
        "scopes": scopes,
        "markets": "*",
        "instruments": "*",
        "paper_only": True,
        "rate_limit_per_min": 100,
        "max_order_notional": 1000,
        "max_daily_notional": 5000,
        "status": "active",
        "expires_at": None,
    }


def _headers(*, key: str | None = None) -> dict:
    headers = {"Authorization": "Bearer qd_agent_FULLSURFACE12345"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    agent_auth._schema_ready = True
    agent_auth._rate_state.clear()
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda _raw: _token())
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *args, **kwargs: None)
    yield
    agent_auth._rate_state.clear()


def test_rate_limit_headers_are_returned(client):
    response = client.get("/api/agent/v1/whoami", headers=_headers())
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert int(response.headers["X-RateLimit-Remaining"]) == 99
    assert int(response.headers["X-RateLimit-Reset"]) > 0


def test_mutating_scope_requires_idempotency_key(client):
    response = client.post(
        "/api/agent/v1/research/watchlist",
        headers=_headers(),
        json={"market": "USStock", "symbol": "AAPL"},
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.get_json()["message"]


def test_completed_idempotent_response_is_replayed(client, monkeypatch):
    monkeypatch.setattr(
        agent_auth,
        "_reserve_idempotency",
        lambda *_: (
            "completed",
            {
                "response_body": {
                    "code": 0,
                    "message": "added",
                    "data": {"market": "USStock", "symbol": "AAPL"},
                },
                "response_status": 200,
            },
        ),
    )
    monkeypatch.setattr(
        research,
        "add_watchlist_item",
        lambda *_args, **_kwargs: pytest.fail("replayed request reached route logic"),
    )
    response = client.post(
        "/api/agent/v1/research/watchlist",
        headers=_headers(key="watchlist-aapl"),
        json={"market": "USStock", "symbol": "AAPL"},
    )
    assert response.status_code == 200
    assert response.headers["Idempotent-Replayed"] == "true"
    assert response.get_json()["data"]["symbol"] == "AAPL"


def test_reused_key_with_different_payload_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        agent_auth,
        "_reserve_idempotency",
        lambda *_: ("mismatch", {"request_hash": "different"}),
    )
    response = client.post(
        "/api/agent/v1/research/watchlist",
        headers=_headers(key="reused-key"),
        json={"market": "USStock", "symbol": "MSFT"},
    )
    assert response.status_code == 409


def test_agent_asset_search_returns_logical_catalog_ids(client, monkeypatch):
    monkeypatch.setattr(
        research,
        "search_market_assets",
        lambda **kwargs: [{"asset_id": 41, "market": "Crypto", "symbol": "BTC/USDT"}],
    )

    response = client.get(
        "/api/agent/v1/research/assets?market=Crypto&keyword=BTC",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["items"][0]["asset_id"] == 41


def test_factor_registry_is_exposed(client):
    response = client.get(
        "/api/agent/v1/research/factors?category=momentum",
        headers=_headers(),
    )
    assert response.status_code == 200
    items = response.get_json()["data"]["items"]
    assert any(item["factor_id"] == "rsi" for item in items)


def test_safe_account_metadata_never_returns_credential_blob(client, monkeypatch):
    class Cursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchall(self):
            return [{
                "id": 9,
                "name": "main",
                "exchange_id": "binance",
                "api_key_hint": "abcd...wxyz",
                "encrypted_config": "not-a-valid-ciphertext",
                "created_at": None,
                "updated_at": None,
            }]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_db():
        yield Connection()

    monkeypatch.setattr(trading_data, "get_db_connection", fake_db)
    response = client.get("/api/agent/v1/trading/accounts", headers=_headers())
    assert response.status_code == 200
    item = response.get_json()["data"][0]
    assert item["api_key_hint"] == "abcd...wxyz"
    assert "encrypted_config" not in item
    assert "api_key" not in item


def test_live_notional_cap_rejects_before_order_and_releases_lock(app, monkeypatch):
    state = {"rolled_back": False}

    class Cursor:
        rowcount = 1

        def execute(self, _sql, _params=None):
            pass

        def fetchone(self):
            return {"max_order_notional": 100, "max_daily_notional": 1000}

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def rollback(self):
            state["rolled_back"] = True

    @contextmanager
    def fake_db():
        yield Connection()

    monkeypatch.setattr(quick_trade, "get_db_connection", fake_db)
    with app.test_request_context(
        "/api/agent/v1/quick-trade/orders",
        method="POST",
        headers={"Idempotency-Key": "oversized-order"},
    ):
        g.agent_token = _token()
        g.agent_user_id = 7
        allowed, details = quick_trade._reserve_live_notional(150)

    assert allowed is False
    assert details["reason"] == "max_order_notional"
    assert state["rolled_back"] is True
