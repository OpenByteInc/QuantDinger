from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from xml.sax.saxutils import escape

import pytest

from app.data_providers import asset_events as events
from app.professional_report.builder import build_professional_report
from app.professional_report.prompt import build_professional_analysis_prompt
from app.services.market_data_collector import MarketDataCollector


def item(title="Apple update", days=0, kind="news"):
    return events._item(title, (datetime.now(timezone.utc) - timedelta(days=days, seconds=1)).isoformat(),
                        "https://example.org/news", "Test", kind=kind)


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(events, "get_cached", lambda _: None)
    cache = Mock()
    monkeypatch.setattr(events, "set_cached", cache)
    monkeypatch.setattr(events, "fetch_news_rss", lambda *_: [])
    monkeypatch.setattr(events, "fetch_disclosures", lambda *_, **__: [])
    return cache


def test_published_dates_keep_timezone_and_unknown_is_not_today():
    assert events.published_at("2026-11-20 15:30:00", "Asia/Shanghai") == "2026-11-20T07:30:00Z"
    assert events.published_at("Fri, 20 Nov 2026 15:30:00 GMT") == "2026-11-20T15:30:00Z"
    assert events.published_at("not a date") == events.published_at(None) == ""


def test_rss_filters_unrelated_and_automated_holdings_titles():
    titles = ["Apple announces a product", "AAPL reports earnings", "Pineapple farm expansion",
              "Apple Inc. $AAPL Stake Raised by Fund LLC", "Bitcoin Live Price, News and Price History"]
    xml = "<rss><channel>" + "".join(f"<item><title>{escape(t)}</title><pubDate>Fri, 20 Nov 2026 15:30:00 GMT</pubDate></item>" for t in titles) + "</channel></rss>"
    http = SimpleNamespace(get=Mock(return_value=SimpleNamespace(content=xml.encode(), raise_for_status=lambda: None)))
    rows = events.fetch_news_rss("USStock", "AAPL", "Apple Inc.", http=http)
    assert [x["headline"] for x in rows] == titles[:2]
    assert all(x["evidence_depth"] == "headline" and x["summary"] == "" for x in rows)


def test_disclosures_match_issuer_and_fixed_host_only():
    row = {"secCode": "00700", "announcementTitle": "Issuer results", "announcementTime": 1794931200000,
           "adjunctUrl": "finalpage/2026-11-18/1225677381.PDF"}
    payload = {"totalAnnouncement": 3, "announcements": [row, {**row, "secCode": "09988"},
                                                            {**row, "adjunctUrl": "https://evil.test/a.pdf"}]}
    http = SimpleNamespace(post=Mock(return_value=SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)))
    rows = events.fetch_disclosures("HKStock", "HK00700", http=http)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://static.cninfo.com.cn/" + row["adjunctUrl"]
    assert http.post.call_args.kwargs["data"]["column"] == "hke"


def test_collection_filters_old_future_duplicate_events_and_caches(sources, monkeypatch):
    rows = [item(), item(), item("Old", 9), item("Future", -1), {**item("Unknown"), "datetime": ""}]
    monkeypatch.setattr(events, "fetch_news_rss", lambda *_: rows)
    result = events.collect_asset_events("USStock", "AAPL")
    assert [x["headline"] for x in result["news"]] == ["Apple update"]
    assert result["collection"]["status"] == "available"
    assert sources.call_args.args[-1] == 600


@pytest.mark.parametrize("fails,expected,ttl", [(True, "fetch_failed", 60), (False, "checked_empty", 600)])
def test_failure_is_not_reported_as_no_events(sources, monkeypatch, fails, expected, ttl):
    def fetch(*_):
        if fails:
            raise TimeoutError("upstream unavailable")
        return []
    monkeypatch.setattr(events, "fetch_news_rss", fetch)
    result = events.collect_asset_events("Crypto", "BTC/USDT")
    assert result["collection"]["status"] == expected
    assert sources.call_args.args[-1] == ttl


