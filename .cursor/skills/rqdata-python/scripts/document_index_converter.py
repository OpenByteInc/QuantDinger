#!/usr/bin/env python3
"""
RQData文档索引获取脚本
从官方获取文档索引并保存为Markdown文件
"""

import requests
from pathlib import Path
from datetime import datetime
from typing import Optional


class DocumentIndexFetcher:
    """文档索引获取器"""

    def __init__(self):
        self.index_url = "https://www.ricequant.com/doc/document-index.txt"

    def fetch(self, timeout: int = 60) -> str:
        """获取文档索引内容"""
        response = requests.get(self.index_url, timeout=timeout)
        response.raise_for_status()
        return response.text

    def save(self, content: str, output_path: Optional[Path] = None) -> Path:
        """保存内容到文件"""
        if output_path is None:
            skill_root = Path(__file__).parent.parent
            output_path = skill_root / "cache" / "document_index.md"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def run(self, output_path: Optional[Path] = None) -> Path:
        """执行获取并保存"""
        print(f"正在获取文档索引: {self.index_url}")
        content = self.fetch()
        print(f"获取成功，内容长度: {len(content)} 字符")

        saved_path = self.save(content, output_path)
        print(f"已保存到: {saved_path}")
        return saved_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="RQData文档索引获取脚本")
    parser.add_argument("-o", "--output", type=str, help="输出文件路径")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None
    fetcher = DocumentIndexFetcher()

    try:
        saved_path = fetcher.run(output_path)
        print(f"\n成功: {saved_path}")
        return 0
    except Exception as e:
        print(f"\n错误: {e}")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
