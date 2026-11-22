from app.services.fast_analysis import FastAnalysisService
from app.services.market_data_collector import MarketDataCollector
from types import SimpleNamespace
from unittest.mock import ANY, Mock


def _service():
    return FastAnalysisService.__new__(FastAnalysisService)


def test_background_global_news_does_not_change_asset_sentiment_score():
    news = [{
        "headline": "War escalates in unrelated region",
        "sentiment": "negative",
        "is_global_event": True,
        "asset_relevance": "background",
    }]

    assert _service()._calculate_sentiment_score(news) == 0
    assert _service()._has_major_news(news) is False


def test_direct_material_event_remains_eligible_for_risk_scoring():
    news = [{
        "headline": "Sanctions target the issuer's principal operating subsidiary",
        "sentiment": "negative",
        "asset_relevance": "material",
    }]

    assert _service()._calculate_sentiment_score(news) < 0
    assert _service()._has_major_news(news) is True


def test_cn_company_search_is_not_suppressed_by_finnhub_general_headlines(monkeypatch):
    monkeypatch.setattr("app.data_providers.asset_events.collect_asset_events", lambda *_: {
        "news": [], "disclosures": [], "collection": {"status": "checked_empty", "providers": {}},
    })
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = Mock()
    collector._finnhub_client.general_news.return_value = [{"headline": f"Global {i}"} for i in range(10)]
    collector._get_news_from_search = Mock(return_value=[{"headline": "贵州茅台公司公告", "asset_relevance": "direct"}])
    monkeypatch.delenv("FAST_ANALYSIS_INCLUDE_GLOBAL_NEWS", raising=False)

    result = collector._get_news("CNStock", "600519", "贵州茅台")
    collector._finnhub_client.general_news.assert_not_called()
    collector._get_news_from_search.assert_called_once_with("CNStock", "600519", "贵州茅台", diagnostics=ANY)
    assert len(result["news"]) == 1


def test_cn_search_classifies_unrelated_results_as_background(monkeypatch):
    import app.services.search as search

    def result(title, snippet=""):
        return SimpleNamespace(title=title, snippet=snippet, published_date="2026-11-19",
                               source="test", url="https://example.com/news", sentiment="negative")

    provider = SimpleNamespace(is_available=True, search_stock_news=Mock(return_value=SimpleNamespace(
        success=True, provider="test", results=[
            result("贵州茅台发布公告"), result("600519 发布公告"), result("Unrelated global sanctions"),
        ],
    )))
    monkeypatch.setattr(search, "get_search_service", lambda: provider)
    collector = MarketDataCollector.__new__(MarketDataCollector)
    rows = collector._get_news_from_search("CNStock", "SH600519", "贵州茅台")
    assert [row["asset_relevance"] for row in rows] == ["direct", "direct", "background"]
    assert _service()._calculate_sentiment_score(rows[-1:]) == 0
