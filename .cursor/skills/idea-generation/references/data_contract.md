# idea-generation 数据契约

`idea-generation/scripts/build_screening_snapshot.py` 默认从 `--data-dir` 读取原始 JSON 文件，并生成 `idea_screening_snapshot.json`。当前 LLM 需先回写其中 `summaries.*`，再由 `idea-generation/scripts/generate_report.py` 生成最终 Markdown。

## 1. `stock_pool.json`

允许格式：

```json
[
  { "order_book_id": "600519.XSHG" }
]
```

或：

```json
{
  "data": [
    { "order_book_id": "600519.XSHG" }
  ]
}
```

最关键字段：

- `order_book_id`

用途：

- 定义筛选股票池

## 2. `instrument_meta.json`

典型字段：

- `order_book_id`
- `symbol`
- `display_name`
- `sector_code_name`
- `industry_name`

用途：

- 补充公司名称和行业 / 板块信息

## 3. `latest_financials.json`

典型字段：

- `order_book_id`
- `quarter`
- `info_date`
- `revenue`
- `net_profit`
- `total_assets`
- `total_liabilities`

用途：

- 为每只股票抽取最近一期财务快照
- 计算资产负债率

## 4. `financials_history.json`

字段与 `latest_financials.json` 一致。

用途：

- 查找同季度去年同期数据
- 计算营收同比、净利润同比

## 5. `roe.json`

典型字段：

- `order_book_id`
- `date`
- `return_on_equity_weighted_average`

用途：

- 质量与价值筛选

## 6. `market_cap.json`

典型字段：

- `order_book_id`
- `date`
- `market_cap`

用途：

- 候选排序与规模描述

## 7. `pe_ratio.json`

典型字段：

- `order_book_id`
- `date`
- `pe_ratio`

用途：

- 价值筛选

## 8. `pb_ratio.json`

典型字段：

- `order_book_id`
- `date`
- `pb_ratio`

用途：

- 价值筛选

## 9. `web_search_findings.json`

该文件可选，仅用于量化筛选后的主题 / 政策 / 催化剂验证。

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
- `related_tickers`
- `stance`

说明：

- `generate_report.py` 不直接读取该文件；当前 LLM 应在回写 `idea_screening_snapshot.json -> summaries.*` 前读取它
- `web_search_findings.json` 不能替代财务、估值、股票池和排序主数据
- `source_type` / `confidence` 需遵守 [references/web_search.md](web_search.md) 的来源等级约束

## 10. `idea_screening_snapshot.json`

这是 `build_screening_snapshot.py` 生成、并由当前 LLM 继续回写 summary 的核心中间文件。

关键结构：

- `report_date`
- `strategy`
- `inputs.*`
- `universe.stock_pool_size`
- `universe.valid_financials`
- `universe.valid_roe`
- `universe.valid_pe`
- `universe.valid_pb`
- `universe.latest_quarters[]`
- `universe.top_sectors[]`
- `universe.median_metrics.*`
- `strategies.value / growth / quality`
- `strategies.*.enabled`
- `strategies.*.candidate_count`
- `strategies.*.display_count`
- `strategies.*.thresholds.*`
- `strategies.*.display_candidates[]`
- `strategies.*.median_metrics.*`
- `overlap.candidate_count`
- `overlap.display_candidates[]`
- `risk_flags.*`
- `summaries.exec_summary`
- `summaries.universe_overview`
- `summaries.metric_scoreboard`
- `summaries.value_section`
- `summaries.growth_section`
- `summaries.quality_section`
- `summaries.overlap_section`
- `summaries.risk_section`
- `summaries.appendix`

说明：

- `summaries.*` 初始应为空字符串，表示 Python 没有越界生成正文
- `summaries.*` 必须由当前 LLM 回写客户可读正文，不能只复制表格字段或写流程描述
- 若某策略未启用，相应 summary 也必须解释该章节的对照价值或后续用途
- `strategies.*.thresholds` 记录了本次实际使用的用户阈值，最终报告必须按这些阈值解释结果，不能自行改口径

## 解析约定

- 所有文件都允许 `{"data": [...]}`、`{"data": {...}}`、`[...]`、`{...}` 四种包装方式
- `financial-indicator` 文件中的值字段不是统一 `value`，而是因子名本身
- 同一股票同一季度若存在多条财务记录，脚本会按 `info_date` 选择最新披露版本
- 缺失值不会抛异常，但报告中必须明确体现为“无数据 / 未启用 / 未验证”
- 金额类字段最终报告中应按客户可读口径展示，例如市值与净利润使用“亿元”
- 最终报告面向客户阅读：正文由当前 LLM 基于快照事实和可选 `web_search_findings.json` 生成，Python 只负责结构化数据与表格渲染
