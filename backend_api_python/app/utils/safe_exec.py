"""
安全的代码执行工具
提供超时、资源限制、沙箱环境和子进程隔离
"""
import signal
import sys
import os
import threading
import traceback
import builtins as _builtins_mod
from typing import Dict, Any, Optional, Tuple, Set
from contextlib import contextmanager

from app.utils.logger import get_logger

logger = get_logger(__name__)


class TimeoutError(Exception):
    """代码执行超时异常"""
    pass


# ── Whitelisted builtins (strict) ──────────────────────────────────────────
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
    # String / repr
    'repr', 'ascii', 'chr', 'ord', 'format', 'bin', 'hex', 'oct',
    'hash', 'id',
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
    # Functional
    'staticmethod', 'classmethod', 'property', 'super',
    'object',
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
}

_OPERATOR_ACCESSOR_NAMES: Set[str] = {'attrgetter', 'itemgetter', 'methodcaller'}

# Method names that read/write files, evaluate strings, or pivot to other
# processes — must be rejected on ANY receiver (df.to_csv, np.array().tofile,
# pd.read_csv, etc.) because numpy and pandas are intentionally whitelisted.
_DANGEROUS_METHOD_NAMES: Set[str] = {
    # pandas read_* — arbitrary file read or SSRF via URL / pickle deser RCE.
    'read_csv', 'read_table', 'read_fwf', 'read_excel', 'read_xml',
    'read_html', 'read_json', 'read_pickle', 'read_parquet', 'read_orc',
    'read_feather', 'read_hdf', 'read_sql', 'read_sql_query',
    'read_sql_table', 'read_clipboard', 'read_gbq', 'read_sas',
    'read_spss', 'read_stata',
    # pandas to_* / ndarray.tofile — arbitrary file write / pickle write.
    'to_csv', 'to_excel', 'to_xml', 'to_html', 'to_json', 'to_pickle',
    'to_parquet', 'to_orc', 'to_feather', 'to_hdf', 'to_sql',
    'to_clipboard', 'to_gbq', 'to_stata', 'to_latex',
    'tofile',
    # numpy IO — arbitrary read / write / pickle deser.
    'save', 'savez', 'savez_compressed', 'savetxt',
    'load', 'loadtxt', 'genfromtxt', 'fromfile', 'memmap',
    # String-expression evaluators that execute attacker-controlled code.
    'eval', 'query',
    # Frame / introspection accessors should never be invoked.
    'getframe', 'currentframe', 'stack', 'getouterframes',
    # pandas.io.common — file/URL IO bypassing blocked read_* entry points.
    'urlopen', '_urlopen', 'get_filepath_or_buffer', '_get_filepath_or_buffer',
    'file_exists', 'file_open', 'open_url',
}

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


def _make_safe_import():
    """Create a restricted __import__ that only allows whitelisted modules."""
    def safe_import(name, *args, **kwargs):
        root = name.split('.')[0]
        if root in SAFE_IMPORT_MODULES:
            return _builtins_mod.__import__(name, *args, **kwargs)
        raise ImportError(f"Import not allowed: {name}")
    return safe_import


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


# ── Timeout (cross-platform) ──────────────────────────────────────────────

@contextmanager
def timeout_context(seconds: int):
    """
    代码执行超时上下文管理器

    - Unix 主线程: signal.SIGALRM
    - Windows / 非主线程: threading.Timer + ctypes 异常注入
    """
    is_main_thread = threading.current_thread() is threading.main_thread()

    # Strategy 1: Unix SIGALRM (most reliable, main thread only)
    if sys.platform != 'win32' and is_main_thread:
        def timeout_handler(signum, frame):
            raise TimeoutError(f"代码执行超时（超过{seconds}秒）")
        try:
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(seconds)
            try:
                yield
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            return
        except ValueError:
            pass  # fall through to timer strategy

    # Strategy 2: threading.Timer + ctypes async exception (cross-platform)
    target_tid = threading.current_thread().ident
    timed_out = threading.Event()

    def _inject_timeout():
        timed_out.set()
        try:
            import ctypes
            exc = ctypes.py_object(TimeoutError)
            ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(target_tid), exc
            )
            if ret == 0:
                logger.warning("timeout inject: invalid thread id")
            elif ret > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(target_tid), ctypes.py_object(0)
                )
        except Exception as e:
            logger.warning(f"timeout inject failed: {e}")

    timer = threading.Timer(seconds, _inject_timeout)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        if timed_out.is_set():
            raise TimeoutError(f"代码执行超时（超过{seconds}秒）")


