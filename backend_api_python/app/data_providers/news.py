"""Financial news and economic calendar data providers."""
from __future__ import annotations

from typing import Any, Dict, List

from app.data_providers.economic_calendar import (
    get_economic_calendar,
    get_economic_calendar_payload,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def fetch_financial_news(lang: str = "all") -> Dict[str, List[Dict[str, Any]]]:
    """Fetch financial news using search service — separated by language."""
    result: Dict[str, List[Dict[str, Any]]] = {"cn": [], "en": []}

    try:
        from app.services.search import SearchService
        search = SearchService()

        cn_queries = [
            "加密货币新闻", "美联储利率", "美股市场最新消息",
            "外汇市场分析", "全球经济数据", "期货市场动态",
        ]
        en_queries = [
            "stock market news today", "cryptocurrency bitcoin news",
            "forex market analysis", "federal reserve interest rate",
            "global economic outlook", "S&P 500 market update",
        ]

        if lang in ("all", "cn"):
            for query in cn_queries:
                try:
                    results = search.search(query, num_results=5, date_restrict="d1")
                    for r in results:
                        result["cn"].append({
                            "title": r.get("title", ""), "link": r.get("link", ""),
                            "snippet": r.get("snippet", ""), "source": r.get("source", ""),
                            "published": r.get("published", ""), "category": query, "lang": "cn",
                        })
                except Exception:
                    pass

        if lang in ("all", "en"):
            for query in en_queries:
                try:
                    results = search.search(query, num_results=5, date_restrict="d1")
                    for r in results:
                        result["en"].append({
                            "title": r.get("title", ""), "link": r.get("link", ""),
                            "snippet": r.get("snippet", ""), "source": r.get("source", ""),
                            "published": r.get("published", ""), "category": query, "lang": "en",
                        })
                except Exception:
                    pass

        for lang_key in ["cn", "en"]:
            seen: set = set()
            unique = []
            for news in result[lang_key]:
                link = news.get("link", "")
                if link and link not in seen:
                    seen.add(link)
                    unique.append(news)
            result[lang_key] = unique[:15]

    except Exception as e:
        logger.error("Failed to fetch financial news: %s", e)

    return result


__all__ = ["fetch_financial_news", "get_economic_calendar", "get_economic_calendar_payload"]
