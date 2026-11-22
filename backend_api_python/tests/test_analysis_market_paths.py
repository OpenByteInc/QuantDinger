"""Cross-market regressions from collection through professional-report quality."""

import copy
import sys
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.data_sources import cn_hk_fundamentals as providers
from app.professional_report.builder import build_professional_report
from app.professional_report.features.equity import build_equity_features
from app.professional_report.prompt import build_professional_analysis_prompt
from app.professional_report.snapshot import build_evidence_snapshot
from app.research.fundamentals import normalize_fundamentals
from app.research.intelligence import build_research_intelligence
from app.services.fast_analysis import FastAnalysisService
from app.services.fast_analysis_fundamentals import format_financial_statements
from app.services.market_data_collector import MarketDataCollector
from app.services.fundamental_data import FundamentalDataService


def _payload(market="CNStock"):
    return {
        "market": market, "symbol": "600498" if market == "CNStock" else "TEST",
        "collected_at": "2026-11-22T10:00:00Z",
        "company": {"name": "Test company"},
        "price": {"price": 24.32, "source": "test"},
        "kline": [{"timestamp": "2026-11-20T07:00:00Z", "open": 24, "close": 24.32, "high": 25, "low": 23}],
        "indicators": {"rsi": {"value": 32}, "moving_averages": {"trend": "downtrend"}},
        "fundamental": {
            "source": "fixture", "market_cap": 100000, "revenue_growth": 3.5, "profit_margin": None,
            "financial_statements": {
                "latest_quarter": {"period_end": "2026-09-30", "source": "quarter_provider",
                                   "income_statement": {"total_revenue": 8067, "net_income": 122}},
                "ttm": {"period_end": "2026-09-30", "income_statement": {"total_revenue": 28291, "net_income": 238}},
            },
        },
    }


@pytest.mark.parametrize("market", ["CNStock", "HKStock", "USStock"])
def test_statement_margin_is_available_in_evidence_features_prompt_and_cached_reads(market):
    payload = _payload(market)
    before = copy.deepcopy(payload)
    snapshot = build_evidence_snapshot(payload)
    margin = next(item for item in snapshot["observations"] if item["metric"] == "profit_margin")
    assert margin["value"] == pytest.approx(122 / 8067 * 100)
    assert margin["unit"] == "percent"
    assert margin["period_end"] == "2026-09-30"
    assert margin["source"] == "quarter_provider"
    features = build_equity_features(market, payload, snapshot["observations"])
    assert features["valuation_and_quality"]["profit_margin"]["evidence_refs"] == [margin["evidence_id"]]
    _, prompt = build_professional_analysis_prompt(payload, "zh-CN")
    assert 'missing_required_metrics=[]' in prompt
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._load_persisted_fundamental = lambda *_: {
        "payload": payload["fundamental"], "fresh": True, "has_provider_payload": True,
    }
    collector._fetch_fundamental_uncached = lambda *_: pytest.fail("must reuse persisted payload")
    assert collector._get_fundamental(market, "TEST")["profit_margin"] == margin["value"]
    assert payload == before


@pytest.mark.parametrize("profit,expected", [(0, 0), (-20, -20), (20, 20)])
def test_zero_and_negative_income_are_not_missing(profit, expected):
    result = normalize_fundamentals({"financial_statements": {"latest_quarter": {
        "income_statement": {"net_income": profit, "total_revenue": 100},
    }}})
    assert result["profit_margin"] == expected


def test_canonical_provider_basis_is_retained_and_periods_are_not_conflicts():
    payload = _payload()
    payload["fundamental"].update(profit_margin=0.84, field_metadata={"profit_margin": {"period_type": "ttm"}})
    result = normalize_fundamentals(payload["fundamental"])
    assert result["profit_margin"] == .84
    assert result["field_metadata"]["profit_margin"]["period_type"] == "ttm"
    report = build_professional_report(payload, {"language": "zh-CN", "decision": "HOLD"})
    assert "profit_margin" not in report["data_quality"]["missing_metrics"]
    assert report["data_quality"]["conflict_ratio"] == 0


