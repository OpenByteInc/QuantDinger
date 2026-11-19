from io import BytesIO
import queue
import threading
from unittest.mock import Mock

import pytest

from app.services.strategy_v2.sandbox import (
    StrategySandboxError,
    StrategySandboxTerminatedError,
    _JsonSandboxProcess,
    live_sandbox_timeout_seconds,
)


@pytest.mark.parametrize(
    "configured,legacy,expected",
    [
        (None, None, 120),
        (None, "5", 120),
        (None, "300", 120),
        ("45", "5", 45),
        ("5", "60", 5),
        ("", "5", 120),
        ("invalid", "5", 120),
        (None, "invalid", 120),
        ("nan", "5", 120),
        (None, "inf", 120),
        ("inf", "5", 120),
        ("0", "5", 2),
        ("9999", "5", 300),
    ],
)
def test_live_request_budget_ignores_retired_callback_limit(monkeypatch, configured, legacy, expected):
    for key, value in (
        ("STRATEGY_RUNTIME_SANDBOX_TIMEOUT_SECONDS", configured),
        ("STRATEGY_SCRIPT_CALLBACK_TIMEOUT_SEC", legacy),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    assert live_sandbox_timeout_seconds() == expected


@pytest.fixture
def transport():
    process = _JsonSandboxProcess.__new__(_JsonSandboxProcess)
    process._lock = threading.Lock()
    process._responses = queue.Queue()
    process._stderr = bytearray()
    process._closed = False
    process._terminal_error = None
    process._process = Mock()
    process._process.poll.return_value = None
    process._process.stdin = BytesIO()

    def terminate():
        process._process.poll.return_value = -9

    process._process.kill.side_effect = terminate
    return process


def test_timeout_remains_the_error_on_followup_requests(transport):
    with pytest.raises(StrategySandboxTerminatedError, match="timed out.*operation=process") as first:
        transport.request({"op": "process"}, timeout=0.1)
    written = transport._process.stdin.getvalue()

    for _ in range(4):
        with pytest.raises(StrategySandboxTerminatedError) as repeated:
            transport.request({"op": "context", "action": "order_references"})
        assert str(repeated.value) == str(first.value)
    assert transport._process.stdin.getvalue() == written
    transport._process.kill.assert_called_once()


def test_worker_failure_is_terminal_and_retains_original_error(transport):
    transport._responses.put({"success": False, "error": "strategyV2.runtimeFailed:on_rebalance:bad value"})
    with pytest.raises(StrategySandboxTerminatedError, match="on_rebalance:bad value"):
        transport.request({"op": "process"})
    with pytest.raises(StrategySandboxTerminatedError, match="on_rebalance:bad value"):
        transport.request({"op": "session_snapshot"})


def test_worker_exit_includes_return_code(transport):
    transport._process.poll.return_value = -9
    with pytest.raises(StrategySandboxTerminatedError, match="exit_code=-9"):
        transport.request({"op": "process"})


@pytest.mark.parametrize("response", [[], StrategySandboxError("protocol error")])
def test_broken_protocol_terminates_the_session(transport, response):
    transport._responses.put(response)
    with pytest.raises(StrategySandboxTerminatedError):
        transport.request({"op": "process"})
    transport._process.kill.assert_called_once()


def test_invalid_parent_input_does_not_destroy_a_healthy_session(transport):
    with pytest.raises(StrategySandboxError, match="input is invalid"):
        transport.request({"op": "process", "unsupported": object()})
    transport._process.kill.assert_not_called()
    transport._responses.put({"success": True, "result": {"value": []}})
    assert transport.request({"op": "context"}) == {"value": []}
