#!/usr/bin/env python3
"""Verify that every version declaration matches the repo-root VERSION file.

Run locally with:
    python scripts/check_version.py

Exits non-zero (and prints offenders) if any tracked location drifts.
Designed to run in CI as a one-line guardrail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEMVER = r"\d+\.\d+\.\d+"

# Each entry: (path, regex with one capture group for the version).
# The captured group is compared against the VERSION file.
CHECKS: list[tuple[str, str]] = [
    ("backend_api_python/app/_version.py", rf'APP_VERSION\s*=\s*"({SEMVER})"'),
    ("QuantDinger-Vue-src/package.json", rf'"version"\s*:\s*"({SEMVER})"'),
    ("QuantDinger-Vue-src/src/config/defaultSettings.js", rf"appVersion:\s*'({SEMVER})'"),
    ("QuantDinger-Vue-src/src/store/modules/brand.js", rf"app_version:\s*'({SEMVER})'"),
    (
        "QuantDinger-Vue-src/src/layouts/BasicLayout.vue",
        rf"defaultSettings\.appVersion \|\| '({SEMVER})'",
    ),
    # README shields.io badges are dynamic (GitHub release endpoint) and not checked here.
    # README `quantdinger-frontend:X.Y.Z` mentions are not checked — FE and BE
    # are versioned independently and the compose default is `latest`.
]


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
        if not path.is_file():
            # Frontend source lives in a private repo and is gitignored here;
            # only verify paths that are actually tracked in this checkout.
            skipped.append(f"  SKIP    : {rel_path} (not in repo)")
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
