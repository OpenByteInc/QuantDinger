#!/usr/bin/env python3
"""
RQData文档缓存管理器
提供缓存机制，优化文档访问速度
"""

import io
import re
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote
import logging

# 配置日志（INFO级别）
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("RQDataCacheManager")


INDEX_URL = "https://www.ricequant.com/doc/document-index.txt"
DEFAULT_CACHE_DAYS = 7


class RQDataCacheManager:
    """RQData文档缓存管理器"""

    def __init__(self, cache_dir: Optional[str] = None):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录路径，默认为skill的cache/api_docs目录
        """
        if cache_dir is None:
            skill_root = Path(__file__).parent.parent
            self.cache_dir = skill_root / "cache" / "api_docs"
        else:
            self.cache_dir = Path(cache_dir)

        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _extract_filename_from_url(self, url: str) -> str:
        """从URL提取文件名"""
        filename = url.split("/")[-1]
        if not filename:
            raise ValueError(f"URL does not contain a filename: {url}")

        clean_filename = filename.split("?")[0].split("#")[0]
        decoded_filename = unquote(clean_filename)

        if not re.match(r"^[a-zA-Z0-9\-_.]+$", decoded_filename):
            raise ValueError(
                f"Invalid filename contains special characters: {decoded_filename}"
            )

        return decoded_filename

    def _is_cache_expired(
        self, cache_path: Path, max_age_days: int = DEFAULT_CACHE_DAYS
    ) -> bool:
        """检查缓存文件是否过期（基于文件修改时间）"""
        if not cache_path.exists():
            return True

        file_mtime = cache_path.stat().st_mtime
        file_age = time.time() - file_mtime
        max_age_seconds = max_age_days * 24 * 60 * 60

        return file_age > max_age_seconds

    def _get_cache_path(self, url: str) -> Path:
        """根据URL生成缓存文件路径，使用真实文件名"""
        decoded_filename = self._extract_filename_from_url(url)
        cache_path = self.cache_dir / decoded_filename
        if cache_path.exists():
            raise FileExistsError(f"Cache file already exists: {cache_path}")
        return cache_path

    def _get_cache_path_for_read(self, url: str) -> Optional[Path]:
        """获取缓存文件路径（用于读取，不检查冲突）"""
        try:
            decoded_filename = self._extract_filename_from_url(url)
        except ValueError:
            return None

        cache_path = self.cache_dir / decoded_filename
        if not cache_path.exists():
            return None

        return cache_path

    def get_cached_content(self, url: str) -> Optional[str]:
        """
        获取缓存的文档内容

        Args:
            url: 文档URL

        Returns:
            缓存的文档内容，如果缓存不存在则返回None
        """
        cache_path = self._get_cache_path_for_read(url)
        if cache_path is None:
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read()
        except IOError:
            return None

    def save_to_cache(
        self, url: str, content: str, allow_overwrite: bool = False
    ) -> None:
        """
        保存文档内容到缓存

        Args:
            url: 文档URL
            content: 文档内容
            allow_overwrite: 是否允许覆盖现有文件
        """
        decoded_filename = self._extract_filename_from_url(url)
        cache_path = self.cache_dir / decoded_filename

        if cache_path.exists():
            if not allow_overwrite:
                raise FileExistsError(f"Cache file already exists: {cache_path}")
            cache_path.unlink()

        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(content)

    def fetch_document(self, url: str, timeout: int = 60, retries: int = 3) -> str:
        """
        使用curl获取文档内容，支持重试

        Args:
            url: 文档URL
            timeout: 超时时间（秒），默认60秒
            retries: 重试次数，默认3次

        Returns:
            文档内容

        Raises:
            RuntimeError: 如果获取失败
        """
        last_error = None

        for attempt in range(retries):
            try:
                result = subprocess.run(
                    ["curl", "-s", "-L", "--max-time", str(timeout), url],
                    capture_output=True,
                    timeout=timeout + 5,
                    encoding="utf-8",
                    errors="replace",
                )

                if (
                    result.returncode == 0
                    and result.stdout
                    and len(result.stdout) > 100
                ):
                    return result.stdout
                else:
                    last_error = (
                        f"curl failed with code {result.returncode} or empty response"
                    )
                    if attempt < retries - 1:
                        continue
                    raise RuntimeError(f"Failed to fetch document: {url}")

            except subprocess.TimeoutExpired:
                last_error = f"Timeout after {timeout}s"
                if attempt < retries - 1:
                    continue
                raise RuntimeError(f"Timeout fetching document: {url}")
            except Exception as e:
                last_error = str(e)
                if attempt < retries - 1:
                    continue
                raise RuntimeError(f"Error fetching document: {url}, {str(e)}")

        raise RuntimeError(f"Failed after {retries} attempts: {last_error}")

    def _parse_index_content(self, content: str) -> list[str]:
        """解析索引文档内容，提取URL列表"""
        import re

        urls = []
        url_pattern = re.compile(
            r"https://www\.ricequant\.com/doc/sources/rqdata/python/[^\)]+\.md"
        )

        for line in content.splitlines():
            matches = url_pattern.findall(line)
            for url in matches:
                if url not in urls:
                    urls.append(url)

        return urls

    def fetch_document_index(self, max_age_days: int = DEFAULT_CACHE_DAYS) -> list[str]:
        """
        获取文档索引列表（带过期检查）

        Args:
            max_age_days: 索引缓存的最大天数

        Returns:
            URL列表

        Raises:
            RuntimeError: 如果获取失败且没有可用缓存
        """
        cache_path = self._get_cache_path_for_read(INDEX_URL)

        if cache_path and not self._is_cache_expired(cache_path, max_age_days):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    index_content = f.read()
                return self._parse_index_content(index_content)
            except IOError:
                pass

        try:
            index_content = self.fetch_document(INDEX_URL)
            self.save_to_cache(INDEX_URL, index_content, allow_overwrite=True)
            return self._parse_index_content(index_content)
        except RuntimeError as e:
            if cache_path and cache_path.exists():
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        index_content = f.read()
                    print(f"Warning: Using expired index cache due to fetch error: {e}")
                    return self._parse_index_content(index_content)
                except IOError:
                    pass
            raise

    def _build_doc_name_map(self, max_age_days: int = DEFAULT_CACHE_DAYS) -> dict:
        """构建文档名到URL的映射（从索引动态生成）"""
        urls = self.fetch_document_index(max_age_days)
        mapping = {}
        for url in urls:
            filename = url.split("/")[-1]
            if filename:
                mapping[filename] = url
        return mapping

    def get_document(
        self,
        url: str,
        force_refresh: bool = False,
        max_age_days: int = DEFAULT_CACHE_DAYS,
    ) -> str:
        """
        获取文档内容（优先使用缓存，带过期检查）

        Args:
            url: 文档URL
            force_refresh: 是否强制刷新缓存
            max_age_days: 缓存的最大天数

        Returns:
            文档内容
        """
        cache_path = self._get_cache_path_for_read(url)

        if (
            not force_refresh
            and cache_path
            and not self._is_cache_expired(cache_path, max_age_days)
        ):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return f.read()
            except IOError:
                pass

        try:
            content = self.fetch_document(url)
            self.save_to_cache(url, content, allow_overwrite=True)
            return content
        except RuntimeError as e:
            if cache_path and cache_path.exists():
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    print(f"Warning: Using cached version due to fetch error: {e}")
                    return content
                except IOError:
                    pass
            raise

    def get_document_by_name(
        self,
        doc_name: str,
        force_refresh: bool = False,
        max_age_days: int = DEFAULT_CACHE_DAYS,
    ) -> str:
        """
        根据文档名获取文档内容（带过期检查）

        Args:
            doc_name: 文档名（如 "stock-mod.md"）
            force_refresh: 是否强制刷新缓存
            max_age_days: 缓存的最大天数

        Returns:
            文档内容
        """
        doc_map = self._build_doc_name_map(max_age_days)

        if doc_name not in doc_map:
            available = list(doc_map.keys())
            raise ValueError(
                f"Unknown document: {doc_name}\n"
                f"Available documents: {', '.join(available)}"
            )

        url = doc_map[doc_name]
        return self.get_document(url, force_refresh, max_age_days)

    def list_documents(self, max_age_days: int = DEFAULT_CACHE_DAYS) -> list[str]:
        """
        获取所有可用文档名称列表

        Args:
            max_age_days: 索引缓存的最大天数

        Returns:
            文档名称列表
        """
        doc_map = self._build_doc_name_map(max_age_days)
        return list(doc_map.keys())

    def clear_cache(self, url: Optional[str] = None) -> int:
        """
        清理缓存

        Args:
            url: 如果指定，只清理该URL的缓存；否则清理所有缓存

        Returns:
            清理的文件数量
        """
        if url:
            cache_path = self._get_cache_path_for_read(url)
            if cache_path is not None and cache_path.exists():
                cache_path.unlink()
                return 1
            return 0
        else:
            count = 0
            for cache_file in self.cache_dir.glob("*"):
                if cache_file.is_file():
                    cache_file.unlink()
                    count += 1
            return count

    def clear_all_cache(self) -> int:
        """
        清理所有缓存文件

        Returns:
            清理的文件数量
        """
        return self.clear_cache()

    def get_cache_info(self) -> dict:
        """
        获取缓存统计信息

        Returns:
            缓存统计信息字典
        """
        cache_files = [f for f in self.cache_dir.glob("*") if f.is_file()]
        total_count = len(cache_files)
        total_size = 0

        for cache_file in cache_files:
            try:
                file_size = cache_file.stat().st_size
                total_size += file_size
            except IOError:
                continue

        return {
            "cache_dir": str(self.cache_dir),
            "total_count": total_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
        }

    def read_document_lines(
        self, document_name: str, start_line: int, end_line: Optional[int] = None
    ) -> str:
        """读取文档的特定行范围"""
        logger.info(
            f"Reading lines {start_line}-{end_line or start_line} from document '{document_name}'"
        )

        cache_path = self.cache_dir / document_name

        if not cache_path.exists():
            error_msg = f"Document not found in cache: {document_name}"
            logger.error(error_msg)
            raise FileNotFoundError(f"{error_msg}\nCache directory: {self.cache_dir}")

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            error_msg = f"Error reading document '{document_name}': {e}"
            logger.error(error_msg)
            raise IOError(error_msg)

        start_idx = max(0, start_line - 1)
        end_idx = end_line if end_line else start_line
        end_idx = min(len(lines), end_idx)

        selected_lines = lines[start_idx:end_idx]
        content = "".join(selected_lines)

        logger.info(f"Extracted {len(selected_lines)} lines")
        return content

    def get_api_definition(
        self,
        api_name: str,
        document_name: str,
        context_lines: int = 100,
        api_index_manager: Optional[object] = None,
    ) -> dict:
        """获取 API 定义（使用 api_index 优化）"""
        logger.info(
            f"Getting API definition for '{api_name}' in document '{document_name}'"
        )

        if api_index_manager is None:
            from api_index_manager import APIIndexManager

            api_index_manager = APIIndexManager()

        try:
            # type: ignore - APIIndexManager 会在运行时正确导入
            api_info = api_index_manager.get_api_location(api_name, document_name)  # type: ignore
            line_number = api_info["line_number"]
            logger.info(f"API '{api_name}' found at line {line_number}")
        except (ValueError, FileNotFoundError) as e:
            logger.error(f"Failed to get API location: {e}")
            raise

        start_line = max(1, line_number - context_lines // 2)
        end_line = line_number + context_lines // 2

        try:
            content = self.read_document_lines(document_name, start_line, end_line)
            logger.info(
                f"Successfully read {len(content)} characters for API '{api_name}'"
            )
        except (FileNotFoundError, IOError) as e:
            logger.error(f"Failed to read document lines: {e}")
            raise

        return {
            "api_name": api_name,
            "document_name": document_name,
            "line_number": line_number,
            "description": api_info.get("description", ""),
            "content": content,
            "context_range": (start_line, end_line),
        }

    def find_factor_download_link(
        self, doc_content: str, api_start_line: int = 0
    ) -> Optional[str]:
        """从API文档的特定API部分查找宏观因子xlsx下载链接"""
        lines = doc_content.split("\n")
        search_start = api_start_line if api_start_line > 0 else 0

        for i, line in enumerate(lines[search_start:], search_start):
            if ("factors" in line.lower() or "宏观因子" in line) and ".xlsx" in line:
                match = re.search(r'https://[^\s"\'<>]+\.xlsx', line)
                if match:
                    url = match.group(0)
                    logger.info(f"Found factor download link at line {i + 1}: {url}")
                    return url

        logger.warning("No factor download link found in document")
        return None

    def fetch_binary_file(
        self, url: str, output_name: str, timeout: int = 60, retries: int = 3
    ) -> Path:
        """下载二进制文件（xlsx等）"""
        output_path = self.cache_dir / output_name

        last_error = None
        for attempt in range(retries):
            try:
                result = subprocess.run(
                    [
                        "curl",
                        "-s",
                        "-L",
                        "--max-time",
                        str(timeout),
                        "-o",
                        str(output_path),
                        url,
                    ],
                    capture_output=True,
                    timeout=timeout + 5,
                    encoding="utf-8",
                    errors="replace",
                )

                if (
                    result.returncode == 0
                    and output_path.exists()
                    and output_path.stat().st_size > 0
                ):
                    logger.info(
                        f"Successfully downloaded {output_name} ({output_path.stat().st_size} bytes)"
                    )
                    return output_path
                else:
                    last_error = f"curl failed with code {result.returncode}"
                    if output_path.exists():
                        output_path.unlink()
                    if attempt < retries - 1:
                        continue

            except subprocess.TimeoutExpired:
                last_error = f"Timeout after {timeout}s"
                if output_path.exists():
                    output_path.unlink()
                if attempt < retries - 1:
                    continue
            except Exception as e:
                last_error = str(e)
                if output_path.exists():
                    output_path.unlink()
                if attempt < retries - 1:
                    continue

        raise RuntimeError(
            f"Failed to download binary file after {retries} attempts: {last_error}"
        )

    def convert_xlsx_to_csv(
        self, xlsx_path: Path, csv_name: Optional[str] = None
    ) -> Path:
        """将xlsx文件转换为CSV"""
        if not xlsx_path.exists():
            raise FileNotFoundError(f"Xlsx file not found: {xlsx_path}")

        if csv_name is None:
            csv_name = xlsx_path.stem + ".csv"
        csv_path = self.cache_dir / csv_name

        try:
            import pandas as pd

            df = pd.read_excel(xlsx_path, engine="openpyxl")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            logger.info(
                f"Converted {xlsx_path.name} to {csv_name} ({csv_path.stat().st_size} bytes)"
            )
            return csv_path

        except ImportError:
            raise ImportError("pandas and openpyxl are required for xlsx conversion")
        except Exception as e:
            raise RuntimeError(f"Failed to convert xlsx to csv: {e}")

    def download_and_convert_factor_file(
        self, doc_name: str = "macro-economy.md"
    ) -> Optional[Path]:
        """下载并转换宏观因子名称文件"""
        cache_path = self.cache_dir / doc_name

        if not cache_path.exists():
            logger.warning(f"Document not found: {doc_name}")
            return None

        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read()

        link = self.find_factor_download_link(content, api_start_line=87)
        if not link:
            logger.warning("No factor download link found in macro-economy.md")
            return None

        xlsx_path = self.fetch_binary_file(link, "macro_factor_names.xlsx")
        csv_path = self.convert_xlsx_to_csv(xlsx_path, "macro_factor_names.csv")

        if xlsx_path.exists():
            xlsx_path.unlink()
            logger.info(f"Removed temporary xlsx file: {xlsx_path.name}")

        return csv_path


if __name__ == "__main__":
    cache_mgr = RQDataCacheManager()

    test_url = "https://www.ricequant.com/doc/sources/rqdata/python/stock-mod.md"

    content = cache_mgr.get_document(test_url)
    print(f"Document length: {len(content)}")

    info = cache_mgr.get_cache_info()
    print(f"\nCache Info:")
    print(f"  Total files: {info['total_count']}")
    print(f"  Total size: {info['total_size_mb']} MB")

    cleared = cache_mgr.clear_cache()
    print(f"\nCleared {cleared} cache files")
