#!/usr/bin/env python3
"""Fail when tracked text files contain common mojibake markers.

These markers usually mean UTF-8 text was decoded as GBK/CP936 or Windows-1252
and then saved back to the repository. The check is intentionally conservative:
it scans tracked files only and skips binary/generated file extensions.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lock",
    ".pdf",
    ".png",
    ".pyc",
    ".svg",
    ".webp",
    ".zip",
}

MOJIBAKE_MARKERS = {
    "\u040e\u0404": "UTF-8 punctuation decoded through the wrong legacy code page",
    "\u0432\u2013": "UTF-8 symbol decoded through the wrong legacy code page",
    "\u9225": "UTF-8 punctuation decoded as GBK/CP936, e.g. em dash or curly quote",
    "\u922b": "UTF-8 arrow decoded as GBK/CP936",
    "\u9239": "UTF-8 box-drawing character decoded as GBK/CP936",
    "\u923b": "UTF-8 symbol decoded as GBK/CP936",
    "\u951f": "UTF-8 decoded through the wrong Chinese code page",
    "\u95b0": "Chinese text decoded through the wrong UTF-8/GBK path",
    "\u93c8": "Chinese text decoded through the wrong UTF-8/GBK path",
    "\u675e": "Chinese text decoded through the wrong UTF-8/GBK path",
    "\u947e": "Chinese text decoded through the wrong UTF-8/GBK path",
    "\u5a13": "Chinese text decoded through the wrong UTF-8/GBK path",
    "\ufffd": "Unicode replacement character",
    "\u00ef\u00bf\u00bd": "UTF-8 bytes for replacement character decoded as Latin-1",
    "\u00c3": "Likely UTF-8 decoded as Windows-1252/Latin-1",
    "\u00c2": "Likely UTF-8 decoded as Windows-1252/Latin-1",
}


def has_private_use_char(text: str) -> bool:
    """Private-use code points often appear after failed UTF-8/GBK recovery."""
    return any("\ue000" <= char <= "\uf8ff" for char in text)


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    paths = completed.stdout.decode("utf-8", errors="replace").split("\0")
    return [REPO_ROOT / p for p in paths if p]


def should_scan(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    parts = set(path.relative_to(REPO_ROOT).parts)
    return not ({"node_modules", "dist", "__pycache__"} & parts)


def main() -> int:
    hits: list[str] = []
    for path in tracked_files():
        if not should_scan(path) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            if has_private_use_char(line):
                preview = line.strip()
                if len(preview) > 160:
                    preview = preview[:157] + "..."
                hits.append(f"{rel}:{line_no}: private-use mojibake marker: {preview}")
                continue
            for marker, reason in MOJIBAKE_MARKERS.items():
                if marker in line:
                    preview = line.strip()
                    if len(preview) > 160:
                        preview = preview[:157] + "..."
                    hits.append(f"{rel}:{line_no}: {reason}: {preview}")
                    break

    if hits:
        print("Mojibake check FAILED. Suspect encoding corruption found:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        print(
            "\nLikely cause: UTF-8 text was read with a legacy encoding such as GBK/CP936 "
            "or Windows-1252 and then saved back as UTF-8.",
            file=sys.stderr,
        )
        return 1

    print("Mojibake check OK. No tracked text files contain known corruption markers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
