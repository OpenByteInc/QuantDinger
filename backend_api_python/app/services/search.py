"""
Search service v2.0 - 增强版搜索服务
整合多个搜索引擎，支持 API Key 轮换和故障转移

支持的搜索引擎（按优先级）：
1. Tavily - 专为AI设计，免费1000次/月
2. SearXNG - 自建聚合搜索服务
3. GDELT - 免费全球新闻兜底
4. SerpAPI - Google/Bing 结果抓取
5. Google CSE - 自定义搜索引擎
6. Bing Search API
7. DuckDuckGo - 免费兜底

参考：daily_stock_analysis-main/src/search_service.py
"""
import os
import requests
import json
import time
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from itertools import cycle
from urllib.parse import urlparse

from app.utils.logger import get_logger
from app.utils.config_loader import load_addon_config
from app.config.data_sources import AlphaVantageConfig, GDELTConfig, SearXNGConfig

logger = get_logger(__name__)

# Track Google API quota status
_google_quota_exhausted = False
_google_quota_reset_time = 0


@dataclass
class SearchResult:
    """搜索结果数据类"""
    title: str
    snippet: str  # 摘要
    url: str
    source: str  # 来源网站
    published_date: Optional[str] = None
    sentiment: str = 'neutral'  # 情绪标签
    
    def to_text(self) -> str:
        """转换为文本格式"""
        date_str = f" ({self.published_date})" if self.published_date else ""
        return f"【{self.source}】{self.title}{date_str}\n{self.snippet}"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'title': self.title,
            'link': self.url,
            'snippet': self.snippet,
            'source': self.source,
            'published': self.published_date or '',
            'sentiment': self.sentiment,
        }


@dataclass 
class SearchResponse:
    """搜索响应"""
    query: str
    results: List[SearchResult]
    provider: str  # 使用的搜索引擎
    success: bool = True
    error_message: Optional[str] = None
    search_time: float = 0.0  # 搜索耗时（秒）
    
    def to_context(self, max_results: int = 5) -> str:
        """将搜索结果转换为可用于 AI 分析的上下文"""
        if not self.success or not self.results:
            return f"搜索 '{self.query}' 未找到相关结果。"
        
        lines = [f"【{self.query} 搜索结果】（来源：{self.provider}）"]
        for i, result in enumerate(self.results[:max_results], 1):
            lines.append(f"\n{i}. {result.to_text()}")
        
        return "\n".join(lines)
    
    def to_list(self) -> List[Dict[str, Any]]:
        """转换为列表格式（兼容旧接口）"""
        return [r.to_dict() for r in self.results]


