"""Standalone clean-environment worker for untrusted strategy/indicator code.

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
_STRATEGY_TYPE_KEY = "__quantdinger_strategy_type__"
_DEFAULT_OUTPUT_LIMIT = 32 * 1024 * 1024
_STRATEGY_OUTPUT_LIMIT = 4 * 1024 * 1024


def _preload_safe_modules(safe_exec) -> None:
    """Load every approved module before filesystem access is restricted."""
    for name in sorted(safe_exec.SAFE_IMPORT_MODULES):
        __import__(name)


def _system_library_roots(base_dir: Path) -> list[Path]:
    """Return trusted runtime roots without admitting the application tree."""
    import site
    import sysconfig

    application_root = base_dir.parent.parent.resolve()
    candidates: set[Path] = set()
    runtime_paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = runtime_paths.get(key)
        if value:
            candidates.add(Path(value).resolve())
    for value in site.getsitepackages():
        if value:
            candidates.add(Path(value).resolve())
    for value in (
        "/lib",
        "/lib64",
        "/usr/lib",
        "/usr/lib64",
        "/usr/share/zoneinfo",
    ):
        path = Path(value)
        if path.exists():
            candidates.add(path.resolve())

    roots: list[Path] = []
    for path in sorted(candidates, key=lambda item: (len(item.parts), str(item))):
        if not path.exists():
            continue
        try:
            path.relative_to(application_root)
        except ValueError:
            pass
        else:
            continue
        if any(path == existing or path.is_relative_to(existing) for existing in roots):
            continue
        roots.append(path)
    return roots


def _apply_landlock_filesystem_policy(base_dir: Path) -> None:
    """Deny application, secret, procfs, and arbitrary host-file access on Linux."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("sandbox filesystem isolation requires Linux")

    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long

    # Landlock uses the generic syscall numbers on supported Linux architectures.
    create_ruleset_nr = 444
    add_rule_nr = 445
    restrict_self_nr = 446
    create_version = 1
    rule_path_beneath = 1
    read_file = 1 << 2
    read_dir = 1 << 3
    execute = 1 << 0
    write_file = 1 << 1
    remove_dir = 1 << 4
    remove_file = 1 << 5
    make_char = 1 << 6
    make_dir = 1 << 7
    make_reg = 1 << 8
    make_sock = 1 << 9
    make_fifo = 1 << 10
    make_block = 1 << 11
    make_sym = 1 << 12
    refer = 1 << 13
    truncate = 1 << 14

    abi = int(syscall(create_ruleset_nr, 0, 0, create_version))
    if abi < 1:
        error = ctypes.get_errno()
        raise RuntimeError(f"Landlock is unavailable (errno={error})")

    handled = (
        execute | write_file | read_file | read_dir | remove_dir | remove_file
        | make_char | make_dir | make_reg | make_sock | make_fifo | make_block
        | make_sym
    )
    if abi >= 2:
        handled |= refer
    if abi >= 3:
        handled |= truncate

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneathAttr(ctypes.Structure):
        _fields_ = [
            ("allowed_access", ctypes.c_uint64),
            ("parent_fd", ctypes.c_int32),
        ]

    ruleset_attr = RulesetAttr(handled_access_fs=handled)
    ruleset_fd = int(syscall(
        create_ruleset_nr,
        ctypes.byref(ruleset_attr),
        ctypes.sizeof(ruleset_attr),
        0,
    ))
    if ruleset_fd < 0:
        error = ctypes.get_errno()
        raise RuntimeError(f"Landlock ruleset creation failed (errno={error})")

    path_fds: list[int] = []
    try:
        for root in _system_library_roots(base_dir):
            path_fd = os.open(root, os.O_PATH | os.O_CLOEXEC)
            path_fds.append(path_fd)
            rule = PathBeneathAttr(
                allowed_access=read_file | read_dir | execute,
                parent_fd=path_fd,
            )
            if int(syscall(
                add_rule_nr,
                ruleset_fd,
                rule_path_beneath,
                ctypes.byref(rule),
                0,
            )) != 0:
                error = ctypes.get_errno()
                raise RuntimeError(f"Landlock path rule failed (errno={error})")

        pr_set_no_new_privs = 38
        if libc.prctl(pr_set_no_new_privs, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise RuntimeError(f"no_new_privs failed (errno={error})")
        if int(syscall(restrict_self_nr, ruleset_fd, 0)) != 0:
            error = ctypes.get_errno()
            raise RuntimeError(f"Landlock activation failed (errno={error})")
    finally:
        for path_fd in path_fds:
            os.close(path_fd)
        os.close(ruleset_fd)


def _apply_seccomp_capability_policy() -> None:
    """Deny networking, process control, execution, and cross-process IPC."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("sandbox network isolation requires Linux")

    import ctypes
    import platform

    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        audit_arch = 0xC000003E
        denied = {
            # Network operations.
            41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 288,
            # Process creation/execution, namespace changes, and process control.
            56, 57, 58, 59, 62, 101, 200, 234, 272, 298, 308, 310, 311,
            321, 322, 323, 424, 425, 426, 427, 434, 435, 438,
            # File mutation operations not fully mediated by older Landlock ABIs.
            76, 77, 90, 91, 92, 93, 94, 188, 189, 190, 197, 198, 199,
            260, 268,
            # Kernel keyrings and System V IPC.
            29, 30, 31, 64, 65, 66, 67, 68, 69, 70, 71, 248, 249, 250,
        }
    elif machine in {"aarch64", "arm64"}:
        audit_arch = 0xC00000B7
        denied = {
            # Network operations.
            198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209,
            210, 211, 212, 242,
            # Process creation/execution, namespace changes, and process control.
            97, 117, 129, 130, 131, 220, 221, 241, 268, 270, 271, 280,
            281, 282, 424, 425, 426, 427, 434, 435, 438,
            # File mutation operations not fully mediated by older Landlock ABIs.
            5, 6, 7, 14, 15, 16, 45, 46, 52, 53, 54, 55,
            # Kernel keyrings and System V IPC.
            186, 187, 188, 189, 190, 191, 192, 194, 195, 196, 197,
            217, 218, 219,
        }
    else:
        raise RuntimeError(f"unsupported sandbox architecture: {machine}")

    class SockFilter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint32),
        ]

    class SockFprog(ctypes.Structure):
        _fields_ = [
            ("len", ctypes.c_ushort),
            ("filter", ctypes.POINTER(SockFilter)),
        ]

    bpf_ld_w_abs = 0x20
    bpf_jmp_jeq_k = 0x15
    bpf_ret_k = 0x06
    seccomp_ret_allow = 0x7FFF0000
    seccomp_ret_errno = 0x00050000
    eperm = 1
    instructions = [
        SockFilter(bpf_ld_w_abs, 0, 0, 4),
        SockFilter(bpf_jmp_jeq_k, 1, 0, audit_arch),
        SockFilter(bpf_ret_k, 0, 0, 0),
        SockFilter(bpf_ld_w_abs, 0, 0, 0),
    ]
    for syscall_nr in sorted(denied):
        instructions.extend([
            SockFilter(bpf_jmp_jeq_k, 0, 1, syscall_nr),
            SockFilter(bpf_ret_k, 0, 0, seccomp_ret_errno | eperm),
        ])
    instructions.append(SockFilter(bpf_ret_k, 0, 0, seccomp_ret_allow))

    filter_array = (SockFilter * len(instructions))(*instructions)
    program = SockFprog(len=len(instructions), filter=filter_array)
    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_no_new_privs = 38
    pr_set_seccomp = 22
    seccomp_mode_filter = 2
    if libc.prctl(pr_set_no_new_privs, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise RuntimeError(f"no_new_privs failed (errno={error})")
    if libc.prctl(pr_set_seccomp, seccomp_mode_filter, ctypes.byref(program)) != 0:
        error = ctypes.get_errno()
        raise RuntimeError(f"seccomp activation failed (errno={error})")


def _apply_os_isolation(base_dir: Path) -> None:
    """Apply fail-closed kernel boundaries before any untrusted code runs."""
    _apply_seccomp_capability_policy()
    _apply_landlock_filesystem_policy(base_dir)


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


def _apply_resource_limits(timeout: int, max_memory_mb: int) -> bool:
    """Apply limits inside the exec'd worker, before third-party imports."""
    if sys.platform == "win32":
        return False
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
        memory_limited = False
        if hasattr(resource, "RLIMIT_AS"):
            memory = max(128, max_memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            memory_limited = True
        return memory_limited
    except (ImportError, OSError, ValueError):
        # The parent still enforces a wall-clock timeout and output cap.
        return False


def _format_worker_error(exc: Exception, *, memory_limited: bool) -> str:
    """Return a stable error without leaking host paths from native loaders."""
    detail = str(exc)
    normalized = detail.lower()
    memory_markers = (
        "cannot allocate memory",
        "failed to map segment from shared object",
        "out of memory",
    )
    if memory_limited and (
        isinstance(exc, MemoryError)
        or (
            isinstance(exc, (ImportError, OSError))
            and any(marker in normalized for marker in memory_markers)
        )
    ):
        return "Sandbox worker error: execution exceeded the configured memory limit"
    return f"Sandbox worker error: {type(exc).__name__}: {detail[:1000]}"


class _StrategyDiscoveryState:
    pass


class _StrategyDiscoveryLogger:
    def __call__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    debug = __call__
    info = __call__
    warning = __call__
    warn = __call__
    error = __call__


_INDICATOR_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


def _indicator_timeframe_seconds(value: Any) -> int:
    key = str(value or "").strip().lower()
    if key not in _INDICATOR_TIMEFRAME_SECONDS:
        raise ValueError(f"Unsupported indicator timeframe: {value}")
    return _INDICATOR_TIMEFRAME_SECONDS[key]


class _IndicatorContext:
    """Trusted helper exposed to indicator code inside the sandbox."""

    def __init__(self, base_df: Any, payload: Any, pd, np) -> None:
        self._pd = pd
        self._np = np
        self._base = base_df.copy() if isinstance(base_df, pd.DataFrame) else pd.DataFrame()
        self._payload = payload if isinstance(payload, dict) else {}
        self.market = str(self._payload.get("market") or "")
        self.symbol = str(self._payload.get("symbol") or "")
        self.timeframe = str(self._payload.get("timeframe") or "")
        self.exchange_id = str(self._payload.get("exchange_id") or "")
        self.market_type = str(self._payload.get("market_type") or "")
        self.instrument_id = str(self._payload.get("instrument_id") or "")
        self._series: dict[str, Any] = {}
        for timeframe, rows in (self._payload.get("series") or {}).items():
            frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
            self._series[str(timeframe).lower()] = self._normalize_frame(frame)

    def _normalize_frame(self, frame: Any):
        if not isinstance(frame, self._pd.DataFrame):
            return self._pd.DataFrame()
        result = frame.copy()
        if "time" not in result.columns:
            result["time"] = self._pd.Series(dtype=float)
        numeric_time = self._pd.to_numeric(result["time"], errors="coerce")
        result["time"] = numeric_time.where(numeric_time <= 10_000_000_000, numeric_time / 1000.0)
        for column in ("open", "high", "low", "close", "volume"):
            if column in result.columns:
                result[column] = self._pd.to_numeric(result[column], errors="coerce")
        return result.dropna(subset=["time"]).sort_values("time").drop_duplicates("time").reset_index(drop=True)

    def raw_series(self, timeframe: str):
        key = str(timeframe or "").strip().lower()
        if key == str(self.timeframe or "").strip().lower():
            return self._base.copy()
        return self._series.get(key, self._pd.DataFrame()).copy()

    def request_series(
        self,
        timeframe: str,
        field: str | None = None,
        *,
        gaps: str = "off",
        lookahead: str = "off",
    ):
        source = self._normalize_frame(self.raw_series(timeframe))
        base = self._normalize_frame(self._base)
        result = self._pd.DataFrame(index=self._base.index)
        source_columns = [column for column in source.columns if column != "time"]
        if base.empty or source.empty:
            for column in source_columns or ["open", "high", "low", "close", "volume"]:
                result[column] = self._np.nan
            return result.get(field, self._pd.Series(self._np.nan, index=result.index)) if field else result

        base_seconds = _indicator_timeframe_seconds(self.timeframe)
        source_seconds = _indicator_timeframe_seconds(timeframe)
        use_lookahead = str(lookahead or "off").strip().lower() in {"on", "true", "1"}
        target_key = base["time"] + (0 if use_lookahead else base_seconds)
        source_key = source["time"] + (0 if use_lookahead else source_seconds)

        target = self._pd.DataFrame({"_key": target_key, "_row": range(len(base))}).sort_values("_key")
        source_data = source[source_columns].copy()
        source_data["_key"] = source_key
        source_data = source_data.sort_values("_key")
        merged = self._pd.merge_asof(target, source_data, on="_key", direction="backward")

        if str(gaps or "off").strip().lower() in {"on", "true", "1"}:
            exact = merged["_key"].isin(set(source_data["_key"].tolist()))
            for column in source_columns:
                merged.loc[~exact, column] = self._np.nan

        merged = merged.sort_values("_row")
        for column in source_columns:
            result[column] = merged[column].tolist()
        result["time"] = self._base["time"].tolist() if "time" in self._base.columns else [None] * len(result)
        if field is not None:
            return result.get(str(field), self._pd.Series(self._np.nan, index=result.index))
        return result

    def request_lower_tf(self, timeframe: str, fields: Any = None):
        source = self._normalize_frame(self.raw_series(timeframe))
        base = self._normalize_frame(self._base)
        base_seconds = _indicator_timeframe_seconds(self.timeframe)
        source_seconds = _indicator_timeframe_seconds(timeframe)
        if source_seconds >= base_seconds:
            raise ValueError("request_lower_tf requires a timeframe below the chart timeframe")

        if fields is None:
            selected_fields = ["open", "high", "low", "close", "volume"]
            return_records = True
        elif isinstance(fields, str):
            selected_fields = [fields]
            return_records = False
        else:
            selected_fields = [str(item) for item in fields]
            return_records = False

        available_fields = [field for field in selected_fields if field in source.columns]
        source_times = source["time"].to_numpy(dtype=float) if not source.empty else self._np.asarray([])
        values: dict[str, list[list[Any]]] = {field: [] for field in selected_fields}
        records: list[list[dict[str, Any]]] = []

        for start in base.get("time", self._pd.Series(dtype=float)).tolist():
            left = int(self._np.searchsorted(source_times, float(start), side="left"))
            right = int(self._np.searchsorted(source_times, float(start) + base_seconds, side="left"))
            window = source.iloc[left:right]
            if return_records:
                records.append(window[["time", *available_fields]].to_dict(orient="records"))
            for field in selected_fields:
                values[field].append(window[field].tolist() if field in window.columns else [])

        if return_records:
            return records
        if isinstance(fields, str):
            return values[selected_fields[0]]
        return values


def _strategy_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 30:
        raise ValueError("strategy discovery result nesting is too deep")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return {_STRATEGY_TYPE_KEY: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_STRATEGY_TYPE_KEY: "date", "value": value.isoformat()}
    if isinstance(value, list):
        return [_strategy_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return {
            _STRATEGY_TYPE_KEY: "tuple",
            "items": [_strategy_json_value(item, depth=depth + 1) for item in value],
        }
    if isinstance(value, set):
        return {
            _STRATEGY_TYPE_KEY: "set",
            "items": [_strategy_json_value(item, depth=depth + 1) for item in value],
        }
    if isinstance(value, types.SimpleNamespace):
        return {
            _STRATEGY_TYPE_KEY: "namespace",
            "values": _strategy_json_value(vars(value), depth=depth + 1),
        }
    if isinstance(value, dict):
        return {
            str(key): _strategy_json_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    raise TypeError(
        f"strategy initialize state must be JSON-compatible, got {type(value).__name__}"
    )


class _StrategyDiscoveryContext:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.schedules: list[dict[str, Any]] = []
        self.current_dt = None
        self.previous_trading_date = None
        self.portfolio = types.SimpleNamespace(
            starting_cash=0.0,
            available_cash=0.0,
            total_value=0.0,
            positions={},
        )

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.calls.append({
            "name": name,
            "args": _strategy_json_value(list(args)),
            "kwargs": _strategy_json_value(kwargs),
        })

    def set_universe(self, *args: Any, **kwargs: Any) -> None:
        self._record("set_universe", args, kwargs)

    def set_benchmark(self, *args: Any, **kwargs: Any) -> None:
        self._record("set_benchmark", args, kwargs)

    def subscribe(self, *args: Any, **kwargs: Any) -> None:
        self._record("subscribe", args, kwargs)

    def set_warmup(self, *args: Any, **kwargs: Any) -> None:
        self._record("set_warmup", args, kwargs)

    def allow_leverage(self, *args: Any, **kwargs: Any) -> None:
        self._record("allow_leverage", args, kwargs)

    def set_metadata(self, *args: Any, **kwargs: Any) -> None:
        self._record("set_metadata", args, kwargs)

    def schedule(self, frequency: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        callback = kwargs.get("callback")
        for value in args:
            if callable(value):
                callback = value
                break
        if not callable(callback):
            raise ValueError("strategyV2.scheduleCallbackRequired")
        self.schedules.append({
            "frequency": frequency,
            "callback": str(getattr(callback, "__name__", "scheduled")),
            "time": str(kwargs.get("time") or ""),
            "weekday": int(kwargs.get("weekday", 1)) if frequency == "weekly" else None,
            "monthday": int(kwargs.get("monthday", 1)) if frequency == "monthly" else None,
        })


def _execute_strategy_discovery(request: dict[str, Any], safe_exec) -> dict[str, Any]:
    code = str(request.get("code") or "")
    timeout = max(0.05, float(request.get("timeout") or 10))
    state = _StrategyDiscoveryState()
    context = _StrategyDiscoveryContext()
    logger = _StrategyDiscoveryLogger()
    position = types.SimpleNamespace(amount=0.0, avg_cost=0.0, last_price=0.0)
    namespace: dict[str, Any] = {
        "__builtins__": safe_exec.build_safe_builtins(),
        "g": state,
        "run_daily": lambda *args, **kwargs: context.schedule("daily", args, kwargs),
        "run_weekly": lambda *args, **kwargs: context.schedule("weekly", args, kwargs),
        "run_monthly": lambda *args, **kwargs: context.schedule("monthly", args, kwargs),
        "get_index_stocks": lambda value, **_kwargs: [value],
        "get_universe_stocks": lambda *_args, **_kwargs: [],
        "get_position": lambda *_args, **_kwargs: position,
        "get_positions": lambda *_args, **_kwargs: {},
        "get_order_status": lambda *_args, **_kwargs: {
            "status": "unknown",
            "filled_quantity": 0.0,
            "filled_notional": 0.0,
            "fee": 0.0,
        },
        "cancel_order": lambda *_args, **_kwargs: False,
        "consume_last_exit_reason": lambda *_args, **_kwargs: "",
        "get_history": lambda *_args, **_kwargs: [],
        "history": lambda *_args, **_kwargs: [],
        "is_trade": lambda *_args, **_kwargs: False,
        "log": logger,
    }
    module_result = safe_exec.safe_exec_with_validation(
        code,
        namespace,
        namespace,
        timeout=timeout,
        max_memory_mb=max(128, int(request.get("max_memory_mb") or 512)),
        pre_import="",
    )
    if not module_result.get("success"):
        return {
            "success": False,
            "error": str(module_result.get("error") or "strategyV2.compileFailed")[:4000],
            "result": None,
        }
    if not callable(namespace.get("initialize")):
        return {
            "success": False,
            "error": "strategyV2.initializeRequired",
            "result": None,
        }
    namespace["discovery_context"] = context
    try:
        initialize_result = safe_exec.safe_exec_code(
            "initialize(discovery_context)",
            namespace,
            namespace,
            timeout=timeout,
            max_memory_mb=max(128, int(request.get("max_memory_mb") or 512)),
        )
    finally:
        namespace.pop("discovery_context", None)
    if not initialize_result.get("success"):
        return {
            "success": False,
            "error": (
                "strategyV2.initializeFailed:"
                + str(initialize_result.get("error") or "strategyV2.compileFailed")[:3900]
            ),
            "result": None,
        }
    handlers = [
        name for name in (
            "initialize",
            "before_trading_start",
            "handle_data",
            "after_trading_end",
            "on_rebalance",
            "on_price_tick",
            "_equity_risk_exit",
            "_release_equity_risk_exit",
        )
        if callable(namespace.get(name))
    ]
    return {
        "success": True,
        "error": None,
        "result": {
            "calls": context.calls,
            "schedules": context.schedules,
            "state": _strategy_json_value(vars(state)),
            "handlers": handlers,
        },
    }


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    _deny_network()
    base_dir = Path(__file__).resolve().parent
    safe_exec = _load_safe_exec(base_dir)
    # Load approved dependencies before Landlock removes access to the
    # application tree and all non-runtime filesystem paths.
    _preload_safe_modules(safe_exec)
    import numpy as np
    import pandas as pd

    if request.get("mode") == "strategy_discovery":
        _apply_os_isolation(base_dir)
        return _execute_strategy_discovery(request, safe_exec)

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
        indicator_context = _IndicatorContext(
            df,
            exec_env.get("indicator_context"),
            pd,
            np,
        )
        exec_env["ctx"] = indicator_context
        exec_env["request_series"] = indicator_context.request_series
        exec_env["request_lower_tf"] = indicator_context.request_lower_tf

    _apply_os_isolation(base_dir)

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
    # Keep a private protocol descriptor, then detach untrusted native writes
    # from the parent pipes. Python-level stdout/stderr are also suppressed by
    # _execute, but dup2 protects against C-extension writes to fd 1 or 2.
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", closefd=True)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, sys.stdout.fileno())
        os.dup2(devnull_fd, sys.stderr.fileno())
    finally:
        os.close(devnull_fd)
    request: dict[str, Any] = {}
    memory_limited = False
    try:
        decoded_request = json.loads(sys.stdin.buffer.read())
        if not isinstance(decoded_request, dict):
            raise TypeError("sandbox request must be an object")
        request = decoded_request
        memory_limited = _apply_resource_limits(
            max(1, int(request.get("timeout") or 20)),
            max(128, int(request.get("max_memory_mb") or 1024)),
        )
        if (
            request.get("mode") == "strategy_discovery"
            and not memory_limited
            and not request.get("parent_memory_limit")
        ):
            raise RuntimeError("strategy sandbox memory limit could not be applied")
        response = _execute(request)
    except Exception as exc:
        response = {
            "success": False,
            "error": _format_worker_error(exc, memory_limited=memory_limited),
            "result": None,
        }
    output_limit = (
        _STRATEGY_OUTPUT_LIMIT
        if request.get("mode") == "strategy_discovery"
        else _DEFAULT_OUTPUT_LIMIT
    )
    serialized = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > output_limit:
        response = {
            "success": False,
            "error": f"Sandbox result exceeded the {output_limit // (1024 * 1024)}MB output limit",
            "result": None,
        }
        serialized = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    protocol_out.write(serialized)
    protocol_out.flush()
    protocol_out.close()
    return 0 if response.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
