import ast
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).parents[1]
DOCS_DIR = Path(__file__).parents[2] / "docs" / "trading"
RUNTIME_PATH = BACKEND_DIR / "app" / "services" / "strategy_v2" / "runtime.py"
CONTRACT_PATH = BACKEND_DIR / "app" / "services" / "strategy_v2" / "contract.py"


def _runtime_order_option_names():
    tree = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        matches = any(
            isinstance(target, ast.Name) and target.id == "_ORDER_OPTION_NAMES"
            for target in node.targets
        )
        if not matches:
            continue
        call = node.value
        assert isinstance(call, ast.Call)
        assert call.args
        return set(ast.literal_eval(call.args[0]))
    raise AssertionError("_ORDER_OPTION_NAMES was not found in Strategy API V2 runtime")


def _literal_name_set(path, assignment_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        matches = any(
            isinstance(target, ast.Name) and target.id == assignment_name
            for target in node.targets
        )
        if not matches:
            continue
        return set(ast.literal_eval(node.value))
    raise AssertionError(f"{assignment_name} was not found in {path.name}")


def _discovery_context_api_names():
    tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "DiscoveryContext":
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
    raise AssertionError("DiscoveryContext was not found in Strategy API V2 contract")


@pytest.mark.parametrize(
    "filename",
    ["STRATEGY_DEV_GUIDE.md", "STRATEGY_DEV_GUIDE_CN.md"],
)
def test_strategy_guide_documents_every_order_option(filename):
    guide = (DOCS_DIR / filename).read_text(encoding="utf-8")

    for option_name in sorted(_runtime_order_option_names()):
        assert f"<code>{option_name}</code>" in guide


@pytest.mark.parametrize(
    "filename",
    ["STRATEGY_DEV_GUIDE.md", "STRATEGY_DEV_GUIDE_CN.md"],
)
def test_strategy_guide_explains_protection_units_and_non_executable_params(filename):
    guide = (DOCS_DIR / filename).read_text(encoding="utf-8")

    assert "<code>0..1</code>" in guide
    assert "<code>0..5</code>" in guide
    assert "<code>stop_loss</code>" in guide
    assert "# @param stop_loss_pct" in guide
    assert "set_default_protection()" in guide


@pytest.mark.parametrize(
    "filename",
    ["STRATEGY_DEV_GUIDE.md", "STRATEGY_DEV_GUIDE_CN.md"],
)
def test_strategy_guide_names_every_runtime_global(filename):
    guide = (DOCS_DIR / filename).read_text(encoding="utf-8")

    for api_name in sorted(_literal_name_set(CONTRACT_PATH, "_RUNTIME_GLOBAL_CALL_NAMES")):
        assert api_name in guide

    for api_name in sorted(_discovery_context_api_names()):
        assert api_name in guide
