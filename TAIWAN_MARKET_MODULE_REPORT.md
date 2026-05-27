# 台灣股市資訊分析模組實作報告

2026/05/27 Steve Peng：新增本實作報告。
修改原因：記錄 QuantDinger 台股資訊分析 MVP 的完成項目、限制、驗證結果與後續工作。
修改前內容：專案尚無台股資訊模組實作報告。
修改後功能：提供本次變更的交付摘要與安全邊界確認。

## 摘要

已完成 Taiwan market information module MVP，可用 mock provider 產生：

- TWSE/TPEx 股票池
- 前 20 檔強勢候選股
- 開盤前 report
- 收盤後 report
- 風險欄位與事件風險
- 資訊型候選股回測摘要
- 指定個股分析，可輸入股票名稱或代號查看單檔現況、量化分數、風險與觀察說明
- Flask API 與手動 CLI 觸發方式
- Gradio 本機圖形化介面，可雙擊啟動並直接顯示報告、表格、明細、風險與下載檔
- TWSE/TPEx 官方 OpenAPI provider 與 auto fallback

所有輸出均包含：

> 非投資建議，請自行評估風險

## 現有架構盤點

實際程式碼顯示：

- 後端：`backend_api_python/app`，Flask app factory 與 blueprint route registration。
- 資料來源：`backend_api_python/app/data_sources` 與 `backend_api_python/app/data_providers`。
- 回測：`backend_api_python/app/services/backtest.py` 與 `backend_api_python/app/routes/backtest.py`。
- Agent Gateway：`backend_api_python/app/routes/agent_v1`，`/api/agent/v1`。
- scheduler/worker 類功能：策略恢復、pending order worker、portfolio monitor、USDT payment worker 等位於 `app/__init__.py` 與 `app/services/*worker*`。
- 交易執行相關：`quick_trade.py`、`trading_executor.py`、`live_trading/*`、`ibkr_trading/*`、`mt5_trading/*`、`alpaca_trading/*`。
- 前端：README 指出 Vue source 在 sibling repo `QuantDinger-Vue`；本 repo 不含可直接修改的 frontend source。

本次最適合插入點是新增獨立 `app/services/taiwan_market.py` 與 `app/routes/taiwan_market.py`，避免接觸既有下單與交易執行路徑。

## README 與程式碼一致性

README 描述 QuantDinger 支援 backtest、paper/live execution、broker accounts、Agent Gateway 與 MCP。程式碼中確實存在 quick trade、broker、live trading、Agent Gateway 等路徑。

與本次任務相關的差異/限制：

- README 提到 web frontend source 需使用 sibling `QuantDinger-Vue`，此 repo 沒有前端 source，因此本次沒有新增台股前端頁面。
- README 強調交易能力，但本次台股模組刻意獨立為 read-only，不接任何交易 service。

## 新增檔案

- `backend_api_python/app/services/taiwan_market.py`
- `backend_api_python/app/routes/taiwan_market.py`
- `backend_api_python/scripts/generate_taiwan_market_report.py`
- `backend_api_python/app/ui/taiwan_market_app.py`
- `backend_api_python/tests/test_taiwan_market.py`
- `backend_api_python/tests/test_taiwan_market_ui.py`
- `docs/Taiwan_Market_Module.md`
- `TAIWAN_MARKET_MODULE_REPORT.md`
- `run_taiwan_market_ui.cmd`

## 修改檔案

- `backend_api_python/app/routes/__init__.py`
- `backend_api_python/requirements.txt`
- `README.md`

## 功能完成項目

- 支援 TWSE、TPEx 市場欄位；ETF 預設排除，可用 `include_etf=true` 或 CLI `--include-etf` 納入。
- 建立 `TaiwanMarketProvider` interface。
- 建立 `MockTaiwanMarketProvider`，可離線產生報告。
- 建立 `OfficialTaiwanOpenDataProvider` 擴充點，未確認授權前不自動抓外部資料。
- 擴充 `OfficialTaiwanOpenDataProvider`，可讀取 TWSE/TPEx 官方 OpenAPI 的日成交、估值、TPEx 注意/處置與部分法人資料。
- 新增 `AutoTaiwanMarketProvider`，官方資料失敗或不足時自動 fallback 到 mock，並輸出 `data_source_status`。
- 建立 universe builder，排除低流動性、全額交割、處置股、重大異常、資料不足與 ETF。
- 建立強勢股量化評分模型，輸出前 20 檔候選股。
- 每檔候選股包含代號、名稱、市場別、產業、強勢分數、信心分數、風險等級、觀察價位、流動性、主要理由、主要風險、追高適合度與事件風險。
- 新增指定個股分析，支援股票代號或名稱片段查詢，輸出現價、漲跌幅、量能、均線、強勢分數、信心分數、風險等級、股票池排除原因、觀察價位與事件風險。
- 產生開盤前 report。
- 產生收盤後 report，包含今日回顧、族群強弱、明日預測、異動股、轉弱股與避免追高股。
- 加入停損、停利、追高、跳空、流動性、重大訊息、財報、法說、除權息、交易成本、手續費、證交稅、滑價估算等風險參考。
- 加入候選股回測摘要介面，資料不足時可標記不可回測或低可信度。
- 提供 API 與 CLI 手動觸發。
- 提供 Gradio 本機 GUI，雙擊 `run_taiwan_market_ui.cmd` 可開啟 `http://127.0.0.1:7860`。
- 提供 Asia/Taipei 排程參考，不新增會下單的 worker。

