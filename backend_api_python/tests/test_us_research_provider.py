from types import SimpleNamespace

import pandas as pd
import pytest

from app.data_providers import us_research


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_sec_research_normalizes_filings_and_does_not_infer_form4_direction(monkeypatch):
    monkeypatch.setattr(us_research, "get_cached", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(us_research, "set_cached", lambda *_args, **_kwargs: None)

    def fake_get(url, **_kwargs):
        if url.endswith("company_tickers.json"):
            return FakeResponse({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
        assert url.endswith("CIK0000320193.json")
        return FakeResponse({
            "name": "Apple Inc.",
            "filings": {"recent": {
                "form": ["8-K", "4", "10-Q"],
                "filingDate": ["2026-09-07", "2026-09-06", "2026-08-01"],
                "reportDate": ["2026-09-07", "", "2026-06-30"],
                "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002", "0000320193-26-000003"],
                "primaryDocument": ["a8k.htm", "form4.xml", "a10q.htm"],
                "primaryDocDescription": ["Current report", "Ownership", "Quarterly report"],
            }},
        })

    result = us_research.fetch_sec_research("AAPL", http_get=fake_get)

    assert result["identity"]["cik"] == "0000320193"
    assert [item["form"] for item in result["sec_filings"]] == ["8-K", "4", "10-Q"]
    assert result["sec_filings"][0]["url"].endswith("/000032019326000001/a8k.htm")
    assert result["insider_activity"]["recent_form4_filing_count"] == 1
    assert result["insider_activity"]["scope"] == "form4_filing_activity_not_trade_direction"
    assert "direction" not in result["insider_activity"]


class FakeTicker:
    info = {
        "currentPrice": 200,
        "recommendationKey": "buy",
        "recommendationMean": 1.8,
        "numberOfAnalystOpinions": 40,
        "targetMedianPrice": 240,
        "targetMeanPrice": 242,
        "targetLowPrice": 180,
        "targetHighPrice": 300,
        "sharesShort": 10_000,
        "sharesShortPriorMonth": 9_000,
        "shortPercentOfFloat": 0.0125,
        "shortRatio": 1.75,
        "dateShortInterest": 1_788_739_200,
    }
    options = ("2026-09-18",)
    institutional_holders = pd.DataFrame([
        {"Date Reported": pd.Timestamp("2026-06-30"), "Holder": "Fund A", "pctHeld": 0.081, "Shares": 81_000, "Value": 16_200_000},
        {"Date Reported": pd.Timestamp("2026-06-30"), "Holder": "Fund B", "pctHeld": 0.052, "Shares": 52_000, "Value": 10_400_000},
    ])

    def option_chain(self, expiry):
        assert expiry == "2026-09-18"
        calls = pd.DataFrame([
            {"strike": 195, "volume": 100, "openInterest": 1000, "impliedVolatility": 0.25},
            {"strike": 200, "volume": 200, "openInterest": 2000, "impliedVolatility": 0.20},
        ])
        puts = pd.DataFrame([
            {"strike": 200, "volume": 150, "openInterest": 1200, "impliedVolatility": 0.22},
            {"strike": 205, "volume": 50, "openInterest": 800, "impliedVolatility": 0.26},
        ])
        return SimpleNamespace(calls=calls, puts=puts)


class FakeYahoo:
    @staticmethod
    def Ticker(symbol):
        assert symbol == "AAPL"
        return FakeTicker()


def test_yahoo_research_labels_consensus_options_and_short_interest_scope():
    result = us_research.fetch_yahoo_us_research("AAPL", yf_client=FakeYahoo)

    assert result["analyst_expectations"]["target_price_median_usd"] == 240
    assert result["options"]["put_call_volume_ratio"] == 200 / 300
    assert result["options"]["nearest_atm_implied_volatility_pct"] == pytest.approx(21)
    assert result["options"]["scope"] == "bounded_multi_expiry_surface_summary"
    assert result["options"]["expiry_count"] == 1
    assert result["short_interest"]["short_percent_of_float_pct"] == 1.25
    assert result["short_interest"]["scope"] == "reported_short_interest_not_daily_short_volume"
    assert [item["name"] for item in result["ownership"]["top_institutional_holders"]] == ["Fund A", "Fund B"]
    assert result["ownership"]["top_institutional_holders"][0]["pct_held"] == 8.1
    assert result["ownership"]["top_holders_reported_pct"] == 13.3
    assert result["ownership"]["other_shareholders_pct"] == 86.7
    assert result["ownership"]["scope"] == "latest_available_reported_institutional_holders_not_realtime_ownership"


def test_alpha_vantage_transcript_is_bounded_and_aggregated(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")

    def fake_get(_url, **kwargs):
        assert kwargs["params"]["function"] == "EARNINGS_CALL_TRANSCRIPT"
        return FakeResponse({
            "quarter": kwargs["params"]["quarter"],
            "fiscal_date": "2026-09-30",
            "transcript": [
                {"speaker": "CEO", "title": "Chief Executive Officer", "content": "Demand remained strong.", "sentiment": "0.7"},
                {"speaker": "Analyst", "title": "Analyst", "content": "What are the margin risks?", "sentiment": "-0.4"},
            ],
        })

    result = us_research.fetch_alpha_vantage_transcript("AAPL", http_get=fake_get)

    assert result["turn_count"] == 2
    assert result["average_turn_sentiment"] == pytest.approx(0.15)
    assert len(result["notable_segments"]) == 2
    assert result["scope"] == "provider_transcript_sentiment_and_bounded_excerpts"


def test_finra_short_volume_is_not_labelled_short_interest():
    rows = [
        {
            "tradeReportDate": "2026-10-28",
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "shortParQuantity": 60,
            "shortExemptParQuantity": 3,
            "totalParQuantity": 240,
            "reportingFacilityCode": "NQTRF",
        },
        {
            "tradeReportDate": "2026-10-28",
            "securitiesInformationProcessorSymbolIdentifier": "AAPL",
            "shortParQuantity": 40,
            "shortExemptParQuantity": 2,
            "totalParQuantity": 160,
            "reportingFacilityCode": "NYTRF",
        },
    ]
    result = us_research.fetch_finra_short_volume(
        "AAPL", http_post=lambda *args, **kwargs: FakeResponse(rows)
    )

    assert result["short_volume_ratio_pct"] == 25
    assert result["reporting_facility_count"] == 2
    assert "not_short_interest" in result["scope"]


def test_yahoo_ownership_lookup_does_not_require_quote_or_options_data():
    class OwnershipOnlyTicker:
        institutional_holders = FakeTicker.institutional_holders

        @property
        def info(self):
            raise AssertionError("ownership lookup must not load quote info")

        @property
        def options(self):
            raise AssertionError("ownership lookup must not load option chains")

    class OwnershipOnlyYahoo:
        @staticmethod
        def Ticker(symbol):
            assert symbol == "AAPL"
            return OwnershipOnlyTicker()

    result = us_research.fetch_yahoo_us_ownership("AAPL", yf_client=OwnershipOnlyYahoo)

    assert result["ownership"]["top_institutional_holders"][0]["name"] == "Fund A"


def test_nasdaq_ownership_normalizes_top_holders_and_percentages():
    def fake_get(url, **kwargs):
        assert url.endswith("/TSLA/institutional-holdings")
        assert kwargs["params"]["limit"] == 10
        return FakeResponse({
            "data": {
                "ownershipSummary": {
                    "SharesOutstandingPCT": {"label": "Institutional Ownership", "value": "49.31%"},
                    "ShareoutstandingTotal": {"label": "Total Shares Outstanding (millions)", "value": "4"},
                },
                "holdingsTransactions": {
                    "table": {
                        "rows": [
                            {
                                "ownerName": "Fund A",
                                "date": "06/30/2026",
                                "sharesHeld": "400,000",
                                "marketValue": "$80,000",
                            },
                            {
                                "ownerName": "Fund B",
                                "date": "06/30/2026",
                                "sharesHeld": "200,000",
                                "marketValue": "$40,000",
                            },
                            {
                                "ownerName": "Fund C",
                                "date": "06/30/2026",
                                "sharesHeld": "100,000",
                                "marketValue": "$20,000",
                            },
                        ]
                    }
                },
            }
        })

    result = us_research.fetch_nasdaq_us_ownership("TSLA", http_get=fake_get)
    ownership = result["ownership"]

    assert ownership["source"] == "nasdaq"
    assert ownership["shares_outstanding"] == 4_000_000
    assert ownership["institutional_ownership_pct"] == 49.31
    assert ownership["top_institutional_holders"][0] == {
        "name": "Fund A",
        "shares": 400_000,
        "value_usd": 80_000_000,
        "pct_held": 10.0,
        "report_date": "2026-06-30",
    }
    assert ownership["top_holders_reported_pct"] == 17.5
    assert ownership["other_shareholders_pct"] == 82.5


def test_collect_ownership_falls_back_to_yahoo_and_reports_nasdaq_error(monkeypatch):
    monkeypatch.setattr(us_research, "get_cached", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(us_research, "set_cached", lambda *_args, **_kwargs: None)

    def failed_nasdaq(*_args, **_kwargs):
        raise RuntimeError("nasdaq unavailable")

    monkeypatch.setattr(us_research, "fetch_nasdaq_us_ownership", failed_nasdaq)
    result = us_research.collect_us_ownership("AAPL", yf_client=FakeYahoo)

    assert result["ownership"]["source"] == "yahoo_finance"
    assert result["_provider_status"]["attempted"] == ["nasdaq_ownership", "yahoo_ownership"]
    assert "RuntimeError" in result["_provider_status"]["errors"]["nasdaq_ownership"]


def test_nearest_atm_iv_ignores_yahoo_placeholder_values():
    calls = pd.DataFrame([
        {"strike": 200, "impliedVolatility": 0.00001},
        {"strike": 205, "impliedVolatility": 0.24},
    ])
    puts = pd.DataFrame([
        {"strike": 200, "impliedVolatility": 0.00001},
        {"strike": 195, "impliedVolatility": 0.22},
    ])

    assert us_research._nearest_atm_iv(calls, puts, 200) == pytest.approx(23)
