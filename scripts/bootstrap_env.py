#!/usr/bin/env python3
"""Create or upgrade QuantDinger's environment file without exposing secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ASSIGNMENT_RE = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=.*$")
_INSECURE_SECRET_KEYS = {"", "quantdinger-secret-key-change-me", "legacy-secret"}


class BootstrapError(RuntimeError):
    """Raised when generating a new credential could disconnect existing data."""


def _decode_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
        if raw.strip().startswith('"'):
            value = value.replace(r'\"', '"').replace(r"\\", "\\")
        else:
            value = value.replace(r"\'", "'").replace(r"\\", "\\")
    return value


def _read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        _, raw = line.split("=", 1)
        values[match.group("key")] = _decode_dotenv_value(raw)
    return values


def _set_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    replacement = f"{key}={value}"
    output: list[str] = []
    replaced = False
    for line in lines:
        match = _ASSIGNMENT_RE.match(line)
        if match and match.group("key") == key:
            if not replaced:
                output.append(replacement)
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(replacement)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _sync_missing_keys(env_file: Path, template: Path) -> None:
    existing = set(_read_values(env_file))
    additions: list[str] = []
    for line in template.read_text(encoding="utf-8-sig").splitlines():
        match = _ASSIGNMENT_RE.match(line)
        if match and match.group("key") not in existing:
            additions.append(line)
            existing.add(match.group("key"))
    if additions:
        with env_file.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n# Added automatically during QuantDinger environment bootstrap\n")
            stream.write("\n".join(additions) + "\n")


def _password_from_database_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.password is None:
        return ""
    return unquote(parsed.password)


def _run(command: list[str], *, cwd: Path) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _container_password(compose_file: Path) -> str:
    raw = _run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", "quantdinger-db"],
        cwd=compose_file.parent,
    )
    if not raw:
        return ""
    try:
        environment = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    for item in environment:
        if isinstance(item, str) and item.startswith("POSTGRES_PASSWORD="):
            return item.split("=", 1)[1]
    return ""


def _compose_project_name(root: Path) -> str:
    configured = os.getenv("COMPOSE_PROJECT_NAME", "").strip()
    if configured:
        return configured
    normalized = re.sub(r"[^a-z0-9_-]", "", root.name.lower()).lstrip("_-")
    return normalized or "quantdinger"


def _has_existing_database_volume(compose_file: Path) -> bool:
    project = _compose_project_name(compose_file.parent)
    output = _run(
        [
            "docker",
            "volume",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.volume=postgres_data",
        ],
        cwd=compose_file.parent,
    )
    return bool(output)


def bootstrap_environment(
    env_file: Path,
    template: Path,
    compose_file: Path,
    *,
    probe_docker: bool = True,
    allow_new_database: bool = False,
) -> dict[str, str]:
    if not template.is_file():
        raise BootstrapError(f"Environment template not found: {template}")

    created = not env_file.exists()
    if created:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, env_file)
    else:
        _sync_missing_keys(env_file, template)

    values = _read_values(env_file)
    postgres_password = values.get("POSTGRES_PASSWORD", "").strip()
    recovery_source = "configured"
    if not postgres_password:
        postgres_password = _password_from_database_url(values.get("DATABASE_URL", ""))
        recovery_source = "database-url"
    if not postgres_password and probe_docker and compose_file.is_file():
        postgres_password = _container_password(compose_file)
        recovery_source = "container"
    if not postgres_password:
        has_volume = probe_docker and compose_file.is_file() and _has_existing_database_volume(compose_file)
        if has_volume or (not created and not allow_new_database):
            raise BootstrapError(
                "The existing environment has no recoverable PostgreSQL password. Refusing to generate "
                "a replacement because it could disconnect an existing database. Restore POSTGRES_PASSWORD "
                "in .env before continuing. For a verified new deployment only, rerun with "
                "--allow-new-database."
            )
        postgres_password = secrets.token_hex(18)
        recovery_source = "generated"
    _set_value(env_file, "POSTGRES_PASSWORD", postgres_password)

    values = _read_values(env_file)
    secret_key = values.get("SECRET_KEY", "").strip()
    if secret_key in _INSECURE_SECRET_KEYS or len(secret_key.encode("utf-8")) < 10:
        _set_value(env_file, "SECRET_KEY", secrets.token_hex(32))
    if not values.get("CREDENTIAL_ENCRYPTION_KEY", "").strip():
        _set_value(env_file, "CREDENTIAL_ENCRYPTION_KEY", secrets.token_hex(32))

    if os.name != "nt":
        env_file.chmod(0o600)
    return {"postgres_password_source": recovery_source, "created": str(created).lower()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or safely upgrade QuantDinger's .env with random local credentials."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--template", type=Path, default=Path(".env.example"))
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--allow-new-database",
        action="store_true",
        help="Allow a password to be generated for an existing .env only when no database exists.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = bootstrap_environment(
            args.env_file.resolve(),
            args.template.resolve(),
            args.compose_file.resolve(),
            allow_new_database=args.allow_new_database,
        )
    except (BootstrapError, OSError) as exc:
        print(f"Environment bootstrap failed: {exc}", file=sys.stderr)
        return 2
    action = "Created" if result["created"] == "true" else "Updated"
    print(f"{action} {args.env_file} securely; no secret values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
