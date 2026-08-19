"""Sandbox static validation and known escape regression tests."""

import builtins
import math
import threading

import pandas as pd

from app.utils.safe_exec import (
    build_safe_builtins,
    safe_exec_code,
    safe_exec_indicator_isolated,
    safe_exec_with_validation,
    timeout_context,
    validate_code_safety,
)

# Classic subclass-chain escape (CVE-class pattern for restricted exec).
_SUBCLASS_ESCAPE = """
import operator
ag = operator.attrgetter
cls = ag('__' + 'class__')('')
bases = ag('__' + 'bases__')(cls)
subs = ag('__' + 'subclasses__')(bases[0])()
os_mod = None
for s in subs:
    try:
        init = ag('__' + 'init__')(s)
        globs = ag('__' + 'globals__')(init)
        if globs and 'os' in globs:
            os_mod = globs['os']
            break
    except Exception:
        pass
output = {'plots': [], 'signals': [], 'marker': os_mod.getcwd() if os_mod else None}
"""

_LEGIT_INDICATOR = """
my_indicator_name = "T"
df = df.copy()
output = {'plots': [], 'signals': []}
"""

_RUNTIME_FORMAT_ESCAPE = """
u = chr(95) + chr(95)
spec = (chr(123) + "0.DataFrame." + u + "init" + u + "." + u + "globals" + u
        + "[sys].modules[os].environ[SANDBOX_TEST_SECRET]" + chr(125))
output = spec.format(pd)
"""


def test_subclass_escape_rejected_by_validator():
    ok, err = validate_code_safety(_SUBCLASS_ESCAPE)
    assert ok is False
    assert err


def test_subclass_escape_rejected_at_exec():
    env = {
        '__builtins__': build_safe_builtins(),
        'output': None,
        'df': None,
        'params': {},
    }
    result = safe_exec_with_validation(_SUBCLASS_ESCAPE, env, env, timeout=5, pre_import='')
    assert result['success'] is False


def test_legit_indicator_passes_validator():
    ok, err = validate_code_safety(_LEGIT_INDICATOR)
    assert ok is True
    assert err is None


def test_runtime_built_format_field_escape_is_rejected():
    ok, err = validate_code_safety(_RUNTIME_FORMAT_ESCAPE)
    assert ok is False
    assert err

    env = {"output": None}
    result = safe_exec_with_validation(
        _RUNTIME_FORMAT_ESCAPE,
        env,
        env,
        timeout=5,
    )
    assert result["success"] is False
    assert env["output"] is None


def test_format_attribute_alias_is_rejected():
    ok, err = validate_code_safety(
        "resolver = '{0.value}'.format\noutput = resolver(item)"
    )
    assert ok is False
    assert err


def test_removed_reflection_builders_are_not_in_safe_builtins():
    safe = build_safe_builtins()
    assert "chr" not in safe
    assert "format" not in safe


def test_sandbox_error_redacts_configured_secret(monkeypatch):
    marker = "qd-sensitive-marker-8f92a7"
    monkeypatch.setenv("SANDBOX_TEST_SECRET", marker)
    env = {"output": None}
    result = safe_exec_with_validation(
        f"raise ValueError({marker!r})",
        env,
        env,
        timeout=5,
        pre_import="",
    )
    assert result["success"] is False
    assert marker not in result["error"]
    assert "[REDACTED]" in result["error"]


def test_indicator_worker_returns_json_only_output_and_dataframe():
    df = pd.DataFrame({
        "open": [1.0, 2.0],
        "high": [2.0, 3.0],
        "low": [0.5, 1.5],
        "close": [1.5, 2.5],
        "volume": [10.0, 20.0],
    }, index=pd.date_range("2026-08-16", periods=2, freq="min", tz="UTC"))
    result = safe_exec_indicator_isolated(
        """
df = df.copy()
df['average'] = (high + low) / 2
output = {'plots': [{'name': f\"avg-{len(df)}\", 'data': df['average'].tolist()}], 'signals': []}
""",
        df,
        {},
        timeout=10,
    )
    assert result["success"] is True, result["error"]
    payload = result["result"]
    assert payload["output"]["plots"][0]["data"] == [1.25, 2.25]
    assert payload["df"]["average"].tolist() == [1.25, 2.25]
    assert isinstance(payload["df"].index, pd.DatetimeIndex)
    assert str(payload["df"].index.tz) == "UTC"

