#!/usr/bin/env python3
"""Fix flask imports after blueprint conversion."""
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "app" / "routes"
FLASK_ONLY = {
    "jsonify", "request", "g", "Response", "stream_with_context",
    "send_file", "redirect", "url_for", "session", "make_response", "abort",
}


def fix_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^from flask_smorest import (.+)$", text, re.MULTILINE)
    if not m:
        return
    names = [x.strip() for x in m.group(1).split(",")]
    flask_items = sorted({n for n in names if n in FLASK_ONLY})
    other = [n for n in names if n not in FLASK_ONLY]
    if "Blueprint" not in other:
        other.insert(0, "Blueprint")
    lines = []
    if flask_items:
        lines.append(f"from flask import {', '.join(flask_items)}")
    lines.append(f"from flask_smorest import {', '.join(other)}")
    replacement = "\n".join(lines)
    text = text[: m.start()] + replacement + text[m.end() :]
    path.write_text(text, encoding="utf-8")
    print("fixed", path.name)


for path in sorted(ROUTES.glob("*.py")):
    if path.name in ("__init__.py", "health.py"):
        continue
    fix_file(path)
