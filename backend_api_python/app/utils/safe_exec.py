"""Safe execution helpers for user-provided strategy and indicator code."""
import signal
import sys
import os
import threading
import time
import builtins as _builtins_mod
import types
import re
from typing import Dict, Any, Optional, Tuple, Set
from contextlib import contextmanager

from app.utils.logger import get_logger
from app.utils.thread_capacity import format_thread_capacity

logger = get_logger(__name__)


_SENSITIVE_ENV_NAME = re.compile(
    r"(?:SECRET|PASSWORD|PASSWD|TOKEN|API_?KEY|PRIVATE_?KEY|"
    r"CREDENTIAL|DATABASE_URL|DB_URL|DSN|SMTP|OAUTH|STRIPE|WEBHOOK)",
    re.IGNORECASE,
)


def _redact_sensitive_text(value: object) -> str:
    """Remove configured secret values from sandbox-facing error text."""
    text = str(value or "")[:4000]
    for name, secret in os.environ.items():
        if not _SENSITIVE_ENV_NAME.search(str(name)):
            continue
        secret_text = str(secret or "")
        # Avoid replacing short/common values such as ``true`` or ``UTC``.
        if len(secret_text) >= 8 and secret_text in text:
            text = text.replace(secret_text, "[REDACTED]")
    return text


class TimeoutError(Exception):
    """Raised when sandboxed code execution exceeds its time limit."""
    pass


# Whitelisted builtins (strict)
# Only pure computational builtins. No I/O, no introspection, no code gen.
_BUILTINS_WHITELIST: Set[str] = {
    # Types / constructors
    'bool', 'int', 'float', 'complex', 'str', 'bytes', 'bytearray',
    'list', 'tuple', 'dict', 'set', 'frozenset',
    'range', 'slice', 'memoryview',
    # Math / comparison
    'abs', 'round', 'pow', 'divmod', 'min', 'max', 'sum',
    # Iteration
    'len', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
    'iter', 'next', 'all', 'any',
    # String / repr. ``chr`` and ``format`` are intentionally absent: pairing
    # runtime-built strings with Python's format-field attribute traversal can
    # turn a rich object such as a pandas DataFrame into a reflection gadget.
    'repr', 'ascii', 'ord', 'bin', 'hex', 'oct',
    'hash',
    # Type checking (safe, no mutation)
    'isinstance', 'issubclass', 'hasattr', 'callable',
    # Conversion
    'print',
    # Exceptions (needed for try/except in user code)
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'ZeroDivisionError', 'StopIteration',
    'RuntimeError', 'OverflowError', 'ArithmeticError',
    'NotImplementedError', 'NameError', 'ImportError',
    # Constants
    'True', 'False', 'None',
    'Ellipsis', 'NotImplemented',
}

# Modules allowed in user code via `import xxx`
# operator is excluded: attrgetter/itemgetter enable dunder introspection escapes.
SAFE_IMPORT_MODULES: Set[str] = {
    'numpy', 'pandas', 'math', 'json', 'datetime', 'time',
    'collections', 'functools', 'itertools', 'statistics',
    'decimal', 'fractions', 'copy',
}

# Dunder names reachable via string-built attribute access (e.g. operator.attrgetter).
_FORBIDDEN_DUNDER_SUFFIXES: Set[str] = {
    'builtins__', 'import__', 'class__', 'bases__', 'subclasses__', 'mro__',
    'globals__', 'code__', 'func__', 'dict__', 'module__', 'getattribute__',
    'setattr__', 'delattr__', 'init__', 'reduce__', 'getstate__', 'setstate__',
    'call__', 'getitem__', 'setitem__', 'delitem__', 'iter__', 'next__',
    # Frame / traceback / closure chains used to reach the un-sandboxed scope.
    'traceback__', 'closure__', 'defaults__', 'kwdefaults__',
    # Importer and module-spec escape hatches.
    'loader__', 'spec__', 'path__', 'file__', 'cached__', 'package__',
}

_OPERATOR_ACCESSOR_NAMES: Set[str] = {'attrgetter', 'itemgetter', 'methodcaller'}

