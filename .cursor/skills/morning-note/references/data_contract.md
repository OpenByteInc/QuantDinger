# morning-note 数据契约

`morning-note/scripts/generate_report.py` 默认从 `--data-dir` 读取以下 JSON 文件。

## 1. `stock_pool.json`

允许格式：

```json
{
  "data": ["600519.XSHG", "300750.XSHE"]
}
```

用途：

- 明确盘前覆盖股票池
- 与元数据文件交叉校验公司名称

## 2. `instrument_meta.json`

典型字段：

- `order_book_id`
- `symbol`
- `abbrev_symbol`
- `listed_date`
- `sector_code_name`

用途：

- 映射股票代码与公司简称
- 生成覆盖范围文字

## 3. `latest_earnings.json`

典型字段：

- `order_book_id`
- `quarter` 或 `report_period`
- `info_date` 或 `report_date`
- `revenue`
- `net_profit`

用途：

- 识别隔夜窗口内的财报披露
- 为执行摘要和重点跟踪项提供财务事实

补充说明：

- 晨会纪要只消费隔夜窗口内已披露的记录，不能把更早的财务数据写成“隔夜更新”

## 4. `price_recent.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`
- `total_turnover`

用途：

- 回顾昨日股价表现
- 识别相对强弱个股

补充说明：

- 至少需要两个交易日观察值才能计算涨跌幅

## 5. `hs300_recent.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`

用途：

- 计算沪深300基准涨跌幅
- 给覆盖池相对强弱提供参考基线

## 6. `dividend_news.json`

典型字段：

- `order_book_id`
- `announcement_date`
- `ex_dividend_date`
- `book_closure_date`
- `payable_date`
- `dividend_cash_before_tax`
- `cash_dividend_per_share`

用途：

- 识别新披露分红信息
- 标记临近除权除息事项

## 7. `announcement_raw.json`

典型字段：

- `order_book_id`
- `title` / `announcement_title` / `info_name`
- `announcement_date` / `ann_date` / `pub_date` / `info_date`
- `announcement_link`
- `info_type`
- `media`

用途：

- 识别隔夜重点公告
- 为盘前关注名单保留原始追溯链接

补充说明：

- 若存在 `announcement_link`，高优先级事项应在正文中保留客户可点击链接
- 客户稿可以保留原文链接，但不能暴露内部字段名

## 8. `web_search_findings.json`

该文件可选，仅用于补充宏观、政策、海外市场、行业新闻和监管动态。

每条记录至少包含：

- `query`
- `source_name`
- `source_type`
- `title`
- `url`
- `published_at`
- `retrieved_at`
- `summary`
- `why_relevant`
- `confidence`
- `finding_type`

推荐附加字段：

- `subject`
- `related_entities`

用途：

- 补充 RQData 无法直接提供的盘前宏观和行业语境
- 支持执行摘要里的“盘前定位”与“今日重点关注”

限制：

- `web_search_findings.json` 不能替代个股行情、公告、财报和分红等结构化主数据
- 若未提供该文件，晨会仍可交付，但需保持“结构化盘前纪要”边界，不能伪造实时新闻
