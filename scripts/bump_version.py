#!/usr/bin/env python3
"""Bump backend fallback version files.

Usage:
    python scripts/bump_version.py 3.0.14

Release builds should normally get the displayed version from a Git tag via
Docker/CI build args. The repo-root ``VERSION`` file is only a local/dev
fallback; this script updates that fallback and the backend fallback version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"
SEMVER = r"\d+\.\d+\.\d+"

# Each entry: (path relative to repo root, regex pattern, replacement template).
# ``{v}`` in the replacement is substituted with the new version string.
# Patterns must capture the surrounding context so we do not accidentally
# rewrite unrelated semver-like strings (for example Python version "3.12").
PATCHES: list[tuple[str, str, str]] = [
    (
        "backend_api_python/VERSION",
        rf"^{SEMVER}$",
        "{v}",
    ),
]


def _validate(version: str) -> None:
    if not re.fullmatch(SEMVER, version):
        sys.exit(f"error: '{version}' is not semver X.Y.Z")


def _patch(rel_path: str, pattern: str, repl_template: str, version: str) -> int:
    """Rewrite ``rel_path`` in place. Returns the number of substitutions."""
    path = REPO_ROOT / rel_path
    if not path.is_file():
        print(f"  skip (missing): {rel_path}")
        return 0

    original = path.read_text(encoding="utf-8")
    replacement = repl_template.format(v=version)
    updated, n = re.subn(pattern, replacement, original, flags=re.MULTILINE)
    if n == 0:
        print(f"  warn (no match): {rel_path} :: /{pattern}/")
        return 0
    if updated != original:
        path.write_text(updated, encoding="utf-8")
    print(f"  patched {n}x: {rel_path}")
    return n


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit("usage: python scripts/bump_version.py X.Y.Z")
    version = argv[1].strip().lstrip("v")
    _validate(version)

    VERSION_FILE.write_text(f"{version}\n", encoding="utf-8")
    print(f"VERSION -> {version}")

    total = 0
    for rel_path, pattern, repl in PATCHES:
        total += _patch(rel_path, pattern, repl, version)

    print(f"\nDone. {total} substitution(s) across {len(PATCHES)} optional target(s).")
    print("Release flow: commit, then `git tag v{0} && git push --tags`.".format(version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
