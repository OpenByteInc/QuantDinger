# 台灣股市資訊分析與強勢候選股報告模組

2026/05/27 Steve Peng：新增本文件。
修改原因：說明台股資訊模組的架構、資料來源、API、CLI、風險欄位與安全限制。
修改前內容：專案尚無台股 TWSE/TPEx 強勢候選股模組文件。
修改後功能：提供維護者與使用者可手動產生報告、檢查資料授權、理解 read-only 邊界的操作說明。

## 安全限制

本模組只做資訊蒐集、量化分析、候選股篩選、風險提示、報告產生與資訊型回測摘要。所有輸出都會標示：

> 非投資建議，請自行評估風險

本模組沒有新增任何真實下單、自動下單、半自動下單、paper trading、live trading、broker API、券商連線、order service、buy/sell button、委託單或交易執行功能。實際買賣必須由使用者自行到券商系統人工操作。

## 專案插入點

QuantDinger 目前 repo 主要包含 Flask 後端、Docker Compose、文件、MCP server。README 說明前端 Vue 原始碼在 sibling repo `QuantDinger-Vue`，此 repo 預設只使用 prebuilt frontend image，因此本次未修改前端 UI。

本模組新增位置：

- `backend_api_python/app/services/taiwan_market.py`：台股 provider interface、mock provider、universe builder、強勢評分、風險參考、報告與回測摘要。
- `backend_api_python/app/routes/taiwan_market.py`：`/api/taiwan-market/*` read-only API。
- `backend_api_python/app/ui/taiwan_market_app.py`：Gradio 本機圖形化介面，直接顯示報告、表格、明細與下載檔。
- `backend_api_python/scripts/generate_taiwan_market_report.py`：手動產生台股報告或回測摘要。
- `backend_api_python/tests/test_taiwan_market.py`：mock provider、股票池過濾、報告、回測與 API smoke tests。
- `backend_api_python/tests/test_taiwan_market_ui.py`：GUI helper 的摘要、表格、明細與下載檔測試。

## 資料 Provider 設計

`TaiwanMarketProvider` 是台股資料 adapter 介面：

- `list_snapshots(as_of)`：回傳同一批次的 TWSE/TPEx 股票日級資料。
- `get_market_context(as_of)`：回傳加權/櫃買方向、族群強弱與事件風險。

目前可用 provider：

- `auto`：GUI 預設。先使用 TWSE/TPEx 官方 OpenAPI；若網路、欄位或官方端點失敗，會回退 `mock` 並在報告中顯示 `data_source_status`。
- `official`：使用 TWSE/TPEx 官方 OpenAPI。已接日成交、估值、TPEx 注意/處置與部分法人資料；完整歷史均線、MOPS 重大訊息、TDCC 籌碼與完整休市日曆仍需後續補齊。
- `mock`：免 API key，可離線產生前 20 檔候選股、開盤前/收盤後報告與回測摘要。

## 官方或可信資料來源

後續真實 provider 建議優先研究：

- TWSE OpenAPI：https://openapi.twse.com.tw/
- TPEx OpenAPI：https://www.tpex.org.tw/openapi/
- MOPS 公開資訊觀測站：https://mops.twse.com.tw/
- TAIFEX OpenAPI：https://openapi.taifex.com.tw/
- TDCC 集保結算所：https://www.tdcc.com.tw/

若資料需要授權、API key、付費、頻率限制或有再利用限制，需先記錄於部署文件，不可硬寫死在程式碼。

目前 official provider 使用的主要端點：

- TWSE：`/exchangeReport/STOCK_DAY_ALL`、`/exchangeReport/BWIBBU_ALL`、`/fund/MI_QFIIS_sort_20`
- TPEx：`/tpex_mainboard_daily_close_quotes`、`/tpex_mainboard_peratio_analysis`、`/tpex_trading_warning_information`、`/tpex_disposal_information`、`/tpex_3insti_trading`

若官方資料缺少歷史均線、產業或事件欄位，候選股會標示事件風險並降低信心分數，不會假裝資料完整。

## 股票池 Universe Builder

預設只納入 `TWSE` 與 `TPEx` 個股，ETF 預設分開處理且不納入排行。以下情況會排除：

- ETF 且未指定 `include_etf=true`
- 成交量低於 100,000 股或成交金額低於 10,000,000
- 全額交割、處置股、重大異常
- 資料天數少於 60 日
- 關鍵價格、均線或均量欄位缺漏

## 強勢評分模型

