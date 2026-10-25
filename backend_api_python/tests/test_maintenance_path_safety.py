from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = REPOSITORY_ROOT / "backend_api_python" / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_audit_rejects_output_outside_audit_root(tmp_path: Path) -> None:
    module = _load_script("audit_strategy_runtime.py")
    root = tmp_path / "audit"
    root.mkdir()

    assert module.contained_path(root, root / "result.json") == (root / "result.json").resolve()
    with pytest.raises(ValueError, match="must stay within"):
        module.contained_path(root, tmp_path / "outside.json")


@pytest.mark.parametrize(
    "script_name",
    [
        "convert_blueprints_to_smorest.py",
        "fix_flask_imports.py",
        "use_human_blueprint.py",
    ],
)
def test_route_maintenance_scripts_reject_paths_outside_routes(script_name: str, tmp_path: Path) -> None:
    module = _load_script(script_name)
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must stay within app/routes"):
        module.route_file(outside)
