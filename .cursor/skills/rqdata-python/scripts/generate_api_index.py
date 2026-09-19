#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQData API Index Generator

Scans all markdown documentation files in the cache/api_docs directory and generates
individual API index files for each source file.
"""

import re
import sys
from pathlib import Path
from typing import List, Dict, Optional


class APIIndexGenerator:
    """Generates API index files from markdown documentation"""

    def __init__(
        self, api_docs_dir: Optional[Path] = None, output_dir: Optional[Path] = None
    ):
        """
        Initialize the generator

        Args:
            api_docs_dir: Directory containing source markdown files
            output_dir: Directory to output index files
        """
        if api_docs_dir is None:
            # Default to cache/api_docs relative to script location
            script_dir = Path(__file__).parent
            skill_dir = script_dir.parent
            self.api_docs_dir = skill_dir / "cache" / "api_docs"
        else:
            self.api_docs_dir = Path(api_docs_dir)

        if output_dir is None:
            # Default to cache/api_index relative to script location
            skill_dir = Path(__file__).parent.parent
            self.output_dir = skill_dir / "cache" / "api_index"
        else:
            self.output_dir = Path(output_dir)

        # Patterns to exclude (non-API entries)
        self.exclude_patterns = [
            re.compile(r"^[A-Z]+$"),  # All caps (API, FAQ, etc.)
            re.compile(r"^\d+$"),  # Pure numbers
            re.compile(r"^[\u4e00-\u9fff]+$"),  # Pure Chinese
        ]

        # Regex to match all ## headings (both top-level and API)
        self.heading_pattern = re.compile(r"^#{2,3}\s+(.+?)\s*(\{#.+})?$")

        # Regex to match API headings with function name and description
        # Format: ## or ### function_name - description {#xxx-API-anchor}
        # Matches any anchor with -API- in it (rqdata-API, stock-API, etc.)
        self.api_pattern = re.compile(
            r"^#{2,3}\s+([\w\.]+)\s*[-–]\s*(.+?)\s*\{#[^}]+-API-[^}]+\}\s*$"
        )

        self.max_paragraphs = (
            5  # Maximum paragraphs to extract for detailed description
        )

    def _is_valid_api(self, api_name: str) -> bool:
        """
        Check if the name is a valid API function

        Args:
            api_name: The API name to check

        Returns:
            True if valid API, False otherwise
        """
        # Must contain underscore or dot (module.function or function_name)
        # OR be a valid alphanumeric name (at least 2 chars, starting with letter)
        if "_" not in api_name and "." not in api_name:
            # Check if it's a valid alphanumeric name (e.g., instruments, get_price)
            if not re.match(r"^[a-zA-Z][a-zA-Z0-9]+$", api_name):
                return False

        # Check against exclude patterns
        for pattern in self.exclude_patterns:
            if pattern.match(api_name):
                return False

        return True

    def _extract_detailed_description(
        self, lines: List[str], start_line: int, end_line: int
    ) -> str:
        """
        Extract detailed description from lines between API header and next section.

        Args:
            lines: All lines from the file
            start_line: Line number after the API header (0-indexed)
            end_line: Line number before the next API header (0-indexed)

        Returns:
            Detailed description as a string, or empty string if not found
        """
        paragraphs = []
        prev_was_empty = False
        table_line_count = 0
        in_code_block = False

        for i in range(start_line, min(end_line, len(lines))):
            line = lines[i].rstrip("\n")

            # Handle code blocks
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                continue

            # Check for table (two consecutive lines starting with |)
            if line.strip().startswith("|"):
                table_line_count += 1
                if table_line_count >= 2:
                    break  # Stop at table
                continue

            # Reset table counter if line doesn't start with |
            if not line.strip().startswith("|"):
                table_line_count = 0

            # Check for new API header
            if line.strip().startswith("##"):
                break

            # Collect paragraphs (non-empty lines)
            if line.strip():
                if not prev_was_empty or not paragraphs:
                    if not paragraphs:
                        paragraphs.append(line.strip())
                    else:
                        paragraphs[-1] += " " + line.strip()
                else:
                    paragraphs.append(line.strip())
                prev_was_empty = False
            else:
                prev_was_empty = True
                # Stop if we have enough paragraphs
                if len(paragraphs) >= self.max_paragraphs:
                    break

        return "<br/>".join(paragraphs) if paragraphs else ""

    def extract_apis_from_file(self, file_path: Path) -> List[Dict]:
        """
        Extract API definitions from a markdown file

        Args:
            file_path: Path to the markdown file

        Returns:
            List of API dictionaries with name, description, line_number, end_line_number
        """
        apis = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)
            api_positions = []

            for line_num, line in enumerate(lines):
                line = line.strip()

                match = self.api_pattern.match(line)
                if match:
                    api_name = match.group(1)
                    description = match.group(2).strip()

                    if self._is_valid_api(api_name):
                        api_positions.append(
                            {
                                "api_name": api_name,
                                "description": description,
                                "line_number": line_num + 1,
                            }
                        )

            for i, api in enumerate(api_positions):
                if i + 1 < len(api_positions):
                    api["end_line_number"] = api_positions[i + 1]["line_number"] - 1
                else:
                    api["end_line_number"] = total_lines

                # Extract detailed description
                start_idx = api["line_number"]  # 0-indexed, line_number is 1-indexed
                end_idx = api["end_line_number"]

                detailed_desc = self._extract_detailed_description(
                    lines, start_idx, end_idx
                )
                api["detailed_description"] = detailed_desc

                apis.append(api)

        except Exception as e:
            print(f"  Warning: Error processing {file_path.name}: {e}")

        return apis

    def _extract_titles_from_file(self, file_path: Path) -> List[str]:
        """
        Extract all titles from a markdown file.

        Args:
            file_path: Path to the markdown file

        Returns:
            List of title strings (top-level headings and API descriptions without function names)
        """
        titles = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()

                # Check if it's a heading
                match = self.heading_pattern.match(line)
                if not match:
                    continue

                heading_text = match.group(1).strip()

                # Check if it's an API heading (contains function name)
                api_match = self.api_pattern.match(line)
                if api_match:
                    # Extract only the description part (after the dash)
                    description = api_match.group(2).strip()
                    titles.append(description)
                else:
                    # It's a top-level heading, use as-is
                    titles.append(heading_text)

        except Exception as e:
            print(f"  Warning: Error extracting titles from {file_path.name}: {e}")

        return titles

    def generate_doc_index(self) -> Optional[Path]:
        """
        Generate api_doc_index.md - a combined index of all API documents.

        Returns:
            Path to the generated file, or None if failed
        """
        exclude_files = {"changelogs.md", "manual.md"}

        output_file = self.api_docs_dir.parent / "api_doc_index.md"

        source_files = [
            f for f in self.api_docs_dir.glob("*.md") if f.name not in exclude_files
        ]

        if not source_files:
            print(f"Error: No markdown files found in {self.api_docs_dir}")
            return None

        # Check if regeneration is needed based on source file freshness
        if output_file.exists():
            output_mtime = output_file.stat().st_mtime
            # Only regenerate if any source file is newer than output
            if all(f.stat().st_mtime <= output_mtime for f in source_files):
                print(f"Skipped: {output_file.name} (up to date)")
                return output_file

        md_files = sorted(self.api_docs_dir.glob("*.md"))

        doc_descriptions = []

        for md_file in md_files:
            if md_file.name in exclude_files:
                continue

            titles = self._extract_titles_from_file(md_file)
            if titles:
                description = "。".join(titles)
                doc_descriptions.append(
                    {"filename": md_file.name, "description": description}
                )

        if not doc_descriptions:
            print("Warning: No document descriptions found")
            return None

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("# API Doc Index\n\n")
            f.write("| Document | Description |\n")
            f.write("|----------|-------------|\n")

            for doc in doc_descriptions:
                escaped_desc = doc["description"].replace("|", "\\|")
                f.write(f"| {doc['filename']} | {escaped_desc} |\n")

        print(f"Generated: {output_file.name}")
        return output_file

    def generate_index_file(
        self, source_file: Path, apis: List[Dict]
    ) -> Optional[Path]:
        """
        Generate an index file for a source markdown file

        Args:
            source_file: Path to the source markdown file
            apis: List of API dictionaries

        Returns:
            Path to the generated index file, or None if no APIs found
        """
        if not apis:
            print(f"  No APIs found in {source_file.name}, skipping...")
            return None

        # Sort by line number
        apis.sort(key=lambda x: x["line_number"])

        # Create output file path
        source_name = source_file.stem  # filename without extension
        output_file = self.output_dir / f"{source_name}_index.md"

        # Check freshness: skip if output exists and source hasn't changed
        if output_file.exists():
            output_mtime = output_file.stat().st_mtime
            source_mtime = source_file.stat().st_mtime
            if source_mtime <= output_mtime:
                print(f"  Skipped: {output_file.name} (up to date)")
                return None

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Write markdown content
        with open(output_file, "w", encoding="utf-8") as f:
            # Header
            f.write(f"# API Index for {source_file.name}\n\n")

            # Summary
            f.write("## Summary\n\n")
            f.write(f"- Source File: {source_file.name}\n")
            f.write(f"- Total APIs: {len(apis)}\n\n")

            # API Definitions
            f.write("## API Definitions (Sorted by Line Number)\n\n")
            f.write("| API Name | Description | Line Range |\n")
            f.write("|----------|-------------|------------|\n")

            for api in apis:
                # Build description: combine short description and detailed description
                short_desc = api["description"]
                detailed_desc = api.get("detailed_description", "")

                if detailed_desc:
                    full_description = f"{short_desc}<br/>{detailed_desc}"
                else:
                    full_description = short_desc

                # Escape pipe characters in description
                full_description = full_description.replace("|", "\\|")
                # Replace newlines with <br/>
                full_description = full_description.replace("\n", "<br/>")

                line_range = f"{api['line_number']}-{api['end_line_number']}"
                f.write(
                    f"| `{api['api_name']}` | {full_description} | {line_range} |\n"
                )

            f.write("\n")

        return output_file

    def run(self) -> int:
        """
        Run the index generator

        Returns:
            Number of index files generated
        """
        print(f"API docs directory: {self.api_docs_dir}")
        print(f"Output directory: {self.output_dir}")
        print()

        # Check if api_docs directory exists
        if not self.api_docs_dir.exists():
            print(f"Error: API docs directory not found: {self.api_docs_dir}")
            return 0

        # Find all markdown files
        md_files = sorted(self.api_docs_dir.glob("*.md"))

        if not md_files:
            print(f"Error: No markdown files found in {self.api_docs_dir}")
            return 0

        print(f"Found {len(md_files)} markdown files\n")

        # Files to exclude from API index generation
        exclude_from_api_index = {"changelogs.md", "manual.md", "api_doc_index.md"}

        # Process each file
        index_count = 0
        total_apis = 0

        for md_file in md_files:
            if md_file.name in exclude_from_api_index:
                print(f"Skipping: {md_file.name} (excluded)")
                continue

            print(f"Processing: {md_file.name}")

            try:
                # Extract APIs from file
                apis = self.extract_apis_from_file(md_file)

                if apis:
                    # Generate index file
                    output_file = self.generate_index_file(md_file, apis)
                    if output_file:
                        print(f"  Generated: {output_file.name} ({len(apis)} APIs)")
                        index_count += 1
                        total_apis += len(apis)
                else:
                    print(f"  No APIs found, skipping...")

            except Exception as e:
                print(f"  Error: {e}")
                continue

        print()
        print("=" * 50)
        print(f"Summary:")
        print(f"  - Index files generated: {index_count}")
        print(f"  - Total APIs indexed: {total_apis}")
        print(f"  - Output directory: {self.output_dir}")
        print("=" * 50)
        print()

        print("Generating API doc index...")
        doc_index_file = self.generate_doc_index()
        if doc_index_file:
            print(f"  Success: {doc_index_file.name}")
        else:
            print("  Warning: Failed to generate API doc index")

        return index_count


def main():
    """Main entry point"""
    print("RQData API Index Generator")
    print("=" * 50)
    print()

    generator = APIIndexGenerator()
    index_count = generator.run()

    if index_count > 0:
        print(f"\nSuccess! Generated {index_count} index file(s).")
        return 0
    else:
        print("\nNo index files generated.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
