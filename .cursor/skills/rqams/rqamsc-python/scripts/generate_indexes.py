#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate documentation indexes for rqamsc cached markdown sources.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
API_SIGNATURE_PATTERN = re.compile(r"^\s*rqamsc\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
API_MANUAL_TITLE = "RQAMSC API 手册"


def read_source_lines(source_path: Path) -> list[str]:
    """
    Read markdown source lines once for downstream index generation.

    :param source_path: Markdown source file path.
    :return: Source lines with trailing newlines preserved.
    :raises OSError: Raised when the source file cannot be read.
    """

    return source_path.read_text(encoding="utf-8").splitlines(keepends=True)


def extract_headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """
    Extract markdown headings outside fenced code blocks.

    :param lines: Source markdown lines.
    :return: Tuples of heading line number, level, and title.
    """

    headings: list[tuple[int, int, str]] = []
    in_code_block = False
    for idx, line in enumerate(lines, start=1):
        stripped = line.rstrip("\n")
        if stripped.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        match = HEADING_PATTERN.match(stripped)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        headings.append((idx, level, title))
    return headings


def build_heading_ranges(headings: list[tuple[int, int, str]], total_lines: int) -> list[tuple[int, int, str, int]]:
    """
    Build closed line ranges for headings.

    :param headings: Extracted heading tuples.
    :param total_lines: Total line count in the source document.
    :return: Tuples of heading start line, level, title, and end line.
    """

    ranges: list[tuple[int, int, str, int]] = []
    for index, (start, level, title) in enumerate(headings):
        end = total_lines
        for next_start, next_level, _next_title in headings[index + 1 :]:
            if next_level <= level:
                end = next_start - 1
                break
        ranges.append((start, level, title, end))
    return ranges


def find_api_manual_range(heading_ranges: list[tuple[int, int, str, int]]) -> tuple[int, int] | None:
    """
    Locate the formal API manual section in the markdown document.

    :param heading_ranges: Heading ranges for the source document.
    :return: Inclusive start and end line numbers for the API manual section, or None.
    """

    for start, _level, title, end in heading_ranges:
        if title == API_MANUAL_TITLE:
            return start, end
    return None


def extract_api_matches(lines: list[str], start_line: int = 1, end_line: int | None = None) -> list[tuple[str, int]]:
    """
    Extract rqamsc API calls from fenced code blocks within a line range.

    :param lines: Source markdown lines.
    :param start_line: Inclusive line number where scanning starts.
    :param end_line: Inclusive line number where scanning ends. Defaults to the full document.
    :return: Ordered tuples of API name and source line number.
    """

    matches: list[tuple[str, int]] = []
    in_code_block = False
    inclusive_end_line = end_line or len(lines)

    for idx, line in enumerate(lines, start=1):
        if idx < start_line:
            continue
        if idx > inclusive_end_line:
            break

        stripped = line.rstrip("\n")
        if stripped.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            continue

        match = API_SIGNATURE_PATTERN.match(stripped)
        if match:
            matches.append((match.group(1), idx))

    return matches


def deduplicate_api_matches(matches: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """
    Keep the first occurrence of each API within ordered matches.

    :param matches: Ordered API matches.
    :return: Deduplicated ordered API matches.
    """

    deduplicated: list[tuple[str, int]] = []
    seen_names: set[str] = set()
    for api_name, line_number in matches:
        if api_name in seen_names:
            continue
        seen_names.add(api_name)
        deduplicated.append((api_name, line_number))
    return deduplicated


def map_section_apis(
    heading_ranges: list[tuple[int, int, str, int]],
    api_matches: list[tuple[str, int]],
) -> dict[int, list[str]]:
    """
    Map headings to the APIs that appear inside their line ranges.

    :param heading_ranges: Heading ranges for the source document.
    :param api_matches: API matches scoped to the formal API manual.
    :return: Mapping from heading start line to ordered API names.
    """

    mapping: dict[int, list[str]] = {}
    for start, _level, _title, end in heading_ranges:
        section_apis: list[str] = []
        seen_names: set[str] = set()
        for api_name, line_number in api_matches:
            if start <= line_number <= end and api_name not in seen_names:
                section_apis.append(api_name)
                seen_names.add(api_name)
        mapping[start] = section_apis
    return mapping


def generate_section_index(source_path: Path, output_path: Path) -> None:
    """
    Generate the section index for the cached markdown source.

    :param source_path: Markdown source file path.
    :param output_path: Output section index file path.
    :return: None.
    :raises OSError: Raised when the source file cannot be read or the output cannot be written.
    """

    lines = read_source_lines(source_path)
    headings = extract_headings(lines)
    heading_ranges = build_heading_ranges(headings, len(lines))
    api_manual_range = find_api_manual_range(heading_ranges)
    api_matches = []
    if api_manual_range is not None:
        api_matches = deduplicate_api_matches(extract_api_matches(lines, *api_manual_range))
    else:
        api_matches = deduplicate_api_matches(extract_api_matches(lines))
    section_api_mapping = map_section_apis(heading_ranges, api_matches)

    rows = [
        "# section_index",
        "",
        f"Source: `{source_path.name}`",
        "",
        "| Level | Title | line_range | apis |",
        "| --- | --- | --- | --- |",
    ]
    for start, level, title, end in heading_ranges:
        apis = ", ".join(f"`{api_name}`" for api_name in section_api_mapping.get(start, [])) or "-"
        rows.append(f"| `{level}` | `{title}` | `{start}-{end}` | {apis} |")

    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def generate_changelog_index(source_path: Path, output_path: Path) -> None:
    """
    Generate a compact changelog index from the cached markdown source.

    :param source_path: Markdown source file path.
    :param output_path: Output changelog index file path.
    :return: None.
    :raises OSError: Raised when the source file cannot be read or the output cannot be written.
    """

    lines = source_path.read_text(encoding="utf-8").splitlines()
    rows = [
        "# changelog_index",
        "",
        f"Source: `{source_path.name}`",
        "",
        "| Version | Date | Summary |",
        "| --- | --- | --- |",
    ]
    capture = False
    for line in lines:
        if line.strip() == "## 更新履历":
            capture = True
            continue
        if not capture and line.strip().startswith("| 0."):
            capture = True
        if not capture:
            continue
        if capture and line.startswith("# RQAMSC API 手册"):
            break
        if line.startswith("| 0."):
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(parts) >= 3:
                version = parts[0]
                date = parts[1]
                change_labels = ["新增", "改善", "不兼容改动"]
                changes = []
                for label, value in zip(change_labels, parts[2:5]):
                    if value:
                        changes.append(f"**{label}**: {value}")
                summary = "<br> ".join(changes) if changes else parts[2]
                rows.append(f"| `{version}` | `{date}` | {summary} |")

    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def generate_api_index(source_path: Path, output_path: Path) -> None:
    """
    Generate a formal API index scoped to the API manual section only.

    :param source_path: Markdown source file path.
    :param output_path: Output API index file path.
    :return: None.
    :raises OSError: Raised when the source file cannot be read or the output cannot be written.
    """

    lines = read_source_lines(source_path)
    headings = extract_headings(lines)
    heading_ranges = build_heading_ranges(headings, len(lines))
    api_manual_range = find_api_manual_range(heading_ranges)
    rows = [
        "# api_index",
        "",
        f"Source: `{source_path.name}`",
        "",
        "| API | line_range |",
        "| --- | --- |",
    ]

    if api_manual_range is None:
        matches = deduplicate_api_matches(extract_api_matches(lines))
    else:
        matches = deduplicate_api_matches(extract_api_matches(lines, *api_manual_range))

    for i, (api_name, start) in enumerate(matches):
        end = len(lines)
        if i + 1 < len(matches):
            end = matches[i + 1][1] - 1
        rows.append(f"| `{api_name}` | `{start}-{end}` |")

    output_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def generate_all_indexes(source_path: Path, output_dir: Path, changelog_source_path: Path | None = None) -> None:
    """
    Generate all cached documentation indexes.

    :param source_path: Markdown source file path.
    :param output_dir: Output directory for generated indexes.
    :param changelog_source_path: Optional changelog markdown source path.
    :return: None.
    :raises OSError: Raised when output directories cannot be created or files cannot be written.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    generate_section_index(source_path, output_dir / "section_index.md")
    generate_changelog_index(changelog_source_path or source_path, output_dir / "changelog_index.md")
    generate_api_index(source_path, output_dir / "api_index.md")


def default_changelog_source_path() -> Path:
    """
    Resolve the default cached changelog markdown source path.

    :return: Default cached changelog source path.
    """

    return Path(__file__).resolve().parent.parent / "cache" / "api_docs" / "changelogs.md"


def default_source_path() -> Path:
    """
    Resolve the default cached markdown source path.

    :return: Default cached markdown source path.
    """

    return Path(__file__).resolve().parent.parent / "cache" / "api_docs" / "api-rqamsc.md"


def default_output_dir() -> Path:
    """
    Resolve the default output directory for generated indexes.

    :return: Default cache index directory.
    """

    return Path(__file__).resolve().parent.parent / "cache" / "api_index"


def main() -> int:
    """
    Parse command line arguments and generate all indexes.

    :return: Process exit code.
    :raises OSError: Raised when source or output files cannot be accessed.
    """

    parser = argparse.ArgumentParser(description="Generate rqamsc markdown indexes")
    parser.add_argument(
        "--source",
        type=Path,
        default=default_source_path(),
        help="Path to the cached markdown source file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output_dir(),
        help="Directory for generated index files",
    )
    parser.add_argument(
        "--changelog-source",
        type=Path,
        default=default_changelog_source_path(),
        help="Path to the cached changelog markdown source file",
    )
    args = parser.parse_args()

    generate_all_indexes(args.source, args.output_dir, args.changelog_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
