#!/usr/bin/env python3
"""Bump the project version across all tracked locations.

Usage:
    python scripts/bump_version.py 3.0.14

The repo-root ``VERSION`` file is the canonical source. This script rewrites
every other place that hardcodes the version, so the human only edits one
file (or runs this script). Run ``scripts/check_version.py`` afterwards to
verify everything is in sync.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"

# Each entry: (path relative to repo root, regex pattern, replacement template).
# ``{v}`` in the replacement is substituted with the new version string.
# Patterns must capture the surrounding context so we don't accidentally
# rewrite unrelated semver-like strings (e.g. Python version "3.10").
SEMVER = r"\d+\.\d+\.\d+"
PATCHES: list[tuple[str, str, str]] = [
    # Backend baked-in version constant.
    (
        "backend_api_python/app/_version.py",
        rf'APP_VERSION\s*=\s*"{SEMVER}"',
        'APP_VERSION = "{v}"',
    ),
    # Frontend package metadata + first-paint fallbacks.
    (
        "QuantDinger-Vue-src/package.json",
        rf'"version"\s*:\s*"{SEMVER}"',
        '"version": "{v}"',
    ),
    (
        "QuantDinger-Vue-src/src/config/defaultSettings.js",
        rf"appVersion:\s*'{SEMVER}'",
        "appVersion: '{v}'",
    ),
    (
        "QuantDinger-Vue-src/src/store/modules/brand.js",
        rf"app_version:\s*'{SEMVER}'",
        "app_version: '{v}'",
    ),
    (
        "QuantDinger-Vue-src/src/layouts/BasicLayout.vue",
        rf"defaultSettings\.appVersion \|\| '{SEMVER}'",
        "defaultSettings.appVersion || '{v}'",
    ),
    # README shields.io badges are dynamic (pulled from GitHub releases via
    # `/github/v/release/<owner>/<repo>`) and need no manual bump here.
    # `quantdinger-frontend:X.Y.Z` mentions in README are intentionally left
    # alone — FE and BE can ship on independent cadences, and the compose
    # default is `latest`, not a pinned tag.
]


def _validate(version: str) -> None:
    if not re.fullmatch(SEMVER, version):
        sys.exit(f"error: '{version}' is not semver X.Y.Z")


def _patch(rel_path: str, pattern: str, repl_template: str, version: str) -> int:
    """Rewrite ``rel_path`` in place. Returns the number of substitutions made."""
    path = REPO_ROOT / rel_path
    if not path.is_file():
        print(f"  skip (missing): {rel_path}")
        return 0
    original = path.read_text(encoding="utf-8")
    replacement = repl_template.format(v=version)
    # Use re.MULTILINE so ``^`` anchors match per-line (needed for env.example).
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
    print(f"VERSION → {version}")

    total = 0
    for rel_path, pattern, repl in PATCHES:
        total += _patch(rel_path, pattern, repl, version)

    print(f"\nDone. {total} substitution(s) across {len(PATCHES)} target(s).")
    print("Next: git diff, commit, then `git tag v{0} && git push --tags`.".format(version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
