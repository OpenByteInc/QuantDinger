#!/usr/bin/env python3
"""Convert app/routes/*.py from Flask Blueprint to flask-smorest Blueprint."""
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "app" / "routes"

SKIP = {"__init__.py", "health.py"}


def convert(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    orig = text

    if "agent_v1" in str(path):
        return False

    # Import smorest Blueprint
    if "from flask_smorest import Blueprint" not in text:
        if "from flask import Blueprint" in text:
            text = text.replace(
                "from flask import Blueprint",
                "from flask_smorest import Blueprint",
                1,
            )
        elif "Blueprint(" in text:
            lines = text.splitlines()
            insert_at = 0
            for i, line in enumerate(lines):
                if line.startswith("from flask import"):
                    insert_at = i + 1
            lines.insert(insert_at, "from flask_smorest import Blueprint")
            text = "\n".join(lines) + ("\n" if orig.endswith("\n") else "")

    text = text.replace("_bp = Blueprint(", "_blp = Blueprint(")
    text = text.replace("_bp.route(", "_blp.route(")

    # Keep backward compat alias for tests importing *_bp
    if "_blp = Blueprint" in text and "\n# openapi-compat" not in text:
        import re
        m = re.search(r"(\w+)_blp = Blueprint", text)
        if m:
            name = m.group(1)
            text += f"\n# openapi-compat: legacy import name\n{name}_bp = {name}_blp\n"

    if text != orig:
        path.write_text(text, encoding="utf-8")
        print("converted", path.name)
        return True
    return False


def main():
    n = 0
    for path in sorted(ROUTES.glob("*.py")):
        if path.name in SKIP:
            continue
        if convert(path):
            n += 1
    print(f"done: {n} files")


if __name__ == "__main__":
    main()