def test_interim_report_is_not_relabelled_as_quarter_and_ratios_are_multiples():
    payload = _payload("HKStock")
    payload["fundamental"] = {"source": "interim", "financial_statements": {
        "income_statement": {"latest_date": "2026-06-30", "net_income": 10, "total_revenue": 100},
        "balance_sheet": {"latest_date": "2026-06-30", "current_assets": 60, "current_liabilities": 30},
    }}
    result = normalize_fundamentals(payload["fundamental"])
    assert result["profit_margin"] == 10
    assert "latest_quarter" not in result["financial_statements"]
    assert "NOT NECESSARILY A QUARTER" in format_financial_statements(result["financial_statements"])
    snapshot = build_evidence_snapshot(payload)
    ratio = next(item for item in snapshot["observations"] if item["metric"].endswith("derived.current_ratio"))
    assert ratio["value"] == 2
    assert ratio["unit"] == "multiple"
    assert ratio["currency"] is None
    assert ratio["period_end"] == "2026-06-30"


def test_partial_equity_capabilities_remain_visible_without_fabricated_signal_score():
    payload = _payload()
    report = build_professional_report(payload, {"language": "zh-CN", "decision": "HOLD"})
    dimension = next(item for item in report["dimensions"] if item["key"] == "market_specific")
    assert dimension["status"] == "partial"
    assert dimension["score_kind"] == "capability_coverage"
    assert 0 < dimension["score"] < 100
    assert report["contract_validation"]["valid"]
    assert report["market_features"]["capability_status"]["exchange_disclosures"] == "not_integrated"


def test_hk_applicability_and_provider_failure_propagate_to_readiness():
    payload = _payload("HKStock")
    payload["hk_security_profile"] = {"is_h_share": False, "southbound_eligible_sh": False, "southbound_eligible_sz": False}
    payload["_meta"] = {"hk_provider_status": {"attempted": ["analyst_expectations"], "errors": {"analyst_expectations": "timeout"}}}
    intelligence = build_research_intelligence(payload, quality={"quality_score": 1})
    assert "southbound_holdings" not in intelligence["missing_optional_capabilities"]
    assert "southbound_holdings" in intelligence["not_applicable_capabilities"]
    assert intelligence["capability_status"]["ah_premium"] == "not_applicable"
    assert intelligence["capability_status"]["analyst_expectations"] == "fetch_failed"
    assert intelligence["data_source_status"]["errors"]


@pytest.mark.parametrize("market_type,required", [("spot", False), ("swap", True), ("perpetual", True)])
def test_crypto_requirements_follow_instrument_type_and_volume_is_not_percent(market_type, required):
    payload = _payload("Crypto")
    payload.update(symbol="BTC/USDT", fundamental={}, crypto_instrument={"market_type": market_type}, crypto_factors={"volume_24h": 1000})
    snapshot = build_evidence_snapshot(payload)
    assert ("crypto.funding_rate" in snapshot["required_metrics"]) is required
    assert next(item for item in snapshot["observations"] if item["metric"] == "crypto.volume_24h")["unit"] == "usd"
    research = build_research_intelligence(payload, quality={"quality_score": 1})
    assert ("funding" in research["missing_required_capabilities"]) is required
    payload["crypto_factors"].update(funding_rate=0, open_interest=2000)
    research = build_research_intelligence(payload, quality={"quality_score": 1})
    assert "funding" in research["available_capabilities"]
    assert "open_interest" not in research["missing_optional_capabilities"]


