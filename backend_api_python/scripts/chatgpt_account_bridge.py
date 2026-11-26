"""Loopback-only bridge from an isolated QD container to local Codex app-server.

The bearer secret is ephemeral and supplied through the environment.  The
bridge never exposes or reads the Codex OAuth credential store.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "app" / "services" / "chatgpt_account.py"
SPEC = importlib.util.spec_from_file_location("qd_chatgpt_account_bridge_transport", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
Client = MODULE.CodexAppServerClient


class Handler(BaseHTTPRequestHandler):
    server_version = "QuantDingerChatGPTBridge/1"

    def _authorized(self) -> bool:
        expected = os.environ["CHATGPT_ACCOUNT_BRIDGE_TOKEN"]
        return self.headers.get("Authorization") == f"Bearer {expected}"

    def _json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/models":
            self._json(404, {"error": "not_found"})
            return
        models = Client(timeout_seconds=30).list_models()
        self._json(200, {"models": [{"id": item.slug, "name": item.display_name} for item in models]})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/v1/complete":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 256_000:
            self._json(413, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError("messages")
            text = Client(timeout_seconds=180).complete(messages, model=payload.get("model"))
            self._json(200, {"text": text})
        except Exception:
            self._json(502, {"error": "account_transport_failed"})

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    if not os.getenv("CHATGPT_ACCOUNT_BRIDGE_TOKEN", "").strip():
        raise SystemExit("CHATGPT_ACCOUNT_BRIDGE_TOKEN is required")
    os.environ.pop("CHATGPT_ACCOUNT_BRIDGE_URL", None)
    host = os.getenv("CHATGPT_ACCOUNT_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("CHATGPT_ACCOUNT_BRIDGE_PORT", "5137"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
