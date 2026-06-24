"""Tests for the free-first economic calendar providers."""
from __future__ import annotations

from app.data_providers.economic_calendar import (
    _fetch_finnhub_calendar,
    _fetch_tradingeconomics_calendar,
    _normalize_finnhub_event,
    _should_include_finnhub_row,
    get_economic_calendar,
)


def test_normalize_finnhub_event_maps_fields():
    row = {
        "event": "Initial Jobless Claims",
        "country": "US",
        "date": "2026-06-01",
        "time": "08:30",
        "impact": "medium",
        "unit": "k",
        "estimate": 220,
        "prev": 212,
        "actual": 215,
    }
    evt = _normalize_finnhub_event(row, 0)
    assert evt is not None
    assert evt["name"] == "美国初请失业金人数"
    assert evt["name_en"] == "Initial Jobless Claims"
    assert evt["country"] == "US"
    assert evt["date"] == "2026-06-01"
    assert evt["time"] == "08:30"
    assert evt["importance"] == "medium"
    assert evt["forecast"] == "220K"
    assert evt["previous"] == "212K"
    assert evt["actual"] == "215K"
    assert evt["is_released"] is True
    assert evt["actual_impact"] == "bullish"
    assert evt["source"] == "finnhub"


def test_normalize_finnhub_event_maps_gb_to_uk():
    row = {
        "event": "BoE Interest Rate Decision",
        "country": "GB",
        "date": "2026-06-02",
        "time": "12:00",
        "impact": "high",
        "estimate": 5.25,
        "unit": "%",
    }
    evt = _normalize_finnhub_event(row, 1)
    assert evt is not None
    assert evt["country"] == "UK"
    assert evt["importance"] == "high"
    assert evt["is_released"] is False
    assert evt["actual"] is None


def test_get_economic_calendar_without_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("FINNHUB_FREE_ONLY", "true")
    monkeypatch.setattr(
        "app.data_providers.economic_calendar._fetch_tradingeconomics_calendar",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.data_providers.economic_calendar._fallback_calendar_payload",
        lambda *args, **kwargs: None,
    )
    events = get_economic_calendar()
    assert events == []


def test_get_economic_calendar_fetches_from_tradingeconomics(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return [
                {
                    "Event": "US CPI m/m",
                    "Country": "United States",
                    "CountryCode": "US",
                    "Date": "2026-06-01T08:30:00",
                    "Importance": 3,
                    "Unit": "%",
                    "Forecast": 0.3,
                    "Previous": 0.4,
                }
            ]

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setenv("TRADING_ECONOMICS_CLIENT", "test_client")
    monkeypatch.setenv("TRADING_ECONOMICS_KEY", "test_key")
    monkeypatch.setattr(
        "app.data_providers.economic_calendar.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    events = get_economic_calendar()
    assert len(events) == 1
    assert events[0]["name_en"] == "US CPI m/m"
    assert events[0]["country"] == "US"
    assert events[0]["forecast"] == "0.30%"
    assert events[0]["source"] == "tradingeconomics"
    assert events[0]["ai_insight"]["event_type"] == "inflation"


def test_get_economic_calendar_fetches_from_finnhub(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "economicCalendar": [
                    {
                        "event": "Non Farm Payrolls",
                        "country": "US",
                        "date": "2026-06-05",
                        "time": "08:30",
                        "impact": "high",
                        "unit": "k",
                        "estimate": 180,
                        "prev": 175,
                    }
                ]
            }

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setenv("FINNHUB_API_KEY", "test_finnhub_key")
    monkeypatch.setenv("FINNHUB_FREE_ONLY", "false")
    monkeypatch.setattr(
        "app.data_providers.economic_calendar._fetch_tradingeconomics_calendar",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.data_providers.economic_calendar._fallback_calendar_payload",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.data_providers.economic_calendar.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    events = get_economic_calendar()
    assert len(events) == 1
    assert events[0]["name_en"] == "Non Farm Payrolls"
    assert events[0]["forecast"] == "180K"
    assert events[0]["source"] == "finnhub"


def test_should_exclude_market_holidays_without_figures():
    row = {
        "event": "Eid Al-Adha",
        "country": "NG",
        "date": "2026-05-29",
        "time": "00:00",
        "impact": "low",
    }
    assert _should_include_finnhub_row(row, "Eid Al-Adha") is False


def test_fetch_finnhub_calendar_dedupes_same_holiday_across_countries(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "economicCalendar": [
                    {
                        "event": "Non Farm Payrolls",
                        "country": "US",
                        "date": "2026-06-05",
                        "time": "08:30",
                        "impact": "high",
                        "unit": "k",
                        "estimate": 180,
                        "prev": 175,
                    },
                    {
                        "event": "Eid Al-Adha",
                        "country": "NG",
                        "date": "2026-05-29",
                        "time": "00:00",
                        "impact": "low",
                    },
                    {
                        "event": "Eid Al-Adha",
                        "country": "ID",
                        "date": "2026-05-29",
                        "time": "00:00",
                        "impact": "low",
                    },
                ]
            }

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setenv("FINNHUB_API_KEY", "test_finnhub_key")
    monkeypatch.setattr(
        "app.data_providers.economic_calendar.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    events = _fetch_finnhub_calendar()
    assert len(events) == 1
    assert events[0]["name_en"] == "Non Farm Payrolls"


def test_fetch_tradingeconomics_calendar_normalizes_and_filters(monkeypatch):
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "Calendar": [
                    {
                        "Event": "Non Farm Payrolls",
                        "CountryCode": "US",
                        "Date": "2026-06-05T08:30:00",
                        "Importance": 3,
                        "Unit": "K",
                        "Forecast": 180,
                        "Previous": 175,
                    },
                    {
                        "Event": "Bank Holiday",
                        "CountryCode": "US",
                        "Date": "2026-06-05T00:00:00",
                        "Importance": 1,
                    },
                ]
            }

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(
        "app.data_providers.economic_calendar.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    events = _fetch_tradingeconomics_calendar()
    assert len(events) == 1
    assert events[0]["name_en"] == "Non Farm Payrolls"
    assert events[0]["forecast"] == "180K"
    assert events[0]["source"] == "tradingeconomics"
