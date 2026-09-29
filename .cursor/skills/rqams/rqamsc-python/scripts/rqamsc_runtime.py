#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared rqamsc runtime initialization helpers for skill scripts.
"""

from __future__ import annotations

import os
import sys
import json
import platform
from dataclasses import dataclass
from pathlib import Path

CLI_CONFIG_ENV = "RQAMS_CLI_CONFIG"
RQAMSC_PROFILE_ENV = "RQAMSC_PROFILE"


@dataclass(frozen=True)
class RuntimeConfig:
    """
    Runtime configuration loaded from a shared profile.

    :param python_executable: Active Python interpreter path.
    :param username: RQAMSC login username.
    :param password: RQAMSC login password.
    :param uri: AMS endpoint URI.
    :param ssl_verify: Whether SSL certificate verification is enabled.
    :param workspace: Optional workspace name to switch to after initialization.
    :param config_source: Source used for auth fields.
    :param profile: Optional CLI config profile used for auth fields.
    """

    python_executable: Path
    username: str | None
    password: str | None
    uri: str | None
    ssl_verify: bool
    workspace: str | None
    config_source: str = "environment"
    profile: str | None = None


@dataclass(frozen=True)
class RuntimeInitResult:
    """
    Result returned by rqamsc runtime initialization.

    :param python_executable: Active Python interpreter path.
    :param username: RQAMSC login username.
    :param uri: AMS endpoint URI.
    :param workspace_name: Active workspace name after initialization.
    :param config_source: Source used for auth fields.
    :param profile: Shared rqams-cli profile used for auth fields.
    """

    python_executable: Path
    username: str
    uri: str
    workspace_name: str
    config_source: str
    profile: str | None


class RuntimeConfigError(RuntimeError):
    """
    Raised when required RQAMSC runtime fields are missing.
    """

    def __init__(self, missing_keys: list[str]) -> None:
        """
        Initialize the configuration error with missing keys.

        :param missing_keys: Required runtime field names that are absent.
        """

        self.missing_keys = missing_keys
        message = f"Missing required rqamsc config fields: {', '.join(missing_keys)}"
        super().__init__(message)


def parse_ssl_verify(uri: str, raw_value: str | None) -> bool:
    """
    Parse SSL verification behavior from environment variables.

    :param uri: AMS endpoint URI.
    :param raw_value: Raw RQAMSC_SSL_VERIFY value from the environment.
    :return: Parsed SSL verification flag.
    :raises ValueError: Raised when the raw value cannot be interpreted.
    """

    if raw_value is not None and raw_value != "":
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
        raise ValueError(f"Unsupported RQAMSC_SSL_VERIFY value: {raw_value}")
    return uri.startswith("https://")


def _string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _user_config_dir() -> Path:
    override = os.getenv("XDG_CONFIG_HOME")
    if override:
        return Path(override)
    system = platform.system()
    if system == "Windows":
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata)
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".config"


def get_cli_config_path() -> Path:
    """
    Return the rqams-cli config path, matching the CLI path rules.

    :return: Path to rqams-cli config.json.
    """

    override = os.getenv(CLI_CONFIG_ENV)
    if override and override.strip():
        return Path(override)
    return _user_config_dir() / "rqams-cli" / "config.json"


def _legacy_cli_config_path() -> Path:
    return _user_config_dir() / "rqamsc-demo" / "config.json"


def load_cli_config() -> dict[str, object]:
    """
    Load rqams-cli local configuration.

    Missing config files are treated as empty configuration so environment-only
    setups continue to work.
    """

    path = get_cli_config_path()
    if not path.exists():
        legacy_path = _legacy_cli_config_path()
        if not legacy_path.exists():
            return {}
        path = legacy_path
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"rqams-cli config must be a JSON object: {path}")
    return loaded


def select_cli_profile(config: dict[str, object], profile: str | None) -> dict[str, object]:
    """
    Promote a selected rqams-cli profile to the active config shape.
    """

    selected_profile = profile or _string_or_none(config.get("profile"))
    if not selected_profile:
        return config
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        return config
    selected = profiles.get(selected_profile)
    if not isinstance(selected, dict):
        return config
    merged = dict(config)
    merged.update(selected)
    merged["profile"] = selected_profile
    return merged


def build_runtime_config() -> RuntimeConfig:
    """
    Load rqamsc runtime configuration.

    The runtime is profile-based: `rqamsc setup` writes credentials to the
    rqams-cli config, and `RQAMSC_PROFILE` selects the same profile for Python
    SDK initialization. Auth fields are not read from standalone environment
    variables; the shared profile is the single source of truth.

    :return: Parsed runtime configuration.
    :raises ValueError: Raised when SSL verification configuration is invalid.
    """

    profile = _string_or_none(os.getenv(RQAMSC_PROFILE_ENV))
    cli_config = select_cli_profile(load_cli_config(), profile)
    profile = _string_or_none(cli_config.get("profile")) or profile

    username = _string_or_none(cli_config.get("username"))
    password = _string_or_none(cli_config.get("password"))
    uri = _string_or_none(cli_config.get("base_url"))
    workspace = _string_or_none(cli_config.get("workspace_id"))
    ssl_verify_raw = os.getenv("RQAMSC_SSL_VERIFY")
    ssl_verify = parse_ssl_verify(uri or "", ssl_verify_raw)

    return RuntimeConfig(
        python_executable=Path(sys.executable),
        username=username,
        password=password,
        uri=uri,
        ssl_verify=ssl_verify,
        workspace=workspace,
        config_source="rqams-cli config",
        profile=profile,
    )


def get_missing_required_keys(config: RuntimeConfig) -> list[str]:
    """
    Compute required environment keys that are missing from configuration.

    :param config: Runtime configuration to validate.
    :return: Missing required key names.
    """

    missing_keys: list[str] = []
    if not config.username:
        missing_keys.append("username")
    if not config.password:
        missing_keys.append("password")
    if not config.uri:
        missing_keys.append("base_url")
    return missing_keys


def initialize_rqamsc(config: RuntimeConfig | None = None) -> RuntimeInitResult:
    """
    Initialize rqamsc and switch to the configured workspace when provided.

    :param config: Optional prebuilt runtime configuration.
    :return: Structured initialization result.
    :raises RuntimeConfigError: Raised when required environment variables are missing.
    :raises Exception: Propagates rqamsc import and initialization failures.
    """

    runtime_config = config or build_runtime_config()
    missing_keys = get_missing_required_keys(runtime_config)
    if missing_keys:
        raise RuntimeConfigError(missing_keys)

    import rqamsc

    rqamsc.init(
        username=runtime_config.username,
        password=runtime_config.password,
        uri=runtime_config.uri,
        ssl_verify=runtime_config.ssl_verify,
    )

    workspace_name = "<default>"
    if runtime_config.workspace:
        rqamsc.choose_workspace(runtime_config.workspace)
    current_workspace = rqamsc.current_workspace()
    if current_workspace is not None:
        workspace_name = getattr(current_workspace, "name", None) or str(current_workspace)

    return RuntimeInitResult(
        python_executable=runtime_config.python_executable,
        username=runtime_config.username or "",
        uri=runtime_config.uri or "",
        workspace_name=workspace_name,
        config_source=runtime_config.config_source,
        profile=runtime_config.profile,
    )
