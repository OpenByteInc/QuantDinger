# sector-overview 数据契约

`sector-overview/scripts/generate_report.py` 默认从 `--data-dir` 读取以下 JSON 文件。

## 1. `sector_definition.json`

可选文件。推荐字段：

- `industry_name`
- `industry_level`
- `industry_value`
- `industry_source`
- `benchmark_order_book_id`
- `factor_date`

用途：

- 记录股票池生成规则和基准指数

## 2. `stock_pool.json`

允许格式：

```json
[
  { "order_book_id": "600519.XSHG" }
]
```

也允许直接包含 `industry level 0` 的完整行。

用途：

- 定义行业成分股

## 3. `instrument_meta.json`

典型字段：

- `order_book_id`
- `symbol`
- `sector_code_name`
- `industry_name`

用途：

- 提供公司名称和基础元数据

## 4. `industry_map.json`

典型字段：

- `order_book_id`
- `first_industry_name`
- `second_industry_name`
- `third_industry_name`

用途：

- 行业分层描述
- 校验股票池确实来自同一行业口径

## 5. `historical_financials.json` / `latest_financials.json`

典型字段：

- `order_book_id`
- `quarter`
- `info_date`
- `revenue`
- `net_profit`
- `gross_profit`
- `total_assets`
- `total_liabilities`
- `cash_from_operating_activities`

用途：

- 行业整体财务结构
- 计算营收/利润同比、毛利率、资产负债率、现金转化率

## 6. `roe.json`

典型字段：

- `order_book_id`
- `date`
- `return_on_equity_weighted_average`

用途：

- 行业盈利质量和龙头对比

## 7. `market_cap.json`

典型字段：

- `order_book_id`
- `date`
- `market_cap`

用途：

- 行业总市值、CR3/CR5/CR10 计算
- 龙头梯队与集中度分析

## 8. `pe_ratio.json` / `pb_ratio.json` / `dividend_yield.json`

典型字段：

- `order_book_id`
- `date`
- `pe_ratio` / `pb_ratio` / `dividend_yield`

说明：

- `dividend_yield` 原始值为 bps，生成报告时需要除以 `100` 后按百分比展示

用途：

- 行业估值中位数
- 机会筛选
- 股东回报比较

补充说明：

- `PE` 相关横向比较默认以正值样本为主，亏损或异常高估值样本会单独作为压力样本观察，不直接充当“低估值”候选

## 9. `price_period.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`

用途：

- 计算股票池区间收益

## 10. `benchmark_price.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`

用途：

- 计算行业相对基准的超额收益

## 11. `web_search_findings.json`

该文件可选，仅用于补充市场规模、行业趋势、监管政策、竞争格局和并购整合等网络搜索信息。

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

- 补充 RQData 无法直接提供的行业背景层
- 为行业状态判断和投资线索提供实时网络搜索信息

## 解析约定

- 所有文件都允许 `{"data": [...]}`、`{"data": {...}}`、`[...]`、`{...}` 四种包装方式
- 同一股票同一季度若存在多条财务记录，脚本会按 `info_date` 选择最新披露版本
- 估值和价格文件必须使用最近非空因子日；如果最近交易日返回空字符串，需要继续向前回溯
- `web_search_findings.json` 不能替代财务、估值、行情和公告等结构化主数据
- 报告正文面向客户阅读，附录仅保留必要口径说明，不回显输入文件清单或执行过程
