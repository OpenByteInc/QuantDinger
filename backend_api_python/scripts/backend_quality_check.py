#!/usr/bin/env python3
"""Backend structure guardrail.

This check intentionally starts as a regression guard, not a perfection gate:
legacy hot spots are recorded in ``backend_quality_baseline.json`` and future
changes should not make them larger or reintroduce removed exchange code.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "scripts" / "backend_quality_baseline.json"
DEFAULT_SCAN_ROOTS = ("app", "scripts", "tests")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _iter_files(roots: Iterable[str]) -> Iterable[Path]:
    ignored_names = {"backend_quality_baseline.json"}
    for root in roots:
        base = ROOT / root
        if base.is_file():
            yield base
            continue
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".md", ".json", ".txt"):
                if path.name in ignored_names:
                    continue
                if ".venv" not in path.parts and ".pytest_cache" not in path.parts:
                    yield path


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def collect_file_lines() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for path in (ROOT / "app").rglob("*.py"):
        out[_rel(path)] = len(_read_text(path).splitlines())
    return out


def collect_function_lines() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for path in (ROOT / "app").rglob("*.py"):
        text = _read_text(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                out[f"{_rel(path)}::{node.name}"] = end - node.lineno + 1
    return out


def find_banned_tokens(tokens: Iterable[str], roots: Iterable[str]) -> List[Tuple[str, str]]:
    patterns = [re.compile(str(t), re.IGNORECASE) for t in tokens if str(t).strip()]
    hits: List[Tuple[str, str]] = []
    if not patterns:
        return hits
    for path in _iter_files(roots):
        text = _read_text(path)
        for pattern in patterns:
            if pattern.search(text):
                hits.append((_rel(path), pattern.pattern))
    return hits


def _compare_metric(name: str, actual: Dict[str, int], baseline: Dict[str, int]) -> List[str]:
    errors: List[str] = []
    for key, old_value in sorted(baseline.items()):
        new_value = actual.get(key)
        if new_value is None:
            continue
        if int(new_value) > int(old_value):
            errors.append(f"{name} grew: {key} baseline={old_value} actual={new_value}")
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", action="store_true", help="print metrics as JSON")
    args = parser.parse_args(argv)

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    file_lines = collect_file_lines()
    function_lines = collect_function_lines()
    banned_hits = find_banned_tokens(
        baseline.get("banned_exchange_patterns") or baseline.get("banned_exchange_tokens") or [],
        DEFAULT_SCAN_ROOTS,
    )

    errors: List[str] = []
    errors.extend(_compare_metric("file_lines", file_lines, baseline.get("max_file_lines") or {}))
    errors.extend(_compare_metric("function_lines", function_lines, baseline.get("max_function_lines") or {}))
    errors.extend(f"banned token '{token}' found in {path}" for path, token in banned_hits)

    if args.json:
        print(json.dumps(
            {
                "max_file_lines": dict(sorted(file_lines.items(), key=lambda kv: kv[1], reverse=True)[:20]),
                "max_function_lines": dict(sorted(function_lines.items(), key=lambda kv: kv[1], reverse=True)[:20]),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ))
    elif errors:
        print("Backend quality check failed:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Backend quality check passed")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