## API

- `GET|POST /api/taiwan-market/report`
- `GET /api/taiwan-market/candidates`
- `GET /api/taiwan-market/backtest`
- `GET|POST /api/taiwan-market/stock-analysis`
- `GET /api/taiwan-market/sources`
- `GET /api/taiwan-market/schedule`

所有 API 都沿用既有 `login_required` JWT 驗證。

## 手動產生方式

在 `backend_api_python` 目錄：

```bash
python scripts/generate_taiwan_market_report.py --session pre_market --provider mock --top 20 --output taiwan_pre_market.json
python scripts/generate_taiwan_market_report.py --session post_market --provider mock --top 20 --output taiwan_post_market.json
python scripts/generate_taiwan_market_report.py --backtest --days 60 --top 20 --output taiwan_backtest.json
```

圖形化介面：

```text
雙擊 run_taiwan_market_ui.cmd
```

或在 `backend_api_python` 目錄：

```bash
python -m app.ui.taiwan_market_app
```

## 資料來源與授權

本 MVP 可使用 mock data，不需要 API key。GUI 預設 `auto` 模式會先嘗試 TWSE/TPEx 官方 OpenAPI，失敗時 fallback mock。

真實 provider 啟用前需確認：

- TWSE OpenAPI：https://openapi.twse.com.tw/
- TPEx OpenAPI：https://www.tpex.org.tw/openapi/
- MOPS：https://mops.twse.com.tw/
- TAIFEX OpenAPI：https://openapi.taifex.com.tw/
- TDCC：https://www.tdcc.com.tw/

若有授權、付費、API key、頻率限制或再利用限制，需先記錄與回報。

目前 official provider 已使用：

- TWSE `/exchangeReport/STOCK_DAY_ALL`
- TWSE `/exchangeReport/BWIBBU_ALL`
- TWSE `/fund/MI_QFIIS_sort_20`
- TPEx `/tpex_mainboard_daily_close_quotes`
- TPEx `/tpex_mainboard_peratio_analysis`
- TPEx `/tpex_trading_warning_information`
- TPEx `/tpex_disposal_information`
- TPEx `/tpex_3insti_trading`

## 驗證紀錄

已執行：

- `python -m compileall app/services/taiwan_market.py app/routes/taiwan_market.py scripts/generate_taiwan_market_report.py`
- `python -m pytest tests/test_taiwan_market.py -q`
- `python -m pytest tests/test_taiwan_market.py tests/test_taiwan_market_ui.py -q`
- `python -m compileall app/services/taiwan_market.py app/ui/taiwan_market_app.py`
- `python scripts/generate_taiwan_market_report.py --session pre_market --date 2026-05-27 --top 20 --output ..\taiwan_report_smoke.json`
- Python 讀回 UTF-8 JSON 並確認 disclaimer 與 20 檔候選股。

測試結果：

- `tests/test_taiwan_market.py` 與 `tests/test_taiwan_market_ui.py`：涵蓋 mock、official fixture、auto fallback、指定個股分析、GUI helper。
- CLI JSON UTF-8 讀回成功。

## 未完成項目

- TWSE/TPEx 官方 OpenAPI 已接入基礎資料；MOPS、TAIFEX、TDCC 尚未實作。
- official provider 尚未補完整歷史均線、完整三大法人日資料、完整產業分類與完整休市日曆；資料不足時已在報告中降低信心並標示事件風險。
- 尚未加入台灣完整休市日曆，目前只以週末判斷。
- 尚未修改前端，因為此 repo 不含 Vue frontend source。
- 尚未新增 Agent Gateway/MCP 台股工具；目前已有一般 Flask API 與 CLI。

## 明確安全確認

本次沒有新增任何真實下單、自動下單、半自動下單、paper trading、live trading、broker API、券商連線、order service、buy/sell button、委託單或交易執行功能。