候選股以以下項目加權：

- 價格動能：單日變動、收盤價相對 20 日均線、20 日均線相對 60 日均線。
- 量能動能：成交量相對 20 日均量。
- 法人籌碼：外資、投信、自營商合計相對成交金額。
- 流動性品質：成交金額與成交量。
- 事件扣分：法說、重大訊息、除權息等事件風險。

每檔候選股輸出：

- 代號、名稱、市場別、產業
- 強勢分數、信心分數、風險等級
- 觀察進場價位區間、停損觀察價位、停利/賣出觀察區間
- 最大觀察部位比例、流動性、主要理由、主要風險
- 是否適合追高、事件風險

上述價位都是觀察參考，不是交易指令。

## 報告類型

開盤前報告：

- 今日大盤方向
- 方向依據
- 前 20 檔強勢候選股
- 風險參考與手動操作提醒

收盤後報告：

- 今日回顧
- 族群強弱
- 明日走勢預測
- 明日前 20 檔候選股
- 異動股、轉弱股、應避免追高股

週末或休市日：

- 目前 MVP 以週末判斷輸出休市報告。
- 真實台股休市日曆需由 official provider 或日曆資料補齊。

## API

所有端點都需要既有 JWT `Authorization: Bearer <token>`。

產生報告：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8888/api/taiwan-market/report?session=pre_market&provider=mock&top=20"
```

候選股排行：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8888/api/taiwan-market/candidates?provider=mock&top=20"
```

資訊型回測摘要：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8888/api/taiwan-market/backtest?provider=mock&days=60&top=20"
```

資料來源說明：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8888/api/taiwan-market/sources"
```

排程建議：

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8888/api/taiwan-market/schedule"
```

## 手動產生報告

在 `backend_api_python` 目錄執行：

```bash
python scripts/generate_taiwan_market_report.py --session pre_market --provider mock --top 20 --output taiwan_pre_market.json
python scripts/generate_taiwan_market_report.py --session post_market --provider mock --top 20 --output taiwan_post_market.json
python scripts/generate_taiwan_market_report.py --backtest --days 60 --top 20 --output taiwan_backtest.json
```

輸出 JSON 使用 UTF-8，包含 disclaimer 與 read-only notice。

## 圖形化介面

Windows 使用者可直接雙擊專案根目錄：

```text
run_taiwan_market_ui.cmd
```

啟動器會：

- 檢查 Python 3。
- 檢查 `flask` 與 `gradio`，缺少時依 `backend_api_python/requirements.txt` 安裝。
- 啟動本機 Gradio UI。
- 自動開啟 `http://127.0.0.1:7860`。

也可在 `backend_api_python` 目錄手動執行：

```bash
python -m app.ui.taiwan_market_app
```

GUI 分頁：

- 開盤前報告
- 收盤後報告
- 強勢候選股排行榜 / 個股明細
- 回測摘要
- 資料來源/授權說明

GUI 控制項：

- 資料來源：`auto`、`official`、`mock`
- 日期：`YYYY-MM-DD`，可留空使用 Asia/Taipei 今日
- Top N 候選股數量
- 是否納入 ETF
- 回測天數

GUI 輸出：

- 報告摘要 Markdown
- 候選股表格
- 個股明細
- 風險提示
- JSON 下載
- CSV 下載

## 排程建議

本 MVP 提供排程參考，不啟動新的背景 worker：

- Asia/Taipei 開盤前約 08:30
- Asia/Taipei 收盤後約 14:30 或資料更新後
- 週末不執行一般交易日報告；可輸出休市報告

若日後接入 scheduler，請只呼叫報告產生 API 或 service，不得接入任何交易執行、委託、券商連線或 broker API。

## 回測說明

`backtest_top_candidates()` 使用候選股內的歷史報酬序列做等權資訊型模擬，輸出：

- 勝率
- 平均日報酬
- 累積報酬
- 最大回撤
- Sharpe-like 指標
- 交易成本、證交稅與滑價假設
- 資料可信度

若可用歷史資料少於最低門檻，會標記 `not_backtestable` 或 `low` confidence。

## 後續擴充

- 實作 TWSE/TPEx 真實 provider 前，先確認資料授權與頻率限制。
- 加入完整台灣休市日曆。
- 若前端 Vue source repo 納入工作區，可新增 Taiwan Market / Opportunity Radar read-only 頁面，但不得新增下單按鈕。
- 可擴充 MOPS 重大訊息、財報、法說與除權息事件風險欄位。