def test_failed_document_read_keeps_title_not_fake_excerpt(sources, monkeypatch):
    monkeypatch.setattr(events, "fetch_disclosures", lambda *_, **__: [item("Results", kind="issuer_disclosure")])
    monkeypatch.setattr(events, "read_disclosure", Mock(side_effect=ValueError("unreadable")))
    result = events.collect_asset_events("HKStock", "00700")
    assert result["disclosures"][0]["evidence_depth"] == "headline"
    assert result["collection"]["providers"]["disclosure_text"]["status"] == "fetch_failed"


def test_pdf_reader_rejects_arbitrary_urls():
    http = SimpleNamespace(get=Mock())
    with pytest.raises(ValueError, match="URL"):
        events.read_disclosure({"url": "http://127.0.0.1/private"}, http=http)
    http.get.assert_not_called()


@pytest.mark.parametrize("text,valid", [("公告正文 Revenue and profit 2026 " * 10, True), ("࠰ಥʹ׸ʿഐၑהϞࠢʮ̡ʿ" * 30, False)])
def test_pdf_reader_rejects_garbled_font_encoding(monkeypatch, text, valid):
    response = Mock(status_code=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b"pdf"]
    http = SimpleNamespace(get=Mock(return_value=response))
    monkeypatch.setattr("pypdf.PdfReader", lambda _: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)]))
    row = {"url": "https://static.cninfo.com.cn/finalpage/2026-11-18/123.PDF"}
    if valid:
        assert events.read_disclosure(row, http=http)["evidence_depth"] == "document_excerpt"
    else:
        with pytest.raises(ValueError, match="encoding"):
            events.read_disclosure(row, http=http)


def payload():
    return {"market": "USStock", "symbol": "AAPL", "collected_at": datetime.now(timezone.utc).isoformat(),
            "price": {"price": 100}, "analyst_expectations": {"source": "provider", "rating": "buy"},
            "_meta": {"news_collection": {"status": "fetch_failed"}}}


def test_missing_news_does_not_erase_positioning_evidence():
    report = build_professional_report(payload(), {"decision": "HOLD"})
    dimension = next(x for x in report["dimensions"] if x["key"] == "news_sentiment")
    assert dimension["status"] == "partial"
    assert dimension["news_status"] == "fetch_failed"
    assert dimension["sentiment_status"] == "available"
    assert dimension["score_kind"] == "evidence_coverage"
    assert "news_collection_failed" in report["data_quality"]["warnings"]


def test_news_excerpt_reaches_prompt_without_240_character_truncation():
    data = payload()
    row = {**item(), "summary": "Annual report opening text. " * 20 + "MATERIAL_EVENT_DETAIL",
           "evidence_depth": "document_excerpt", "datetime": ""}
    data["news"] = [row]
    system, prompt = build_professional_analysis_prompt(data, "en-US")
    assert "MATERIAL_EVENT_DETAIL" in prompt
    assert "untrusted evidence" in system
    report = build_professional_report(data, {"decision": "HOLD"})
    dimension = next(x for x in report["dimensions"] if x["key"] == "news_sentiment")
    assert dimension["events"][0]["published_at"] is None
    assert dimension["events"][0]["evidence_depth"] == "document_excerpt"


def test_headline_only_collection_is_explicit(monkeypatch):
    monkeypatch.setattr(events, "collect_asset_events", lambda *_: {
        "news": [item()], "disclosures": [], "collection": {"status": "available", "providers": {}},
    })
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = None
    collector._get_news_from_search = Mock(side_effect=AssertionError("unnecessary search"))
    result = collector._get_news("USStock", "AAPL")
    assert result["collection"]["status"] == "headline_only"
    data = payload()
    data["_meta"]["news_collection"] = result["collection"]
    data["news"] = result["news"]
    report = build_professional_report(data, {"decision": "HOLD"})
    assert report["data_quality"]["overall_score"] <= 90
    assert "news_content_limited" in report["data_quality"]["warnings"]
