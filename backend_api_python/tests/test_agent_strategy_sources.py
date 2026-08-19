"""Agent Gateway Strategy API V2 source workspace tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routes.agent_v1 import strategy_sources
from app.services.strategy_authoring import get_strategy_authoring_contract
from app.services.strategy_v2 import compile_strategy_v2
from app.utils import agent_auth


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    agent_auth._rate_state.clear()
    yield
    agent_auth._rate_state.clear()


def _token(scopes: str = "R,W") -> dict:
    return {
        "id": 801,
        "user_id": 7,
        "name": "source-agent",
        "scopes": scopes,
        "markets": "*",
        "instruments": "*",
        "paper_only": True,
        "rate_limit_per_min": 100,
        "status": "active",
        "expires_at": None,
    }


def _headers(idempotency_key: str = "test-source-write") -> dict:
    return {
        "Authorization": "Bearer qd_agent_SOURCEWORKSPACE12345",
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }


def _authorize(monkeypatch, scopes: str = "R,W") -> None:
    agent_auth._schema_ready = True
    monkeypatch.setattr(agent_auth, "_lookup_token", lambda raw: _token(scopes))
    monkeypatch.setattr(agent_auth, "_touch_token_last_used", lambda *_: None)
    monkeypatch.setattr(agent_auth, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(agent_auth, "_reserve_idempotency", lambda *_: ("reserved", None))
    monkeypatch.setattr(agent_auth, "_complete_idempotency", lambda *_: None)


class _SourceService:
    def __init__(self):
        self.created = None
        self.updated = None
        self.restored = None
        self.source = {
            "id": 12,
            "user_id": 7,
            "name": "SPY trend",
            "description": "",
            "code": "def initialize(context):\n    pass\n",
            "asset_type": "script",
            "metadata": {"contract": "v2"},
            "visibility": "private",
            "status": "draft",
        }

    def list_templates(self):
        return [{"key": "starter", "code": self.source["code"]}]

    def list_sources(self, user_id):
        assert user_id == 7
        return [self.source]

    def get_source(self, source_id, user_id=None):
        return dict(self.source) if source_id == 12 and user_id == 7 else None

    def create_source(self, payload):
        self.created = dict(payload)
        return 12

    def update_source(self, source_id, user_id, payload):
        self.updated = dict(payload)
        self.source.update(payload)
        return source_id == 12 and user_id == 7

    def list_versions(self, source_id, user_id):
        return source_id == 12 and user_id == 7, [{"id": 44, "source_id": 12, "version_no": 1}]

    def get_version(self, version_id, user_id):
        return {"id": 44, "source_id": 12} if version_id == 44 and user_id == 7 else None

    def restore_version(self, version_id, user_id):
        self.restored = (version_id, user_id)
        return {**self.source, "version_no": 2}


@pytest.fixture
def source_service(monkeypatch):
    service = _SourceService()
    monkeypatch.setattr(strategy_sources, "get_script_source_service", lambda: service)
    monkeypatch.setattr(
        strategy_sources,
        "canonical_source_metadata",
        lambda code, metadata: ({**metadata, "contract": "v2"}, {"strategyType": "single"}),
    )
    monkeypatch.setattr(
        strategy_sources,
        "compile_strategy_v2",
        lambda code: SimpleNamespace(manifest=SimpleNamespace(metadata=lambda: {"strategyType": "single"})),
    )
    return service


def test_strategy_source_compile_and_create(client, monkeypatch, source_service):
    _authorize(monkeypatch)
    code = "def initialize(context):\n    pass\n"

    compiled = client.post(
        "/api/agent/v1/strategy-sources/compile",
        headers=_headers(),
        json={"code": code},
    )
    assert compiled.status_code == 200
    assert compiled.get_json()["data"]["manifest"]["strategyType"] == "single"

    created = client.post(
        "/api/agent/v1/strategy-sources",
        headers=_headers("source-create"),
        json={"name": "SPY trend", "code": code, "metadata": {"owner": "agent"}},
    )
    assert created.status_code == 200
    assert source_service.created["user_id"] == 7
    assert source_service.created["visibility"] == "private"
    assert source_service.created["status"] == "draft"
    assert source_service.created["metadata"]["contract"] == "v2"


def test_strategy_authoring_contract_is_source_owned(client, monkeypatch):
    _authorize(monkeypatch, "R")

    response = client.get(
        "/api/agent/v1/strategy-sources/authoring-contract",
        headers=_headers("source-restore-confirm"),
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["version"].startswith("strategy-api-v2")
    assert "frequency" in data["ownership"]["source"]
    assert "initial_capital" in data["ownership"]["run_panel"]
    assert "get_current_data" in " ".join(data["forbidden"])
    assert "context.set_universe" in data["starter_template"]


def test_strategy_authoring_starter_compiles_with_strategy_v2():
    contract = get_strategy_authoring_contract()

    compiled = compile_strategy_v2(contract["starter_template"])
    multi_timeframe = compile_strategy_v2(contract["multi_timeframe_template"])

    assert compiled.manifest.strategy_type == "cta"
    assert compiled.manifest.subscriptions[0].frequency == "1d"
    assert multi_timeframe.manifest.frequencies == ("1m", "1h")
    assert multi_timeframe.manifest.driving_frequency == "1m"
    assert contract["timeframes"]["weekly_literal"] == "1w"


def test_strategy_source_list_omits_code_and_detail_masks_hidden(client, monkeypatch, source_service):
    _authorize(monkeypatch, "R")

    listed = client.get("/api/agent/v1/strategy-sources", headers=_headers())
    assert listed.status_code == 200
    assert "code" not in listed.get_json()["data"][0]

    source_service.source["metadata"] = {"code_hidden": True}
    detail = client.get("/api/agent/v1/strategy-sources/12", headers=_headers())
    assert detail.status_code == 200
    assert detail.get_json()["data"]["code"] == ""
    assert detail.get_json()["data"]["code_hidden"] is True


def test_restore_strategy_source_requires_confirmation(client, monkeypatch, source_service):
    _authorize(monkeypatch)

    rejected = client.post(
        "/api/agent/v1/strategy-sources/12/versions/44/restore",
        headers=_headers("source-restore-reject"),
        json={"confirm": False},
    )
    assert rejected.status_code == 400

    restored = client.post(
        "/api/agent/v1/strategy-sources/12/versions/44/restore",
        headers=_headers("source-restore-confirm"),
        json={"confirm": True},
    )
    assert restored.status_code == 200
    assert source_service.restored == (44, 7)


def test_strategy_source_write_requires_w_scope(client, monkeypatch, source_service):
    _authorize(monkeypatch, "R")
    response = client.post(
        "/api/agent/v1/strategy-sources",
        headers=_headers(),
        json={"name": "SPY", "code": "def initialize(context):\n    pass\n"},
    )
    assert response.status_code == 403
