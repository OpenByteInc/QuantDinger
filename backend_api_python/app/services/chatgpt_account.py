"""Official Codex app-server transport for a local ChatGPT account.

The transport never reads or copies OAuth credentials.  Authentication and
refresh remain owned by the locally installed Codex CLI.  QuantDinger talks to
the documented app-server JSON-RPC protocol over stdio and deliberately does
not fall back to an API-key provider.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class ChatGPTAccountError(RuntimeError):
    """Safe, credential-free account transport failure."""


@dataclass(frozen=True)
class ChatGPTAccountModel:
    slug: str
    display_name: str


def _message_input(messages: Iterable[dict[str, Any]]) -> str:
    """Render complete QD history into one bounded app-server turn."""
    rendered: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        if role not in {"system", "developer", "user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                str(item.get("text") or "") if isinstance(item, dict) else str(item)
                for item in content
            )
        text = str(content or "").strip()
        if text:
            rendered.append(f"[{role.upper()}]\n{text}")
    if not rendered:
        raise ChatGPTAccountError("ChatGPT account request has no text input")
    return "\n\n".join(rendered)


class CodexAppServerClient:
    """One-request client for the official local Codex app-server."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: float = 180.0,
        popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.executable = executable or os.getenv("CODEX_CLI_PATH", "codex")
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        self._popen_factory = popen_factory

    def _bridge_request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        base_url = os.getenv("CHATGPT_ACCOUNT_BRIDGE_URL", "").strip().rstrip("/")
        if not base_url:
            return None
        token = os.getenv("CHATGPT_ACCOUNT_BRIDGE_TOKEN", "").strip()
        if not token:
            raise ChatGPTAccountError("ChatGPT account bridge token is not configured")
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=body,
            method="GET" if body is None else "POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError) as exc:
            raise ChatGPTAccountError("ChatGPT account bridge request failed") from exc

    def _command(self) -> list[str]:
        resolved = shutil.which(self.executable) if not os.path.isabs(self.executable) else self.executable
        if not resolved or not os.path.exists(resolved):
            raise ChatGPTAccountError(
                "ChatGPT account mode requires the official Codex CLI on this local host"
            )
        return [
            resolved,
            "app-server",
            "--listen",
            "stdio://",
            "-c",
            "mcp_servers={}",
            "-c",
            "analytics.enabled=false",
        ]

    def _run(
        self,
        *,
        requests: list[dict[str, Any]],
        notification_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[int, dict[str, Any]]:
        with tempfile.TemporaryDirectory(
            prefix="quantdinger-chatgpt-", ignore_cleanup_errors=True
        ) as isolated_cwd:
            process = self._popen_factory(
                self._command(),
                cwd=isolated_cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env={**os.environ, "NO_COLOR": "1"},
            )
            if process.stdin is None or process.stdout is None:
                process.kill()
                raise ChatGPTAccountError("Codex app-server stdio is unavailable")

            responses: dict[int, dict[str, Any]] = {}
            read_error: list[BaseException] = []

            def read_stdout() -> None:
                try:
                    for line in process.stdout:
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload.get("id"), int):
                            responses[int(payload["id"])] = payload
                        elif notification_handler:
                            notification_handler(payload)
                except BaseException as exc:  # pragma: no cover - OS pipe failure
                    read_error.append(exc)

            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()
            try:
                for payload in requests:
                    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                    process.stdin.flush()
                deadline = time.monotonic() + self.timeout_seconds
                expected_ids = {int(item["id"]) for item in requests if isinstance(item.get("id"), int)}
                while not expected_ids.issubset(responses):
                    if read_error:
                        raise ChatGPTAccountError("Codex app-server output failed")
                    if process.poll() is not None:
                        raise ChatGPTAccountError("Codex app-server stopped before completing the request")
                    if time.monotonic() >= deadline:
                        raise ChatGPTAccountError("Codex app-server request timed out")
                    time.sleep(0.02)
                return responses
            finally:
                try:
                    process.stdin.close()
                except Exception:
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                reader.join(timeout=1)

    @staticmethod
    def _raise_rpc_error(response: dict[str, Any], operation: str) -> None:
        if response.get("error"):
            error = response["error"]
            code = error.get("code") if isinstance(error, dict) else "unknown"
            raise ChatGPTAccountError(f"{operation} failed (code={code})")

    @staticmethod
    def _initialize_request() -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "quantdinger",
                    "title": "QuantDinger",
                    "version": "5.7",
                }
            },
        }

    def list_models(self) -> list[ChatGPTAccountModel]:
        bridged = self._bridge_request("/v1/models")
        if bridged is not None:
            return [
                ChatGPTAccountModel(slug=str(item["id"]), display_name=str(item.get("name") or item["id"]))
                for item in bridged.get("models", [])
                if item.get("id")
            ]
        responses = self._run(
            requests=[
                self._initialize_request(),
                {"jsonrpc": "2.0", "method": "initialized", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "model/list",
                    "params": {"includeHidden": False},
                },
            ]
        )
        self._raise_rpc_error(responses[1], "ChatGPT account initialization")
        self._raise_rpc_error(responses[2], "ChatGPT account model discovery")
        data = (responses[2].get("result") or {}).get("data") or []
        models: list[ChatGPTAccountModel] = []
        for item in data:
            slug = str(item.get("model") or item.get("slug") or "").strip()
            if slug:
                models.append(
                    ChatGPTAccountModel(
                        slug=slug,
                        display_name=str(item.get("displayName") or item.get("display_name") or slug),
                    )
                )
        return models

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        bridged = self._bridge_request(
            "/v1/complete",
            {"messages": messages, "model": model},
        )
        if bridged is not None:
            result = str(bridged.get("text") or "").strip()
            if not result:
                raise ChatGPTAccountError("ChatGPT account bridge returned an empty response")
            if on_delta:
                on_delta(result)
            return result
        text_parts: list[str] = []
        completed: list[str] = []

        def notification(payload: dict[str, Any]) -> None:
            method = payload.get("method")
            params = payload.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta:
                    text_parts.append(delta)
                    if on_delta:
                        on_delta(delta)
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                completed.append(str(turn.get("status") or ""))

        thread_params: dict[str, Any] = {
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
            "developerInstructions": (
                "Return text only. Do not call tools, read files, access MCP servers, "
                "or execute commands. Follow the supplied QuantDinger conversation."
            ),
        }
        if model:
            thread_params["model"] = model
        with tempfile.TemporaryDirectory(
            prefix="quantdinger-chatgpt-", ignore_cleanup_errors=True
        ) as isolated_cwd:
            process = self._popen_factory(
                self._command(), cwd=isolated_cwd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                env={**os.environ, "NO_COLOR": "1"},
            )
            if process.stdin is None or process.stdout is None:
                process.kill()
                raise ChatGPTAccountError("Codex app-server stdio is unavailable")
            responses: dict[int, dict[str, Any]] = {}
            condition = threading.Condition()

            def read_stdout() -> None:
                for line in process.stdout:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    with condition:
                        if isinstance(payload.get("id"), int):
                            responses[int(payload["id"])] = payload
                        else:
                            notification(payload)
                        condition.notify_all()

            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()

            def send(payload: dict[str, Any]) -> None:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()

            def wait_for(predicate: Callable[[], bool], error: str) -> None:
                deadline = time.monotonic() + self.timeout_seconds
                with condition:
                    while not predicate():
                        if process.poll() is not None:
                            raise ChatGPTAccountError("Codex app-server stopped before completing the request")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ChatGPTAccountError(error)
                        condition.wait(min(remaining, 0.25))

            try:
                send(self._initialize_request())
                wait_for(lambda: 1 in responses, "ChatGPT account initialization timed out")
                self._raise_rpc_error(responses[1], "ChatGPT account initialization")
                send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                send({"jsonrpc": "2.0", "id": 2, "method": "thread/start", "params": thread_params})
                wait_for(lambda: 2 in responses, "ChatGPT account thread start timed out")
                self._raise_rpc_error(responses[2], "ChatGPT account thread start")
                thread = (responses[2].get("result") or {}).get("thread") or {}
                thread_id = str(thread.get("id") or "")
                if not thread_id:
                    raise ChatGPTAccountError("Codex app-server returned no thread id")
                send({
                    "jsonrpc": "2.0", "id": 3, "method": "turn/start",
                    "params": {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": _message_input(messages)}],
                    },
                })
                wait_for(lambda: 3 in responses, "ChatGPT account turn start timed out")
                self._raise_rpc_error(responses[3], "ChatGPT account turn start")
                wait_for(lambda: bool(completed), "ChatGPT account turn timed out")
                if completed[-1] != "completed":
                    raise ChatGPTAccountError("ChatGPT account turn did not complete successfully")
            finally:
                try:
                    process.stdin.close()
                except Exception:
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                reader.join(timeout=1)
        result = "".join(text_parts).strip()
        if not result:
            raise ChatGPTAccountError("ChatGPT account returned an empty response")
        return result


def account_mode_enabled() -> bool:
    return os.getenv("LLM_AUTH_MODE", "api_key").strip().lower() == "chatgpt_account"