class BaseSearchProvider(ABC):
    """搜索引擎基类"""
    
    def __init__(self, api_keys: List[str], name: str):
        """
        初始化搜索引擎
        
        Args:
            api_keys: API Key 列表（支持多个 key 负载均衡）
            name: 搜索引擎名称
        """
        self._api_keys = api_keys
        self._name = name
        self._key_cycle = cycle(api_keys) if api_keys else None
        self._key_usage: Dict[str, int] = {key: 0 for key in api_keys}
        self._key_errors: Dict[str, int] = {key: 0 for key in api_keys}
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def is_available(self) -> bool:
        """检查是否有可用的 API Key"""
        return bool(self._api_keys)
    
    def _get_next_key(self) -> Optional[str]:
        """
        获取下一个可用的 API Key（负载均衡）
        
        策略：轮询 + 跳过错误过多的 key
        """
        if not self._key_cycle:
            return None
        
        for _ in range(len(self._api_keys)):
            key = next(self._key_cycle)
            if self._key_errors.get(key, 0) < 3:
                return key
        
        logger.warning(f"[{self._name}] 所有 API Key 都有错误记录，重置错误计数")
        self._key_errors = {key: 0 for key in self._api_keys}
        return self._api_keys[0] if self._api_keys else None
    
    def _record_success(self, key: str) -> None:
        """记录成功使用"""
        self._key_usage[key] = self._key_usage.get(key, 0) + 1
        if key in self._key_errors and self._key_errors[key] > 0:
            self._key_errors[key] -= 1
    
    def _record_error(self, key: str) -> None:
        """记录错误"""
        self._key_errors[key] = self._key_errors.get(key, 0) + 1
        logger.warning(f"[{self._name}] API Key {key[:8]}... 错误计数: {self._key_errors[key]}")
    
    @abstractmethod
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行搜索（子类实现）"""
        pass
    
    def search(self, query: str, max_results: int = 5, days: int = 7) -> SearchResponse:
        """
        执行搜索
        
        Args:
            query: 搜索关键词
            max_results: 最大返回结果数
            days: 搜索最近几天的时间范围（默认7天）
            
        Returns:
            SearchResponse 对象
        """
        api_key = self._get_next_key()
        if not api_key:
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=f"{self._name} 未配置 API Key"
            )
        
        start_time = time.time()
        try:
            response = self._do_search(query, api_key, max_results, days=days)
            response.search_time = time.time() - start_time
            
            if response.success:
                self._record_success(api_key)
                logger.info(f"[{self._name}] 搜索 '{query}' 成功，返回 {len(response.results)} 条结果，耗时 {response.search_time:.2f}s")
            else:
                self._record_error(api_key)
            
            return response
            
        except Exception as e:
            self._record_error(api_key)
            elapsed = time.time() - start_time
            logger.error(f"[{self._name}] 搜索 '{query}' 失败: {e}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=str(e),
                search_time=elapsed
            )
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 提取域名作为来源"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.replace('www.', '')
            return domain or '未知来源'
        except:
            return '未知来源'


class TavilySearchProvider(BaseSearchProvider):
    """
    Tavily 搜索引擎
    
    特点：
    - 专为 AI/LLM 优化的搜索 API
    - 免费版每月 1000 次请求
    - 返回结构化的搜索结果
    
    文档：https://docs.tavily.com/
    """
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "Tavily")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 Tavily 搜索"""
        try:
            from tavily import TavilyClient
        except ImportError:
            return self._do_search_rest(query, api_key, max_results, days)
        
        try:
            client = TavilyClient(api_key=api_key)
            
            response = client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                include_answer=False,
                include_raw_content=False,
                days=days,
            )
            
            results = []
            for item in response.get('results', []):
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('content', '')[:500],
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=item.get('published_date'),
                ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            error_msg = str(e)
            if 'rate limit' in error_msg.lower() or 'quota' in error_msg.lower():
                error_msg = f"API 配额已用尽: {error_msg}"
            
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=error_msg
            )
    
    def _do_search_rest(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """使用 REST API 执行 Tavily 搜索（备选方案）"""
        try:
            url = "https://api.tavily.com/search"
            headers = {
                'Content-Type': 'application/json',
            }
            payload = {
                'api_key': api_key,
                'query': query,
                'search_depth': 'advanced',
                'max_results': max_results,
                'include_answer': False,
                'include_raw_content': False,
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            
            if response.status_code != 200:
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text}"
                )
            
            data = response.json()
            results = []
            for item in data.get('results', []):
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('content', '')[:500],
                    url=item.get('url', ''),
                    source=self._extract_domain(item.get('url', '')),
                    published_date=item.get('published_date'),
                ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )


