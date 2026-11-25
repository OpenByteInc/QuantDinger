"""OpenAPI infrastructure tests."""
import os
from pathlib import Path

import pytest
import yaml


@pytest.fixture(scope="module")
def openapi_spec(app):
    from app.openapi import get_openapi_api

    api = get_openapi_api(app)
    assert api is not None
    with app.app_context():
        return api.spec.to_dict()


def test_health_endpoints_still_work(client):
    for path in ("/health", "/api/health"):
        resp = client.get(path)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


def test_api_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "QuantDinger Python API"
    assert data["status"] == "running"


def test_openapi_spec_has_health_paths(openapi_spec):
    paths = openapi_spec.get("paths", {})
    assert "/health" in paths
    assert "/api/health" in paths
    assert "/" in paths
    assert len(paths) >= 150, "expected bulk smorest migration to register most human routes"


def test_openapi_shared_schemas(openapi_spec):
    schemas = openapi_spec.get("components", {}).get("schemas", {})
    for name in (
        "HumanSuccessEnvelope",
        "HumanErrorEnvelope",
        "AgentSuccessEnvelope",
        "AgentError",
    ):
        assert name in schemas, f"missing shared schema {name}"


def test_author_published_query_contract_is_generated(openapi_spec):
    operation = openapi_spec["paths"]["/api/community/author/published"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {
        "page", "page_size", "keyword", "asset_type", "review_status", "sort_by", "sort_order",
    }
    assert all(item["in"] == "query" for item in parameters.values())
    assert parameters["page_size"]["schema"]["maximum"] == 50
    assert parameters["asset_type"]["schema"]["enum"] == ["indicator", "script_template"]
    assert parameters["review_status"]["schema"]["enum"] == ["approved", "pending", "rejected"]
    assert parameters["sort_by"]["schema"]["default"] == "updated_at"
    assert parameters["sort_order"]["schema"]["default"] == "desc"
    assert "current author" in operation["description"]


def test_export_script_writes_yaml(tmp_path):
    """Regression: export_openapi.py produces parseable YAML."""
    import subprocess
    import sys

    out = tmp_path / "out.yaml"
    env = os.environ.copy()
    env["SKIP_STARTUP_HOOKS"] = "1"
    env["OPENAPI_ENABLED"] = "false"
    backend_root = os.path.join(os.path.dirname(__file__), "..")
    script = os.path.join(backend_root, "scripts", "export_openapi.py")
    subprocess.run(
        [sys.executable, script, "--output", str(out)],
        check=True,
        cwd=backend_root,
        env=env,
    )
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["openapi"].startswith("3.")
    assert "x-api-app-version" not in loaded.get("info", {})
    assert "/health" in loaded["paths"]
    committed = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.yaml"
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), (
        "Committed OpenAPI spec is stale; run python scripts/export_openapi.py"
    )
