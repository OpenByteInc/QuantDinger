# earnings-analysis 数据契约

`earnings-analysis/scripts/generate_report.py` 默认从 `--data-dir` 读取以下 JSON 文件。

## 1. `company_info.json`

典型字段：

- `order_book_id`
- `symbol`
- `abbrev_symbol`
- `industry_name`
- `listed_date`

用途：

- 公司名称、简称、上市信息

## 2. `industry.json`

典型字段：

- `first_industry_name`
- `second_industry_name`
- `third_industry_name`

用途：

- 行业分层描述

## 3. `historical_financials.json`

典型字段：

- `order_book_id`
- `quarter`
- `info_date`
- `revenue`
- `net_profit`
- `gross_profit`
- `cash_from_operating_activities`
- `total_assets`
- `total_liabilities`

用途：

- 自动识别最新财报季度
- 计算同比、环比、毛利率、现金转化率、资产负债率

## 4. `roe_history.json`

典型字段：

- `order_book_id`
- `date`
- `return_on_equity_weighted_average`

用途：

- 盈利质量趋势

## 5. `market_cap.json` / `pe_ratio.json` / `pb_ratio.json` / `dividend_yield.json`

典型字段：

- `order_book_id`
- `date`
- 对应 factor 字段

说明：

- `dividend_yield` 原始值为 bps，生成报告时需要除以 `100` 后按百分比展示

用途：

- 当前估值与股东回报定位

## 6. `price_window.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`
- `volume`
- `total_turnover`

用途：

- 计算财报前后价格反应
- 计算成交额放大

## 7. `benchmark_window.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`

用途：

- 计算相对沪深300的超额收益

## 8. `consensus.json`

典型字段：

- `date`
- `create_tm`
- `report_year_t`
- `comp_con_operating_revenue_t / t1 / t2 / t3`
- `comp_con_net_profit_t / t1 / t2 / t3`
- `comp_con_eps_t / t1 / t2 / t3`
- `con_targ_price`

用途：

- 财报前一致预期
- 财报后预期变化

## 9. `research_reports.json`

典型字段：

- `create_tm`
- `date`
- `report_title`
- `summary`
- `institute`
- `author`
- `fiscal_year`
- `net_profit_t / t1 / t2`
- `eps_t / t1 / t2`
- `targ_price`

用途：

- 财报后卖方解读
- 目标价和年度利润口径的补充

说明：

- `summary` 是财报后“文字解释层”的首选字段，用于补充卖方对业绩、预期修正和核心关注点的描述
- `report_title`、`summary`、`targ_price` 与 `net_profit_t / t1 / t2` 需要一起看，不能只保留数值预测

## 10. `announcement_raw.json`

典型字段：

- `info_date`
- `title`
- `info_type`
- `media`
- `file_type`
- `announcement_link`

用途：

- 识别正式财报、主要经营数据公告和业绩说明会公告
- 在正文中保留公告原文链接，供后续 PDF / HTML 读取

## 11. `announcement_extracts.json`

该文件可选，可由具备 PDF / HTML 原文解析能力的流程生成。

典型字段：

- `records[].title`
- `records[].info_date`
- `records[].announcement_link`
- `records[].is_annual_or_interim_report`
- `records[].fetch_status`
- `records[].extract_status`
- `records[].raw_sections.company_intro`
- `records[].raw_sections.management_discussion`
- `records[].raw_sections.risk_warning`
- `records[].raw_sections.outlook`
- `records[].summaries.company_intro`
- `records[].summaries.management_discussion`
- `records[].summaries.risk_warning`
- `records[].summaries.outlook`
- `records[].sections.company_intro`
- `records[].sections.management_discussion`
- `records[].sections.risk_warning`
- `records[].sections.outlook`

用途：

- `raw_sections` 保存较长原文段落，供当前 skill 内的 LLM 直接读取
- `summaries` 保存基于 `raw_sections` 回写的总结性文本；报告生成时只消费该层
- `summaries` 必须由当前 LLM 回写客户可读摘要，不能直接复制 `raw_sections` 原文或 PDF 抽取碎片
- `sections` 为兼容旧结构保留，当前可视为 `raw_sections` 的兼容镜像
- `company_intro` / `management_discussion` / `outlook` 主要面向年报、半年报正文；季度报告和临时公告默认不强制抽取这三类字段
- 若源站拦截或 PDF 不可读，也必须保留失败状态和原文链接，不能静默丢失
- 不额外创建 `announcement_summaries.json`；LLM 应直接在 `announcement_extracts.json` 的 `records[].summaries.*` 中回写结果

## 12. `web_search_findings.json`

该文件可选，仅用于补充 `RQData CLI` 无法直接提供的实时外部语境。

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
- `stance`

允许的 `finding_type`：

- `company_news`
- `management_update`
- `earnings_call`
- `industry_context`
- `policy_context`

说明：

- `published_at` 是源内容发布时间，不是财报披露日
- `retrieved_at` 是实际检索时间
- `source_type` / `confidence` 需遵守 `references/web_search.md` 的来源等级约束
- `web_search_findings.json` 不能替代财务、估值、价格、公告和一致预期主源

## 解析约定

- 所有文件都允许 `{"data": [...]}`、`{"data": {...}}`、`[...]`、`{...}` 四种包装方式
- 同一季度多次披露时，脚本按 `info_date <= report-date` 选择最新版本
- 财报分析使用的目标年度是最新已披露财报年度；读取 `consensus` 时不能机械假设 `*_t` 一定非空，必须继续核查实际有值的 forecast slot
- 研报必须做相关性过滤，至少要求标题或摘要命中公司名称/代码/英文名关键词
- 可直接运行 `earnings-analysis/scripts/extract_announcements.py` 生成该文件；若源站阻断，也应保留失败状态
- 若存在 `web_search_findings.json`，脚本会校验必填字段、来源类别和置信度上限