@pytest.mark.parametrize("market,symbol,name", [("USStock", "AAPL", "Apple"), ("HKStock", "00700", "Tencent"), ("CNStock", "600498", "Fiberhome"), ("Crypto", "BTC/USDT", "Bitcoin")])
def test_general_headlines_do_not_displace_asset_specific_search(monkeypatch, market, symbol, name):
    monkeypatch.setattr("app.data_providers.asset_events.collect_asset_events", lambda *_: {
        "news": [], "disclosures": [], "collection": {"status": "checked_empty", "providers": {}},
    })
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = SimpleNamespace(
        general_news=lambda *_args, **_kwargs: [{"headline": "Unrelated general story", "datetime": 1795255200}] * 10,
        company_news=lambda *_args, **_kwargs: [],
    )
    calls = []
    collector._get_news_from_search = lambda *args, **kwargs: calls.append(args) or [{"headline": f"{name} result", "asset_relevance": "direct"}]
    result = collector._get_news(market, symbol, name)
    assert calls
    assert result["news"][0]["asset_relevance"] == "direct"
    assert collector._news_relevance(market, symbol, name, f"{name} reports earnings") == "direct"
    assert collector._news_relevance(market, symbol, name, "Unrelated general story") == "background"


def test_cn_native_report_columns_symbols_and_same_period_growth(monkeypatch):
    calls = []
    profit = pd.DataFrame([
        {"REPORT_DATE": "2025-09-30", "TOTAL_OPERATE_INCOME": 200, "NETPROFIT": 10},
        {"REPORT_DATE": "2026-06-30", "TOTAL_OPERATE_INCOME": 100, "NETPROFIT": 5},
        {"REPORT_DATE": "2026-09-30", "TOTAL_OPERATE_INCOME": 240, "NETPROFIT": 12},
    ])
    def fetch(symbol):
        calls.append(symbol)
        return profit
    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(
        stock_profit_sheet_by_report_em=fetch,
        stock_balance_sheet_by_report_em=lambda **_: pd.DataFrame(),
        stock_cash_flow_sheet_by_report_em=lambda **_: pd.DataFrame(),
    ))
    result = providers.fetch_cn_financial_indicators("sh600498")
    statements = providers.fetch_cn_financial_statements("sh600498")
    assert set(calls) == {"SH600498"}
    assert result["revenue_growth"] == 20
    assert result["profit_margin"] == 5
    assert result["field_metadata"]["revenue_growth"]["period_end"] == "2026-09-30"
    assert statements["income_statement"]["period_type"] == "reported_ytd"
    assert statements["income_statement"]["total_revenue"] == 240


@pytest.mark.parametrize("market", ["CNStock", "HKStock"])
def test_foreign_macro_background_cannot_change_domestic_equity_score(market):
    service = FastAnalysisService.__new__(FastAnalysisService)
    assert service._calculate_macro_score({"VIX": {"price": 40}, "DXY": {"price": 100, "changePercent": 5}}, market) == 0


def test_semiannual_reporting_and_gaps_cannot_be_summed_as_four_quarter_ttm():
    collector = MarketDataCollector.__new__(MarketDataCollector)
    ticker = SimpleNamespace(quarterly_income_stmt=pd.DataFrame(
        [[240, 200, 220, 180], [12, 10, 11, 9]], index=["Total Revenue", "Net Income"],
        columns=pd.to_datetime(["2026-06-30", "2025-12-31", "2025-06-30", "2024-12-31"]),
    ))
    statements = collector._get_financial_statements("00003.HK", ticker=ticker, currency="HKD")
    assert "latest_quarter" not in statements
    assert statements["latest_report"]["period_type"] == "reported_interim"
    assert statements["ttm"] == {}
    assert statements["latest_report"]["derived"]["revenue_growth"] == pytest.approx((240 / 220 - 1) * 100)


