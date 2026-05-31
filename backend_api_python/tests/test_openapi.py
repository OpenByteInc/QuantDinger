"""OpenAPI infrastructure tests."""
import os

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
    assert "/health" in loaded["paths"]
