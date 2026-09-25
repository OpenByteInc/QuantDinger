#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQData Skill Initialization Script

Performs all necessary setup before using the RQData skill:
1. Verify RQData license
2. Check if cache needs refresh (any api_doc expired)
3. Initialize document cache (only if needed)
4. Generate API indices (only if cache was refreshed)
5. Generate macro factor file (only if cache was refreshed)

Exit codes:
- 0: Success
- 1: License check failed
- 2: Cache initialization failed
- 3: Both failed
"""

import sys
import subprocess
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_CACHE_DAYS = 7


def print_header():
    """Print script header"""
    pass  # Suppressed for clean output on success


def check_license():
    """Check RQData license by calling rqdatac.init()"""
    try:
        import rqdatac

        rqdatac.init()
        return True
    except ImportError:
        print("[FAIL] rqdatac not installed", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[FAIL] RQData license invalid: {e}", file=sys.stderr)
        return False


def check_api_docs_expired(cache_dir: Optional[Path] = None) -> bool:
    """Check if any api_docs file is expired (older than DEFAULT_CACHE_DAYS)"""
    if cache_dir is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        skill_root = Path(script_dir).parent
        cache_dir = skill_root / "cache" / "api_docs"

    if not cache_dir.exists():
        return True

    max_age_seconds = DEFAULT_CACHE_DAYS * 24 * 60 * 60
    current_time = time.time()

    md_files = list(cache_dir.glob("*.md"))
    if not md_files:
        return True

    for md_file in md_files:
        file_age = current_time - md_file.stat().st_mtime
        if file_age > max_age_seconds:
            return True

    return False


def run_cache_init(force_refresh: bool = False):
    """Run cache initialization (calls init_cache.py)"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    init_cache_path = os.path.join(script_dir, "init_cache.py")

    if not os.path.exists(init_cache_path):
        print(f"[FAIL] init_cache.py not found at: {init_cache_path}", file=sys.stderr)
        return False

    args = [sys.executable, init_cache_path]
    if force_refresh:
        args.append("--force-refresh")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=300,  # 5 minutes timeout when refreshing all
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode == 0:
            return True
        else:
            print("[FAIL] Cache initialization failed", file=sys.stderr)
            if result.stdout:
                print("Output:", file=sys.stderr)
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print("Errors:", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("[FAIL] Cache initialization timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[FAIL] Cache initialization error: {e}", file=sys.stderr)
        return False


def generate_api_indices():
    """生成 API 索引文件"""

    script_dir = os.path.dirname(os.path.abspath(__file__))
    generate_script = os.path.join(script_dir, "generate_api_index.py")

    if not os.path.exists(generate_script):
        error_msg = f"generate_api_index.py not found at: {generate_script}"
        print(f"[FAIL] {error_msg}", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [sys.executable, generate_script],
            capture_output=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode == 0:
            return True
        else:
            error_msg = "API indices generation failed"
            print(f"[FAIL] {error_msg}", file=sys.stderr)
            if result.stdout:
                print("Output:", file=sys.stderr)
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print("Errors:", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
            return False

    except subprocess.TimeoutExpired:
        error_msg = "API indices generation timed out"
        print(f"[FAIL] {error_msg}", file=sys.stderr)
        return False
    except Exception as e:
        error_msg = f"API indices generation error: {e}"
        print(f"[FAIL] {error_msg}", file=sys.stderr)
        return False


def build_code_indices():
    """构建资产代码索引"""

    script_dir = os.path.dirname(os.path.abspath(__file__))
    code_index_script = os.path.join(script_dir, "code_index_manager.py")

    if not os.path.exists(code_index_script):
        print(f"[WARN] code_index_manager.py not found, skipping code index build")
        return True

    try:
        from code_index_manager import CodeIndexManager

        manager = CodeIndexManager()

        cn_ok = manager.build_index("cn", "CS")
        if not cn_ok:
            print("[WARN] CN stock code index build failed")

        return True

    except Exception as e:
        print(f"[WARN] Code index build error: {e}", file=sys.stderr)
        return True


def refresh_macro_factor_file():
    """刷新宏观因子名称文件"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    init_cache_path = os.path.join(script_dir, "init_cache.py")

    if not os.path.exists(init_cache_path):
        print(f"[WARN] init_cache.py not found, skipping macro factor file refresh")
        return True

    try:
        from cache_manager import RQDataCacheManager

        cache_mgr = RQDataCacheManager()
        csv_path = cache_mgr.download_and_convert_factor_file("macro-economy.md")
        if csv_path and csv_path.exists():
            print(f"[OK] Macro factor names refreshed: {csv_path.name}")
            return True
        else:
            print("[WARN] Macro factor names file download failed")
            return False
    except Exception as e:
        print(f"[WARN] Macro factor file refresh error: {e}", file=sys.stderr)
        return False


def main():
    """Main function"""
    print_header()

    license_ok = check_license()
    print(file=sys.stderr)

    if not license_ok:
        print(file=sys.stderr)
        print("[FAIL] License check failed - skill cannot be used", file=sys.stderr)
        return 1

    docs_expired = check_api_docs_expired()
    if docs_expired:
        print("[INFO] API docs expired or missing, refreshing all...")
        cache_ok = run_cache_init(force_refresh=True)
    else:
        cache_ok = True
    print(file=sys.stderr)

    indices_ok = False
    if docs_expired and cache_ok:
        print("[INFO] Regenerating API indices...")
        indices_ok = generate_api_indices()
    elif not docs_expired:
        indices_ok = True
    print(file=sys.stderr)

    macro_ok = False
    if docs_expired and cache_ok:
        print("[INFO] Regenerating macro factor file...")
        macro_ok = refresh_macro_factor_file()
    elif not docs_expired:
        macro_ok = True
    print(file=sys.stderr)

    code_index_ok = build_code_indices()
    print(file=sys.stderr)

    if license_ok and cache_ok and indices_ok and macro_ok and code_index_ok:
        print("Done")
        return 0
    elif license_ok and not cache_ok:
        print("[FAIL] Cache init failed - skill cannot be used", file=sys.stderr)
        return 2
    elif license_ok and cache_ok and not indices_ok:
        print(
            "[WARN] API indices generation failed - skill may still work",
            file=sys.stderr,
        )
        return 0
    else:
        print("[FAIL] Initialization failed - skill cannot be used", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
