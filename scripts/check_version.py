#!/usr/bin/env python3
"""Verify checked-in fallback version declarations.

Run locally with:
    python scripts/check_version.py

Release Docker images get their runtime version from the Git tag. The repo-root
VERSION file remains as a local/dev fallback, and this script makes sure backend
fallback declarations stay aligned with it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEMVER = r"\d+\.\d+\.\d+"

# Each entry: (path, regex with one capture group for the version).
# The captured group is compared against the VERSION file.
CHECKS: list[tuple[str, str]] = [
    ("backend_api_python/VERSION", rf"^({SEMVER})$"),
    # README shields.io badges are dynamic (GitHub release endpoint) and not checked here.
]


def _is_git_tracked(rel_path: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_path],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return True
    return completed.returncode == 0


def main() -> int:
    canonical_path = REPO_ROOT / "VERSION"
    if not canonical_path.is_file():
        print("error: VERSION file missing at repo root", file=sys.stderr)
        return 2
    canonical = canonical_path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(SEMVER, canonical):
        print(f"error: VERSION file content '{canonical}' is not semver", file=sys.stderr)
        return 2

    drift: list[str] = []
    skipped: list[str] = []
    checked = 0
    for rel_path, pattern in CHECKS:
        path = REPO_ROOT / rel_path
        if not path.is_file() or not _is_git_tracked(rel_path):
            # Only verify files that exist and are tracked in this checkout.
            skipped.append(f"  SKIP    : {rel_path} (not tracked here)")
            continue
        text = path.read_text(encoding="utf-8")
        # MULTILINE so ``^`` works for env-style files.
        matches = re.findall(pattern, text, flags=re.MULTILINE)
        if not matches:
            drift.append(f"  NO MATCH: {rel_path}  /{pattern}/")
            continue
        for found in matches:
            checked += 1
            if found != canonical:
                drift.append(f"  DRIFT   : {rel_path}  got '{found}', want '{canonical}'")

    if drift:
        print(f"Version check FAILED. Canonical = {canonical}", file=sys.stderr)
        for line in drift:
            print(line, file=sys.stderr)
        print("\nRun: python scripts/bump_version.py " + canonical, file=sys.stderr)
        return 1

    msg = f"Version check OK. Canonical = {canonical} ({checked} declarations verified)."
    if skipped:
        msg += f" Skipped {len(skipped)} missing path(s)."
    print(msg)
    for line in skipped:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