# Method names that read/write files, evaluate strings, or pivot to other
# processes; reject them on ANY receiver (df.to_csv, np.array().tofile,
# pd.read_csv, etc.) because numpy and pandas are intentionally whitelisted.
_DANGEROUS_METHOD_NAMES: Set[str] = {
    # ``str.format`` / ``format_map`` resolve dotted fields with implicit
    # getattr calls.  That bypasses source-level dunder checks when the format
    # string is assembled at runtime (for example with chr(95)).
    'format', 'format_map',
    # pandas read_*: arbitrary file read or SSRF via URL / pickle deser RCE.
    'read_csv', 'read_table', 'read_fwf', 'read_excel', 'read_xml',
    'read_html', 'read_json', 'read_pickle', 'read_parquet', 'read_orc',
    'read_feather', 'read_hdf', 'read_sql', 'read_sql_query',
    'read_sql_table', 'read_clipboard', 'read_gbq', 'read_sas',
    'read_spss', 'read_stata',
    # pandas to_* / ndarray.tofile: arbitrary file write / pickle write.
    'to_csv', 'to_excel', 'to_xml', 'to_html', 'to_json', 'to_pickle',
    'to_parquet', 'to_orc', 'to_feather', 'to_hdf', 'to_sql',
    'to_clipboard', 'to_gbq', 'to_stata', 'to_latex',
    'tofile',
    # numpy IO: arbitrary read / write / pickle deser.
    'save', 'savez', 'savez_compressed', 'savetxt',
    'load', 'loadtxt', 'genfromtxt', 'fromfile', 'memmap', 'DataSource',
    # String-expression evaluators that execute attacker-controlled code.
    'eval', 'query',
    # Frame / introspection accessors should never be invoked.
    'getframe', 'currentframe', 'stack', 'getouterframes',
    # pandas.io.common: file/URL IO bypassing blocked read_* entry points.
    'urlopen', '_urlopen', 'get_filepath_or_buffer', '_get_filepath_or_buffer',
    'file_exists', 'file_open', 'open_url',
    # Import machinery can bypass the restricted __import__ implementation.
    'load_module', 'exec_module', 'create_module', 'find_module',
    'find_spec', 'path_hook', 'path_hooks', 'get_data', 'get_code',
    'get_source', 'get_resource_reader',
}

_BLOCKED_MODULE_ATTRS: Set[str] = {
    # Keep the proxy's own module metadata from becoming an introspection
    # primitive.  In particular, ``proxy.__dict__`` would otherwise expose
    # the copied module namespace and make it possible to recover importer
    # internals through indirect lookups.
    '__loader__', '__spec__', '__builtins__', '__path__', '__file__',
    '__cached__', '__package__', '__dict__', '__class__', '__module__',
}
_SAFE_MODULE_DUNDER_ATTRS: Set[str] = {'__name__', '__doc__', '__all__'}


class _SafeModuleProxy(types.ModuleType):
    """Read-only view of an allowed module without importer internals."""

    def __getattribute__(self, name: str) -> Any:
        if name in _BLOCKED_MODULE_ATTRS or (
            name.startswith('__')
            and name.endswith('__')
            and name not in _SAFE_MODULE_DUNDER_ATTRS
        ):
            raise AttributeError(f"module attribute is not available: {name}")
        value = super().__getattribute__(name)
        # ``from package import submodule`` reads the submodule attribute from
        # the returned package. Never hand that raw module back to user code,
        # and do not expose transitive modules outside the import allow-list
        # (for example collections._sys -> sys.modules -> os).
        if isinstance(value, types.ModuleType) and not isinstance(value, _SafeModuleProxy):
            ok, _ = _is_safe_import_name(getattr(value, '__name__', ''))
            if not ok:
                raise AttributeError(f"module attribute is not available: {name}")
            return _wrap_imported_module(value)
        return value

# Attribute names whose access leaks frames / closures / code objects, even
# without dunder syntax.
_DANGEROUS_FRAME_ATTRS: Set[str] = {
    'gi_frame', 'gi_code', 'gi_yieldfrom',
    'cr_frame', 'cr_code', 'cr_await',
    'ag_frame', 'ag_code', 'ag_await',
    'f_globals', 'f_locals', 'f_back', 'f_builtins',
    'f_code', 'f_trace', 'f_lasti', 'f_lineno',
    'tb_frame', 'tb_next', 'tb_lasti', 'tb_lineno',
    'func_globals', 'func_code', 'func_closure', 'func_dict',
}

# Sub-modules of whitelisted packages that expose C/native escapes.
_DANGEROUS_SUBMODULE_ATTRS: Set[str] = {
    'ctypeslib', 'distutils', 'f2py',
}

# pandas / numpy module roots and internal sub-packages that bypass top-level IO bans.
_PANDAS_NUMPY_ROOTS: Set[str] = {'pd', 'pandas', 'np', 'numpy'}
_DANGEROUS_PD_NUMPY_ATTRS: Set[str] = {
    'io', 'compat', 'util', 'core', 'arrays', 'plotting', 'errors',
    'testing', 'tseries', 'api', 'conftest', 'lib',
}


def _dangerous_pd_numpy_import(name: str) -> Optional[str]:
    """Return the blocked import path when a pandas/numpy submodule is unsafe."""
    parts = [p for p in str(name or '').split('.') if p]
    if len(parts) < 2:
        return None
    root, attrs = parts[0], parts[1:]
    alias_root = {'pandas': 'pd', 'numpy': 'np'}.get(root, root)
    internal = _dangerous_pd_numpy_internal(alias_root, attrs)
    if internal:
        return name
    for attr in attrs:
        if attr in _DANGEROUS_SUBMODULE_ATTRS:
            return name
    return None


def _is_safe_import_name(name: str) -> Tuple[bool, Optional[str]]:
    """Validate import names with package-root and dangerous-submodule checks."""
    root = str(name or '').split('.')[0]
    if root not in SAFE_IMPORT_MODULES:
        return False, f"Import not allowed: {name}"
    blocked = _dangerous_pd_numpy_import(str(name or ''))
    if blocked:
        return False, f"Import not allowed: dangerous pandas/numpy submodule {blocked}"
    return True, None


