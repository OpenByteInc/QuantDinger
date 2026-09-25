#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rqamsc skill initialization.

Refreshes cached online docs and regenerates lightweight indexes when cache files
are missing or older than DEFAULT_CACHE_DAYS.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_CACHE_DAYS = 7
DOCUMENT_INDEX_URL = "https://www.ricequant.com/doc/document-index.txt"
DEFAULT_DOC_URLS = {
    "api-rqamsc.md": "https://www.ricequant.com/doc/sources/rqamsc/api-rqamsc.md",
    "changelogs.md": "https://www.ricequant.com/doc/sources/rqamsc/changelogs.md",
    "manual-rqamsc.md": "https://www.ricequant.com/doc/sources/rqamsc/manual-rqamsc.md",
    "tutorial-rqamsc.md": "https://www.ricequant.com/doc/sources/rqamsc/tutorial-rqamsc.md",
    "rqamsc-faq.md": "https://www.ricequant.com/doc/sources/rqamsc/rqamsc-faq.md",
}
DOC_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https://www\.ricequant\.com/doc/sources/rqamsc/[^)#]+\.md)(?:#[^)]+)?\)")


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cache_docs_dir() -> Path:
    return skill_root() / "cache" / "api_docs"


def cached_doc_path(filename: str) -> Path:
    return cache_docs_dir() / filename


def api_doc_path() -> Path:
    return cached_doc_path("api-rqamsc.md")


def changelog_doc_path() -> Path:
    return cached_doc_path("changelogs.md")


def index_dir() -> Path:
    return skill_root() / "cache" / "api_index"


def required_index_paths() -> list[Path]:
    return [
        index_dir() / "api_index.md",
        index_dir() / "section_index.md",
        index_dir() / "changelog_index.md",
    ]


def cache_expired(path: Path, cache_days: int = DEFAULT_CACHE_DAYS) -> bool:
    if not path.exists():
        return True
    max_age_seconds = cache_days * 24 * 60 * 60
    return time.time() - path.stat().st_mtime > max_age_seconds


def fetch_text(url: str, retries: int = 3, timeout_seconds: int = 60) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(url, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8")
        except (OSError, TimeoutError, URLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"Failed to download {url}") from last_error


def doc_filename(url: str) -> str:
    return Path(urlparse(url).path).name


def discover_doc_urls() -> dict[str, str]:
    """
    Discover current rqamsc source markdown URLs from the public document index.

    Falls back to known source URLs if the index format changes.
    """

    doc_urls = dict(DEFAULT_DOC_URLS)
    try:
        index_text = fetch_text(DOCUMENT_INDEX_URL)
    except RuntimeError:
        return doc_urls

    for _title, url in DOC_LINK_PATTERN.findall(index_text):
        filename = doc_filename(url)
        if filename:
            doc_urls[filename] = url
    return doc_urls


def download_docs() -> None:
    cache_docs_dir().mkdir(parents=True, exist_ok=True)
    for filename, url in discover_doc_urls().items():
        cached_doc_path(filename).write_text(fetch_text(url), encoding="utf-8")


def generate_indexes() -> None:
    from generate_indexes import generate_all_indexes

    index_dir().mkdir(parents=True, exist_ok=True)
    generate_all_indexes(api_doc_path(), index_dir(), changelog_doc_path())


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize rqamsc skill cache")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force refresh cached docs and indexes",
    )
    parser.add_argument(
        "--show-env",
        action="store_true",
        help="Show concise environment summary after initialization",
    )
    args = parser.parse_args()

    essential_docs = [api_doc_path(), changelog_doc_path()]
    required_cache_files = essential_docs + required_index_paths()
    needs_refresh = args.force_refresh or any(cache_expired(path) for path in required_cache_files)

    if needs_refresh:
        print("[INFO] Refreshing rqamsc docs cache...")
        download_docs()
        print("[INFO] Regenerating indexes...")
        generate_indexes()

    if args.show_env:
        from inspect_env import main as inspect_env_main

        inspect_env_main()

    print("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
