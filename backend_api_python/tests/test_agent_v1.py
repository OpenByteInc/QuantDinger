"""Smoke tests for the Agent Gateway (`/api/agent/v1`).

These tests exercise the route layer in isolation:
  * `/health` is reachable without a token.
  * Token-required routes reject missing / malformed bearer tokens.
  * Scope enforcement returns 403 (not 401) when the token is valid but
    lacks the required capability class.

We monkey-patch `agent_auth._lookup_token` so we don't need a live Postgres
to validate the auth state machine.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.utils import agent_auth


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    agent_auth._rate_state.clear()
    yield
    agent_auth._rate_state.clear()


def _fake_token_row(scopes: str = "R", paper_only: bool = True, status: str = "active",
                    expires_at=None, rate_limit_per_min: int = 60) -> dict:
    return {
        "id": 999,
        "user_id": 1,
        "name": "test-agent",
        "owner_role": "user",
        "scopes": scopes,
        "markets": "*",
        "instruments": "*",
        "paper_only": paper_only,
        "rate_limit_per_min": rate_limit_per_min,
        "status": status,
        "expires_at": expires_at,
    }


def _bearer(headers: dict | None = None, token: str = "qd_agent_TESTTOKEN12345") -> dict:
    out = {"Authorization": f"Bearer {token}"}
    out.update(headers or {})
    return out


def test_health_is_public(client):
    """Public liveness probe should not require authentication."""
    resp = client.get("/api/agent/v1/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["service"] == "quantdinger-agent-gateway"
    assert body["version"] == "v1"
    assert body["status"] == "ok"


def test_whoami_requires_token(client):
    """Without a token the gateway must return 401, not 500."""
    # Bypass schema bootstrap (no DB in unit tests) so we exit early.
    agent_auth._schema_ready = True
    resp = client.get("/api/agent/v1/whoami")
    assert resp.status_code == 401


def test_unknown_token_rejected(client, monkeypatch):
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: None)
    resp = client.get("/api/agent/v1/whoami", headers=_bearer())
    assert resp.status_code == 401


def test_inactive_token_rejected(client, monkeypatch):
    agent_auth._schema_ready = True
    monkeypatch.setattr(
        agent_auth, "_lookup_token",
        lambda raw: _fake_token_row(status="revoked"),
    )
    resp = client.get("/api/agent/v1/whoami", headers=_bearer())
    assert resp.status_code == 401


def test_expired_token_rejected(client, monkeypatch):
    agent_auth._schema_ready = True
    monkeypatch.setattr(
        agent_auth, "_lookup_token",
        lambda raw: _fake_token_row(expires_at=datetime.now() - timedelta(days=1)),
    )
    resp = client.get("/api/agent/v1/whoami", headers=_bearer())
    assert resp.status_code == 401


def test_scope_mismatch_returns_403(client, monkeypatch):
    """A valid token without B scope must NOT be able to submit backtests."""
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token",
                        lambda raw: _fake_token_row(scopes="R"))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    resp = client.post(
        "/api/agent/v1/backtest/run",
        headers=_bearer({"Content-Type": "application/json"}),
        json={"code": "x", "market": "Crypto", "symbol": "BTC/USDT",
              "timeframe": "1D", "start_date": "2024-01-01", "end_date": "2024-12-31"},
    )
    assert resp.status_code == 403


def test_viewer_owned_token_cannot_use_trading_scope(client, monkeypatch):
    """Existing T-scope tokens stop trading when their owner becomes a viewer."""
    agent_auth._schema_ready = True
    token = _fake_token_row(scopes="R,T")
    token["owner_role"] = "viewer"
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: token)
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    resp = client.post(
        "/api/agent/v1/quick-trade/orders",
        headers=_bearer({
            "Content-Type": "application/json",
            "Idempotency-Key": "viewer-trading-denied",
        }),
        json={"market": "Crypto", "symbol": "BTC/USDT", "side": "buy", "qty": 0.01},
    )

    assert resp.status_code == 403
    assert "owner" in resp.get_json()["message"].lower()


def test_whoami_with_valid_token(client, monkeypatch):
    """Successful auth path should reach the route and return its payload."""
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token",
                        lambda raw: _fake_token_row(scopes="R,B"))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    resp = client.get("/api/agent/v1/whoami", headers=_bearer())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 0
    data = body["data"]
    assert data["agent_name"] == "test-agent"
    assert "R" in data["scopes"] and "B" in data["scopes"]
    assert data["paper_only"] is True


def test_rate_limit_returns_429(client, monkeypatch):
    """Per-token rate limit must trip with 429 once the bucket is full."""
    agent_auth._schema_ready = True
    monkeypatch.setattr(
        agent_auth, "_lookup_token",
        lambda raw: _fake_token_row(rate_limit_per_min=2),
    )
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    headers = _bearer()
    assert client.get("/api/agent/v1/whoami", headers=headers).status_code == 200
    assert client.get("/api/agent/v1/whoami", headers=headers).status_code == 200
    third = client.get("/api/agent/v1/whoami", headers=headers)
    assert third.status_code == 429
    body = third.get_json()
    assert body["retriable"] is True


def test_token_generator_format():
    """Generated tokens must use the documented prefix and stable hash."""
    token, prefix, token_hash = agent_auth.generate_token()
    assert token.startswith(agent_auth.TOKEN_PREFIX)
    assert prefix.startswith(agent_auth.TOKEN_PREFIX)
    assert len(token_hash) == 64  # sha256 hex
    # Same input → same hash; different inputs → different hashes.
    assert agent_auth._hash_token(token) == token_hash
    other_token, _, other_hash = agent_auth.generate_token()
    assert token != other_token
    assert token_hash != other_hash
