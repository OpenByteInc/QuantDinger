#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inspect rqamsc runtime environment and print concise initialization context.
"""

from __future__ import annotations

from rqamsc_runtime import RuntimeConfigError, build_runtime_config, initialize_rqamsc


def main() -> int:
    config = build_runtime_config()
    print(f"Python: {config.python_executable}")

    try:
        result = initialize_rqamsc(config)
        print(f"Config source: {result.config_source}")
        print(f"Profile: {result.profile or '<active>'}")
        print(f"Account: {result.username}")
        print(f"AMS URI: {result.uri}")
        print(f"Workspace: {result.workspace_name}")
        return 0
    except RuntimeConfigError as exc:
        print("RQAMSC config: incomplete")
        print(f"Missing fields: {' / '.join(exc.missing_keys)}")
        print("Fix: run rqamsc setup with a profile, or set RQAMSC_PROFILE to an existing profile")
        return 0
    except Exception as exc:
        print(f"RQAMSC init failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