class SerpAPISearchProvider(BaseSearchProvider):
    """
    SerpAPI 搜索引擎
    
    特点：
    - 支持 Google、Bing、百度等多种搜索引擎
    - 免费版每月 100 次请求
    
    文档：https://serpapi.com/
    """
    
    def __init__(self, api_keys: List[str]):
        super().__init__(api_keys, "SerpAPI")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 SerpAPI 搜索"""
        try:
            from serpapi import GoogleSearch
        except ImportError:
            return self._do_search_rest(query, api_key, max_results, days)
        
        try:
            tbs = "qdr:w"
            if days <= 1:
                tbs = "qdr:d"
            elif days <= 7:
                tbs = "qdr:w"
            elif days <= 30:
                tbs = "qdr:m"
            else:
                tbs = "qdr:y"

            params = {
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "google_domain": "google.com.hk",
                "hl": "zh-cn",
                "gl": "cn",
                "tbs": tbs,
                "num": max_results
            }
            
            search = GoogleSearch(params)
            response = search.get_dict()
            
            results = []
            organic_results = response.get('organic_results', [])

            for item in organic_results[:max_results]:
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('snippet', '')[:500],
                    url=item.get('link', ''),
                    source=item.get('source', self._extract_domain(item.get('link', ''))),
                    published_date=item.get('date'),
                ))

            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )
    
    def _do_search_rest(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """使用 REST API 执行 SerpAPI 搜索"""
        try:
            tbs = "qdr:w"
            if days <= 1:
                tbs = "qdr:d"
            elif days <= 7:
                tbs = "qdr:w"
            elif days <= 30:
                tbs = "qdr:m"
            
            url = "https://serpapi.com/search"
            params = {
                "engine": "google",
                "q": query,
                "api_key": api_key,
                "hl": "zh-cn",
                "gl": "cn",
                "tbs": tbs,
                "num": max_results
            }
            
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code != 200:
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=f"HTTP {response.status_code}"
                )
            
            data = response.json()
            results = []
            
            for item in data.get('organic_results', [])[:max_results]:
                results.append(SearchResult(
                    title=item.get('title', ''),
                    snippet=item.get('snippet', '')[:500],
                    url=item.get('link', ''),
                    source=self._extract_domain(item.get('link', '')),
                    published_date=item.get('date'),
                ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )


class GoogleSearchProvider(BaseSearchProvider):
    """Google Custom Search (CSE) 搜索引擎"""
    
    def __init__(self, api_key: str, cx: str):
        super().__init__([api_key] if api_key else [], "Google")
        self._cx = cx
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 Google 搜索"""
        global _google_quota_exhausted, _google_quota_reset_time
        
        if not self._cx:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="Google Search 未配置 CX"
            )
        
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                'key': api_key,
                'cx': self._cx,
                'q': query,
                'num': min(max_results, 10),
            }
            
            if days <= 1:
                params['dateRestrict'] = 'd1'
            elif days <= 7:
                params['dateRestrict'] = 'w1'
            elif days <= 30:
                params['dateRestrict'] = 'm1'
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 429:
                _google_quota_exhausted = True
                import datetime
                tomorrow = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
                _google_quota_reset_time = tomorrow.timestamp()
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message="Google API 配额已用尽"
                )
            
            response.raise_for_status()
            data = response.json()
            
            results = []
            if 'items' in data:
                for item in data['items']:
                    results.append(SearchResult(
                        title=item.get('title', ''),
                        snippet=item.get('snippet', ''),
                        url=item.get('link', ''),
                        source='Google',
                        published_date=item.get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', ''),
                    ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )


class BingSearchProvider(BaseSearchProvider):
    """Bing Search API 搜索引擎"""
    
    def __init__(self, api_key: str):
        super().__init__([api_key] if api_key else [], "Bing")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 Bing 搜索"""
        try:
            url = "https://api.bing.microsoft.com/v7.0/search"
            headers = {"Ocp-Apim-Subscription-Key": api_key}
            params = {
                "q": query,
                "count": max_results,
                "textDecorations": True,
                "textFormat": "HTML"
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            if 'webPages' in data and 'value' in data['webPages']:
                for item in data['webPages']['value']:
                    results.append(SearchResult(
                        title=item.get('name', ''),
                        snippet=item.get('snippet', ''),
                        url=item.get('url', ''),
                        source='Bing',
                        published_date=item.get('datePublished', ''),
                    ))
            
            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=True,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )


class GDELTSearchProvider(BaseSearchProvider):
    """Free global news fallback backed by the GDELT DOC 2.0 API."""

    def __init__(self):
        super().__init__(['free'], "GDELT")

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        try:
            params = {
                "query": query,
                "mode": "artlist",
                "format": "json",
                "sort": "datedesc",
                "maxrecords": min(max(max_results, 1), 50),
                "timespan": f"{max(1, min(int(days or 7), 90))}d",
            }
            response = requests.get(GDELTConfig.BASE_URL, params=params, timeout=GDELTConfig.TIMEOUT)
            response.raise_for_status()
            data = response.json()
            articles = data.get("articles") if isinstance(data, dict) else []
            results: List[SearchResult] = []
            for item in articles[:max_results]:
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or ""
                results.append(SearchResult(
                    title=item.get("title") or url or query,
                    snippet=item.get("seendate") or "",
                    url=url,
                    source=item.get("domain") or self._extract_domain(url) or "GDELT",
                    published_date=item.get("seendate") or "",
                ))
            return SearchResponse(query=query, results=results, provider=self.name, success=bool(results))
        except Exception as e:
            return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=str(e))


class SearXNGSearchProvider(BaseSearchProvider):
    """Self-hosted SearXNG metasearch provider."""

    def __init__(self):
        super().__init__(['free'], "SearXNG")

    @property
    def is_available(self) -> bool:
        return bool(SearXNGConfig.BASE_URL)

    def _search_endpoint(self) -> str:
        base_url = SearXNGConfig.BASE_URL.rstrip('/')
        if base_url.endswith('/search'):
            return base_url
        return f"{base_url}/search"

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        if not SearXNGConfig.BASE_URL:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="SearXNG 未配置 Base URL",
            )

        try:
            params = {
                "q": query,
                "format": "json",
                "pageno": 1,
            }
            if SearXNGConfig.CATEGORIES:
                params["categories"] = SearXNGConfig.CATEGORIES
            if SearXNGConfig.ENGINES:
                params["engines"] = SearXNGConfig.ENGINES
            if SearXNGConfig.LANGUAGE and SearXNGConfig.LANGUAGE != "auto":
                params["language"] = SearXNGConfig.LANGUAGE

            headers = {
                "Accept": "application/json",
                "User-Agent": "QuantDinger/4.0 SearXNGSearchProvider",
            }
            response = requests.get(
                self._search_endpoint(),
                params=params,
                headers=headers,
                timeout=SearXNGConfig.TIMEOUT,
            )

            if response.status_code != 200:
                return SearchResponse(
                    query=query,
                    results=[],
                    provider=self.name,
                    success=False,
                    error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                )

            data = response.json()
            raw_results = data.get("results") if isinstance(data, dict) else []
            results: List[SearchResult] = []
            for item in raw_results or []:
                if len(results) >= max_results:
                    break
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or ""
                title = item.get("title") or url or query
                if not url:
                    continue
                published = item.get("publishedDate") or item.get("published_date") or ""
                source = item.get("engine") or item.get("category") or self._extract_domain(url) or "SearXNG"
                results.append(SearchResult(
                    title=title,
                    snippet=(item.get("content") or item.get("snippet") or "")[:500],
                    url=url,
                    source=source,
                    published_date=published,
                ))

            return SearchResponse(
                query=query,
                results=results,
                provider=self.name,
                success=bool(results),
                error_message=None if results else "SearXNG 未返回结果",
            )

        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e),
            )


class AlphaVantageNewsProvider(BaseSearchProvider):
    """Company news and sentiment supplement backed by Alpha Vantage NEWS_SENTIMENT."""

    def __init__(self, api_key: str):
        super().__init__([api_key] if api_key else [], "AlphaVantage")

    def _extract_tickers(self, query: str) -> List[str]:
        blocked = {"AI", "API", "CPI", "GDP", "ETF", "USD", "HK", "US", "CN"}
        tokens = re.findall(r"\b[A-Z]{1,6}(?:/[A-Z]{2,6})?\b", query or "")
        tickers: List[str] = []
        for token in tokens:
            symbol = token.replace("/", "")
            if symbol in blocked:
                continue
            if symbol not in tickers:
                tickers.append(symbol)
        return tickers[:5]

    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        tickers = self._extract_tickers(query)
        if not tickers:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="No ticker-like symbol found for Alpha Vantage NEWS_SENTIMENT",
            )

        try:
            params = {
                "function": "NEWS_SENTIMENT",
                "tickers": ",".join(tickers),
                "sort": "LATEST",
                "limit": min(max(max_results, 1), AlphaVantageConfig.NEWS_LIMIT),
                "apikey": api_key,
            }
            response = requests.get(AlphaVantageConfig.BASE_URL, params=params, timeout=AlphaVantageConfig.TIMEOUT)
            response.raise_for_status()
            data = response.json()
            feed = data.get("feed") if isinstance(data, dict) else []
            results: List[SearchResult] = []
            for item in feed[:max_results]:
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or ""
                sentiment = str(item.get("overall_sentiment_label") or "neutral").lower()
                summary = item.get("summary") or ""
                results.append(SearchResult(
                    title=item.get("title") or query,
                    snippet=summary[:500],
                    url=url,
                    source=item.get("source") or self._extract_domain(url) or "Alpha Vantage",
                    published_date=item.get("time_published") or "",
                    sentiment=sentiment,
                ))
            return SearchResponse(query=query, results=results, provider=self.name, success=bool(results))
        except Exception as e:
            return SearchResponse(query=query, results=[], provider=self.name, success=False, error_message=str(e))


class DuckDuckGoSearchProvider(BaseSearchProvider):
    """DuckDuckGo 搜索引擎（免费，无需 API Key）"""
    
    def __init__(self):
        super().__init__(['free'], "DuckDuckGo")
    
    def _do_search(self, query: str, api_key: str, max_results: int, days: int = 7) -> SearchResponse:
        """执行 DuckDuckGo 搜索"""
        try:
            url = "https://api.duckduckgo.com/"
            params = {
                'q': query,
                'format': 'json',
                'no_html': 1,
                'skip_disambig': 1
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            
            related_topics = data.get('RelatedTopics', [])
            for topic in related_topics[:max_results]:
                if isinstance(topic, dict):
                    if 'FirstURL' in topic:
                        results.append(SearchResult(
                            title=topic.get('Text', '')[:100],
                            snippet=topic.get('Text', ''),
                            url=topic.get('FirstURL', ''),
                            source='DuckDuckGo',
                        ))
                    elif 'Topics' in topic:
                        for sub_topic in topic['Topics']:
                            if len(results) >= max_results:
                                break
                            if 'FirstURL' in sub_topic:
                                results.append(SearchResult(
                                    title=sub_topic.get('Text', '')[:100],
                                    snippet=sub_topic.get('Text', ''),
                                    url=sub_topic.get('FirstURL', ''),
                                    source='DuckDuckGo',
                                ))
            
            if data.get('AbstractURL') and len(results) < max_results:
                results.insert(0, SearchResult(
                    title=data.get('Heading', query),
                    snippet=data.get('AbstractText', ''),
                    url=data.get('AbstractURL', ''),
                    source='DuckDuckGo',
                ))
            
            if not results:
                results = self._search_html(query, max_results)
            
            return SearchResponse(
                query=query,
                results=results[:max_results],
                provider=self.name,
                success=len(results) > 0,
            )
            
        except Exception as e:
            return SearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message=str(e)
            )
    
    def _search_html(self, query: str, max_results: int) -> List[SearchResult]:
        """DuckDuckGo HTML 搜索备选"""
        try:
            url = "https://lite.duckduckgo.com/lite/"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            data = {'q': query}
            
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            
            results = []
            html = response.text
            
            link_pattern = r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>'
            snippet_pattern = r'<td[^>]*class="result-snippet"[^>]*>([^<]*)</td>'
            
            links = re.findall(link_pattern, html)
            snippets = re.findall(snippet_pattern, html)
            
            for i, (link, title) in enumerate(links[:max_results]):
                snippet = snippets[i] if i < len(snippets) else ''
                if link and title:
                    results.append(SearchResult(
                        title=title.strip(),
                        snippet=snippet.strip(),
                        url=link,
                        source='DuckDuckGo',
                    ))
            
            return results
            
        except Exception as e:
            logger.debug(f"DuckDuckGo HTML search failed: {e}")
            return []


class SearchService:
    """
    搜索服务
    
    功能：
    1. 管理多个搜索引擎
    2. 自动故障转移
    3. 结果聚合和格式化
    """
    
    def __init__(self):
        self._providers: List[BaseSearchProvider] = []
        self._config = {}
        self._load_config()
        self._init_providers()
    
    def _load_config(self):
        """加载配置"""
        config = load_addon_config()
        self._config = config.get('search', {})
        self.provider = str(self._config.get('provider') or os.getenv('SEARCH_PROVIDER') or 'tavily').strip().lower()
        self.max_results = int(self._config.get('max_results', 10))

    def _search_config_value(self, section: str, key: str, env_name: str = '') -> str:
        section_cfg = self._config.get(section, {}) if isinstance(self._config, dict) else {}
        value = section_cfg.get(key) if isinstance(section_cfg, dict) else None
        if not value and env_name:
            value = os.getenv(env_name)
        return str(value or '').strip()
    
    def _init_providers(self):
        """Initialize search providers in the configured research fallback order."""
        from app.config import APIKeys
        if self.provider in {"none", "off", "disabled"}:
            logger.info("Search is disabled by SEARCH_PROVIDER=none")
            return

        provider_map: Dict[str, BaseSearchProvider] = {}

        tavily_keys = APIKeys.TAVILY_API_KEYS
        if tavily_keys:
            provider_map["tavily"] = TavilySearchProvider(tavily_keys)
            logger.info(f"Tavily search is configured with {len(tavily_keys)} API key(s)")

        provider_map["gdelt"] = GDELTSearchProvider()

        if SearXNGConfig.CONFIGURED:
            provider_map["searxng"] = SearXNGSearchProvider()
            logger.info("SearXNG search is configured")

        serpapi_keys = APIKeys.SERPAPI_KEYS
        if serpapi_keys:
            provider_map["serpapi"] = SerpAPISearchProvider(serpapi_keys)
            logger.info(f"SerpAPI search is configured with {len(serpapi_keys)} API key(s)")

        alpha_key = APIKeys.ALPHA_VANTAGE_API_KEY
        if alpha_key:
            provider_map["alpha_vantage"] = AlphaVantageNewsProvider(alpha_key)
            logger.info("Alpha Vantage NEWS_SENTIMENT is configured")

        google_api_key = self._search_config_value('google', 'api_key', 'SEARCH_GOOGLE_API_KEY')
        google_cx = self._search_config_value('google', 'cx', 'SEARCH_GOOGLE_CX')
        if google_api_key and google_cx:
            provider_map["google"] = GoogleSearchProvider(google_api_key, google_cx)
            logger.info("Google CSE search is configured")

        bing_api_key = self._search_config_value('bing', 'api_key', 'SEARCH_BING_API_KEY')
        if bing_api_key:
            provider_map["bing"] = BingSearchProvider(bing_api_key)
            logger.info("Bing search is configured")

        provider_map["duckduckgo"] = DuckDuckGoSearchProvider()

        preferred = self.provider if self.provider in provider_map else ""
        if preferred:
            self._providers.append(provider_map.pop(preferred))

        for key in ("tavily", "searxng", "gdelt", "serpapi", "alpha_vantage", "google", "bing", "duckduckgo"):
            provider = provider_map.pop(key, None)
            if provider:
                self._providers.append(provider)

        logger.info("Search provider order: %s", " -> ".join(p.name for p in self._providers))

    @property
    def is_available(self) -> bool:
        """检查是否有可用的搜索引擎"""
        return any(p.is_available for p in self._providers)

    def provider_status(self) -> List[Dict[str, Any]]:
        """Return configured search provider diagnostics for agent context."""
        from app.config import APIKeys

        google_api_key = self._search_config_value('google', 'api_key', 'SEARCH_GOOGLE_API_KEY')
        google_cx = self._search_config_value('google', 'cx', 'SEARCH_GOOGLE_CX')
        bing_api_key = self._search_config_value('bing', 'api_key', 'SEARCH_BING_API_KEY')
        configured = {
            "Google": bool(google_api_key and google_cx),
            "Bing": bool(bing_api_key),
            "Tavily": bool(APIKeys.TAVILY_API_KEYS),
            "SearXNG": bool(SearXNGConfig.CONFIGURED),
            "SerpAPI": bool(APIKeys.SERPAPI_KEYS),
            "AlphaVantage": bool(APIKeys.ALPHA_VANTAGE_API_KEY),
            "GDELT": self.provider not in {"none", "off", "disabled"},
            "DuckDuckGo": self.provider not in {"none", "off", "disabled"},
        }
        active_names = {provider.name for provider in self._providers}
        active_available = {provider.name: provider.is_available for provider in self._providers}
        return [
            {
                "provider": name,
                "configured": bool(configured.get(name)),
                "registered": name in active_names,
                "available": bool(active_available.get(name)),
                "note": _search_provider_note(name, bool(configured.get(name)), name in active_names),
            }
            for name in ("Tavily", "SearXNG", "GDELT", "SerpAPI", "AlphaVantage", "Google", "Bing", "DuckDuckGo")
        ]
    
    def search(self, query: str, num_results: int = None, date_restrict: str = None, days: int = 7) -> List[Dict[str, Any]]:
        """
        执行搜索（兼容旧接口）
        
        Args:
            query: 搜索关键词
            num_results: 最大返回结果数
            date_restrict: 时间限制（Google 格式，如 'd7'）
            days: 搜索最近几天（优先级高于 date_restrict）
            
        Returns:
            搜索结果列表
        """
        limit = num_results if num_results else self.max_results
        
        if date_restrict and not days:
            if date_restrict.startswith('d'):
                days = int(date_restrict[1:])
            elif date_restrict.startswith('w'):
                days = int(date_restrict[1:]) * 7
            elif date_restrict.startswith('m'):
                days = int(date_restrict[1:]) * 30
        
        response = self.search_with_fallback(query, limit, days)
        return response.to_list()
    
    def search_with_fallback(self, query: str, max_results: int = 5, days: int = 7) -> SearchResponse:
        """
        执行搜索（带自动故障转移）
        
        Args:
            query: 搜索关键词
            max_results: 最大返回结果数
            days: 搜索最近几天
            
        Returns:
            SearchResponse 对象
        """
        for provider in self._providers:
            if not provider.is_available:
                continue
            
            response = provider.search(query, max_results, days)
            
            if response.success and response.results:
                return response
            else:
                logger.warning(f"{provider.name} 搜索失败: {response.error_message}，尝试下一个引擎")
        
        return SearchResponse(
            query=query,
            results=[],
            provider="None",
            success=False,
            error_message="所有搜索引擎都不可用或搜索失败"
        )
    
    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        market: str = "USStock",
        max_results: int = 5
    ) -> SearchResponse:
        """
        搜索股票相关新闻
        
        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            market: 市场类型
            max_results: 最大返回结果数
            
        Returns:
            SearchResponse 对象
        """
        today_weekday = datetime.now().weekday()
        if today_weekday == 0:  # 周一
            search_days = 3
        elif today_weekday >= 5:  # 周末
            search_days = 2
        else:
            search_days = 1
        
        if market == "USStock":
            query = f"{stock_name} {stock_code} stock news latest"
        elif market == "Crypto":
            query = f"{stock_name} crypto news price analysis"
        elif market == "Forex":
            query = f"{stock_name} {stock_code} forex news analysis"
        else:
            query = f"{stock_name} {stock_code} latest news"
        
        logger.info(f"搜索股票新闻: {stock_name}({stock_code}), market={market}, days={search_days}")
        
        return self.search_with_fallback(query, max_results, search_days)
    
    def search_stock_events(
        self,
        stock_code: str,
        stock_name: str,
        event_types: Optional[List[str]] = None
    ) -> SearchResponse:
        """
        搜索股票特定事件（年报预告、减持等）
        """
        if event_types is None:
            event_types = ["年报预告", "减持公告", "业绩快报"]
        
        event_query = " OR ".join(event_types)
        query = f"{stock_name} ({event_query})"
        
        return self.search_with_fallback(query, max_results=5, days=30)


_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """获取搜索服务单例"""
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service


def _search_provider_note(name: str, configured: bool, registered: bool) -> str:
    """Describe why a search provider can or cannot be used."""
    if name == "Google" and not configured:
        return "Set SEARCH_GOOGLE_API_KEY and SEARCH_GOOGLE_CX to enable Google Custom Search fallback."
    if name == "Bing" and not configured:
        return "Set SEARCH_BING_API_KEY to enable Bing fallback."
    if name == "Tavily" and not configured:
        return "Set TAVILY_API_KEYS to enable Tavily AI search."
    if name == "SearXNG" and not configured:
        return "Set SEARCH_SEARXNG_BASE_URL to enable SearXNG metasearch fallback."
    if name == "SerpAPI" and not configured:
        return "Set SERPAPI_KEYS to enable SerpAPI Google/Bing search."
    if name == "AlphaVantage" and not configured:
        return "Set ALPHA_VANTAGE_API_KEY to enable company news and sentiment."
    if name == "GDELT" and configured:
        return "Free global news fallback, no API key required."
    if configured and not registered:
        return "Configured but not registered by the current search service instance; restart backend."
    return "ready" if configured or name in {"GDELT", "DuckDuckGo"} else "not configured"


def reset_search_service() -> None:
    """重置搜索服务（用于测试或配置更新后）"""
    global _search_service
    _search_service = None

