"""MCP security + tool registry tests."""
from __future__ import annotations

import importlib
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest
import httpx

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
    assert len(fresh_module.MCP_TOOL_NAMES) == 58
    # Every exported name should correspond to a registered @mcp.tool function.
    for name in fresh_module.MCP_TOOL_NAMES:
        assert hasattr(fresh_module, name), f"missing tool function: {name}"


def test_gateway_tools_do_not_advertise_an_inferred_result_wrapper(fresh_module):
    tools = asyncio.run(fresh_module.mcp.list_tools())
    assert len(tools) == len(fresh_module.MCP_TOOL_NAMES)
    assert all(tool.outputSchema is None for tool in tools)


@pytest.mark.parametrize("body", [{"strategy_id": 1}, {"items": [], "pagination": {"total": 0}}])
def test_success_payload_is_preserved_without_a_result_wrapper(monkeypatch, fresh_module, body):
    monkeypatch.setattr(fresh_module, '_get', lambda *a, **kw: body)
    result = asyncio.run(fresh_module.mcp.call_tool('get_strategy', {'strategy_id': 1}))
    assert json.loads(result[0].text) == body


@pytest.mark.parametrize("failure", ["http", "json_http", "timeout", "connection", "invalid", "business"])
def test_health_failures_set_protocol_error(fresh_module, monkeypatch, failure):
    from mcp.types import CallToolResult

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "connection":
            raise httpx.ConnectError("connection failed", request=request)
        if failure == "json_http":
            return httpx.Response(503, json={"code": 0, "data": {"status": "ok"}})
        if failure == "business":
            return httpx.Response(200, json={"code": 0, "data": {"ok": False}})
        return httpx.Response(503 if failure == "http" else 200, text="<html>unavailable</html>")

    with httpx.Client(base_url="http://fixture.test", transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(fresh_module, "_public_client", client)
        result = asyncio.run(fresh_module.mcp.call_tool("check_health", {}))
    assert isinstance(result, CallToolResult)
    assert result.isError is True


def test_health_success_stays_success(fresh_module, monkeypatch):
    from mcp.types import CallToolResult

    with httpx.Client(base_url="http://fixture.test", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"code": 0, "data": {"status": "ok"}})
    )) as client:
        monkeypatch.setattr(fresh_module, "_public_client", client)
        result = asyncio.run(fresh_module.mcp.call_tool("check_health", {}))
    assert not isinstance(result, CallToolResult) or result.isError is False


def test_mcp_tools_are_actually_registered(fresh_module):
    registered = set(fresh_module.mcp._tool_manager._tools)
    assert registered == set(fresh_module.MCP_TOOL_NAMES)


def test_every_tool_has_explicit_risk_annotations(fresh_module):
    from quantdinger_mcp.tool_contract import WRITE_TOOLS

    tools = asyncio.run(fresh_module.mcp.list_tools())
    assert set(WRITE_TOOLS).issubset({tool.name for tool in tools})
    for tool in tools:
        hints = tool.annotations
        assert hints is not None
        assert hints.readOnlyHint is (tool.name not in WRITE_TOOLS)
        assert hints.destructiveHint is WRITE_TOOLS.get(tool.name, False)
        assert hints.idempotentHint is (tool.name not in WRITE_TOOLS)
        assert hints.openWorldHint is True


@pytest.mark.parametrize('status,body', [
    (402, {'code': 402, 'message': 'INSUFFICIENT_CREDITS', 'details': {'current': 12, 'required': 30, 'shortage': 18}}),
    (404, {'code': 404, 'message': 'Strategy not found', 'data': None}),
    (200, {'code': 400, 'message': 'Rejected', 'data': None}),
    (200, {'code': 0, 'data': {'error': 'UNSUPPORTED_TRADING_ENVIRONMENT', 'spot_positions': []}}),
    (200, {'code': 0, 'data': {'success': False, 'error_type': 'SecurityError'}}),
])
def test_protocol_marks_gateway_and_business_failures(monkeypatch, fresh_module, status, body):
    from mcp.types import CallToolResult

    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body)),
                      base_url='https://example.test') as client:
        monkeypatch.setattr(fresh_module, '_client', client)
        result = asyncio.run(fresh_module.mcp.call_tool('get_strategy', {'strategy_id': 1}))
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert json.loads(result.content[0].text) == result.structuredContent