def test_older_yahoo_period_does_not_replace_newer_domestic_statements(monkeypatch):
    collector = MarketDataCollector.__new__(MarketDataCollector)
    monkeypatch.setattr("app.services.market_data_collector.yf.Ticker", lambda _: SimpleNamespace(info={"symbol": "600498.SS"}, fast_info={}))
    older = {"latest_quarter": {"period_end": "2026-06-30", "income_statement": {"net_income": 9}}}
    collector._get_financial_statements = lambda *_args, **_kwargs: older
    collector._get_earnings_data = lambda *_args, **_kwargs: {}
    current = {"period_end": "2026-09-30", "income_statement": {"net_income": 12}}
    result = {"financial_statements": {"latest_quarter": copy.deepcopy(current)}}
    collector._enrich_cn_hk_fundamental_with_yfinance(result, "sh600498", is_hk=False)
    assert result["financial_statements"]["latest_quarter"] == current
    assert older["latest_quarter"]["period_end"] == "2026-06-30"


def test_no_news_means_no_fabricated_sentiment_coverage():
    payload = _payload()
    payload["news"] = [{"title": "Unrelated macro headline", "asset_relevance": "background"}]
    report = build_professional_report(payload, {"decision": "HOLD"})
    dimension = next(item for item in report["dimensions"] if item["key"] == "news_sentiment")
    assert dimension["status"] == "insufficient_data"


@pytest.mark.parametrize("market,symbol,reported", [
    ("CNStock", "600498", "600498.SS"),
    ("HKStock", "00700", "0700.HK"),
    ("USStock", "AAPL", "AAPL"),
])
def test_yahoo_dividend_yield_is_already_percentage_points(monkeypatch, market, symbol, reported):
    collector = MarketDataCollector.__new__(MarketDataCollector)
    collector._finnhub_client = None
    ticker = SimpleNamespace(info={"symbol": reported, "dividendYield": 0.4}, fast_info={})
    monkeypatch.setattr("app.services.market_data_collector.yf.Ticker", lambda _: ticker)
    collector._get_financial_statements = lambda *_args, **_kwargs: {}
    collector._get_earnings_data = lambda *_args, **_kwargs: {}
    if market == "USStock":
        result = collector._get_us_fundamental(symbol)
    else:
        result = {}
        collector._enrich_cn_hk_fundamental_with_yfinance(result, symbol, is_hk=market == "HKStock")
    assert result["dividend_yield"] == 0.4
    assert result["field_metadata"]["dividend_yield"]["unit"] == "percent"


@pytest.mark.parametrize("market", ["CNStock", "HKStock", "USStock", "Crypto"])
@pytest.mark.parametrize("support,resistance", [(24.87, 29.23), (20, 23)])
def test_scenarios_do_not_use_broken_levels_as_opposite_direction_targets(market, support, resistance):
    payload = _payload(market)
    report = build_professional_report(payload, {
        "decision": "HOLD", "market_data": {"current_price": 24.32, "support": support, "resistance": resistance},
    })
    bull, base, bear = report["scenarios"]
    assert bull["target_price"] == (resistance if resistance > 24.32 else None)
    assert bear["target_price"] == (support if support < 24.32 else None)
    assert base["target_price"] == 24.32
    assert bull["invalidation"] == bear["target_price"]
    assert bear["invalidation"] == bull["target_price"]


@pytest.mark.parametrize("market", ["CNStock", "HKStock", "USStock"])
def test_cached_provider_fields_keep_collection_instant_not_local_availability_date(monkeypatch, market):
    row = {
        "source": "provider", "period_end": "2026-09-30", "available_at": "2026-11-23",
        "ingested_at": "2026-11-22T16:04:31Z", "frequency": "quarterly", "currency": "CNY",
        "pe_ratio": 138.86, "free_cash_flow": 100,
        "metadata_json": {"collectedAt": "2026-11-22T16:04:30Z",
                          "analysisPayload": {"pe_ratio": 138.86, "free_cash_flow": 100}},
    }
    cursor = SimpleNamespace(execute=lambda *_: None, fetchone=lambda: row, close=lambda: None)
    @contextmanager
    def connect():
        yield SimpleNamespace(cursor=lambda: cursor)
    monkeypatch.setattr("app.services.fundamental_data.get_db_connection", connect)
    service = FundamentalDataService.__new__(FundamentalDataService)
    service.ensure_schema = lambda: None
    cached = service.latest_for_analysis(market=market, symbol="TEST", as_of=date(2026, 11, 23))
    metadata = cached["payload"]["field_metadata"]["pe_ratio"]
    assert metadata["as_of"] == "2026-11-22T16:04:30Z"
    assert metadata["period_type"] == "provider_latest"
    assert metadata["period_end"] is None
    payload = _payload(market)
    payload.update(collected_at="2026-11-22T16:10:00Z", fundamental=cached["payload"])
    snapshot = build_evidence_snapshot(payload)
    assert snapshot["collection"]["excluded_future_timestamp_items"] == 0
    assert any(item["metric"] == "pe_ratio" for item in snapshot["observations"])


