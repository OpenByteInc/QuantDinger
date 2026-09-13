#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Initialize cached rqams-cli docs and regenerate lightweight indexes when needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import URLError
from urllib.request import urlopen


GITHUB_RAW_BASE = "https://raw.githubusercontent.com/ricequant/rqams-cli/master/docs"
DEFAULT_CACHE_DAYS = 7
DOWNLOAD_TIMEOUT_SECONDS = 60
DOWNLOAD_RETRIES = 2
DOC_FILES = [
    "rqams_cli_manual.md",
    "commands/analysis.md",
    "commands/auth_workspace.md",
    "commands/balance.md",
    "commands/customized.md",
    "commands/customized_benchmark.md",
    "commands/customized_indicator.md",
    "commands/events.md",
    "commands/paper_trading.md",
    "commands/permissions.md",
    "commands/products.md",
    "commands/reconciliation.md",
    "commands/reports.md",
    "commands/statements_and_valuation.md",
    "commands/trades.md",
]
DOWNLOAD_WORKERS = min(15, len(DOC_FILES))


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cache_docs_dir() -> Path:
    return skill_root() / "cache" / "docs"


def command_index_path() -> Path:
    return skill_root() / "cache" / "doc_index" / "command_index.md"


def cached_doc_paths() -> list[Path]:
    return [cache_docs_dir() / rel_path for rel_path in DOC_FILES]


def required_cache_paths() -> list[Path]:
    return cached_doc_paths() + [command_index_path()]


def cache_expired(path: Path, cache_days: int = DEFAULT_CACHE_DAYS) -> bool:
    if not path.exists():
        return True
    max_age_seconds = cache_days * 24 * 60 * 60
    return time.time() - path.stat().st_mtime > max_age_seconds


def download_text(
    url: str,
    retries: int = DOWNLOAD_RETRIES,
    timeout_seconds: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            with urlopen(url, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8")
        except (OSError, TimeoutError, URLError) as exc:
            last_error = exc
            if attempt <= retries:
                time.sleep(attempt)
    raise RuntimeError(f"Failed to download {url}: {last_error}") from last_error


def download_doc(rel_path: str) -> tuple[str, str]:
    return rel_path, download_text(f"{GITHUB_RAW_BASE}/{rel_path}")


def download_docs(target_dir: Path) -> None:
    downloaded_docs: dict[str, str] = {}
    failures: dict[str, Exception] = {}

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
        futures = {executor.submit(download_doc, rel_path): rel_path for rel_path in DOC_FILES}
        for future in as_completed(futures):
            rel_path = futures[future]
            try:
                downloaded_path, text = future.result()
                downloaded_docs[downloaded_path] = text
            except Exception as exc:
                failures[rel_path] = exc

    if failures:
        failure_summary = "; ".join(f"{rel_path}: {exc}" for rel_path, exc in failures.items())
        raise RuntimeError(f"Failed to download {len(failures)} rqams-cli docs: {failure_summary}")

    for rel_path, text in downloaded_docs.items():
        target_path = target_dir / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(text, encoding="utf-8")


def generate_indexes() -> None:
    from generate_indexes import generate_command_index

    generate_command_index(cache_docs_dir(), command_index_path())


def refresh_docs() -> str:
    target_dir = cache_docs_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        print(
            "[INFO] Downloading rqams-cli docs: "
            f"{len(DOC_FILES)} files from {GITHUB_RAW_BASE} with {DOWNLOAD_WORKERS} workers"
        )
        download_docs(target_dir)
    except RuntimeError as exc:
        raise RuntimeError(f"Failed to download rqams-cli docs from GitHub: {exc}") from exc
    return f"GitHub raw: {GITHUB_RAW_BASE}"


def cached_docs_available() -> bool:
    return all(path.exists() for path in cached_doc_paths())


def missing_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if not path.exists()]


def expired_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists() and cache_expired(path)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize rqams-cli skill cache")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force refresh cached docs and indexes",
    )
    args = parser.parse_args()

    required_paths = required_cache_paths()
    missing_required_paths = missing_paths(required_paths)
    expired_required_paths = expired_paths(required_paths)
    needs_refresh = args.force_refresh or bool(missing_required_paths or expired_required_paths)

    print(
        "[INFO] rqams-cli init: "
        f"docs={len(DOC_FILES)}, workers={DOWNLOAD_WORKERS}, "
        f"timeout={DOWNLOAD_TIMEOUT_SECONDS}s, retries={DOWNLOAD_RETRIES}"
    )
    if needs_refresh:
        reason_parts = []
        if args.force_refresh:
            reason_parts.append("force refresh requested")
        if missing_required_paths:
            reason_parts.append(f"{len(missing_required_paths)} cache files missing")
        if expired_required_paths:
            reason_parts.append(f"{len(expired_required_paths)} cache files expired")
        print(f"[INFO] Refresh needed: {', '.join(reason_parts)}")

        try:
            source_description = refresh_docs()
            print(f"[INFO] Refreshed rqams-cli docs cache from {source_description}")
            generate_indexes()
            print("[INFO] Regenerated command index")
        except RuntimeError as exc:
            if not cached_docs_available():
                print(f"[FAIL] {exc}", file=sys.stderr)
                print("[FAIL] No complete cached docs are available for fallback", file=sys.stderr)
                return 1

            print(f"[WARN] {exc}")
            print("[WARN] Using existing cached rqams-cli docs")
            if not command_index_path().exists():
                generate_indexes()
                print("[INFO] Regenerated command index from existing cached docs")
    else:
        print("[INFO] Cache is fresh; using existing docs and command index")

    print("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
