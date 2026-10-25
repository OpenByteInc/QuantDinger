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


def test_banned_exchange_scan_ignores_generated_market_data(tmp_path, monkeypatch):
    mod = _load_module()
    generated = tmp_path / "app" / "data" / "market_snapshot.json"
    generated.parent.mkdir(parents=True)
    generated.write_text('"' + "ku" + "coin" + '"', encoding="utf-8")
    service = tmp_path / "app" / "services" / "exchange.py"
    service.parent.mkdir(parents=True)
    service.write_text("SUPPORTED = ()", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    assert mod.find_banned_tokens((r"k[u]coin",), ("app",)) == []

    service.write_text('NAME = "' + "ku" + "coin" + '"', encoding="utf-8")
    assert mod.find_banned_tokens((r"k[u]coin",), ("app",)) == [
        ("app/services/exchange.py", r"k[u]coin"),
    ]
