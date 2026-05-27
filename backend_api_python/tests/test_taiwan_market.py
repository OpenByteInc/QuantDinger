"""Taiwan market information module tests.

2026/05/27 Steve Peng：新增台股資訊分析模組的驗證案例。
修改原因：先以測試定義 read-only MVP 行為，避免後續實作誤接交易執行功能。
修改前代碼：本專案尚無台股強勢候選股報告測試。
修改後功能：驗證 mock provider、股票池過濾、前 20 名報告、回測摘要與 API 輸出。
"""
from __future__ import annotations

import json
from datetime import date

from app.services.taiwan_market import (
    AutoTaiwanMarketProvider,
    MockTaiwanMarketProvider,
    OfficialTaiwanOpenDataProvider,
    StockSnapshot,
    TAIWAN_MARKET_DISCLAIMER,
    TaiwanMarketService,
)


def test_premarket_report_returns_top_20_read_only_candidates():
    """功能：使用 mock 台股資料產生開盤前前 20 檔強勢候選股報告。"""
    service = TaiwanMarketService(provider=MockTaiwanMarketProvider())

    report = service.generate_report("pre_market", as_of=date(2026, 5, 27), top_n=20)

    assert report["disclaimer"] == TAIWAN_MARKET_DISCLAIMER
    assert report["session"] == "pre_market"
    assert report["market_scope"] == ["TWSE", "TPEx"]
    assert len(report["top_candidates"]) == 20
    first = report["top_candidates"][0]
    assert "_historical_returns" not in first
    for key in [
        "code",
        "name",
        "market",
        "industry",
        "strength_score",
        "confidence_score",
        "risk_level",
        "observe_entry_price_range",
        "stop_loss_observe_price",
        "take_profit_observe_range",
        "max_observe_position_pct",
        "liquidity",
        "primary_reasons",
        "primary_risks",
        "chasing_suitability",
        "event_risk",
    ]:
        assert key in first

    serialized = json.dumps(report, ensure_ascii=False).lower()
    for forbidden in ["broker_api", "order_service", "buy_button", "sell_button", "live_trading"]:
        assert forbidden not in serialized


def test_universe_builder_excludes_low_quality_or_etf_by_default():
    """功能：股票池預設排除 ETF、低流動性、全額交割、處置股與資料不足個股。"""
    snapshots = [
        StockSnapshot(
            code="1111",
            name="正常科技",
            market="TWSE",
            industry="半導體",
            close=100,
            previous_close=96,
            volume=4_000_000,
            turnover=400_000_000,
            day_high=104,
            day_low=95,
            ma5=98,
            ma20=90,
            ma60=82,
            volume_ma20=2_000_000,
            foreign_buy_sell=10_000_000,
            investment_trust_buy_sell=3_000_000,
            dealer_buy_sell=2_000_000,
            data_days=120,
        ),
        StockSnapshot(
            code="2222",
            name="低量公司",
            market="TPEx",
            industry="生技",
            close=30,
            previous_close=30,
            volume=30_000,
            turnover=900_000,
            day_high=31,
            day_low=29,
            ma5=30,
            ma20=30,
            ma60=30,
            volume_ma20=25_000,
            data_days=120,
        ),
        StockSnapshot(
            code="3333",
            name="全額交割",
            market="TWSE",
            industry="其他",
            close=20,
            previous_close=20,
            volume=1_000_000,
            turnover=20_000_000,
            day_high=21,
            day_low=19,
            ma5=20,
            ma20=20,
            ma60=20,
            volume_ma20=800_000,
            is_full_delivery=True,
            data_days=120,
        ),
        StockSnapshot(
            code="0050",
            name="台灣50",
            market="TWSE",
            industry="ETF",
            close=180,
            previous_close=179,
            volume=8_000_000,
            turnover=1_440_000_000,
            day_high=181,
            day_low=178,
            ma5=178,
            ma20=175,
            ma60=170,
            volume_ma20=6_000_000,
            is_etf=True,
            data_days=120,
        ),
    ]
    service = TaiwanMarketService(provider=MockTaiwanMarketProvider(snapshots=snapshots))

    universe = service.build_universe(include_etf=False)

    assert [item.code for item in universe] == ["1111"]


