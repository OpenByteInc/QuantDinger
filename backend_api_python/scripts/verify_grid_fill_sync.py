#!/usr/bin/env python3
"""
Verify grid resting-order fill sync without manual exchange testing.

Usage:
    cd backend_api_python
    python scripts/verify_grid_fill_sync.py          # contract tests only (no keys)
    python scripts/verify_grid_fill_sync.py --live   # also run testnet smoke if configured

Contract tests mock each exchange's get_order JSON — run these after every fix.
Live smoke requires .env.testnet.local — see tests/test_grid_exchange_fill_live.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv_local() -> None:
    path = ROOT / ".env.testnet.local"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _run_pytest(extra: list[str]) -> int:
    cmd = [sys.executable, "-m", "pytest", *extra]
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify grid order fill sync tests")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also run optional testnet smoke tests (needs .env.testnet.local)",
    )
    args = parser.parse_args()

    _load_dotenv_local()

    code = _run_pytest(["tests/test_grid_exchange_fill_contracts.py", "-v"])
    if code != 0:
        return code

    code = _run_pytest(["tests/test_grid_bitget_fills.py", "-v"])
    if code != 0:
        return code

    if args.live:
        os.environ.setdefault("RUN_GRID_LIVE_TESTS", "1")
        code = _run_pytest(["tests/test_grid_exchange_fill_live.py", "-m", "integration", "-v"])
        if code != 0:
            return code
        print("\nLive smoke tests passed.", flush=True)
    else:
        print(
            "\nContract tests passed (no API keys used). "
            "For optional testnet smoke: python scripts/verify_grid_fill_sync.py --live",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
