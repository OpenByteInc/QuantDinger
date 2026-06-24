"""Test data_providers cache layer and helpers."""
from app.data_providers import safe_float, get_cached, set_cached, clear_cache


def test_safe_float_valid():
    assert safe_float("42.5") == 42.5
    assert safe_float(10) == 10.0


def test_safe_float_invalid():
    assert safe_float("bad") == 0.0
    assert safe_float(None, -1.0) == -1.0
    assert safe_float("", 0.0) == 0.0


def test_cache_round_trip():
    """set_cached + get_cached should return the same data."""
    set_cached("_test_unit", {"msg": "hello"}, 60)
    result = get_cached("_test_unit")
    assert result == {"msg": "hello"}
    clear_cache()
    assert get_cached("_test_unit") is None


def test_economic_calendar_not_empty(monkeypatch):
    from app.data_providers.economic_calendar import get_economic_calendar

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return [
                {
                    "Event": "US CPI m/m",
                    "CountryCode": "US",
                    "Date": "2026-06-01T08:30:00",
                    "Importance": 3,
                    "Unit": "%",
                    "Forecast": 0.3,
                    "Previous": 0.4,
                }
            ]

    monkeypatch.setenv("TRADING_ECONOMICS_CLIENT", "test_client")
    monkeypatch.setenv("TRADING_ECONOMICS_KEY", "test_key")
    monkeypatch.setattr(
        "app.data_providers.economic_calendar.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    events = get_economic_calendar()
    assert isinstance(events, list)
    assert len(events) > 0
    assert "name" in events[0]
    assert "date" in events[0]


def test_adanos_sentiment_disabled_without_key():
    from app.data_providers.adanos_sentiment import fetch_adanos_market_sentiment

    result = fetch_adanos_market_sentiment("AAPL, TSLA", api_key="")

    assert result["enabled"] is False
    assert result["tickers"] == ["AAPL", "TSLA"]
    assert result["stocks"] == []
    assert "ADANOS_API_KEY" in result["error"]


def test_adanos_sentiment_fetches_and_normalizes(monkeypatch):
    from app.data_providers.adanos_sentiment import fetch_adanos_market_sentiment

    calls = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "stocks": [
                    {
                        "ticker": "AAPL",
                        "company_name": "Apple Inc.",
                        "sentiment_score": "0.21",
                        "buzz_score": "72.5",
                        "bullish_pct": "58",
                        "mentions": "128",
                        "unique_posts": "32",
                        "total_upvotes": "1204",
                        "trend": "rising",
                        "trend_history": [45.0, 51.5, 72.5],
                    }
                ]
            }

    class FakeSession:
        @staticmethod
        def get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setenv("ADANOS_API_KEY", "adanos_test_key")
    result = fetch_adanos_market_sentiment(
        ["$aapl", "AAPL", "bad ticker"],
        source="reddit",
        days=14,
        base_url="https://api.example.test",
        session=FakeSession,
    )

    assert result["enabled"] is True
    assert result["tickers"] == ["AAPL"]
    assert result["stocks"][0] == {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "source": "reddit",
        "sentiment_score": 0.21,
        "buzz_score": 72.5,
        "bullish_pct": 58.0,
        "bearish_pct": None,
        "mentions": 128,
        "source_count": None,
        "subreddit_count": None,
        "unique_posts": 32,
        "unique_tweets": None,
        "trade_count": None,
        "market_count": None,
        "unique_traders": None,
        "total_upvotes": 1204,
        "total_liquidity": None,
        "trend": "rising",
        "trend_history": [45.0, 51.5, 72.5],
    }
    assert calls[0][0] == "https://api.example.test/reddit/stocks/v1/compare"
    assert calls[0][1]["params"] == {"tickers": "AAPL", "days": 14}
    assert calls[0][1]["headers"]["X-API-Key"] == "adanos_test_key"


def test_adanos_sentiment_fail_open_on_http_error(monkeypatch):
    from app.data_providers.adanos_sentiment import fetch_adanos_market_sentiment

    class FakeResponse:
        status_code = 429

    class FakeSession:
        @staticmethod
        def get(url, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("ADANOS_API_KEY", "adanos_test_key")
    result = fetch_adanos_market_sentiment("NVDA", session=FakeSession)

    assert result["enabled"] is True
    assert result["stocks"] == []
    assert result["error"] == "Adanos API returned HTTP 429"


def test_adanos_sentiment_handles_list_payload_and_non_finite_numbers():
    from app.data_providers.adanos_sentiment import fetch_adanos_market_sentiment

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return [
                {
                    "symbol": "$MSFT",
                    "name": "Microsoft Corp.",
                    "sentiment": "NaN",
                    "buzz": "inf",
                    "mentions": "bad",
                    "market_count": "3",
                    "total_liquidity": "45200.5",
                    "trend_history": [12.0, 18.5],
                },
                "not-a-row",
                {},
            ]

    class FakeSession:
        @staticmethod
        def get(url, **kwargs):
            return FakeResponse()

    result = fetch_adanos_market_sentiment(
        None,
        api_key="adanos_test_key",
        session=FakeSession,
    )
    assert result["stocks"] == []
    assert result["error"] == "No valid stock tickers provided"

    result = fetch_adanos_market_sentiment(
        "MSFT",
        api_key="adanos_test_key",
        session=FakeSession,
    )

    assert result["stocks"][0]["ticker"] == "MSFT"
    assert result["stocks"][0]["company_name"] == "Microsoft Corp."
    assert result["stocks"][0]["sentiment_score"] is None
    assert result["stocks"][0]["buzz_score"] is None
    assert result["stocks"][0]["mentions"] is None
    assert result["stocks"][0]["market_count"] == 3
    assert result["stocks"][0]["total_liquidity"] == 45200.5
    assert result["stocks"][0]["trend_history"] == [12.0, 18.5]


def test_adanos_sentiment_validates_source():
    import pytest
    from app.data_providers.adanos_sentiment import normalize_source

    assert normalize_source("twitter") == "x"
    with pytest.raises(ValueError):
        normalize_source("unsupported")
