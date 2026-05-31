#!/usr/bin/env python3
"""
One-off helper: convert Flask Blueprint declarations to flask-smorest Blueprint
and append a minimal @blp.doc line after each @*_bp.route when missing.

Run from backend_api_python/ (review diff before committing):

    python scripts/migrate_blueprint_to_smorest.py --dry-run
    python scripts/migrate_blueprint_to_smorest.py
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROUTES_DIR = Path(__file__).resolve().parents[1] / "app" / "routes"
AGENT_DIR = ROUTES_DIR / "agent_v1"

ROUTE_DECORATOR = re.compile(
    r"^(\s*)@(?P<bp>\w+)_bp\.route\((?P<args>.*)\)\s*$",
    re.MULTILINE,
)
FUNC_DEF = re.compile(
    r"^(\s*)def (?P<name>\w+)\(",
    re.MULTILINE,
)


def _summary_from_name(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _operation_id(name: str) -> str:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
    return parts[0].lower() + "".join(p.title() for p in parts[1:]) if parts else name


def process_file(path: Path, dry_run: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if "from flask_smorest import Blueprint" not in text:
        if "from flask import Blueprint" in text:
            text = text.replace(
                "from flask import Blueprint",
                "from flask_smorest import Blueprint",
                1,
            )
        elif "Blueprint" in text and "flask_smorest" not in text:
            text = "from flask_smorest import Blueprint\n" + text

    text = re.sub(
        r"(\w+)_bp = Blueprint\(",
        r"\1_blp = Blueprint(",
        text,
    )
    text = text.replace("@", "@").replace("_bp.route", "_blp.route")
    text = text.replace("_bp = ", "_blp = ")  # idempotent safety

    # Append @*_blp.doc after route decorator when not already documented
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.match(r"^(\s*)@(?P<bp>\w+)_blp\.route\((.*)\)\s*$", line)
        if m:
            indent = m.group(1)
            # peek next non-decorator line for function name
            j = i + 1
            doc_lines = []
            while j < len(lines) and lines[j].strip().startswith("@"):
                if "_blp.doc" in lines[j]:
                    doc_lines = []
                    break
                doc_lines.append(lines[j])
                out.append(lines[j])
                j += 1
            if j < len(lines) and not any("_blp.doc" in x for x in lines[i:j]):
                fn = re.match(r"^\s*def (\w+)\(", lines[j])
                if fn:
                    name = fn.group(1)
                    summary = _summary_from_name(name)
                    op = _operation_id(name)
                    out.append(
                        f'{indent}@' + f'{m.group("bp")}_blp.doc('
                        f'summary="{summary}", operationId="{op}")'
                    )
            i = j - 1 if j > i + 1 else i
        i += 1
    text = "\n".join(out) + ("\n" if original.endswith("\n") else "")

    if text != original:
        if not dry_run:
            path.write_text(text, encoding="utf-8")
        print(f"{'DRY ' if dry_run else ''}updated {path}")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    changed = 0
    for path in sorted(ROUTES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        if process_file(path, args.dry_run):
            changed += 1
    print(f"Done. {changed} file(s) touched.")


if __name__ == "__main__":
    main()
