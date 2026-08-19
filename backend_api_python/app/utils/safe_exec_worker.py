"""Standalone clean-environment worker for untrusted indicator code.

This module is launched with ``python -I`` and must stay independent from the
``app`` package: importing the application package would load ``.env`` before
the sandbox starts.  The parent passes and receives JSON only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import math
import os
from datetime import date, datetime
from pathlib import Path
import sys
import types
from typing import Any


_TYPE_KEY = "__quantdinger_sandbox_type__"


class _NullWriter(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def _install_safe_exec_import_stubs(base_dir: Path) -> None:
    """Load safe_exec without importing app/__init__.py and its dotenv hook."""
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = []
    utils_pkg = types.ModuleType("app.utils")
    utils_pkg.__path__ = [str(base_dir)]
    logger_stub = types.ModuleType("app.utils.logger")
    logger_stub.get_logger = logging.getLogger

    thread_spec = importlib.util.spec_from_file_location(
        "app.utils.thread_capacity",
        base_dir / "thread_capacity.py",
    )
    if thread_spec is None or thread_spec.loader is None:
        raise RuntimeError("sandbox worker could not load thread capacity helper")
    thread_module = importlib.util.module_from_spec(thread_spec)
    thread_spec.loader.exec_module(thread_module)

    sys.modules.update({
        "app": app_pkg,
        "app.utils": utils_pkg,
        "app.utils.logger": logger_stub,
        "app.utils.thread_capacity": thread_module,
    })


def _load_safe_exec(base_dir: Path):
    _install_safe_exec_import_stubs(base_dir)
    spec = importlib.util.spec_from_file_location(
        "quantdinger_sandbox_safe_exec",
        base_dir / "safe_exec.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("sandbox worker could not load execution helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decode(value: Any, pd, np) -> Any:
    if isinstance(value, list):
        return [_decode(item, pd, np) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get(_TYPE_KEY)
    if kind == "datetime":
        return pd.Timestamp(value.get("value"))
    if kind == "dataframe":
        return pd.DataFrame(
            data=[[_decode(item, pd, np) for item in row] for row in value.get("data", [])],
            columns=[str(item) for item in value.get("columns", [])],
            index=[_decode(item, pd, np) for item in value.get("index", [])],
        )
    if kind == "series":
        return pd.Series(
            [_decode(item, pd, np) for item in value.get("data", [])],
            index=[_decode(item, pd, np) for item in value.get("index", [])],
            name=value.get("name"),
        )
    if kind == "ndarray":
        return np.asarray(value.get("data", []))
    return {str(key): _decode(item, pd, np) for key, item in value.items()}


def _encode(value: Any, pd, np, *, depth: int = 0) -> Any:
    if depth > 40:
        raise ValueError("sandbox result nesting is too deep")
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _encode(value.item(), pd, np, depth=depth + 1)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return {_TYPE_KEY: "datetime", "value": value.isoformat()}
    if isinstance(value, pd.DataFrame):
        return {
            _TYPE_KEY: "dataframe",
            "columns": [str(item) for item in value.columns],
            "index": [_encode(item, pd, np, depth=depth + 1) for item in value.index],
            "data": [
                [_encode(item, pd, np, depth=depth + 1) for item in row]
                for row in value.itertuples(index=False, name=None)
            ],
        }
    if isinstance(value, pd.Series):
        return {
            _TYPE_KEY: "series",
            "name": None if value.name is None else str(value.name),
            "index": [_encode(item, pd, np, depth=depth + 1) for item in value.index],
            "data": [_encode(item, pd, np, depth=depth + 1) for item in value.tolist()],
        }
    if isinstance(value, np.ndarray):
        return {
            _TYPE_KEY: "ndarray",
            "data": _encode(value.tolist(), pd, np, depth=depth + 1),
        }
    if isinstance(value, dict):
        return {
            str(key): _encode(item, pd, np, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_encode(item, pd, np, depth=depth + 1) for item in value]
    raise TypeError(f"sandbox result type is not allowed: {type(value).__name__}")


def _deny_network() -> None:
    """Disable Python socket creation as defense in depth inside the worker."""
    try:
        import socket

        def denied(*_args: Any, **_kwargs: Any):
            raise PermissionError("network access is disabled in the sandbox")

        socket.socket = denied
        socket.create_connection = denied
        socket.socketpair = denied
    except Exception:
        pass


def _apply_resource_limits(timeout: int, max_memory_mb: int) -> None:
    """Apply limits inside the exec'd worker, before third-party imports."""
    if sys.platform == "win32":
        return
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_CPU,
            (max(1, timeout), max(2, timeout + 1)),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (32 * 1024 * 1024, 32 * 1024 * 1024),
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if sys.platform.startswith("linux"):
            memory = max(128, max_memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ImportError, OSError, ValueError):
        # The parent still enforces a wall-clock timeout and output cap.
        return


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    # pandas/numpy are imported only after the process has started with the
    # parent's clean environment.  They never observe API or broker secrets.
    import numpy as np
    import pandas as pd

    _deny_network()
    base_dir = Path(__file__).resolve().parent
    safe_exec = _load_safe_exec(base_dir)
    input_data = _decode(request.get("input") or {}, pd, np)
    if not isinstance(input_data, dict):
        raise TypeError("sandbox input must be an object")
    exec_env: dict[str, Any] = dict(input_data)
    exec_env.update({
        "pd": pd,
        "np": np,
        "math": math,
        "output": exec_env.get("output"),
        "__builtins__": safe_exec.build_safe_builtins(),
    })
    df = exec_env.get("df")
    if isinstance(df, pd.DataFrame):
        for column in ("open", "high", "low", "close", "volume"):
            if column in df.columns:
                exec_env[column] = df[column]

    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()
    try:
        result = safe_exec.safe_exec_with_validation(
            code=str(request.get("code") or ""),
            exec_globals=exec_env,
            exec_locals=exec_env,
            timeout=max(1, int(request.get("timeout") or 20)),
            max_memory_mb=max(128, int(request.get("max_memory_mb") or 1024)),
        )
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr

    if not result.get("success"):
        return {
            "success": False,
            "error": str(result.get("error") or "sandbox execution failed")[:4000],
            "result": None,
        }
    return {
        "success": True,
        "error": None,
        "result": {
            "output": _encode(exec_env.get("output"), pd, np),
            "df": _encode(exec_env.get("df"), pd, np),
        },
    }


def main() -> int:
    # Popen already supplies a clean environment. Clear it again before any
    # third-party import so a future caller cannot accidentally weaken that
    # contract by changing only the parent-side launcher.
    os.environ.clear()
    os.environ.update({
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONNOUSERSITE": "1",
        "QD_SANDBOX_WORKER": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    })
    protocol_out = sys.stdout
    try:
        request = json.loads(sys.stdin.buffer.read())
        _apply_resource_limits(
            max(1, int(request.get("timeout") or 20)),
            max(128, int(request.get("max_memory_mb") or 1024)),
        )
        response = _execute(request)
    except Exception as exc:
        response = {
            "success": False,
            "error": f"Sandbox worker error: {type(exc).__name__}: {str(exc)[:1000]}",
            "result": None,
        }
    protocol_out.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    protocol_out.flush()
    return 0 if response.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
