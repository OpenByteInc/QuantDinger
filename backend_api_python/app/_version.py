"""Application version resolution.

Release builds inject ``APP_VERSION`` from the Git tag. Local source runs can
fall back to Git metadata or the repo-root ``VERSION`` file, so the app version
no longer needs to be edited in Python code for every release.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

FALLBACK_VERSION = "0.0.0-dev"
TAG_REF_PREFIX = "refs/tags/"
BRANCH_REF_PREFIX = "refs/heads/"


def normalize_version(value: object) -> str:
    """Normalize common tag/env formats into the display version."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(TAG_REF_PREFIX):
        text = text[len(TAG_REF_PREFIX) :]
    elif text.startswith(BRANCH_REF_PREFIX):
        text = text[len(BRANCH_REF_PREFIX) :]
    if text.startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    return text


def _find_repo_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    candidates = (current, *current.parents)
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    for candidate in candidates:
        if (candidate / "VERSION").is_file():
            return candidate
    return None


def _git_describe(repo_root: Path) -> str:
    if not (repo_root / ".git").exists():
        return ""

    commands = (
        ("git", "describe", "--tags", "--exact-match", "HEAD"),
        ("git", "describe", "--tags", "--abbrev=7", "--dirty"),
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception:
            continue
        version = normalize_version(completed.stdout)
        if version:
            return version
    return ""


def _version_file(repo_root: Path) -> str:
    version_file = repo_root / "VERSION"
    if not version_file.is_file():
        return ""
    try:
        return normalize_version(version_file.read_text(encoding="utf-8").strip())
    except OSError:
        return ""


def _build_stamp(app_root: Path, names: tuple[str, ...]) -> str:
    for name in names:
        path = app_root / name
        if not path.is_file():
            continue
        try:
            version = normalize_version(path.read_text(encoding="utf-8").strip())
        except OSError:
            continue
        if version:
            return version
    return ""


def resolve_app_version(
    env: Mapping[str, object] | None = None,
    *,
    repo_root: Path | None = None,
    app_root: Path | None = None,
    use_git: bool = True,
) -> str:
    """Resolve the current app version.

    Priority:
    1. Explicit runtime/build environment (APP_VERSION / QUANTDINGER_VERSION)
    2. Docker build stamp file (BUILD_VERSION)
    3. CI tag environment (GIT_TAG / GITHUB_REF_NAME / GITHUB_REF)
    4. Docker tag stamp file (BUILD_GIT_TAG)
    5. Local Git tag/describe metadata
    6. Repo-root VERSION fallback file
    7. Development placeholder
    """
    source_env = os.environ if env is None else env

    for key in ("APP_VERSION", "QUANTDINGER_VERSION"):
        version = normalize_version(source_env.get(key))
        if version:
            return version

    build_root = app_root or Path(__file__).resolve().parents[1]
    version = _build_stamp(build_root, ("BUILD_VERSION",))
    if version:
        return version

    for key in ("GIT_TAG", "GITHUB_REF_NAME", "GITHUB_REF"):
        version = normalize_version(source_env.get(key))
        if version and version not in {"latest", "main", "master"}:
            return version

    version = _build_stamp(build_root, ("BUILD_GIT_TAG",))
    if version and version not in {"latest", "main", "master"}:
        return version

    root = repo_root or _find_repo_root(Path(__file__).resolve())
    if root is not None:
        if use_git:
            version = _git_describe(root)
            if version:
                return version
        version = _version_file(root)
        if version:
            return version

    return FALLBACK_VERSION


APP_VERSION = resolve_app_version()
