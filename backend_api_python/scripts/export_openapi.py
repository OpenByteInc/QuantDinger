#!/usr/bin/env python3
"""
Export the flask-smorest OpenAPI spec to YAML/JSON.

Usage (from backend_api_python/):

    python scripts/export_openapi.py
    python scripts/export_openapi.py --output ../docs/api/openapi.yaml
    python scripts/export_openapi.py --format json --output /tmp/openapi.json

Set SKIP_STARTUP_HOOKS=1 so workers and strategy restore are not started.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Repo layout: backend_api_python/scripts/export_openapi.py
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
_DEFAULT_OUTPUT = _REPO_ROOT / "docs" / "api" / "openapi.yaml"

sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")
os.environ.setdefault("SECRET_KEY", "openapi-export-dev-only-not-for-production")
os.environ.setdefault("OPENAPI_ENABLED", "false")
os.environ.setdefault("CACHE_ENABLED", "false")


def export_spec(output: Path, fmt: str) -> None:
    import yaml
    from app import create_app
    from app.openapi import get_openapi_api

    app = create_app()
    api = get_openapi_api(app)
    if api is None:
        raise SystemExit("OpenAPI Api extension not registered")

    with app.app_context():
        spec_dict = api.spec.to_dict()

    from app.openapi.register import enrich_spec
    spec_dict = enrich_spec(spec_dict)

    output.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "yaml":
        text = yaml.safe_dump(
            spec_dict,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        output.write_text(text, encoding="utf-8")
    elif fmt == "json":
        output.write_text(
            json.dumps(spec_dict, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    else:
        raise SystemExit(f"Unsupported format: {fmt}")

    print(f"Wrote OpenAPI {fmt.upper()} to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export QuantDinger Web API OpenAPI spec")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Output file (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=("yaml", "json"),
        default="yaml",
        help="Serialization format (default: yaml)",
    )
    args = parser.parse_args()
    export_spec(args.output, args.format)


if __name__ == "__main__":
    main()
