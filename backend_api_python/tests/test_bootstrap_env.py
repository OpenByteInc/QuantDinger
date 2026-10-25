from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_env.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_env", SCRIPT)
assert SPEC and SPEC.loader
bootstrap_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap_env)


def _template(path: Path) -> Path:
    path.write_text(
        "POSTGRES_PASSWORD=\nSECRET_KEY=\nCREDENTIAL_ENCRYPTION_KEY=\nDATABASE_URL=\n",
        encoding="utf-8",
    )
    return path


def test_bootstrap_generates_missing_credentials_without_printing_them(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    result = bootstrap_env.bootstrap_environment(
        env_file,
        _template(tmp_path / ".env.example"),
        tmp_path / "docker-compose.yml",
        probe_docker=False,
    )

    values = bootstrap_env._read_values(env_file)
    assert result == {"postgres_password_source": "generated", "created": "true"}
    assert re.fullmatch(r"[0-9a-f]{36}", values["POSTGRES_PASSWORD"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["SECRET_KEY"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["CREDENTIAL_ENCRYPTION_KEY"])


def test_bootstrap_preserves_existing_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=existing-db-secret\n"
        "SECRET_KEY=existing-session-secret\n"
        "CREDENTIAL_ENCRYPTION_KEY=existing-credential-secret\n",
        encoding="utf-8",
    )

    result = bootstrap_env.bootstrap_environment(
        env_file,
        _template(tmp_path / ".env.example"),
        tmp_path / "docker-compose.yml",
        probe_docker=False,
    )

    values = bootstrap_env._read_values(env_file)
    assert result["postgres_password_source"] == "configured"
    assert values["POSTGRES_PASSWORD"] == "existing-db-secret"
    assert values["SECRET_KEY"] == "existing-session-secret"
    assert values["CREDENTIAL_ENCRYPTION_KEY"] == "existing-credential-secret"


def test_bootstrap_recovers_password_from_legacy_database_url(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=\n"
        "DATABASE_URL=postgresql://quantdinger:legacy%23password@postgres:5432/quantdinger\n"
        "SECRET_KEY=existing-session-secret\n"
        "CREDENTIAL_ENCRYPTION_KEY=existing-credential-secret\n",
        encoding="utf-8",
    )

    result = bootstrap_env.bootstrap_environment(
        env_file,
        _template(tmp_path / ".env.example"),
        tmp_path / "docker-compose.yml",
        probe_docker=False,
    )

    assert result["postgres_password_source"] == "database-url"
    assert bootstrap_env._read_values(env_file)["POSTGRES_PASSWORD"] == "legacy#password"


def test_bootstrap_refuses_unrecoverable_existing_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=\nDATABASE_URL=\n", encoding="utf-8")
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap_env, "_container_password", lambda _: "")
    monkeypatch.setattr(bootstrap_env, "_has_existing_database_volume", lambda _: True)

    with pytest.raises(bootstrap_env.BootstrapError, match="no recoverable PostgreSQL password"):
        bootstrap_env.bootstrap_environment(
            env_file,
            _template(tmp_path / ".env.example"),
            compose_file,
        )


def test_bootstrap_refuses_existing_env_when_docker_detection_misses(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=\nDATABASE_URL=\n", encoding="utf-8")

    with pytest.raises(bootstrap_env.BootstrapError, match="no recoverable PostgreSQL password"):
        bootstrap_env.bootstrap_environment(
            env_file,
            _template(tmp_path / ".env.example"),
            tmp_path / "docker-compose.yml",
            probe_docker=False,
        )


def test_bootstrap_allows_verified_new_database_with_existing_env(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=\nDATABASE_URL=\n", encoding="utf-8")

    result = bootstrap_env.bootstrap_environment(
        env_file,
        _template(tmp_path / ".env.example"),
        tmp_path / "docker-compose.yml",
        probe_docker=False,
        allow_new_database=True,
    )

    assert result == {"postgres_password_source": "generated", "created": "false"}
    assert re.fullmatch(r"[0-9a-f]{36}", bootstrap_env._read_values(env_file)["POSTGRES_PASSWORD"])