def _wrap_imported_module(module: Any) -> Any:
    """Copy a module into a proxy that hides loader/spec escape attributes."""
    if isinstance(module, _SafeModuleProxy):
        return module
    if not isinstance(module, types.ModuleType):
        return module

    proxy = _SafeModuleProxy(getattr(module, '__name__', 'safe_module'))
    for name, value in module.__dict__.items():
        if name in _BLOCKED_MODULE_ATTRS:
            continue
        if name.startswith('__') and name.endswith('__') and name not in _SAFE_MODULE_DUNDER_ATTRS:
            continue
        setattr(proxy, name, value)
    return proxy


def _make_safe_import():
    """Create a restricted __import__ that only allows whitelisted modules."""
    def safe_import(name, *args, **kwargs):
        ok, err = _is_safe_import_name(name)
        if ok:
            return _wrap_imported_module(
                _builtins_mod.__import__(name, *args, **kwargs)
            )
        raise ImportError(err or f"Import not allowed: {name}")
    return safe_import


def _sanitize_exec_namespace(namespace: Optional[Dict[str, Any]]) -> None:
    """Replace ambient raw modules with importer-safe proxies in-place."""
    if namespace is None:
        return
    for name, value in list(namespace.items()):
        if name == '__builtins__':
            continue
        if isinstance(value, types.ModuleType) and not isinstance(value, _SafeModuleProxy):
            namespace[name] = _wrap_imported_module(value)


def build_safe_builtins(extra_allowed: Optional[Set[str]] = None) -> Dict[str, Any]:
    """
    Build a restricted __builtins__ dict for sandboxed exec().

    Only includes computational builtins from the whitelist.
    Dangerous capabilities (eval, exec, open, getattr, type, __import__, etc.)
    are excluded by default.

    Args:
        extra_allowed: additional builtin names to include (use with caution)
    """
    allowed = _BUILTINS_WHITELIST | (extra_allowed or set())
    safe = {}
    for name in allowed:
        val = getattr(_builtins_mod, name, None)
        if val is not None:
            safe[name] = val
    safe['__import__'] = _make_safe_import()
    return safe


# Timeout (cross-platform)


