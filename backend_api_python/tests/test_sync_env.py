from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_env.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_env", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_env_module = _load_module()


def test_sync_preserves_existing_content_and_appends_missing_keys(tmp_path):
    env_file = tmp_path / ".env"
    template = tmp_path / ".env.example"
    env_file.write_text("# keep this\nSECRET=online-value\nCUSTOM=yes\n", encoding="utf-8")
    template.write_text("SECRET=template-value\nNEW_KEY=default\nEMPTY=\n", encoding="utf-8")

    missing, backup_path, created = sync_env_module.sync_env(env_file, template)

    text = env_file.read_text(encoding="utf-8")
    assert "# keep this\nSECRET=online-value\nCUSTOM=yes\n" in text
    assert "SECRET=template-value" not in text
    assert "NEW_KEY=default" in text
    assert "EMPTY=" in text
    assert [entry.key for entry in missing] == ["NEW_KEY", "EMPTY"]
    assert backup_path is None
    assert created is False


def test_legacy_values_take_precedence_and_unknown_keys_are_retained(tmp_path):
    env_file = tmp_path / ".env"
    legacy = tmp_path / "backend.env"
    template = tmp_path / ".env.example"
    env_file.write_text("EXISTING=original\n", encoding="utf-8")
    legacy.write_text(
        "EXISTING=legacy\nSECRET=legacy-secret\nCUSTOM_LEGACY=keep-me\n",
        encoding="utf-8",
    )
    template.write_text("SECRET=template-secret\nNEW_KEY=default\n", encoding="utf-8")

    missing, _, created = sync_env_module.sync_env(env_file, template, [legacy])

    text = env_file.read_text(encoding="utf-8")
    assert "EXISTING=original" in text
    assert "EXISTING=legacy" not in text
    assert "SECRET=legacy-secret" in text
    assert "SECRET=template-secret" not in text
    assert "CUSTOM_LEGACY=keep-me" in text
    assert [entry.key for entry in missing] == [
        "SECRET",
        "CUSTOM_LEGACY",
        "NEW_KEY",
    ]
    assert created is False


def test_sync_is_idempotent_and_creates_backup_only_for_changes(tmp_path):
    env_file = tmp_path / ".env"
    template = tmp_path / ".env.example"
    env_file.write_text("A=online\n", encoding="utf-8")
    template.write_text("A=default\nB=two\n", encoding="utf-8")

    first_missing, backup_path, created = sync_env_module.sync_env(
        env_file,
        template,
        backup=True,
    )
    first_result = env_file.read_text(encoding="utf-8")
    second_missing, second_backup, second_created = sync_env_module.sync_env(
        env_file,
        template,
        backup=True,
    )

    assert [entry.key for entry in first_missing] == ["B"]
    assert backup_path is not None
    assert created is False
    assert backup_path.read_text(encoding="utf-8") == "A=online\n"
    assert second_missing == []
    assert second_backup is None
    assert second_created is False
    assert env_file.read_text(encoding="utf-8") == first_result


def test_check_reports_key_names_without_printing_values(tmp_path):
    env_file = tmp_path / ".env"
    template = tmp_path / ".env.example"
    env_file.write_text("A=online-secret\n", encoding="utf-8")
    template.write_text("A=template-secret\nB=never-print-this\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--env-file",
            str(env_file),
            "--template",
            str(template),
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "B" in result.stdout
    assert "online-secret" not in result.stdout
    assert "template-secret" not in result.stdout
    assert "never-print-this" not in result.stdout
    assert env_file.read_text(encoding="utf-8") == "A=online-secret\n"


def test_new_target_preserves_legacy_comments_and_custom_entries(tmp_path):
    env_file = tmp_path / ".env"
    legacy = tmp_path / "backend.env"
    template = tmp_path / ".env.example"
    legacy.write_text("# legacy explanation\nSECRET=online\n", encoding="utf-8")
    template.write_text("# template explanation\nSECRET=default\nNEW_KEY=two\n", encoding="utf-8")

    missing, backup_path, created = sync_env_module.sync_env(
        env_file,
        template,
        [legacy],
        backup=True,
    )

    text = env_file.read_text(encoding="utf-8")
    assert created is True
    assert backup_path is None
    assert "# legacy explanation" in text
    assert "SECRET=online" in text
    assert "SECRET=default" not in text
    assert "NEW_KEY=two" in text
    assert [entry.key for entry in missing] == ["NEW_KEY"]


def test_new_target_without_legacy_copies_complete_template(tmp_path):
    env_file = tmp_path / ".env"
    template = tmp_path / ".env.example"
    template.write_text("# explanation\nA=one\n", encoding="utf-8")

    missing, _, created = sync_env_module.sync_env(env_file, template)

    assert created is True
    assert missing == []
    assert env_file.read_text(encoding="utf-8") == "# explanation\nA=one\n"


def test_canonical_template_contains_every_packaged_backend_key():
    canonical = REPO_ROOT / ".env.example"
    packaged = REPO_ROOT / "backend_api_python" / "env.example"

    canonical_keys = {entry.key for entry in sync_env_module._entries(canonical)}
    packaged_keys = {entry.key for entry in sync_env_module._entries(packaged)}

    assert packaged_keys <= canonical_keys


def test_strategy_sandbox_templates_share_current_defaults():
    expected = {
        "STRATEGY_RUNTIME_SANDBOX_TIMEOUT_SECONDS": "120",
        "STRATEGY_RUNTIME_SANDBOX_MEMORY_MB": "2048",
        "STRATEGY_BACKTEST_SANDBOX_TIMEOUT_SECONDS": "900",
        "STRATEGY_BACKTEST_SANDBOX_MEMORY_MB": "4096",
    }
    for template in (REPO_ROOT / ".env.example", REPO_ROOT / "backend_api_python" / "env.example"):
        values = {
            entry.key: entry.line.partition("=")[2].strip()
            for entry in sync_env_module._entries(template)
        }
        assert "STRATEGY_SCRIPT_CALLBACK_TIMEOUT_SEC" not in values, template
        assert expected.items() <= values.items(), template