@pytest.mark.parametrize('exception_type', [httpx.ConnectError, httpx.ReadTimeout])
def test_protocol_marks_transport_failures(monkeypatch, fresh_module, exception_type):
    def failed(request):
        raise exception_type('fixture transport failure', request=request)

    with httpx.Client(transport=httpx.MockTransport(failed), base_url='https://example.test') as client:
        monkeypatch.setattr(fresh_module, '_client', client)
        result = asyncio.run(fresh_module.mcp.call_tool('get_strategy', {'strategy_id': 1}))
    assert result.isError is True
    assert result.structuredContent['body']['retriable'] is True


def test_protocol_guard_denial_and_error_redaction(monkeypatch, fresh_module):
    monkeypatch.setattr(fresh_module, '_post', lambda *a, **k: pytest.fail('denied operation reached gateway'))
    denied = asyncio.run(fresh_module.mcp.call_tool('stop_strategy', {'strategy_id': 1}))
    assert denied.isError is True
    monkeypatch.setattr(fresh_module, '_get', lambda *a, **k: {'error': True, 'secret_key': 'fixture-secret'})
    failed = asyncio.run(fresh_module.mcp.call_tool('get_strategy', {'strategy_id': 1}))
    assert failed.structuredContent['secret_key'] == '***'
    assert 'fixture-secret' not in failed.content[0].text


def test_successful_empty_snapshot_is_not_an_error(monkeypatch, fresh_module):
    from mcp.types import CallToolResult

    monkeypatch.setattr(fresh_module, '_get', lambda *a, **k: {'error': '', 'spot_positions': [], 'warnings': []})
    result = asyncio.run(fresh_module.mcp.call_tool('get_account_snapshot', {'credential_id': 1}))
    assert not isinstance(result, CallToolResult) or result.isError is False


def test_strategy_authoring_contract_uses_agent_gateway(monkeypatch, fresh_module):
    monkeypatch.setattr(
        fresh_module,
        "_get",
        lambda path, params=None: {"path": path, "params": params},
    )

    result = fresh_module.get_strategy_authoring_contract()

    assert result["path"] == "/api/agent/v1/strategy-sources/authoring-contract"


def test_package_version_matches_pyproject():
    from quantdinger_mcp import __version__

    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
    assert match
    assert __version__ == match.group(1)


def test_protocol_server_version_matches_package(fresh_module):
    from quantdinger_mcp import __version__

    assert fresh_module.mcp._mcp_server.version == __version__


def test_create_strategy_uses_canonical_deployment_payload(monkeypatch, fresh_module):
    captured = {}

    def fake_post(path, json=None, headers=None):
        captured.update(path=path, json=json, headers=headers)
        return {"strategy_id": 7}

    monkeypatch.setattr(fresh_module, "_post", fake_post)
    out = fresh_module.create_strategy(
        "BTC momentum",
        12,
        25000,
        execution_mode="live",
        credential_id=5,
        leverage_enabled=True,
        leverage=2,
        params={"lookback": 40},
        position_side="long",
        account_risk={"maxDrawdownPct": 10},
        idempotency_key="strategy-create-7",
    )

    assert out == {"strategy_id": 7}
    assert captured["path"] == "/api/agent/v1/strategies"
    assert captured["json"] == {
        "name": "BTC momentum",
        "sourceId": 12,
        "initialCapital": 25000.0,
        "executionMode": "live",
        "credentialId": 5,
        "leverageEnabled": True,
        "leverage": 2.0,
        "params": {"lookback": 40},
        "positionSide": "long",
        "accountRisk": {"maxDrawdownPct": 10},
    }
    assert captured["headers"] == {"Idempotency-Key": "strategy-create-7"}


def test_quick_order_forwards_native_protection(monkeypatch, fresh_module):
    captured = {}

    monkeypatch.setattr(fresh_module, "_get", lambda path, params=None: {"paper_only": True})

    def fake_post(path, json=None, headers=None):
        captured.update(path=path, json=json, headers=headers)
        return {"status": "filled"}

    monkeypatch.setattr(fresh_module, "_post", fake_post)
    out = fresh_module.place_quick_order(
        "Crypto",
        "BTC/USDT",
        "buy",
        0.01,
        tp_price=70000,
        sl_price=60000,
        idempotency_key="order-7",
        confirm_order=True,
    )

    assert out == {"status": "filled"}
    assert captured["json"]["tp_price"] == 70000.0
    assert captured["json"]["sl_price"] == 60000.0


