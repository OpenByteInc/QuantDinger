#!/usr/bin/env python3
"""
API 索引管理器 - 快速定位 API 在文档中的位置
"""

from pathlib import Path
from typing import Dict, List, Optional
import re
import logging

# 配置日志（INFO级别）
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("APIIndexManager")


class APIIndexManager:
    """API 索引管理器"""

    def __init__(self, api_index_dir: Optional[str] = None):
        """
        初始化 API 索引管理器

        Args:
            api_index_dir: api_index 目录路径
        """
        if api_index_dir is None:
            skill_root = Path(__file__).parent.parent
            self.api_index_dir = skill_root / "cache" / "api_index"
        else:
            self.api_index_dir = Path(api_index_dir)

        logger.info(
            f"API Index Manager initialized with directory: {self.api_index_dir}"
        )

        # 内存缓存：{document_name: {api_name: line_info}}
        self.memory_cache: Dict[str, Dict[str, dict]] = {}

    def _parse_api_index_file(self, document_name: str) -> Dict[str, dict]:
        """解析 api_index 文件，构建 API 到行号的映射"""
        logger.info(f"Parsing API index for document: {document_name}")

        # 将文档名转换为索引文件名
        index_file_name = f"{document_name.replace('.md', '')}_index.md"
        index_file_path = self.api_index_dir / index_file_name

        if not index_file_path.exists():
            error_msg = f"API index file not found: {index_file_path}"
            logger.error(error_msg)
            raise FileNotFoundError(
                f"{error_msg}\nPlease run init_skill.py to generate API indices."
            )

        # 读取索引文件
        try:
            with open(index_file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            error_msg = f"Error reading index file {index_file_path}: {e}"
            logger.error(error_msg)
            raise IOError(error_msg)

        # 解析表格中的 API 信息
        api_mapping = {}
        table_pattern = re.compile(r"\|\s*`([^`]+)`\s*\|\s*([^\|]+)\|\s*(\d+)\s*\|")

        for line in content.splitlines():
            match = table_pattern.match(line)
            if match:
                api_name = match.group(1).strip()
                description = match.group(2).strip()
                line_number = int(match.group(3).strip())

                api_mapping[api_name] = {
                    "line_number": line_number,
                    "description": description,
                    "document_name": document_name,
                }

        logger.info(f"Parsed {len(api_mapping)} APIs from {document_name}")
        return api_mapping

    def get_api_location(self, api_name: str, document_name: str) -> dict:
        """获取 API 在文档中的位置（单个 API）"""
        logger.info(
            f"Getting location for API '{api_name}' in document '{document_name}'"
        )

        # 检查内存缓存
        if document_name not in self.memory_cache:
            self.memory_cache[document_name] = self._parse_api_index_file(document_name)

        doc_cache = self.memory_cache[document_name]

        # 查找 API
        if api_name in doc_cache:
            api_info = doc_cache[api_name]
            logger.info(f"Found API '{api_name}' at line {api_info['line_number']}")
            return api_info
        else:
            # 获取可用 API 列表（前 10 个）
            available_apis = list(doc_cache.keys())[:10]
            error_msg = (
                f"API '{api_name}' not found in document index '{document_name}'.\n"
                f"Available APIs in this document: {available_apis}...\n"
                f"Please check the API name or use full document search."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

    def get_batch_api_locations(
        self, api_names: List[str], document_name: str
    ) -> Dict[str, dict]:
        """批量获取多个 API 的位置"""
        logger.info(
            f"Batch getting locations for {len(api_names)} APIs in document '{document_name}'"
        )

        # 确保文档索引已加载
        if document_name not in self.memory_cache:
            self.memory_cache[document_name] = self._parse_api_index_file(document_name)

        doc_cache = self.memory_cache[document_name]
        result = {}
        missing_apis = []

        for api_name in api_names:
            if api_name in doc_cache:
                result[api_name] = doc_cache[api_name]
            else:
                missing_apis.append(api_name)

        if missing_apis:
            available_apis = list(doc_cache.keys())[:10]
            error_msg = (
                f"APIs not found in document index '{document_name}': {missing_apis}\n"
                f"Available APIs in this document: {available_apis}...\n"
                f"Please check the API names or use full document search."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info(f"Successfully found {len(result)} APIs")
        return result

    def list_apis(self, document_name: str) -> List[str]:
        """列出指定文档中的所有 API"""
        logger.info(f"Listing APIs for document '{document_name}'")

        if document_name not in self.memory_cache:
            self.memory_cache[document_name] = self._parse_api_index_file(document_name)

        apis = list(self.memory_cache[document_name].keys())
        logger.info(f"Found {len(apis)} APIs in document '{document_name}'")
        return apis

    def clear_cache(self, document_name: Optional[str] = None):
        """清除内存缓存"""
        if document_name:
            logger.info(f"Clearing cache for document '{document_name}'")
            self.memory_cache.pop(document_name, None)
        else:
            logger.info("Clearing all cache")
            self.memory_cache.clear()


if __name__ == "__main__":
    # 测试代码
    manager = APIIndexManager()

    try:
        # 测试单个 API 查找
        location = manager.get_api_location("get_price", "generic-api.md")
        print(f"API 'get_price' found at line {location['line_number']}")
        print(f"Description: {location['description']}")

        # 测试批量查找
        batch_locations = manager.get_batch_api_locations(
            ["get_price", "get_ticks", "current_snapshot"], "generic-api.md"
        )
        print(f"\nBatch lookup found {len(batch_locations)} APIs")
        for api_name, info in batch_locations.items():
            print(f"  {api_name}: line {info['line_number']}")

        # 测试列出所有 API
        apis = manager.list_apis("generic-api.md")
        print(f"\nTotal APIs in generic-api.md: {len(apis)}")

    except Exception as e:
        print(f"Error: {e}")
