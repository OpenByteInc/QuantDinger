from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values


INSTALL_SCRIPT = Path(__file__).resolve().parents[2] / "install.sh"
DOCKER_ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
BASH = shutil.which("bash")
SH = shutil.which("sh")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
INSTALL_PS1 = Path(__file__).resolve().parents[2] / "install.ps1"


@pytest.mark.skipif(os.name == "nt" or BASH is None, reason="Linux or macOS bash is required")
def test_install_script_has_valid_bash_syntax() -> None:
    subprocess.run([BASH, "-n", str(INSTALL_SCRIPT)], check=True)


@pytest.mark.skipif(os.name == "nt" or SH is None, reason="POSIX sh is required")
def test_docker_entrypoint_has_valid_posix_syntax() -> None:
    subprocess.run([SH, "-n", str(DOCKER_ENTRYPOINT)], check=True)


def test_docker_entrypoint_does_not_walk_runtime_volumes_by_default() -> None:
    script = DOCKER_ENTRYPOINT.read_text(encoding="utf-8")

    assert 'find "$RUNTIME_DIR" -mindepth 1 -maxdepth 1' in script
    assert 'FIX_RUNTIME_VOLUME_OWNERSHIP_RECURSIVE' in script
    assert (
        'if [ "${FIX_RUNTIME_VOLUME_OWNERSHIP_RECURSIVE:-0}" = "1" ]; then\n'
        '        echo "[INFO] Repairing runtime volume ownership recursively'
    ) in script


@pytest.mark.skipif(os.name == "nt" or BASH is None, reason="Linux or macOS bash is required")
def test_installer_round_trips_quoted_dotenv_values(tmp_path: Path) -> None:
    env_file = tmp_path / "backend.env"
    password = 'Abc #12"\\path$!=O\'Brien'
    program = r"""
script_path=$1
env_file=$2
expected=$3
set --
QUANTDINGER_INSTALL_LIB_ONLY=true . "$script_path"
env_set_quoted "$env_file" ADMIN_PASSWORD "$expected"
[ "$(env_get "$env_file" ADMIN_PASSWORD)" = "$expected" ]
printf '%s\n' 'ADMIN_PASSWORD=stale-duplicate' >> "$env_file"
env_set_quoted "$env_file" ADMIN_PASSWORD "$expected"
[ "$(grep -c '^ADMIN_PASSWORD=' "$env_file")" -eq 1 ]
"""

    subprocess.run(
        [BASH, "-c", program, "bash", str(INSTALL_SCRIPT), str(env_file), password],
        check=True,
        env={**os.environ, "QUANTDINGER_INSTALL_LIB_ONLY": "true"},
    )

    assert dotenv_values(env_file)["ADMIN_PASSWORD"] == password
    assert env_file.read_text(encoding="utf-8").count("ADMIN_PASSWORD=") == 1


@pytest.mark.skipif(os.name == "nt" or BASH is None, reason="Linux or macOS bash is required")
def test_installer_recovers_postgres_password_from_database_url() -> None:
    program = r"""
script_path=$1
set --
QUANTDINGER_INSTALL_LIB_ONLY=true . "$script_path"
[ "$(postgres_password_from_url 'postgresql://quantdinger:legacy-db-secret@postgres:5432/quantdinger')" = 'legacy-db-secret' ]
"""

    subprocess.run(
        [BASH, "-c", program, "bash", str(INSTALL_SCRIPT)],
        check=True,
        env={**os.environ, "QUANTDINGER_INSTALL_LIB_ONLY": "true"},
    )


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_powershell_installer_sync_preserves_existing_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    template = tmp_path / ".env.example"
    legacy = tmp_path / "backend.env"
    env_file.write_text("# keep\nA=online\n", encoding="utf-8")
    legacy.write_text("A=legacy\nSECRET=legacy-secret\n", encoding="utf-8")
    template.write_text("A=default\nSECRET=template-secret\nB=two\n", encoding="utf-8")
    command = (
        "$env:QUANTDINGER_INSTALL_LIB_ONLY='true'; "
        f". '{INSTALL_PS1}'; "
        f"Sync-MissingEnvKeys '{env_file}' '{template}' '{legacy}'"
    )

    subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    text = env_file.read_text(encoding="utf-8")
    assert "# keep" in text
    assert "A=online" in text
    assert "A=legacy" not in text
    assert "SECRET=legacy-secret" in text
    assert "SECRET=template-secret" not in text
    assert "B=two" in text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is required")
def test_powershell_installer_recovers_postgres_password_from_database_url() -> None:
    command = (
        "$env:QUANTDINGER_INSTALL_LIB_ONLY='true'; "
        f". '{INSTALL_PS1}'; "
        "$password = Get-PostgresPasswordFromUrl "
        "'postgresql://quantdinger:legacy%23secret@postgres:5432/quantdinger'; "
        "if ($password -ne 'legacy#secret') { exit 1 }"
    )

    subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