# ── Core execution ─────────────────────────────────────────────────────────

def safe_exec_code(
    code: str,
    exec_globals: Dict[str, Any],
    exec_locals: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
    max_memory_mb: Optional[int] = None
) -> Dict[str, Any]:
    """
    安全执行Python代码（当前进程内，带超时）

    Args:
        code: 要执行的Python代码
        exec_globals: 全局变量字典
        exec_locals: 局部变量字典（如果为None，则使用exec_globals）
        timeout: 超时时间（秒），默认30秒
        max_memory_mb: 最大内存限制（MB），默认500MB
    """
    if exec_locals is None:
        exec_locals = exec_globals

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
        error_msg = f"代码执行内存不足（超过{max_memory_mb}MB限制）"
        logger.error(f"Code execution out of memory (limit={max_memory_mb}MB)")
        return {'success': False, 'error': error_msg, 'result': None}
    except TimeoutError as e:
        logger.error(f"Code execution timed out (timeout={timeout}s)")
        return {'success': False, 'error': str(e), 'result': None}
    except Exception as e:
        error_msg = f"代码执行错误: {str(e)}\n{traceback.format_exc()}"
        logger.error(f"Code execution error: {e}")
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
    2. Injects build_safe_builtins() if __builtins__ is not already set.
    3. Executes pre_import, then user code via safe_exec_code().

    Returns same dict as safe_exec_code().
    """
    is_safe, err = validate_code_safety(code)
    if not is_safe:
        return {'success': False, 'error': f"Unsafe code rejected: {err}", 'result': None}

    if '__builtins__' not in exec_globals:
        exec_globals['__builtins__'] = build_safe_builtins()

    if pre_import:
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


# ── Subprocess isolation (medium-term) ─────────────────────────────────────

def safe_exec_isolated(
    code: str,
    input_data: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    max_memory_mb: int = 500,
) -> Dict[str, Any]:
    """
    Execute user code in an isolated subprocess.

    Data is serialized via pickle through pipes. The subprocess has its own
    memory space; a crash or infinite loop only kills the child.

    Args:
        code: Python code to execute
        input_data: dict of variable names → values to inject (must be picklable)
        timeout: max seconds
        max_memory_mb: memory limit (Linux only, via RLIMIT_AS)

    Returns:
        dict with 'success', 'error', 'result' (the child's exec_env after run)
    """
    import multiprocessing
    import pickle

    def _worker(code, input_data, max_memory_mb, result_pipe):
        try:
            if sys.platform != 'win32':
                try:
                    import resource
                    mem = max_memory_mb * 1024 * 1024
                    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
                except Exception:
                    pass

            import numpy as np
            import pandas as pd

            exec_env = {
                '__builtins__': build_safe_builtins(),
                'np': np,
                'pd': pd,
            }
            if input_data:
                exec_env.update(input_data)

            pre_import = "import numpy as np\nimport pandas as pd\n"
            exec(pre_import, exec_env)
            exec(code, exec_env)

            # Extract only picklable, non-module results
            output = {}
            for k, v in exec_env.items():
                if k.startswith('_') or k in ('np', 'pd', '__builtins__'):
                    continue
                try:
                    pickle.dumps(v)
                    output[k] = v
                except Exception:
                    pass

            result_pipe.send({'success': True, 'error': None, 'result': output})
        except Exception as e:
            result_pipe.send({
                'success': False,
                'error': f"{type(e).__name__}: {e}",
                'result': None,
            })
        finally:
            result_pipe.close()

    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)

    proc = multiprocessing.Process(
        target=_worker,
        args=(code, input_data, max_memory_mb, child_conn),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)
        return {
            'success': False,
            'error': f"代码执行超时（超过{timeout}秒），子进程已终止",
            'result': None,
        }

    if proc.exitcode != 0 and not parent_conn.poll():
        return {
            'success': False,
            'error': f"子进程异常退出 (exit code: {proc.exitcode})",
            'result': None,
        }

    try:
        if parent_conn.poll(timeout=1):
            return parent_conn.recv()
        return {'success': False, 'error': "子进程未返回结果", 'result': None}
    except Exception as e:
        return {'success': False, 'error': f"读取子进程结果失败: {e}", 'result': None}
    finally:
        parent_conn.close()


# ── Static validation ──────────────────────────────────────────────────────

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
    """
    验证代码安全性（正则 + AST 双重检查）
    """
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
        # pandas / numpy IO and eval — arbitrary file r/w, SSRF, pickle RCE,
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
        # pandas internal IO — bypasses blocked read_csv / read_pickle entry points.
        r'\b(pd|pandas)\.(io|compat|_libs|_testing)\b',
        r'\b(np|numpy)\.lib\b',
        r'\.(urlopen|_urlopen|get_filepath_or_buffer|_get_filepath_or_buffer)\s*\(',
        # sys.settrace / inspect.* could also pivot — block by name.
        r'\b(sys\._getframe|inspect\.(currentframe|stack|getouterframes|getframeinfo))\b',
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, code):
            return False, f"检测到危险代码模式: {pattern}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"Code syntax validation failed: {e}")
        return False, "代码语法错误"
    except Exception as e:
        # AST parse failure → reject (fail-closed, not fail-open)
        logger.exception("AST parse failed, rejecting code")
        return False, "代码解析失败"

    # NOTE: these names are checked on attribute calls like `mod.func(...)`.
    # Names that doubly serve as common user variables (signal/code/io/pickle/
    # ssl/http) are intentionally excluded here — the `import xxx` regex above
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
    }

    dangerous_dunder_attrs = {
        '__builtins__', '__import__', '__class__', '__bases__',
        '__subclasses__', '__mro__', '__globals__', '__code__',
        '__func__', '__dict__', '__module__',
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _string_has_forbidden_dunder(node.value):
                return False, "检测到危险 dunder 字符串字面量"

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in SAFE_IMPORT_MODULES:
                    return False, f"不允许导入模块 '{alias.name}'，仅允许: {', '.join(sorted(SAFE_IMPORT_MODULES))}"

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split('.')[0]
                if root not in SAFE_IMPORT_MODULES:
                    return False, f"不允许导入模块 '{node.module}'，仅允许: {', '.join(sorted(SAFE_IMPORT_MODULES))}"

        elif isinstance(node, ast.Call):
            if _is_operator_accessor_call(node):
                return False, "不允许使用 operator.attrgetter/itemgetter/methodcaller"
            if isinstance(node.func, ast.Name) and node.func.id in dangerous_call_names:
                return False, f"检测到危险函数调用: {node.func.id}()"
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in dangerous_modules:
                    return False, f"检测到危险模块调用: {node.func.value.id}.{node.func.attr}"
                root, attrs = _attribute_access_chain(node.func)
                internal = _dangerous_pd_numpy_internal(root, attrs)
                if internal:
                    return False, f"检测到访问 pandas/numpy 内部模块: {internal}"
                # Block dangerous methods on any receiver. pandas/numpy are
                # whitelisted modules, so we cannot tell statically whether
                # `x.to_csv(...)` targets a DataFrame or some local object.
                # Treat the *method name* itself as poisoned everywhere.
                if isinstance(node.func.attr, str) and node.func.attr in _DANGEROUS_METHOD_NAMES:
                    return False, f"检测到危险方法调用: .{node.func.attr}()"
            for arg in node.args:
                folded = _fold_string_constant(arg)
                if folded is not None and _string_has_forbidden_dunder(folded):
                    return False, "检测到危险 dunder 字符串参数"

        elif isinstance(node, ast.Attribute):
            if isinstance(node.attr, str) and node.attr in dangerous_dunder_attrs:
                return False, f"检测到访问危险属性: .{node.attr}"
            if isinstance(node.attr, str) and node.attr in _DANGEROUS_FRAME_ATTRS:
                return False, f"检测到访问 frame/closure 属性: .{node.attr}"
            if isinstance(node.attr, str) and node.attr in _DANGEROUS_SUBMODULE_ATTRS:
                if isinstance(node.value, ast.Name) and node.value.id in {'np', 'numpy'}:
                    return False, f"检测到访问危险子模块: {node.value.id}.{node.attr}"
            root, attrs = _attribute_access_chain(node)
            internal = _dangerous_pd_numpy_internal(root, attrs)
            if internal:
                return False, f"检测到访问 pandas/numpy 内部模块: {internal}"
            folded = _fold_string_constant(node)
            if folded is not None and _string_has_forbidden_dunder(folded):
                return False, "检测到危险 dunder 属性访问"

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            folded = _fold_string_constant(node)
            if folded is not None and _string_has_forbidden_dunder(folded):
                return False, "检测到危险 dunder 字符串拼接"

    return True, None
