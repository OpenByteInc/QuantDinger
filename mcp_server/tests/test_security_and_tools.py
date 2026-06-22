"""MCP security + tool registry tests."""
from __future__ import annotations

import importlib
import sys

import pytest

pytest.importorskip("mcp")


@pytest.fixture
def fresh_module(monkeypatch):
    monkeypatch.setenv("QUANTDINGER_BASE_URL", "http://localhost:8888")
    monkeypatch.setenv("QUANTDINGER_AGENT_TOKEN", "qd_agent_test_token")
    sys.modules.pop("quantdinger_mcp.server", None)
    sys.modules.pop("quantdinger_mcp.security", None)
    import os
    src_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "src")
    )
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    return importlib.import_module("quantdinger_mcp.server")


def test_mcp_tool_registry_complete(fresh_module):
    assert len(fresh_module.MCP_TOOL_NAMES) == 26
    # Every exported name should correspond to a registered @mcp.tool function.
    for name in fresh_module.MCP_TOOL_NAMES:
        assert hasattr(fresh_module, name), f"missing tool function: {name}"


def test_ai_optimize_requires_confirmation(fresh_module):
    out = fresh_module.submit_ai_optimize({"base": {}})
    assert out.get("error") is True
    assert out.get("status") == 400


def test_update_strategy_blocks_running_without_trade_scope(fresh_module):
    out = fresh_module.update_strategy(1, {"status": "running"})
    assert out.get("error") is True
    assert out.get("status") == 403


def test_indicator_code_size_rejected_in_mcp(monkeypatch, fresh_module):
    from quantdinger_mcp import security as sec

    huge = "x" * (sec.MAX_INDICATOR_CODE_BYTES + 1)
    with pytest.raises(ValueError, match="KiB"):
        fresh_module.validate_indicator_code(huge)


def test_parse_sse_chunk():
    from quantdinger_mcp.security import parse_sse_chunk

    text = (
        'event: snapshot\n'
        'data: {"status":"running"}\n\n'
        'event: result\n'
        'data: {"status":"succeeded"}\n\n'
    )
    frames = parse_sse_chunk(text)
    assert frames[0][0] == "snapshot"
    assert frames[1][0] == "result"
    assert frames[1][1]["status"] == "succeeded"
