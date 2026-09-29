#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQData文档批量缓存初始化脚本
首次运行时缓存所有RQData Python API文档

Usage:
    python init_cache.py [--force-refresh]
"""

import sys
import io
import os
import argparse

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
from cache_manager import RQDataCacheManager


def main():
    """批量缓存所有RQData文档"""
    parser = argparse.ArgumentParser(description="RQData文档缓存初始化")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="强制重新下载所有文档（忽略缓存）",
    )
    args = parser.parse_args()

    force_refresh = args.force_refresh

    print("=" * 80)
    print("RQData文档批量缓存初始化")
    if force_refresh:
        print("[强制刷新模式]")
    print("=" * 80)
    print()

    cache_mgr = RQDataCacheManager()

    info = cache_mgr.get_cache_info()
    print(f"缓存目录: {info['cache_dir']}")
    print(f"当前缓存文件数: {info['total_count']}")
    print(f"缓存大小: {info['total_size_mb']} MB")
    print()

    print("获取文档索引...")
    try:
        urls = cache_mgr.fetch_document_index()
        print(f"找到 {len(urls)} 个文档")
    except RuntimeError as e:
        print(f"[X] 无法获取文档索引: {e}")
        return 1

    print()
    print(f"准备缓存 {len(urls)} 个文档...")
    print()

    success_count = 0
    skip_count = 0
    fail_count = 0

    for i, url in enumerate(urls, 1):
        doc_name = url.split("/")[-1]
        print(f"[{i}/{len(urls)}] {doc_name}...", end=" ")

        try:
            content = cache_mgr.get_document(url, force_refresh=force_refresh)
            success_count += 1

        except RuntimeError as e:
            cache_path = cache_mgr._get_cache_path_for_read(url)
            if cache_path and cache_path.exists():
                print(f"[WARN] 使用缓存版本")
                skip_count += 1
            else:
                print(f"[FAIL] {str(e)}")
                fail_count += 1

    print()
    print("=" * 80)
    print("缓存完成")
    print("=" * 80)
    print(f"成功下载: {success_count}")
    print(f"使用缓存: {skip_count}")
    print(f"失败: {fail_count}")
    print()

    final_info = cache_mgr.get_cache_info()
    print(f"最终缓存文件数: {final_info['total_count']}")
    print(f"总缓存大小: {final_info['total_size_mb']} MB")
    print()

    if force_refresh:
        print("=" * 80)
        print("强制刷新模式：下载宏观因子名称文件...")
        print("=" * 80)
    else:
        print("=" * 80)
        print("下载宏观因子名称文件...")
        print("=" * 80)
    try:
        csv_path = cache_mgr.download_and_convert_factor_file("macro-economy.md")
        if csv_path and csv_path.exists():
            print(f"[OK] 宏观因子名称已保存到: {csv_path.name}")
        else:
            print("[WARN] 宏观因子名称文件下载失败")
    except Exception as e:
        print(f"[WARN] 宏观因子名称文件处理异常: {e}")
    print()

    if fail_count > 0 and success_count + skip_count == 0:
        print("[WARN] 所有文档缓存失败，请检查网络连接后重试")
        return 1
    elif fail_count > 0:
        print("[WARN] 部分文档缓存失败，请检查网络连接后重试")
    return 0


if __name__ == "__main__":
    sys.exit(main())
