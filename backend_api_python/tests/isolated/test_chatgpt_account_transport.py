from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).parents[2] / "app" / "services" / "chatgpt_account.py"
_SPEC = importlib.util.spec_from_file_location("chatgpt_account_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
ChatGPTAccountError = _MODULE.ChatGPTAccountError
CodexAppServerClient = _MODULE.CodexAppServerClient
_message_input = _MODULE._message_input


class _Pipe:
    def __init__(self, lines=()):
        self.lines = iter(lines)
        self.writes = []

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.lines)

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        return None

    def close(self):
        return None


class _Process:
    def __init__(self, lines):
        self.stdin = _Pipe()
        self.stdout = _Pipe(line + "\n" for line in lines)
        self.stderr = _Pipe()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


def test_message_input_preserves_complete_history():
    rendered = _message_input([
        {"role": "system", "content": "Return JSON."},
        {"role": "user", "content": "Build SMA."},
        {"role": "assistant", "content": "Draft one."},
        {"role": "user", "content": "Change 20 to 30."},
    ])
    assert "[SYSTEM]\nReturn JSON." in rendered
    assert rendered.endswith("[USER]\nChange 20 to 30.")


def test_account_mode_never_uses_api_key_fallback(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_MODE", "chatgpt_account")
    assert _MODULE.account_mode_enabled() is True


def test_rpc_errors_are_redacted():
    with pytest.raises(ChatGPTAccountError, match=r"code=-32001") as exc:
        CodexAppServerClient._raise_rpc_error(
            {"error": {"code": -32001, "message": "secret bearer token"}},
            "model discovery",
        )
    assert "secret" not in str(exc.value)


def test_model_list_uses_only_safe_catalog_fields(monkeypatch):
    process = _Process([
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"data": [{"model": "gpt-test", "displayName": "GPT Test", "token": "never expose"}]},
        }),
    ])
    monkeypatch.setattr("shutil.which", lambda value: __file__)
    client = CodexAppServerClient(
        timeout_seconds=5,
        popen_factory=lambda *args, **kwargs: process,
    )
    models = client.list_models()
    assert [(item.slug, item.display_name) for item in models] == [("gpt-test", "GPT Test")]
    assert "never expose" not in repr(models)