def test_postmarket_report_and_backtest_summary_are_available():
    """功能：收盤後報告需包含回顧欄位，並可回測每日前 20 候選股摘要。"""
    service = TaiwanMarketService(provider=MockTaiwanMarketProvider())

    post = service.generate_report("post_market", as_of=date(2026, 5, 27), top_n=20)
    backtest = service.backtest_top_candidates(days=20, top_n=20)

    assert post["session"] == "post_market"
    assert "today_review" in post
    assert "sector_strength" in post
    assert "tomorrow_prediction" in post
    assert len(post["top_candidates"]) == 20
    assert backtest["disclaimer"] == TAIWAN_MARKET_DISCLAIMER
    assert backtest["metrics"]["sample_days"] == 20
    assert backtest["metrics"]["candidate_count_per_day"] == 20
    assert backtest["metrics"]["confidence"] in {"normal", "low", "not_backtestable"}


def test_taiwan_market_report_api_uses_read_only_envelope(client, monkeypatch):
    """功能：API 可手動觸發台股報告，且維持資訊型 read-only 回應。"""
    import app.utils.auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "verify_token",
        lambda token: {"sub": "tester", "user_id": 1, "role": "admin"},
    )

    response = client.get(
        "/api/taiwan-market/report?session=pre_market&provider=mock&top=20",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 1
    assert payload["data"]["disclaimer"] == TAIWAN_MARKET_DISCLAIMER
    assert len(payload["data"]["top_candidates"]) == 20


def test_official_provider_parses_twse_and_tpex_fixture_payloads():
    """功能：解析 TWSE/TPEx 官方 OpenAPI fixtures，資料不足時仍標記低可信度風險。"""

    fixtures = {
        "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL": [
            {
                "Code": "2330",
                "Name": "台積電",
                "TradeVolume": "28000000",
                "TradeValue": "28000000000",
                "OpeningPrice": "1000.00",
                "HighestPrice": "1010.00",
                "LowestPrice": "990.00",
                "ClosingPrice": "1005.00",
                "Change": "+15.00",
            }
        ],
        "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL": [
            {"Code": "2330", "Name": "台積電", "PEratio": "25.3", "DividendYield": "1.7", "PBratio": "6.8"}
        ],
        "https://openapi.twse.com.tw/v1/fund/MI_QFIIS_sort_20": [],
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes": [
            {
                "SecuritiesCompanyCode": "6147",
                "CompanyName": "頎邦",
                "Close": "72.50",
                "Change": "+2.50",
                "Open": "70.20",
                "High": "73.00",
                "Low": "69.80",
                "TradingShares": "5200000",
                "TransactionAmount": "377000000",
            }
        ],
        "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis": [
            {"SecuritiesCompanyCode": "6147", "CompanyName": "頎邦", "PriceEarningRatio": "14.5"}
        ],
        "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information": [
            {"SecuritiesCompanyCode": "6147", "CompanyName": "頎邦", "TradingInformation": "注意股票"}
        ],
        "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information": [],
        "https://www.tpex.org.tw/openapi/v1/tpex_3insti_trading": [
            {"SecuritiesCompanyCode": "6147", "CompanyName": "頎邦", "NetBuy": "2364"}
        ],
    }

    provider = OfficialTaiwanOpenDataProvider(http_get=lambda url: fixtures[url])
    snapshots = provider.list_snapshots()
    by_code = {item.code: item for item in snapshots}

    assert {"2330", "6147"}.issubset(by_code)
    assert by_code["2330"].market == "TWSE"
    assert by_code["2330"].close == 1005.0
    assert by_code["6147"].market == "TPEx"
    assert by_code["6147"].has_major_abnormality is True
    assert any("官方資料" in risk for risk in by_code["2330"].event_risks)
    assert TaiwanMarketService(provider=provider).rank_candidates(top_n=2)


def test_auto_provider_falls_back_to_mock_when_official_unavailable():
    """功能：official provider 失敗時，auto provider 不阻斷 GUI 與報告流程。"""

    def _broken_http_get(_url):
        raise RuntimeError("network down")

    provider = AutoTaiwanMarketProvider(
        official_provider=OfficialTaiwanOpenDataProvider(http_get=_broken_http_get),
        fallback_provider=MockTaiwanMarketProvider(),
    )

    report = TaiwanMarketService(provider=provider).generate_report("pre_market", as_of=date(2026, 5, 27), top_n=20)

    assert report["provider"] == "auto(mock)"
    assert report["data_source_status"]["fallback_used"] is True
    assert "network down" in report["data_source_status"]["message"]
    assert len(report["top_candidates"]) == 20


