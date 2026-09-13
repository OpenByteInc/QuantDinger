# initiating-coverage 数据契约

`initiating-coverage/scripts/generate_report.py` 默认从 `--data-dir` 读取以下 JSON 文件。

## 1. `company_info.json`

典型字段：

- `order_book_id`
- `symbol`
- `abbrev_symbol`
- `industry_name`
- `listed_date`
- `office_address`
- `province`
- `sector_code_name`

用途：

- 公司名称、上市时间、办公地、基础资料

## 2. `industry.json`

典型字段：

- `first_industry_name`
- `second_industry_name`
- `third_industry_name`

用途：

- 行业口径与 peers 选择锚点

## 3. `shares.json`

典型字段：

- `date`
- `total`
- `total_a`
- `circulation_a`
- `free_circulation`

用途：

- 总股本、流通股本、自由流通股本和自由流通比例

## 4. `shareholder_top10.json`

典型字段：

- `end_date`
- `info_date`
- `rank`
- `shareholder_name`
- `hold_percent_total`
- `hold_percent_float`
- `shareholder_kind`

用途：

- 十大股东结构、集中度、国资 / 基金 / 外资等持有人画像

## 5. `historical_financials.json`

典型字段：

- `quarter`
- `info_date`
- `revenue`
- `net_profit`
- `gross_profit`
- `total_assets`
- `total_liabilities`
- `total_equity`
- `cash_from_operating_activities`
- `cash_flow_from_investing_activities`
- `cash_flow_from_financing_activities`

用途：

- 自动识别最新财报季度
- 生成 5 年同口径财务轨迹
- 计算同比、单季度、毛利率、资产负债率、现金转化率

## 6. `roe_history.json`

典型字段：

- `date`
- `return_on_equity_weighted_average`

用途：

- ROE 时间序列与当前盈利质量

## 7. `market_cap.json` / `pe_ratio.json` / `pb_ratio.json` / `dividend_yield.json`

典型字段：

- `date`
- 对应 factor 字段

说明：

- `dividend_yield` 原始值为 bps，生成报告时需要除以 `100` 后按百分比展示

用途：

- 当前估值、股东回报与 peer percentile

## 8. `price_history.json`

典型字段：

- `datetime`
- `close`
- `volume`
- `total_turnover`

用途：

- 1M / 3M / 6M / 1Y / 3Y 绝对收益
- 相对基准收益

## 9. `turnover_history.json`

典型字段：

- `tradedate`
- `today`
- `week`
- `month`
- `year`

用途：

- 最新换手率与历史中位数对比

## 10. `benchmark_price.json`

典型字段：

- `datetime`
- `close`

用途：

- 计算相对沪深300等基准的超额收益

## 11. `dividend_history.json`

典型字段：

- `quarter`
- `dividend_cash_before_tax`
- `round_lot`
- `declaration_announcement_date`
- `ex_dividend_date`

用途：

- 分红历史、年度派现节奏与每手税前现金分配

## 12. `consensus.json`

典型字段：

- `date`
- `create_tm`
- `report_year_t`
- `comp_con_operating_revenue_t / t1 / t2 / t3`
- `comp_con_net_profit_t / t1 / t2 / t3`
- `comp_con_eps_t / t1 / t2 / t3`
- `con_targ_price`

用途：

- 最新一致预期与 60 天前预期对比
- 目标价变化

## 13. `research_reports.json`

典型字段：

- `create_tm`
- `date`
- `report_title`
- `summary`
- `summaries.core_view`
- `institute`
- `author`
- `fiscal_year`
- `net_profit_t / t1 / t2`
- `eps_t / t1 / t2`
- `targ_price`
- `report_main_id`
- `data_source`

用途：

- 卖方口径摘要
- 目标价、盈利预测与机构分布补充

补充说明：

- 若存在 `data_source` 字段，应将 `0` 视为公司研报主样本；其他来源默认不进入最终正文
- 最终报告优先读取 `summaries.core_view` 等客户可读摘要，不直接截断原始 `summary`

## 14. `peer_pool.json`

允许格式：

```json
[
  {
    "order_book_id": "600519.XSHG",
    "selection_level": "third",
    "market_cap": 2000000000000
  }
]
```

用途：

- 显式记录可比公司池来源和选择结果

## 15. `peer_company_info.json` / `peer_industry.json` / `peer_latest_financials.json`

用途：

- 提供 peers 名称、行业归属、最新财务快照

## 16. `peer_roe.json` / `peer_market_cap.json` / `peer_pe_ratio.json` / `peer_pb_ratio.json` / `peer_dividend_yield.json`

用途：

- 生成可比公司估值与盈利质量对比表

## 17. `web_search_findings.json`

该文件可选，仅用于补充管理层、行业、政策、竞争或公司近期动态的定性背景。

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
- `stance`

说明：

- `published_at` 是源内容发布时间，不是报告日期
- `retrieved_at` 是实际检索时间
- `source_type` / `confidence` 需遵守 [references/web_search.md](web_search.md) 的来源等级约束
- `web_search_findings.json` 不能替代财务、估值、价格、分红、一致预期和 peer 量化主数据
- 若该文件存在，报告会将其压缩为客户可读的补充背景，不会直接回显原始字段

## 解析约定

- 所有文件都允许 `{"data": [...]}`、`{"data": {...}}`、`[...]`、`{...}` 四种包装方式
- 同一股票同一季度若存在多条财务记录，脚本会按 `info_date <= report-date` 选择最新披露版本
- `consensus` 年份映射按 `report_year_t + offset`
- 研报必须做相关性过滤，标题或 `report_main_id` 优先，不能把纯行业周报直接塞进正文
- `web_search_findings.json` 若存在，记录必须包含完整来源元数据，且置信度不能超过来源类别上限
- 最终报告面向客户阅读，附录仅保留必要口径说明，不回显内部执行流程或文件名