def test_stop_strategy_requires_confirmation(fresh_module):
    out = fresh_module.stop_strategy(1)
    assert out.get("error") is True
    assert out.get("status") == 400


def test_place_quick_order_requires_confirmation(fresh_module):
    out = fresh_module.place_quick_order("Crypto", "BTC/USDT", "buy", 0.001)
    assert out.get("error") is True
    assert out.get("status") == 400


def test_indicator_code_size_rejected_in_mcp(monkeypatch, fresh_module):
    from quantdinger_mcp import security as sec

    huge = "x" * (sec.MAX_INDICATOR_CODE_BYTES + 1)
    with pytest.raises(ValueError, match="KiB"):
        fresh_module.validate_indicator_code(huge)


def test_submit_backtest_uses_strategy_v2_payload(monkeypatch, fresh_module):
    captured = {}

    def fake_post(path, json=None, headers=None):
        captured["path"] = path
        captured["json"] = json
        captured["headers"] = headers
        return {"job_id": "job-test"}

    monkeypatch.setattr(fresh_module, "_post", fake_post)
    out = fresh_module.submit_backtest(
        "def initialize(context):\n    context.set_universe(['USStock:SPY'])\n\ndef handle_data(context, data):\n    pass\n",
        "2024-01-01",
        "2024-06-30",
        params={"fast": 10},
        idempotency_key="same-key",
    )

    assert out == {"job_id": "job-test"}
    assert captured["path"] == "/api/agent/v1/backtest/run"
    assert captured["json"]["params"] == {"fast": 10}
    assert set(captured["json"]) == {
        "code", "startDate", "endDate", "initialCapital", "commission",
        "leverageEnabled", "leverage", "params",
    }
    assert captured["headers"] == {"Idempotency-Key": "same-key"}


def test_compile_strategy_code_forwards_idempotency_key(monkeypatch, fresh_module):
    captured = {}

    def fake_post(path, json=None, headers=None):
        captured.update(path=path, json=json, headers=headers)
        return {"manifest": {"strategyType": "cta"}}

    monkeypatch.setattr(fresh_module, "_post", fake_post)
    out = fresh_module.compile_strategy_code(code="def initialize(context):\n    pass\n", idempotency_key="compile-1")

    assert out == {"manifest": {"strategyType": "cta"}}
    assert captured["path"] == "/api/agent/v1/strategy-sources/compile"
    assert captured["headers"] == {"Idempotency-Key": "compile-1"}


def test_strategy_source_workspace_payloads(monkeypatch, fresh_module):
    calls = []

    monkeypatch.setattr(
        fresh_module,
        "_post",
        lambda path, json=None, headers=None: calls.append(("POST", path, json)) or {"ok": True},
    )
    monkeypatch.setattr(
        fresh_module,
        "_patch_with_headers",
        lambda path, json=None, headers=None: calls.append(("PATCH", path, json)) or {"ok": True},
    )

    fresh_module.save_strategy_source(
        "SPY trend",
        "def initialize(context):\n    pass\n",
        param_schema={"lookback": {"type": "integer"}},
        idempotency_key="source-create",
    )
    fresh_module.save_strategy_source(
        "SPY trend v2",
        "def initialize(context):\n    pass\n",
        source_id=12,
        idempotency_key="source-update",
    )
    fresh_module.restore_strategy_source_version(
        12,
        44,
        idempotency_key="source-restore",
        confirm_restore=True,
    )

    assert calls[0][0:2] == ("POST", "/api/agent/v1/strategy-sources")
    assert calls[0][2]["param_schema"]["lookback"]["type"] == "integer"
    assert calls[1][0:2] == ("PATCH", "/api/agent/v1/strategy-sources/12")
    assert calls[2] == (
        "POST",
        "/api/agent/v1/strategy-sources/12/versions/44/restore",
        {"confirm": True},
    )


def test_source_restore_and_paper_cancel_require_confirmation(fresh_module):
    assert fresh_module.restore_strategy_source_version(1, 2)["error"] is True
    assert fresh_module.cancel_open_paper_orders()["error"] is True


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


def test_redaction_handles_camel_case_and_deep_values():
    from quantdinger_mcp.security import redact_secrets

    assert redact_secrets({"clientSecret": "hidden"})["clientSecret"] == "***"
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"value": "hidden"}}}}}}}}
    assert "hidden" not in repr(redact_secrets(deep))