def test_operator_import_rejected():
    ok, _ = validate_code_safety("import operator\noutput = {}")
    assert ok is False


_MODULE_LOADER_ESCAPE = """
import math
module_loader = math.__loader__
os_module = module_loader.load_module('os')
output = os_module.system('id')
"""

_MODULE_LOADER_ALIAS_ESCAPE = """
import math
module_loader = math.__loader__
load = module_loader.load_module
os_module = load('os')
output = os_module.system('id')
"""

_STRATEGY_MODULE_LOADER_ESCAPE = """
import math
def initialize(context):
    context['ready'] = True
def handle_data(context, data):
    module = math.__loader__.load_module('os')
    output['marker'] = module.system('id')
"""


def test_module_loader_escape_rejected_by_validator():
    ok, err = validate_code_safety(_MODULE_LOADER_ESCAPE)
    assert ok is False
    assert err

    ok, err = validate_code_safety(_MODULE_LOADER_ALIAS_ESCAPE)
    assert ok is False
    assert err


def test_strategy_shaped_module_loader_escape_rejected():
    ok, err = validate_code_safety(_STRATEGY_MODULE_LOADER_ESCAPE)
    assert ok is False
    assert err


def test_allowed_module_proxy_hides_importer_metadata():
    math_module = build_safe_builtins()['__import__']('math')
    assert math_module.sqrt(9) == 3
    for attr in ('__loader__', '__spec__', '__dict__'):
        try:
            getattr(math_module, attr)
        except AttributeError:
            pass
        else:
            raise AssertionError(f"module metadata should be hidden: {attr}")


def test_exec_boundary_replaces_ambient_builtins_and_raw_modules():
    env = {
        '__builtins__': builtins.__dict__,
        'math': math,
        'output': None,
    }
    result = safe_exec_with_validation(
        "output = math.sqrt(25)",
        env,
        env,
        pre_import='',
    )
    assert result['success'] is True
    assert env['output'] == 5
    assert 'open' not in env['__builtins__']
    for attr in ('__loader__', '__spec__', '__dict__'):
        try:
            getattr(env['math'], attr)
        except AttributeError:
            pass
        else:
            raise AssertionError(f"ambient module metadata should be hidden: {attr}")

    second = safe_exec_with_validation(
        "output = math.sqrt(36)",
        env,
        env,
        pre_import='',
    )
    assert second['success'] is True
    assert env['output'] == 6


def test_from_import_returns_wrapped_submodule():
    env = {'output': None}
    result = safe_exec_with_validation(
        "from json import decoder\noutput = decoder",
        env,
        env,
        pre_import='',
    )
    assert result['success'] is True
    for attr in ('__loader__', '__spec__', '__dict__'):
        try:
            getattr(env['output'], attr)
        except AttributeError:
            pass
        else:
            raise AssertionError(f"imported submodule metadata should be hidden: {attr}")


def test_allowed_module_cannot_expose_transitive_sys_module():
    payload = """
import collections
loaded = collections._sys.modules
module = loaded['os']
output = module.system('id')
"""
    env = {'output': None}
    result = safe_exec_with_validation(payload, env, env, pre_import='')
    assert result['success'] is False
    assert env['output'] is None


def test_pre_import_is_validated_before_execution():
    env = {'output': None}
    result = safe_exec_with_validation(
        "output = 1",
        env,
        env,
        pre_import="import os",
    )
    assert result['success'] is False
    assert result['error'].startswith('Unsafe pre-import rejected:')
    assert env['output'] is None


def test_direct_safe_exec_code_is_fail_closed():
    env = {'output': None, '__builtins__': builtins.__dict__}
    result = safe_exec_code("import os\noutput = os.getcwd()", env, env)
    assert result['success'] is False
    assert result['error'].startswith('Unsafe code rejected:')
    assert env['output'] is None