class _TimeoutWatchdog:
    """One bounded watchdog thread shared by all non-main-thread executions."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._registrations: Dict[int, Tuple[float, int, float, threading.Event]] = {}
        self._next_token = 0
        self._thread: Optional[threading.Thread] = None
        self._pid = os.getpid()
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self) -> None:
        self._condition = threading.Condition()
        self._registrations = {}
        self._thread = None
        self._pid = os.getpid()

    def register(
        self,
        *,
        target_tid: int,
        seconds: float,
        timed_out: threading.Event,
    ) -> int:
        with self._condition:
            current_pid = os.getpid()
            if current_pid != self._pid:
                self._pid = current_pid
                self._registrations.clear()
                self._thread = None
            self._ensure_thread_locked()
            self._next_token += 1
            token = self._next_token
            self._registrations[token] = (
                time.monotonic() + max(0.001, float(seconds)),
                int(target_tid),
                float(seconds),
                timed_out,
            )
            self._condition.notify()
            return token

    def cancel(self, token: int) -> None:
        with self._condition:
            self._registrations.pop(int(token), None)
            self._condition.notify()

    def _ensure_thread_locked(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="SafeExecTimeoutWatchdog",
            daemon=True,
        )
        try:
            self._thread.start()
        except RuntimeError as exc:
            self._thread = None
            raise RuntimeError(
                f"Failed to start shared execution timeout watchdog: {exc}; "
                f"{format_thread_capacity()}"
            ) from exc

    def _run(self) -> None:
        while True:
            due: list[Tuple[int, int, float, threading.Event]] = []
            with self._condition:
                while not self._registrations:
                    self._condition.wait()
                now = time.monotonic()
                next_deadline = min(item[0] for item in self._registrations.values())
                if next_deadline > now:
                    self._condition.wait(next_deadline - now)
                    continue
                for token, (deadline, target_tid, seconds, timed_out) in list(
                    self._registrations.items()
                ):
                    if deadline <= now:
                        self._registrations.pop(token, None)
                        due.append((token, target_tid, seconds, timed_out))
            for _token, target_tid, seconds, timed_out in due:
                self._inject_timeout(target_tid, seconds, timed_out)

    @staticmethod
    def _inject_timeout(
        target_tid: int,
        seconds: float,
        timed_out: threading.Event,
    ) -> None:
        timed_out.set()
        try:
            import ctypes

            ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(target_tid),
                ctypes.py_object(TimeoutError),
            )
            if ret == 0:
                logger.warning("timeout inject: invalid thread id")
            elif ret > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(target_tid),
                    ctypes.py_object(0),
                )
        except Exception as exc:
            logger.warning("timeout inject failed after %ss: %s", seconds, exc)


_TIMEOUT_WATCHDOG = _TimeoutWatchdog()


@contextmanager
def timeout_context(seconds: int):
    """Bound user-code execution time.

    Uses SIGALRM on Unix main threads and a timer-based async exception
    fallback elsewhere.
    """
    is_main_thread = threading.current_thread() is threading.main_thread()

    # Strategy 1: Unix SIGALRM (most reliable, main thread only)
    if sys.platform != 'win32' and is_main_thread:
        def timeout_handler(signum, frame):
            raise TimeoutError(f"Code execution timed out after {seconds} seconds")
        try:
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        except ValueError:
            # ``signal.signal`` itself is unavailable in some embedded
            # runtimes.  Do not catch ValueError raised by the user program.
            pass  # fall through to watchdog strategy
        else:
            signal.alarm(max(1, int(seconds)))
            try:
                yield
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            return

    # Strategy 2: one process-wide watchdog + async exception (cross-platform).
    # A Timer per strategy cycle eventually exhausts the container PID quota
    # because Linux counts threads as PIDs.
    target_tid = threading.current_thread().ident
    if target_tid is None:
        raise RuntimeError("Current execution thread has no identifier")
    timed_out = threading.Event()
    token = _TIMEOUT_WATCHDOG.register(
        target_tid=target_tid,
        seconds=float(seconds),
        timed_out=timed_out,
    )
    try:
        yield
    finally:
        _TIMEOUT_WATCHDOG.cancel(token)
        if timed_out.is_set():
            raise TimeoutError(f"Code execution timed out after {seconds} seconds")


# Core execution

def safe_exec_code(
    code: str,
    exec_globals: Dict[str, Any],
    exec_locals: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
    max_memory_mb: Optional[int] = None
) -> Dict[str, Any]:
    """Validate and execute Python code with sandbox namespace/timeout guards.

    Args:
        code: Python code to execute.
        exec_globals: globals dictionary.
        exec_locals: locals dictionary; defaults to exec_globals.
        timeout: timeout in seconds.
        max_memory_mb: memory limit in MB when RLIMIT is enabled.
    """
    is_safe, validation_error = validate_code_safety(code)
    if not is_safe:
        return {
            'success': False,
            'error': f"Unsafe code rejected: {validation_error}",
            'result': None,
        }

    exec_globals['__builtins__'] = build_safe_builtins()
    if exec_locals is None:
        exec_locals = exec_globals
    elif exec_locals is not exec_globals:
        exec_locals['__builtins__'] = exec_globals['__builtins__']
    _sanitize_exec_namespace(exec_globals)
    if exec_locals is not exec_globals:
        _sanitize_exec_namespace(exec_locals)

    if max_memory_mb is None:
        max_memory_mb = 500

    try:
        if sys.platform != 'win32' and os.getenv('SAFE_EXEC_ENABLE_RLIMIT', 'false').lower() == 'true':
            try:
                import resource
                max_memory_bytes = max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
            except (ImportError, ValueError, OSError) as e:
                logger.warning(f"Failed to set memory limit: {e}")

        with timeout_context(timeout):
            exec(code, exec_globals, exec_locals)

        return {'success': True, 'error': None, 'result': None}

    except MemoryError:
        error_msg = f"Code execution exceeded the {max_memory_mb}MB memory limit"
        logger.error(f"Code execution out of memory (limit={max_memory_mb}MB)")
        return {'success': False, 'error': error_msg, 'result': None}
    except TimeoutError as e:
        logger.error(f"Code execution timed out (timeout={timeout}s)")
        return {'success': False, 'error': str(e), 'result': None}
    except Exception as e:
        # Do not reflect a full traceback or configured secret values through
        # validation endpoints.  The exception class plus a redacted message
        # is sufficient for users to debug indicator/strategy code.
        error_msg = (
            f"Code execution error: {type(e).__name__}: "
            f"{_redact_sensitive_text(str(e))}"
        )
        logger.error("Code execution error: %s", _redact_sensitive_text(e))
        return {'success': False, 'error': error_msg, 'result': None}


def safe_exec_with_validation(
    code: str,
    exec_globals: Dict[str, Any],
    exec_locals: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    max_memory_mb: Optional[int] = None,
    pre_import: str = "import numpy as np\nimport pandas as pd\n",
) -> Dict[str, Any]:
    """
    Validate + execute user code in one call.

    1. Runs validate_code_safety(); rejects unsafe code.
    2. Replaces any caller-provided builtins with build_safe_builtins().
    3. Wraps pre-injected module objects to hide importer metadata.
    4. Executes pre_import, then user code via safe_exec_code().

    Returns same dict as safe_exec_code().
    """
    is_safe, err = validate_code_safety(code)
    if not is_safe:
        return {'success': False, 'error': f"Unsafe code rejected: {err}", 'result': None}

    # This helper is a security boundary: callers must not be able to weaken
    # it accidentally by supplying process-global builtins or raw modules.
    exec_globals['__builtins__'] = build_safe_builtins()
    if exec_locals is not None and exec_locals is not exec_globals:
        exec_locals['__builtins__'] = exec_globals['__builtins__']
    _sanitize_exec_namespace(exec_globals)
    if exec_locals is not exec_globals:
        _sanitize_exec_namespace(exec_locals)

    if pre_import:
        pre_import_safe, pre_import_err = validate_code_safety(pre_import)
        if not pre_import_safe:
            return {
                'success': False,
                'error': f"Unsafe pre-import rejected: {pre_import_err}",
                'result': None,
            }
        try:
            exec(pre_import, exec_globals)
        except Exception as e:
            return {'success': False, 'error': f"Pre-import failed: {e}", 'result': None}

    return safe_exec_code(
        code=code,
        exec_globals=exec_globals,
        exec_locals=exec_locals,
        timeout=timeout,
        max_memory_mb=max_memory_mb,
    )


# Subprocess isolation

_SANDBOX_TRANSPORT_TYPE = "__quantdinger_sandbox_type__"


def _sandbox_transport_encode(value: Any, *, depth: int = 0) -> Any:
    """Encode trusted parent input without using pickle across the boundary."""
    import math
    from datetime import date, datetime

    import numpy as np
    import pandas as pd

    if depth > 40:
        raise ValueError("sandbox input nesting is too deep")
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _sandbox_transport_encode(value.item(), depth=depth + 1)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return {
            _SANDBOX_TRANSPORT_TYPE: "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, pd.DataFrame):
        return {
            _SANDBOX_TRANSPORT_TYPE: "dataframe",
            "columns": [str(item) for item in value.columns],
            "index": [
                _sandbox_transport_encode(item, depth=depth + 1)
                for item in value.index
            ],
            "data": [
                [
                    _sandbox_transport_encode(item, depth=depth + 1)
                    for item in row
                ]
                for row in value.itertuples(index=False, name=None)
            ],
        }
    if isinstance(value, pd.Series):
        return {
            _SANDBOX_TRANSPORT_TYPE: "series",
            "name": None if value.name is None else str(value.name),
            "index": [
                _sandbox_transport_encode(item, depth=depth + 1)
                for item in value.index
            ],
            "data": [
                _sandbox_transport_encode(item, depth=depth + 1)
                for item in value.tolist()
            ],
        }
    if isinstance(value, np.ndarray):
        return {
            _SANDBOX_TRANSPORT_TYPE: "ndarray",
            "data": _sandbox_transport_encode(value.tolist(), depth=depth + 1),
        }
    if isinstance(value, dict):
        return {
            str(key): _sandbox_transport_encode(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sandbox_transport_encode(item, depth=depth + 1)
            for item in value
        ]
    raise TypeError(f"sandbox input type is not supported: {type(value).__name__}")


def _sandbox_transport_decode(value: Any) -> Any:
    """Decode JSON-only child output; no attacker-controlled pickle is loaded."""
    import numpy as np
    import pandas as pd

    if isinstance(value, list):
        return [_sandbox_transport_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get(_SANDBOX_TRANSPORT_TYPE)
    if kind == "datetime":
        return pd.Timestamp(value.get("value"))
    if kind == "dataframe":
        return pd.DataFrame(
            data=[
                [_sandbox_transport_decode(item) for item in row]
                for row in value.get("data", [])
            ],
            columns=[str(item) for item in value.get("columns", [])],
            index=[
                _sandbox_transport_decode(item)
                for item in value.get("index", [])
            ],
        )
    if kind == "series":
        return pd.Series(
            [_sandbox_transport_decode(item) for item in value.get("data", [])],
            index=[
                _sandbox_transport_decode(item)
                for item in value.get("index", [])
            ],
            name=value.get("name"),
        )
    if kind == "ndarray":
        return np.asarray(_sandbox_transport_decode(value.get("data", [])))
    return {
        str(key): _sandbox_transport_decode(item)
        for key, item in value.items()
    }


def safe_exec_isolated(
    code: str,
    input_data: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    max_memory_mb: int = 1024,
) -> Dict[str, Any]:
    """Execute serializable user code in a clean-environment subprocess.

    The worker is started with ``python -I``, receives no parent environment
    variables, and communicates using JSON only.  This prevents both process
    secret inheritance and attacker-controlled pickle deserialization.

    Args:
        code: Python code to execute
        input_data: dict of JSON-compatible values, DataFrames, Series, or
            ndarrays to inject
        timeout: max seconds
        max_memory_mb: memory limit (Linux only, via RLIMIT_AS)

    Returns:
        dict with ``success``, ``error`` and a result containing ``output`` and
        the possibly mutated ``df``.
    """
    import json
    from pathlib import Path
    import subprocess
    import tempfile

    is_safe, err = validate_code_safety(code)
    if not is_safe:
        return {'success': False, 'error': f"Unsafe code rejected: {err}", 'result': None}

    try:
        payload = json.dumps({
            "code": code,
            "input": _sandbox_transport_encode(input_data or {}),
            "timeout": int(timeout),
            "max_memory_mb": int(max_memory_mb),
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return {
            'success': False,
            'error': f"Sandbox input serialization failed: {exc}",
            'result': None,
        }

    worker = Path(__file__).with_name("safe_exec_worker.py")
    clean_env = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONNOUSERSITE": "1",
        "QD_SANDBOX_WORKER": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="quantdinger-sandbox-") as temp_dir:
            proc = subprocess.Popen(
                [sys.executable, "-I", str(worker)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=temp_dir,
                env=clean_env,
                close_fds=True,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(payload, timeout=max(1, timeout + 2))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {
            'success': False,
            'error': f"Code execution timed out after {timeout} seconds; subprocess terminated",
            'result': None,
        }
    except (OSError, ValueError) as exc:
        return {
            'success': False,
            'error': f"Failed to start sandbox worker: {exc}",
            'result': None,
        }

    if len(stdout) > 32 * 1024 * 1024:
        return {
            'success': False,
            'error': "Sandbox result exceeded the 32MB output limit",
            'result': None,
        }
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = _redact_sensitive_text(stderr.decode("utf-8", errors="replace"))
        return {
            'success': False,
            'error': f"Sandbox worker returned an invalid response: {exc}; {detail[:500]}",
            'result': None,
        }
    if isinstance(response.get("result"), dict):
        response["result"] = _sandbox_transport_decode(response["result"])
    response["error"] = (
        _redact_sensitive_text(response.get("error"))
        if response.get("error")
        else None
    )
    return response


def safe_exec_indicator_isolated(
    code: str,
    df: Any,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout: int = 20,
    max_memory_mb: int = 1024,
) -> Dict[str, Any]:
    """Run an indicator in the JSON-only, clean-environment worker."""
    return safe_exec_isolated(
        code=code,
        input_data={
            "df": df.copy(),
            "params": dict(params or {}),
            "output": None,
        },
        timeout=timeout,
        max_memory_mb=max_memory_mb,
    )


# Static validation

def _fold_string_constant(node: Any) -> Optional[str]:
    """Resolve compile-time string concatenation for sandbox static checks."""
    import ast

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_string_constant(node.left)
        right = _fold_string_constant(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _string_has_forbidden_dunder(text: str) -> bool:
    """Reject string literals that name introspection / escape dunders."""
    if not text or '__' not in text:
        return False
    lowered = text.lower()
    for suffix in _FORBIDDEN_DUNDER_SUFFIXES:
        if suffix in lowered:
            return True
    return False


def _is_operator_accessor_call(node: Any) -> bool:
    import ast

    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id == 'operator' and func.attr in _OPERATOR_ACCESSOR_NAMES
    if isinstance(func, ast.Name):
        return func.id in _OPERATOR_ACCESSOR_NAMES
    return False


def _attribute_access_chain(node: Any) -> Tuple[Optional[str], list]:
    """Return (root_name, [attr, ...]) for Name / Attribute chains."""
    import ast

    attrs: list = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.insert(0, cur.attr)
        cur = cur.value
    root = cur.id if isinstance(cur, ast.Name) else None
    return root, attrs


def _dangerous_pd_numpy_internal(root: Optional[str], attrs: list) -> Optional[str]:
    """Detect pd.io / pd._libs / np.lib style internal access."""
    if root not in _PANDAS_NUMPY_ROOTS or not attrs:
        return None
    for idx, attr in enumerate(attrs):
        if not isinstance(attr, str):
            continue
        if attr in _DANGEROUS_PD_NUMPY_ATTRS or attr.startswith('_'):
            return f"{root}.{'.'.join(attrs[:idx + 1])}"
    return None


def validate_code_safety(code: str) -> Tuple[bool, Optional[str]]:
    """Validate code safety with regex and AST checks."""
    import ast
    import re

    dangerous_patterns = [
        r'\bos\.system\b', r'\bos\.popen\b', r'\bos\.spawn\b',
        r'\bos\.exec\b', r'\bos\.fork\b', r'\bos\.environ\b',
        r'\bos\.getenv\b', r'\bos\.putenv\b',
        r'\bos\.remove\b', r'\bos\.unlink\b', r'\bos\.rmdir\b',
        r'\bos\.makedirs\b', r'\bos\.mkdir\b',
        r'\bos\.listdir\b', r'\bos\.walk\b', r'\bos\.scandir\b',
        r'\bos\.path\b',
        r'\bsubprocess\b', r'\bcommands\b',
        r'\b__import__\s*\(', r'\beval\s*\(', r'\bexec\s*\(',
        r'\bcompile\s*\(', r'\bopen\s*\(', r'\bfile\s*\(',
        r'\b__builtins__\b',
        r'\bimport\s+os\b', r'\bimport\s+sys\b',
        r'\bimport\s+subprocess\b', r'\bimport\s+shutil\b',
        r'\bimport\s+pymysql\b', r'\bimport\s+sqlite3\b',
        r'\bimport\s+psycopg\b', r'\bimport\s+sqlalchemy\b',
        r'\bimport\s+requests\b', r'\bimport\s+urllib\b',
        r'\bimport\s+http\b', r'\bimport\s+socket\b',
        r'\bimport\s+ftplib\b', r'\bimport\s+telnetlib\b',
        r'\bimport\s+smtplib\b', r'\bimport\s+ssl\b',
        r'\bimport\s+pickle\b', r'\bimport\s+cpickle\b',
        r'\bimport\s+marshal\b', r'\bimport\s+shelve\b',
        r'\bimport\s+ctypes\b', r'\bimport\s+cffi\b',
        r'\bimport\s+multiprocessing\b', r'\bimport\s+threading\b',
        r'\bimport\s+concurrent\b', r'\bimport\s+asyncio\b',
        r'\bimport\s+signal\b', r'\bimport\s+resource\b',
        r'\bimport\s+importlib\b', r'\bimport\s+imp\b',
        r'\bimport\s+builtins\b', r'\bimport\s+code\b',
        r'\bimport\s+codeop\b', r'\bimport\s+runpy\b',
        r'\bimport\s+tempfile\b', r'\bimport\s+glob\b',
        r'\bimport\s+pathlib\b', r'\bimport\s+io\b',
        r'\bimport\s+operator\b',
        r'\boperator\.(attrgetter|itemgetter|methodcaller)\b',
        r'\bgetattr\s*\(', r'\bsetattr\s*\(', r'\bdelattr\s*\(',
        r'\b__getattribute__\b', r'\b__setattr__\b', r'\b__delattr__\b',
        r'\b__dict__\b', r'\b__class__\b', r'\b__bases__\b',
        r'\b__subclasses__\b', r'\b__mro__\b', r'\b__module__\b',
        r'\b__globals__\b', r'\b__code__\b', r'\b__func__\b',
        r'\bglobals\s*\(', r'\bvars\s*\(', r'\bdir\s*\(',
        r'\bbreakpoint\s*\(',
        r'\b__builtins__\s*[\[.]', r'\b__import__\s*\(',
        r'\bimportlib\b',
        # pandas / numpy IO and eval: arbitrary file r/w, SSRF, pickle RCE,
        # or string-expression evaluation. numpy and pandas are intentionally
        # whitelisted modules, so each dangerous method must be banned by name.
        r'\.(read_csv|read_table|read_fwf|read_excel|read_xml|read_html|'
        r'read_json|read_pickle|read_parquet|read_orc|read_feather|read_hdf|'
        r'read_sql|read_sql_query|read_sql_table|read_clipboard|read_gbq|'
        r'read_sas|read_spss|read_stata)\s*\(',
        r'\.(to_csv|to_excel|to_xml|to_html|to_json|to_pickle|to_parquet|'
        r'to_orc|to_feather|to_hdf|to_sql|to_clipboard|to_gbq|to_stata|'
        r'to_latex|tofile)\s*\(',
        r'\b(np|numpy)\.(save|savez|savez_compressed|savetxt|load|loadtxt|'
        r'genfromtxt|fromfile|memmap|DataSource)\s*\(',
        r'\.(eval|query)\s*\(',
        # Frame / traceback / closure chains used to break out of the sandbox.
        r'\.(gi_frame|gi_code|cr_frame|cr_code|ag_frame|ag_code|'
        r'f_globals|f_locals|f_back|f_builtins|f_code|f_trace|'
        r'tb_frame|tb_next|func_globals|func_code|func_closure)\b',
        # numpy sub-packages that expose C/native escape hatches.
        r'\b(np|numpy)\.(ctypeslib|distutils|f2py)\b',
        # pandas internal IO: bypasses blocked read_csv / read_pickle entry points.
        r'\b(pd|pandas)\.(io|compat|_libs|_testing)\b',
        r'\b(np|numpy)\.lib\b',
        r'\.(urlopen|_urlopen|get_filepath_or_buffer|_get_filepath_or_buffer)\s*\(',
        # sys.settrace / inspect.* could also pivot; block by name.
        r'\b(sys\._getframe|inspect\.(currentframe|stack|getouterframes|getframeinfo))\b',
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, code):
            return False, f"Unsafe code pattern detected: {pattern}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"Code syntax validation failed: {e}")
        return False, "Code syntax error"
    except Exception as e:
        # AST parse failure: reject fail-closed, not fail-open.
        logger.exception("AST parse failed, rejecting code")
        return False, "Code parse failed"

    # NOTE: these names are checked on attribute calls and attribute aliases
    # like `mod.func(...)` or `fn = mod.func`.
    # Names that doubly serve as common user variables (signal/code/io/pickle/
    # ssl/http) are intentionally excluded here; the `import xxx` regex above
    # already blocks them from ever being a real module reference, so any
    # `signal.xxx(...)` call must be a user variable (e.g. MACD `signal`).
    dangerous_modules = {
        'os', 'sys', 'subprocess', 'shutil', 'resource', 'operator',
        'pymysql', 'sqlite3', 'psycopg2', 'sqlalchemy',
        'requests', 'urllib', 'socket', 'ftplib', 'telnetlib', 'smtplib',
        'marshal', 'shelve',
        'ctypes', 'cffi',
        'multiprocessing', 'threading', 'concurrent', 'asyncio',
        'importlib', 'imp', 'builtins', 'codeop', 'runpy',
        'tempfile', 'glob', 'pathlib',
    }

    dangerous_call_names = {
        'eval', 'exec', 'compile', '__import__',
        'getattr', 'setattr', 'delattr',
        'globals', 'vars', 'dir', 'breakpoint',
        'open', 'input', 'exit', 'quit',
        # Runtime string construction plus format-field traversal was the
        # primitive used by the August 2026 sandbox escape.  Keep both names
        # rejected even if a caller accidentally injects broader builtins.
        'chr', 'format',
    }

    dangerous_dunder_attrs = {
        '__builtins__', '__import__', '__class__', '__bases__',
        '__subclasses__', '__mro__', '__globals__', '__code__',
        '__func__', '__dict__', '__module__', '__loader__', '__spec__',
        '__path__', '__file__', '__cached__', '__package__',
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _string_has_forbidden_dunder(node.value):
                return False, "Unsafe dunder string literal detected"

        if isinstance(node, ast.Import):
            for alias in node.names:
                ok, err = _is_safe_import_name(alias.name)
                if not ok:
                    return False, f"Import not allowed: '{alias.name}'. Allowed modules: {', '.join(sorted(SAFE_IMPORT_MODULES))}"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                ok, err = _is_safe_import_name(node.module)
                if not ok:
                    return False, f"Import not allowed: '{node.module}'. Allowed modules: {', '.join(sorted(SAFE_IMPORT_MODULES))}"

                for alias in node.names:
                    if alias.name == '*':
                        return False, "Wildcard imports are not allowed"
                    if alias.name.startswith('_'):
                        return False, f"Private import attributes are not allowed: {alias.name}"
                    if alias.name.lstrip('_') in dangerous_modules:
                        return False, f"Unsafe imported module detected: {alias.name}"
                    if alias.name in dangerous_dunder_attrs or (
                        alias.name.startswith('__') and alias.name.endswith('__')
                    ):
                        return False, f"Unsafe import attribute detected: {alias.name}"
                    if alias.name in _DANGEROUS_METHOD_NAMES:
                        return False, f"Unsafe imported capability detected: {alias.name}"
                    ok, err = _is_safe_import_name(f"{node.module}.{alias.name}")
                    if not ok:
                        return False, err or f"Import not allowed: {node.module}.{alias.name}"

        elif isinstance(node, ast.Call):
            if _is_operator_accessor_call(node):
                return False, "operator.attrgetter/itemgetter/methodcaller are not allowed"
            if isinstance(node.func, ast.Name) and node.func.id in dangerous_call_names:
                return False, f"Unsafe function call detected: {node.func.id}()"
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in dangerous_modules:
                    return False, f"Unsafe module call detected: {node.func.value.id}.{node.func.attr}"
                root, attrs = _attribute_access_chain(node.func)
                internal = _dangerous_pd_numpy_internal(root, attrs)
                if internal:
                    return False, f"Unsafe pandas/numpy internal access detected: {internal}"
                # Block dangerous methods on any receiver. pandas/numpy are
                # whitelisted modules, so we cannot tell statically whether
                # `x.to_csv(...)` targets a DataFrame or some local object.
                # Treat the *method name* itself as poisoned everywhere.
                if isinstance(node.func.attr, str) and node.func.attr in _DANGEROUS_METHOD_NAMES:
                    return False, f"Unsafe method call detected: .{node.func.attr}()"
            for arg in node.args:
                folded = _fold_string_constant(arg)
                if folded is not None and _string_has_forbidden_dunder(folded):
                    return False, "Unsafe dunder string argument detected"

        elif isinstance(node, ast.Attribute):
            if isinstance(node.attr, str) and (
                node.attr in dangerous_dunder_attrs
                or (
                    node.attr.startswith('__')
                    and node.attr.endswith('__')
                    and node.attr not in _SAFE_MODULE_DUNDER_ATTRS
                )
            ):
                return False, f"Unsafe attribute access detected: .{node.attr}"
            # Reject dangerous capability attributes even when user code first
            # stores the bound method and calls it indirectly later
            # (``loader.load_module`` -> ``fn = ...; fn('os')``).
            if isinstance(node.attr, str) and node.attr in _DANGEROUS_METHOD_NAMES:
                return False, f"Unsafe method access detected: .{node.attr}"
            if isinstance(node.attr, str) and node.attr in _DANGEROUS_FRAME_ATTRS:
                return False, f"Unsafe frame/closure attribute access detected: .{node.attr}"
            if isinstance(node.attr, str) and node.attr in _DANGEROUS_SUBMODULE_ATTRS:
                if isinstance(node.value, ast.Name) and node.value.id in {'np', 'numpy'}:
                    return False, f"Unsafe submodule access detected: {node.value.id}.{node.attr}"
            root, attrs = _attribute_access_chain(node)
            internal = _dangerous_pd_numpy_internal(root, attrs)
            if internal:
                return False, f"Unsafe pandas/numpy internal access detected: {internal}"
            folded = _fold_string_constant(node)
            if folded is not None and _string_has_forbidden_dunder(folded):
                return False, "Unsafe dunder attribute access detected"

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            folded = _fold_string_constant(node)
            if folded is not None and _string_has_forbidden_dunder(folded):
                return False, "Unsafe dunder string concatenation detected"

    return True, None
