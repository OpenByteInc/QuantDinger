from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backend_quality_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backend_quality_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_quality_check_main_passes_current_baseline():
    mod = _load_module()

    assert mod.main([]) == 0


def test_quality_check_reports_metrics_json(capsys):
    mod = _load_module()

    assert mod.main(["--json"]) == 0
    out = capsys.readouterr().out
    assert "max_file_lines" in out
    assert "max_function_lines" in out