def test_twelvedata_units_are_normalized_from_documented_provider_fields(monkeypatch):
    monkeypatch.setattr(providers, "_td_request", lambda *_: {"statistics": {
        "financials": {
            "most_recent_quarter": "2026-06-30", "profit_margin": .25004,
            "gross_margin": 46.57807, "operating_margin": .28788,
            "return_on_equity_ttm": 1.27125, "return_on_assets_ttm": .19302,
            "income_statement": {"quarterly_revenue_growth": 12, "quarterly_earnings_growth_yoy": -.5},
            "balance_sheet": {"total_debt_to_equity_mrq": 210.782, "current_ratio_mrq": 1.062},
        }, "dividends_and_splits": {"trailing_annual_dividend_yield": .005},
    }})
    result = providers.fetch_twelvedata_fundamental("HK00700", True)
    assert result["profit_margin"] == pytest.approx(25.004)
    assert result["gross_margin"] == pytest.approx(46.57807)
    assert result["roe"] == pytest.approx(127.125)
    assert result["current_ratio"] == pytest.approx(1.062)
    assert result["debt_to_equity"] == pytest.approx(2.1078)
    assert result["dividend_yield"] == .5
    assert result["revenue_growth"] == 1200
    assert result["earnings_growth"] == -50
    assert result["field_metadata"]["profit_margin"]["period_type"] == "ttm"


@pytest.mark.parametrize("period,dates", [
    ("Annual", ["2026-12-31", "2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]),
    ("Quarterly", ["2026-09-30", "2026-06-30", "2025-09-30", "2025-06-30", "2024-09-30"]),
])
def test_twelvedata_growth_uses_matching_fiscal_period_not_fifth_row(monkeypatch, period, dates):
    previous_date = str(int(dates[0][:4]) - 1) + dates[0][4:]
    items = [{"fiscal_date": date, "sales": 100 if date == previous_date else 120, "net_income": 10}
             for date in dates]
    monkeypatch.setattr(providers, "_td_request", lambda endpoint, *_: {
        "meta": {"period": period, "currency": "HKD"}, "income_statement": items,
    } if endpoint == "/income_statement" else {})
    result = providers.fetch_twelvedata_statements("HK00700", True)
    assert result["revenue_growth"] == 20
    assert result["financial_statements"]["income_statement"]["period_type"] == period.lower()
    assert result["financial_statements"]["income_statement"]["currency"] == "HKD"


def test_hk_reporting_currency_is_not_replaced_by_listing_currency():
    payload = _payload("HKStock")
    payload["symbol"] = "00700"
    period = payload["fundamental"]["financial_statements"]["latest_quarter"]
    period["currency"] = "CNY"
    period["cash_flow"] = {"free_cash_flow": 100, "currency": "CNY"}
    snapshot = build_evidence_snapshot(payload)
    revenue = next(item for item in snapshot["observations"] if item["metric"] == "financial.latest_quarter.income_statement.total_revenue")
    fcf = next(item for item in snapshot["observations"] if item["metric"] == "free_cash_flow")
    assert snapshot["instrument"]["quote_currency"] == "HKD"
    assert revenue["currency"] == fcf["currency"] == "CNY"
