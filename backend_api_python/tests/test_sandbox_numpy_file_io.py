"""Regression coverage for file readers exposed by numerical modules."""

import json
from pathlib import Path
import subprocess
import sys
import types

import numpy as np
import pandas as pd
import pytest

from app.utils import safe_exec
from app.services.strategy_v2.contract import StrategyV2ContractError, compile_strategy_v2


@pytest.mark.parametrize("source", [
    "import numpy as np\nreader = np.fromregex",
    "import numpy as numerical\nreader = numerical.fromregex",
    "from numpy import fromregex as reader",
    "from numpy import fromregex",
    "import numpy as np\nreader = [np.fromregex][0]",
    "import numpy as np\nreader = np.frompyfunc(np.fromregex, 3, 1)",
    "import numpy as np\nother = np\nreader = other.fromregex",
    "import numpy as np\nraw = np.array([1]).ctypes",
    "import numpy as np\nraw = np.array([1]).ctypes._ctypes",
    "import numpy as np\nnp.array([1]).dump('result.bin')",
    "import numpy as np\nrows = np.recfromcsv('input.csv')",
    "import numpy as np\nrows = np.recfromtxt('input.txt')",
    "import pandas as pd\npd.DataFrame({'x': [1]}).to_string('result.txt')",
    "import pandas as pd\npd.DataFrame({'x': [1]}).to_markdown('result.md')",
    "import pandas as pd\nbook = pd.ExcelFile('input.xlsx')",
    "from pandas import ExcelWriter\nwriter = ExcelWriter('output.xlsx')",
    "from pandas import HDFStore\nstore = HDFStore('output.h5')",
])
def test_fromregex_references_are_rejected(source):
    accepted, error = safe_exec.validate_code_safety(source)
    assert not accepted
    assert error


@pytest.fixture
def file_read_source(tmp_path):
    target = tmp_path / "sandbox-canary.txt"
    target.write_text("fixture-canary-731\n", encoding="utf-8")
    return (
        "import numpy as np\n"
        f"rows = np.fromregex({str(target)!r}, r'(fixture-canary-[0-9]+)', "
        "[('value', 'U64')])\n"
        "output = rows['value'].tolist()\n"
    )


@pytest.mark.parametrize("executor", [
    safe_exec.safe_exec_code,
    safe_exec.safe_exec_with_validation,
])
def test_in_process_execution_rejects_file_read(executor, file_read_source):
    namespace = {}
    result = executor(file_read_source, namespace, timeout=5)
    assert result["success"] is False
    assert "output" not in namespace


def test_isolated_execution_rejects_file_read(file_read_source):
    result = safe_exec.safe_exec_isolated(file_read_source, timeout=5)
    assert result["success"] is False
    assert result["result"] is None


def test_strategy_initialize_rejects_file_read(file_read_source):
    source = (
        "def initialize(context):\n"
        + "\n".join("    " + line for line in file_read_source.splitlines())
        + '\n    context.set_universe(["Crypto:BTC/USDT@spot"])\n'
        + "def handle_data(context, data):\n    pass\n"
    )
    with pytest.raises(StrategyV2ContractError):
        compile_strategy_v2(source)


@pytest.mark.parametrize("module, capability", [
    (np, "fromregex"), (np, "load"), (np, "loadtxt"),
    (np, "fromfile"), (np, "memmap"), (np, "save"),
    (np, "DataSource"), (pd, "read_csv"), (pd, "read_pickle"),
    (pd, "ExcelFile"), (pd, "ExcelWriter"), (pd, "HDFStore"),
])
def test_runtime_import_proxy_hides_file_io(module, capability):
    # Verify the runtime boundary independently of AST validation.
    importer = safe_exec.build_safe_builtins()["__import__"]
    proxy = importer(module.__name__)
    with pytest.raises(AttributeError):
        getattr(proxy, capability)
    proxy_exports = types.ModuleType.__getattribute__(proxy, "__dict__")
    assert capability not in proxy_exports
    original = (
        np.lib.npyio.DataSource
        if module is np and capability == "DataSource"
        else getattr(module, capability)
    )
    assert callable(original)


def test_injected_numpy_module_uses_the_same_runtime_restrictions():
    namespace = {"numerical": np}
    safe_exec._sanitize_exec_namespace(namespace)
    with pytest.raises(AttributeError):
        namespace["numerical"].fromregex
    assert callable(np.fromregex)


def test_dotted_import_cannot_reach_a_blocked_capability():
    importer = safe_exec.build_safe_builtins()["__import__"]
    with pytest.raises(ImportError, match="Import not allowed"):
        importer("numpy.fromregex")


def test_computation_and_descriptive_variable_names_remain_supported():
    namespace = {}
    result = safe_exec.safe_exec_with_validation(
        "import numpy as numerical\n"
        "from numpy import mean\n"
        "import pandas as tabular\n"
        "open_long = True\n"
        "fromregex_result = 7\n"
        "values = numerical.array([1.0, 2.0, 3.0])\n"
        "output = [float(mean(values)), "
        "float(tabular.Series(values).rolling(2).mean().iloc[-1])]\n",
        namespace,
        timeout=5,
    )
    assert result["success"], result["error"]
    assert namespace["output"] == [2.0, 2.5]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux kernel sandbox")
def test_worker_kernel_boundary_denies_host_files_and_network(tmp_path):
    target = tmp_path / "isolation-canary.txt"
    target.write_text("non-secret-test-canary\n", encoding="utf-8")
    worker_path = Path(safe_exec.__file__).with_name("safe_exec_worker.py")
    source = f"""
import importlib.util
import json
import socket
from pathlib import Path
import numpy
import pandas

worker_path = Path({str(worker_path)!r})
spec = importlib.util.spec_from_file_location("sandbox_worker_boundary_test", worker_path)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker._apply_os_isolation(worker_path.parent)

result = {{"file": "allowed", "network": "allowed"}}
try:
    open({str(target)!r}, "rb").read()
except PermissionError:
    result["file"] = "blocked"
try:
    socket.socket()
except OSError:
    result["network"] = "blocked"
print(json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "file": "blocked",
        "network": "blocked",
    }
