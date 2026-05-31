"""Tests for Agent Token issuance policy on SaaS and self-service routes."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.routes.agent_v1 import admin as admin_routes
from app.services import agent_token_service
from app.utils import agent_auth, auth as core_auth


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, False),
        ("", False),
        ("self", False),
        ("local", False),
        ("saas", True),
        ("SaaS", True),
        ("HOSTED", True),
        ("shared", True),
        ("multitenant", True),
        ("multi-tenant", True),
    ],
)
def test_is_saas_mode_recognizes_known_spellings(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("QUANTDINGER_DEPLOYMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("QUANTDINGER_DEPLOYMENT_MODE", raw)
    assert agent_token_service.is_saas_mode() is expected
    assert admin_routes._is_saas_mode() is expected


@pytest.fixture
def admin_authed(monkeypatch):
    monkeypatch.setattr(
        core_auth,
        "verify_token",
        lambda _raw: {"sub": "tester", "user_id": 42, "role": "admin"},
    )
    yield {"user_id": 42}


@pytest.fixture
def user_authed(monkeypatch):
    monkeypatch.setattr(
        core_auth,
        "verify_token",
        lambda _raw: {"sub": "alice", "user_id": 7, "role": "user"},
    )
    yield {"user_id": 7}


@pytest.fixture
def stub_db_for_issue(monkeypatch):
    class _StubCursor:
        def __init__(self):
            self._row = None
            self._last_sql = ""

        def execute(self, sql, _params=None):
            self._last_sql = (sql or "").upper()

        def fetchone(self):
            if "INSERT" in self._last_sql:
                return {"id": 1, "created_at": datetime(2026, 5, 2, 0, 0, 0)}
            if "SELECT" in self._last_sql and "TOKEN_HASH" in self._last_sql:
                return {"id": 1, "created_at": datetime(2026, 5, 2, 0, 0, 0)}
            return None

        def fetchall(self):
            if "FROM QD_AGENT_TOKENS" in self._last_sql:
                return [{
                    "id": 1,
                    "name": "cursor-mcp",
                    "token_prefix": "qd_agent_ab",
                    "scopes": "B,R",
                    "markets": ["*"],
                    "instruments": ["*"],
                    "paper_only": True,
                    "rate_limit_per_min": 60,
                    "status": "active",
                    "expires_at": None,
                    "last_used_at": None,
                    "created_at": datetime(2026, 5, 2, 0, 0, 0),
                }]
            if "FROM QD_AGENT_AUDIT" in self._last_sql:
                return []
            return []

        def close(self):
            pass

        @property
        def rowcount(self):
            return 0

    class _StubConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _StubCursor()

        def commit(self):
            pass

    monkeypatch.setattr(agent_token_service, "get_db_connection", lambda: _StubConn())


def _post_admin_issue(client, payload, *, base_url="http://localhost"):
    return client.post(
        "/api/agent/v1/admin/tokens",
        headers={
            "Authorization": "Bearer admin-jwt",
            "Content-Type": "application/json",
        },
        json=payload,
        base_url=base_url,
    )


def _post_me_issue(client, payload, *, base_url="http://localhost"):
    return client.post(
        "/api/agent/v1/me/tokens",
        headers={
            "Authorization": "Bearer user-jwt",
            "Content-Type": "application/json",
        },
        json=payload,
        base_url=base_url,
    )


def test_self_hosted_mode_allows_T_scope(
    client, admin_authed, stub_db_for_issue, monkeypatch
):
    monkeypatch.delenv("QUANTDINGER_DEPLOYMENT_MODE", raising=False)

    resp = _post_admin_issue(client, {
        "name": "selfhost-trader",
        "scopes": "R,T",
        "paper_only": True,
    })
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "T" in data["scopes"]
    assert data["paper_only"] is True
    assert data["token"].startswith(agent_auth.TOKEN_PREFIX)


def test_saas_mode_allows_T_scope_paper_only(
    client, admin_authed, stub_db_for_issue, monkeypatch
):
    monkeypatch.setenv("QUANTDINGER_DEPLOYMENT_MODE", "saas")

    resp = _post_admin_issue(client, {
        "name": "saas-research-bot",
        "scopes": "R,T",
        "paper_only": True,
    })
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert "T" in data["scopes"]
    assert data["paper_only"] is True


def test_saas_mode_live_T_requires_ack(
    client, admin_authed, stub_db_for_issue, monkeypatch
):
    monkeypatch.setenv("QUANTDINGER_DEPLOYMENT_MODE", "hosted")

    resp = _post_admin_issue(client, {
        "name": "saas-live-attempt",
        "scopes": "R,T",
        "paper_only": False,
    })
    assert resp.status_code == 400
    assert "ack_live_trading_risk" in resp.get_json()["message"]


def test_saas_mode_live_T_with_ack_succeeds(
    client, admin_authed, stub_db_for_issue, monkeypatch
):
    monkeypatch.setenv("QUANTDINGER_DEPLOYMENT_MODE", "saas")

    resp = _post_admin_issue(client, {
        "name": "saas-live-ack",
        "scopes": "R,T",
        "paper_only": False,
        "ack_live_trading_risk": True,
    })
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["paper_only"] is False


def test_me_tokens_rejects_C_scope(
    client, user_authed, stub_db_for_issue, monkeypatch
):
    monkeypatch.delenv("QUANTDINGER_DEPLOYMENT_MODE", raising=False)

    resp = _post_me_issue(client, {
        "name": "user-c-attempt",
        "scopes": "R,C",
        "paper_only": True,
    })
    assert resp.status_code == 403
    assert "C-scope" in resp.get_json()["message"]


def test_me_tokens_issue_and_policy(client, user_authed, stub_db_for_issue, monkeypatch):
    monkeypatch.setenv("QUANTDINGER_DEPLOYMENT_MODE", "saas")

    policy = client.get(
        "/api/agent/v1/me/tokens/policy",
        headers={"Authorization": "Bearer user-jwt"},
        base_url="http://localhost",
    )
    assert policy.status_code == 200
    pdata = policy.get_json()["data"]
    assert pdata["is_saas"] is True
    assert "T" in pdata["allowed_scopes"]
    assert "C" not in pdata["allowed_scopes"]
    assert pdata["risk_disclosure"]["system"]

    resp = _post_me_issue(client, {
        "name": "cursor-mcp",
        "scopes": "R,B",
        "paper_only": True,
    })
    assert resp.status_code == 200
    assert resp.get_json()["data"]["token"].startswith(agent_auth.TOKEN_PREFIX)

    listed = client.get(
        "/api/agent/v1/me/tokens",
        headers={"Authorization": "Bearer user-jwt"},
        base_url="http://localhost",
    )
    assert listed.status_code == 200


def test_me_revoke_other_users_token_returns_404(client, user_authed, monkeypatch):
    class _StubCursor:
        def execute(self, _sql, _params=None):
            pass

        def close(self):
            pass

        @property
        def rowcount(self):
            return 0

    class _StubConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _StubCursor()

        def commit(self):
            pass

    monkeypatch.setattr(agent_token_service, "get_db_connection", lambda: _StubConn())

    resp = client.delete(
        "/api/agent/v1/me/tokens/999",
        headers={"Authorization": "Bearer user-jwt"},
        base_url="http://localhost",
    )
    assert resp.status_code == 404