def test_analyze_stock_by_code_returns_read_only_status_snapshot():
    """功能：指定個股分析可依股票代號回傳現況、分數、風險與觀察說明。

    2026/05/27 Steve Peng：新增原因：使用者需要輸入股票名稱或代號後查看單檔股票現況與說明。
    修改前代碼：台股模組只提供排行榜與整體報告，沒有指定個股查詢入口。
    修改後功能：服務層提供 read-only 單檔分析，不加入下單、券商或交易執行功能。
    """
    service = TaiwanMarketService(provider=MockTaiwanMarketProvider())

    analysis = service.analyze_stock("2330", as_of=date(2026, 5, 27), include_etf=False)

    assert analysis["disclaimer"] == TAIWAN_MARKET_DISCLAIMER
    assert analysis["status"] == "found"
    assert analysis["query"] == "2330"
    assert analysis["stock"]["code"] == "2330"
    assert analysis["current_snapshot"]["close"] > 0
    assert analysis["quantitative_analysis"]["strength_score"] > 0
    assert analysis["observation_reference"]["suggested_observation"]
    assert "非投資建議" in analysis["observation_reference"]["guidance_note"]

    serialized = json.dumps(analysis, ensure_ascii=False).lower()
    for forbidden in ["broker_api", "order_service", "buy_button", "sell_button", "live_trading", "paper_trading"]:
        assert forbidden not in serialized


def test_analyze_stock_by_name_and_not_found_suggestions():
    """功能：指定個股分析支援名稱查詢，找不到時回傳候選提示而不是丟錯。

    2026/05/27 Steve Peng：新增原因：使用者可能輸入股票名稱而非代號。
    修改前代碼：無名稱查詢或找不到時的友善提示。
    修改後功能：名稱可做大小寫不敏感比對，找不到時回傳可讀訊息與相近標的。
    """
    snapshots = [
        StockSnapshot(
            code="1111",
            name="Alpha Semiconductor",
            market="TWSE",
            industry="半導體",
            close=100,
            previous_close=96,
            volume=4_000_000,
            turnover=400_000_000,
            day_high=104,
            day_low=95,
            ma5=98,
            ma20=90,
            ma60=82,
            volume_ma20=2_000_000,
            data_days=120,
        ),
        StockSnapshot(
            code="2222",
            name="Beta Cloud",
            market="TPEx",
            industry="雲端服務",
            close=50,
            previous_close=49,
            volume=2_000_000,
            turnover=100_000_000,
            day_high=51,
            day_low=48,
            ma5=49,
            ma20=47,
            ma60=45,
            volume_ma20=1_200_000,
            data_days=120,
        ),
    ]
    service = TaiwanMarketService(provider=MockTaiwanMarketProvider(snapshots=snapshots))

    by_name = service.analyze_stock("alpha", as_of=date(2026, 5, 27))
    missing = service.analyze_stock("cloudx", as_of=date(2026, 5, 27))

    assert by_name["status"] == "found"
    assert by_name["stock"]["code"] == "1111"
    assert missing["status"] == "not_found"
    assert missing["suggestions"]
    assert missing["suggestions"][0]["code"] == "2222"


def test_taiwan_market_stock_analysis_api_uses_read_only_envelope(client, monkeypatch):
    """功能：API 提供指定個股分析，回傳 read-only 分析資料。

    2026/05/27 Steve Peng：新增原因：外部 UI 或手動工具需要以 API 觸發單檔查詢。
    修改前代碼：只有報告、排行榜、回測與資料來源 API。
    修改後功能：新增 `/api/taiwan-market/stock-analysis`，只回傳資訊分析與風險說明。
    """
    import app.utils.auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "verify_token",
        lambda token: {"sub": "tester", "user_id": 1, "role": "admin"},
    )

    response = client.get(
        "/api/taiwan-market/stock-analysis?query=2330&provider=mock",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == 1
    assert payload["data"]["status"] == "found"
    assert payload["data"]["stock"]["code"] == "2330"