# pandas.io.common.urlopen can bypass read_csv bans for local file reads or SSRF.
_PD_IO_FILE_READ_ESCAPE = """
def run(context, data):
    import pandas as pd
    data = pd.io.common.urlopen('file:///etc/passwd').read()
    context.log(str(data[:200]))
"""

_PD_IO_ATTR_ESCAPE = """
def run(context, data):
    import pandas as pd
    x = pd.io
"""

_PD_LIBS_ESCAPE = """
def run(context, data):
    import pandas as pd
    x = pd._libs
"""

_PD_IO_IMPORT_ESCAPE = """
def run(context, data):
    import pandas.io.common as common
    data = common.urlopen('file:///etc/passwd').read()
    context.log(str(data[:200]))
"""

_NP_CTYPESLIB_IMPORT_ESCAPE = """
def run(context, data):
    import numpy.ctypeslib as ctypeslib
    context.log(str(ctypeslib))
"""

_FROM_PD_IO_IMPORT_ESCAPE = """
def run(context, data):
    from pandas.io import common
    context.log(str(common))
"""

_LEGIT_PANDAS_STRATEGY = """
def run(context, data):
    import pandas as pd
    df = pd.DataFrame({'a': [1, 2, 3]})
    context.log(str(float(df['a'].mean())))
"""


def test_pd_io_urlopen_rejected_by_validator():
    ok, err = validate_code_safety(_PD_IO_FILE_READ_ESCAPE)
    assert ok is False
    assert err


def test_pd_io_urlopen_rejected_at_exec():
    env = {
        '__builtins__': build_safe_builtins(),
        'output': None,
    }
    result = safe_exec_with_validation(_PD_IO_FILE_READ_ESCAPE, env, env, timeout=5)
    assert result['success'] is False


def test_pd_io_attr_access_rejected():
    ok, err = validate_code_safety(_PD_IO_ATTR_ESCAPE)
    assert ok is False
    assert err


def test_pd_libs_attr_access_rejected():
    ok, err = validate_code_safety(_PD_LIBS_ESCAPE)
    assert ok is False
    assert err


def test_pd_io_submodule_import_rejected():
    ok, err = validate_code_safety(_PD_IO_IMPORT_ESCAPE)
    assert ok is False
    assert err


def test_np_ctypeslib_submodule_import_rejected():
    ok, err = validate_code_safety(_NP_CTYPESLIB_IMPORT_ESCAPE)
    assert ok is False
    assert err


def test_from_pd_io_import_rejected():
    ok, err = validate_code_safety(_FROM_PD_IO_IMPORT_ESCAPE)
    assert ok is False
    assert err


def test_from_import_cannot_alias_dangerous_io_function():
    ok, err = validate_code_safety(
        "from pandas import read_csv\noutput = read_csv('/etc/passwd')"
    )
    assert ok is False
    assert err


def test_private_transitive_module_import_rejected():
    ok, err = validate_code_safety(
        "from collections import _sys\noutput = _sys.modules"
    )
    assert ok is False
    assert err

    ok, err = validate_code_safety(
        "from datetime import sys\noutput = sys.modules"
    )
    assert ok is False
    assert err


def test_legit_pandas_strategy_passes_validator():
    ok, err = validate_code_safety(_LEGIT_PANDAS_STRATEGY)
    assert ok is True
    assert err is None


def test_non_main_thread_timeouts_share_one_watchdog_thread():
    failures: list[Exception] = []

    def run_many() -> None:
        try:
            for _ in range(100):
                with timeout_context(1):
                    pass
        except Exception as exc:  # pragma: no cover - assertion reports detail
            failures.append(exc)

    worker = threading.Thread(target=run_many)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert failures == []
    watchdogs = [
        thread
        for thread in threading.enumerate()
        if thread.name == "SafeExecTimeoutWatchdog" and thread.is_alive()
    ]
    assert len(watchdogs) == 1


def test_shared_watchdog_still_interrupts_non_main_thread_execution():
    outcomes: list[str] = []

    def run_until_timeout() -> None:
        try:
            with timeout_context(0.05):
                while True:
                    pass
        except Exception as exc:
            outcomes.append(type(exc).__name__)

    worker = threading.Thread(target=run_until_timeout)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert outcomes == ["TimeoutError"]
