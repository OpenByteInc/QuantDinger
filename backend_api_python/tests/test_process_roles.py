"""Process role and startup-boundary tests."""

from __future__ import annotations

import os

import pytest

from app.runtime.roles import ProcessRole, current_process_role, strategy_commands_enabled


def test_process_role_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("QD_PROCESS_ROLE", raising=False)
    monkeypatch.delenv("STRATEGY_COMMANDS_ENABLED", raising=False)
    assert current_process_role() is ProcessRole.LEGACY
    assert strategy_commands_enabled() is False


def test_api_enables_durable_strategy_commands_by_default(monkeypatch):
    monkeypatch.setenv("QD_PROCESS_ROLE", "api")
    monkeypatch.delenv("STRATEGY_COMMANDS_ENABLED", raising=False)
    assert current_process_role() is ProcessRole.API
    assert strategy_commands_enabled() is True


def test_invalid_process_role_fails_fast(monkeypatch):
    monkeypatch.setenv("QD_PROCESS_ROLE", "unknown")
    with pytest.raises(RuntimeError, match="Invalid QD_PROCESS_ROLE"):
        current_process_role()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("kafka-audit", ProcessRole.KAFKA_AUDIT),
        ("strategy-dispatcher", ProcessRole.STRATEGY_DISPATCHER),
        ("strategy-evaluator", ProcessRole.STRATEGY_EVALUATOR),
    ],
)
def test_distributed_worker_roles_are_registered(monkeypatch, value, expected):
    monkeypatch.setenv("QD_PROCESS_ROLE", value)
    assert current_process_role() is expected


@pytest.mark.parametrize(
    "role",
    ["kafka-audit", "strategy-dispatcher", "strategy-evaluator"],
)
def test_distributed_worker_startup_is_entrypoint_owned(monkeypatch, role):
    from flask import Flask
    from app import startup

    monkeypatch.setenv("QD_PROCESS_ROLE", role)
    monkeypatch.delenv("SKIP_STARTUP_HOOKS", raising=False)
    monkeypatch.setattr(startup, "_start_trading_support_services", lambda: pytest.fail("trading started"))
    monkeypatch.setattr(startup, "_start_scheduler_services", lambda **_: pytest.fail("scheduler started"))
    monkeypatch.setattr(startup, "restore_running_strategies", lambda: pytest.fail("strategies restored"))

    startup.run_startup_hooks(Flask(__name__))


def test_api_startup_does_not_launch_process_local_services(monkeypatch):
    from flask import Flask
    from app import startup

    monkeypatch.setenv("QD_PROCESS_ROLE", "api")
    monkeypatch.delenv("SKIP_STARTUP_HOOKS", raising=False)
    monkeypatch.setattr(startup, "_start_trading_support_services", lambda: pytest.fail("trading started"))
    monkeypatch.setattr(startup, "_start_scheduler_services", lambda **_: pytest.fail("scheduler started"))
    startup.run_startup_hooks(Flask(__name__))


def test_config_loader_does_not_override_supervisor_environment(monkeypatch):
    import dotenv
    from app.utils import config_loader

    calls = []
    monkeypatch.setattr(
        config_loader.Path,
        "exists",
        lambda path: path.name == ".env",
    )
    monkeypatch.setattr(
        dotenv,
        "load_dotenv",
        lambda path, override=False: calls.append((path, override)) or True,
    )
    monkeypatch.setattr(config_loader, "_env_loaded", False)

    config_loader._load_env_files_once()

    assert calls
    assert all(override is False for _path, override in calls)


def test_runtime_settings_reload_preserves_process_topology(monkeypatch):
    from app.services.settings import runtime

    monkeypatch.setenv("QD_PROCESS_ROLE", "api")
    monkeypatch.setenv("STRATEGY_COMMANDS_ENABLED", "true")

    runtime.reload_runtime_env({"QD_PROCESS_ROLE": "legacy", "STRATEGY_COMMANDS_ENABLED": "false"})

    assert os.environ["QD_PROCESS_ROLE"] == "api"
    assert os.environ["STRATEGY_COMMANDS_ENABLED"] == "true"


def test_runtime_settings_reload_preserves_process_security_roots(monkeypatch):
    from app.services.settings import runtime

    monkeypatch.setenv("SECRET_KEY", "live-jwt-secret-shared-by-all-workers")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "live-credential-key-shared-by-all-workers",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")

    runtime.reload_runtime_env({
        "SECRET_KEY": "different-file-jwt-secret",
        "CREDENTIAL_ENCRYPTION_KEY": "different-file-credential-key",
        "LLM_PROVIDER": "deepseek",
    })

    assert os.environ["SECRET_KEY"] == "live-jwt-secret-shared-by-all-workers"
    assert (
        os.environ["CREDENTIAL_ENCRYPTION_KEY"]
        == "live-credential-key-shared-by-all-workers"
    )
    assert os.environ["LLM_PROVIDER"] == "deepseek"
