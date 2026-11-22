from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from app.services.market_data_collector import MarketDataCollector


class _FakeFinnhubClient:
    def __init__(self, timestamp: int) -> None:
        self.timestamp = timestamp

    def company_news(self, *_args, **_kwargs):
        return [{
            "datetime": self.timestamp,
            "headline": "Confirmed company update",
            "summary": "A timestamp conversion fixture.",
            "source": "Finnhub",
            "url": "https://example.test/news",
        }]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="requires POSIX timezone control")
def test_finnhub_unix_timestamp_is_utc_when_server_uses_asia_shanghai(monkeypatch):
    monkeypatch.setattr("app.data_providers.asset_events.collect_asset_events", lambda *_: {
        "news": [], "disclosures": [], "collection": {"status": "checked_empty", "providers": {}},
    })
    expected = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = _FakeFinnhubClient(int(expected.timestamp()))
    collector._get_news_from_search = lambda *_args, **_kwargs: []
    monkeypatch.setenv("FINNHUB_FREE_ONLY", "true")

    previous_timezone = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Asia/Shanghai"
        time.tzset()
        result = collector._get_news("USStock", "TSLA")
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()

    assert result["news"][0]["datetime"] == "2026-09-08T12:00:00Z"
