"""Agent Gateway indicator workspace endpoints."""

from __future__ import annotations

import pytest

from app.utils import agent_auth


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    agent_auth._rate_state.clear()
    yield
    agent_auth._rate_state.clear()


def _fake_token_row(scopes: str = "R,W") -> dict:
    return {
        "id": 999,
        "user_id": 1,
        "name": "test-agent",
        "scopes": scopes,
        "markets": "*",
        "instruments": "*",
        "paper_only": True,
        "rate_limit_per_min": 60,
        "status": "active",
        "expires_at": None,
    }


def _bearer(scopes_token: str = "qd_agent_TESTTOKEN12345") -> dict:
    return {"Authorization": f"Bearer {scopes_token}", "Content-Type": "application/json"}


def test_authoring_contract_requires_token(client):
    agent_auth._schema_ready = True
    resp = client.get("/api/agent/v1/indicators/authoring-contract")
    assert resp.status_code == 401


def test_authoring_contract_ok(client, monkeypatch):
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: _fake_token_row("R"))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    resp = client.get("/api/agent/v1/indicators/authoring-contract", headers=_bearer())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 0
    data = body["data"]
    assert "starter_template" in data
    assert "my_indicator_name" in data["starter_template"]


def test_save_indicator_requires_w_scope(client, monkeypatch):
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: _fake_token_row("R"))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    resp = client.post(
        "/api/agent/v1/indicators",
        headers=_bearer(),
        json={"code": "my_indicator_name='x'\nmy_indicator_description='y'\ndf=df.copy()\noutput={'name':'x','plots':[],'signals':[]}"},
    )
    assert resp.status_code == 403


def test_validate_rejects_oversized_code(client, monkeypatch):
    from app.routes.agent_v1._security import MAX_INDICATOR_CODE_BYTES

    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: _fake_token_row("R"))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)

    huge = "x" * (MAX_INDICATOR_CODE_BYTES + 1)
    resp = client.post(
        "/api/agent/v1/indicators/validate",
        headers=_bearer(),
        json={"code": huge},
    )
    assert resp.status_code == 400


def test_link_indicator_config_sets_id(monkeypatch):
    from app.services.indicator_workspace import link_indicator_config

    saved = {}

    def _fake_save(**kwargs):
        saved.update(kwargs)
        return 42

    monkeypatch.setattr(
        "app.services.indicator_workspace.save_user_indicator",
        lambda **kw: _fake_save(**kw) or 42,
    )
    monkeypatch.setattr(
        "app.services.indicator_workspace.get_user_indicator",
        lambda uid, iid: None,
    )

    code = (
        'my_indicator_name = "Bot"\n'
        'my_indicator_description = "test"\n'
        'df = df.copy()\n'
        "output = {'name': 'Bot', 'plots': [], 'signals': []}\n"
    )
    out = link_indicator_config(1, {"indicator_code": code})
    assert out["indicator_id"] == 42
    assert out["indicator_name"] == "Bot"
    assert saved["user_id"] == 1
