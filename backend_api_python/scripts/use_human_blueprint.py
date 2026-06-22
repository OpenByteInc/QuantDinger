#!/usr/bin/env python3
"""Switch app/routes/* from flask_smorest.Blueprint to HumanBlueprint."""
from __future__ import annotations

from pathlib import Path

_ROUTES_DIR = Path(__file__).resolve().parents[1] / "app" / "routes"
_OLD = "from flask_smorest import Blueprint"
_NEW = "from app.openapi.blueprint import HumanBlueprint as Blueprint"


def main() -> None:
    changed = 0
    for path in sorted(_ROUTES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        if _OLD not in text:
            continue
        path.write_text(text.replace(_OLD, _NEW, 1), encoding="utf-8")
        changed += 1
        print(f"updated {path.name}")
    print(f"done: {changed} files")


if __name__ == "__main__":
    main()
