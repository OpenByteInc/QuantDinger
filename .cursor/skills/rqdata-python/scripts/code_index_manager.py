#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资产代码索引管理器
提供股票代码搜索功能，支持通过代码或名称模糊匹配查找股票
"""

import time
from pathlib import Path
from typing import Optional

INDEX_CACHE_DAYS = 1


class CodeIndexManager:
    """资产代码索引管理器"""

    def __init__(self, cache_dir: Optional[str] = None):
        """
        初始化索引管理器

        Args:
            cache_dir: 缓存目录路径，默认为 skill 的 cache/code_index 目录
        """
        if cache_dir is None:
            skill_root = Path(__file__).parent.parent
            self.cache_dir = skill_root / "cache" / "code_index"
        else:
            self.cache_dir = Path(cache_dir)

        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_index_path(self, market: str, asset_type: str = "CS") -> Path:
        """获取索引文件路径"""
        filename = f"{market}_{asset_type.lower()}_code_index.md"
        return self.cache_dir / filename

    def _is_index_expired(self, index_path: Path) -> bool:
        """检查索引是否过期"""
        if not index_path.exists():
            return True

        file_mtime = index_path.stat().st_mtime
        file_age = time.time() - file_mtime
        max_age_seconds = INDEX_CACHE_DAYS * 24 * 60 * 60

        return file_age > max_age_seconds

    def build_index(self, market: str = "cn", asset_type: str = "CS") -> bool:
        """
        构建资产代码索引

        Args:
            market: 市场代码，'cn' - A股，'hk' - 港股
            asset_type: 资产类型，'CS' - 股票

        Returns:
            是否成功构建索引
        """
        import rqdatac

        index_path = self._get_index_path(market, asset_type)

        try:
            df = rqdatac.all_instruments(type=asset_type, market=market)

            if df is None or df.empty:
                print(f"[WARN] No data returned for {market}/{asset_type}")
                return False

            rows = []
            for _, row in df.iterrows():
                order_book_id = row.get("order_book_id", "")
                symbol = row.get("symbol", "")
                abbrev_symbol = row.get("abbrev_symbol", "")

                if order_book_id:
                    rows.append(
                        {
                            "order_book_id": order_book_id,
                            "symbol": symbol,
                            "abbrev_symbol": abbrev_symbol,
                        }
                    )

            lines = [
                "| order_book_id | symbol | abbrev_symbol |",
                "|---------------|--------|----------------|",
            ]

            for r in rows:
                symbol_escaped = r["symbol"].replace("|", "\\|")
                abbrev_escaped = (
                    r["abbrev_symbol"].replace("|", "\\|") if r["abbrev_symbol"] else ""
                )
                lines.append(
                    f"| {r['order_book_id']} | {symbol_escaped} | {abbrev_escaped} |"
                )

            content = "\n".join(lines)

            with open(index_path, "w", encoding="utf-8") as f:
                f.write(content)

            return True

        except Exception as e:
            print(f"[FAIL] Failed to build index: {e}")
            return False

    def get_index(
        self, market: str = "cn", asset_type: str = "CS", force_refresh: bool = False
    ) -> Optional[list[dict]]:
        """
        获取资产代码索引

        Args:
            market: 市场代码
            asset_type: 资产类型
            force_refresh: 是否强制刷新索引

        Returns:
            索引数据列表，每个元素为 {'order_book_id', 'symbol', 'abbrev_symbol'}
        """
        index_path = self._get_index_path(market, asset_type)

        if force_refresh or self._is_index_expired(index_path):
            if not self.build_index(market, asset_type):
                return None

        return self._parse_index(index_path)

    def _parse_index(self, index_path: Path) -> Optional[list[dict]]:
        """解析索引文件"""
        if not index_path.exists():
            return None

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            records = []
            in_table = False

            for line in lines:
                line = line.strip()

                if line.startswith("| order_book_id"):
                    in_table = True
                    continue

                if in_table and line.startswith("|"):
                    if line == "|---|---|---|" or not line.strip():
                        continue

                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4:
                        order_book_id = parts[1]
                        symbol = parts[2]
                        abbrev_symbol = parts[3] if len(parts) > 3 else ""

                        if order_book_id and order_book_id != "order_book_id":
                            records.append(
                                {
                                    "order_book_id": order_book_id,
                                    "symbol": symbol,
                                    "abbrev_symbol": abbrev_symbol,
                                }
                            )

            return records

        except Exception as e:
            print(f"[FAIL] Failed to parse index: {e}")
            return None

    def search(
        self, query: str, market: str = "cn", limit: int = 10, asset_type: str = "CS"
    ) -> list[dict]:
        """
        搜索资产代码

        Args:
            query: 查询字符串（代码或名称）
            market: 市场代码
            limit: 返回结果数量限制
            asset_type: 资产类型

        Returns:
            匹配结果列表，每个元素为 {'order_book_id', 'symbol', 'abbrev_symbol', 'match_type'}
        """
        records = self.get_index(market, asset_type)

        if not records:
            return []

        query = query.strip()
        if not query:
            return []

        results = []
        query_lower = query.lower()

        for r in records:
            match_type = None

            order_book_id = r["order_book_id"]
            symbol = r["symbol"]
            abbrev_symbol = r.get("abbrev_symbol", "")

            code_without_suffix = (
                order_book_id.split(".")[0] if "." in order_book_id else order_book_id
            )

            if order_book_id.lower() == query_lower:
                match_type = "code_exact"
            elif symbol.lower() == query_lower:
                match_type = "name_exact"
            elif code_without_suffix == query:
                match_type = "code_prefix"
            elif query_lower in symbol.lower():
                match_type = "name_contains"
            elif abbrev_symbol and query_lower == abbrev_symbol.lower():
                match_type = "abbrev_exact"
            elif abbrev_symbol and query_lower in abbrev_symbol.lower():
                match_type = "abbrev_contains"

            if match_type:
                results.append(
                    {
                        "order_book_id": order_book_id,
                        "symbol": symbol,
                        "abbrev_symbol": abbrev_symbol,
                        "match_type": match_type,
                    }
                )

        results.sort(
            key=lambda x: (
                0 if x["match_type"] == "code_exact" else 1,
                0 if x["match_type"] == "name_exact" else 1,
                0 if x["match_type"] == "code_prefix" else 1,
                0 if x["match_type"] == "abbrev_exact" else 1,
                0 if x["match_type"] == "name_contains" else 1,
                0 if x["match_type"] == "abbrev_contains" else 1,
            )
        )

        return results[:limit]


def resolve_stock_code(
    query: str, market: str = "cn", limit: int = 10, asset_type: str = "CS"
) -> list[dict]:
    """
    通过股票代码或公司名称查询真实股票代码

    支持输入:
    - 纯数字: 600519, 000001
    - 带后缀: 600519.SH, 000001.SZ
    - 公司名称: 贵州茅台, 智谱AI

    Args:
        query: 查询字符串（代码或名称）
        market: 市场代码，'cn' - A股，'hk' - 港股
        limit: 返回结果数量限制
        asset_type: 资产类型，默认 'CS' (股票)

    Returns:
        匹配结果列表，每个元素为:
        {
            'order_book_id': '600519.XSHG',
            'symbol': '贵州茅台',
            'abbrev_symbol': 'GZMT',
            'match_type': 'name_exact'
        }
    """
    manager = CodeIndexManager()
    return manager.search(query, market, limit, asset_type)


if __name__ == "__main__":
    import argparse
    import rqdatac

    parser = argparse.ArgumentParser(
        description="资产代码搜索工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
允许的市场代码:
  cn    A股 (中国内地市场)
  hk    港股 (香港市场)

允许的资产类型:
  CS          股票 (Common Stock)
  ETF         交易所交易基金
  Future      期货
  Option      期权
  Convertible 可转债
  INDX        指数
  LOF         上市型开放式基金
  FUND        基金

使用示例:
  python code_index_manager.py -q "贵州茅台" -m cn -t CS
  python code_index_manager.py --query "600519" --market cn
  python code_index_manager.py --query "腾讯" --market hk --type CS
        """,
    )
    parser.add_argument("--query", "-q", required=True, help="查询字符串（代码或名称）")
    parser.add_argument(
        "--market",
        "-m",
        default="cn",
        choices=["cn", "hk"],
        help="市场代码: cn (A股), hk (港股) [default: cn]",
    )
    parser.add_argument(
        "--type",
        "-t",
        default="CS",
        help="资产类型: CS(股票), ETF, Future, Option, Convertible, INDX [default: CS]",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=10,
        help="返回结果数量 [default: 10]",
    )

    args = parser.parse_args()

    rqdatac.init()

    manager = CodeIndexManager()
    results = manager.search(args.query, args.market, args.limit, args.type)

    if results:
        for r in results:
            print(f"{r['order_book_id']} | {r['symbol']} | {r['match_type']}")
    else:
        print("未找到匹配结果")
