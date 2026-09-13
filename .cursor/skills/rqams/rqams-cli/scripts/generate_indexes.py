#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate lightweight indexes for cached rqams-cli markdown docs.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


COMMAND_HEADING_PATTERN = re.compile(r"^## `([^`]+)`\s*$")


def generate_command_index(docs_dir: Path, output_path: Path) -> None:
    commands_dir = docs_dir / "commands"
    rows = [
        "# command_index",
        "",
        "Source: `cache/docs/commands/*.md`",
        "",
        "| Command | Source | line |",
        "| --- | --- | --- |",
    ]

    for source_path in sorted(commands_dir.glob("*.md")):
        for line_number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), start=1):
            match = COMMAND_HEADING_PATTERN.match(line)
            if not match:
                continue
            command = match.group(1)
            rel_source = f"commands/{source_path.name}"
            rows.append(f"| <code>{command}</code> | <code>{rel_source}</code> | <code>{line_number}</code> |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def default_docs_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "cache" / "docs"


def default_output_path() -> Path:
    return Path(__file__).resolve().parent.parent / "cache" / "doc_index" / "command_index.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate rqams-cli documentation indexes")
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=default_docs_dir(),
        help="Path to cached rqams-cli docs directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Path to generated command index",
    )
    args = parser.parse_args()

    generate_command_index(args.docs_dir, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
