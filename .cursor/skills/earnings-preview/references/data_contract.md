# earnings-preview 数据契约

`earnings-preview/scripts/generate_report.py` 默认从 `--data-dir` 读取以下 JSON 文件。

## 1. `company_info.json`

典型字段：

- `order_book_id`
- `symbol`
- `listed_date`
- `sector_code_name`

用途：

- 获取公司名称、股票代码和基础元数据

## 2. `industry.json`

典型字段：

- `order_book_id`
- `first_industry_code`
- `first_industry_name`

用途：

- 补充行业信息

## 3. `historical_financials.json`

典型字段：

- `order_book_id`
- `quarter`
- `info_date`
- `revenue`
- `net_profit`
- `gross_profit`
- `profit_from_operation`（可选）
- `operating_expense`
- `cash_from_operating_activities`

用途：

- 历史趋势分析
- 计算同比、利润率、费用率、现金转化率
- 作为目标季度预测锚点

说明：

- 若 `gross_profit` 可用，则优先使用毛利率
- 若 `gross_profit` 缺失但 `profit_from_operation` 可用，则回退到营业利润率
- 若两者都缺失，则回退到 `net_profit / revenue` 的净利率
- 金融股、保险股等样本常见 `gross_profit` 缺失，不应因此整段利润率分析退化为“无数据”

## 4. `roe_history.json`

典型字段：

- `order_book_id`
- `date`
- `return_on_equity_weighted_average`

用途：

- 分析资本效率趋势

## 5. `price_recent.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`
- `volume`
- `total_turnover`

用途：

- 计算近 3 个月股价涨跌幅
- 估算财报前 realized volatility
- 分析成交额变化

说明：

- `close` 应使用未复权现价口径（例如 `adjust_type=none`）
- 若使用后复权价格，和卖方目标价直接比较会导致隐含空间失真

## 6. `hs300_recent.json`

典型字段：

- `order_book_id`
- `datetime`
- `close`

用途：

- 计算相对沪深300的超额收益

## 7. `consensus.json`

典型字段：

- `date`
- `create_tm`
- `con_targ_price`
- `comp_con_operating_revenue_t1`
- `comp_con_operating_revenue_t2`
- `comp_con_operating_revenue_t3`
- `comp_con_net_profit_t1`
- `comp_con_net_profit_t2`
- `comp_con_net_profit_t3`

用途：

- 获取最新一致预期快照
- 作为 market expectations 的结构化参考

注意：

- `t1/t2/t3` 是 RQData 提供的 forward buckets，不应直接假装成目标季度预测值

## 8. `research_reports.json`

典型字段：

- `date`
- `create_tm`
- `data_source`
- `report_title`
- `institute`
- `author`
- `summary`
- `summaries.core_view`
- `targ_price`
- `net_profit_t`
- `net_profit_t1`
- `revenue_t`
- `revenue_t1`

用途：

- 获取近期研报标题、摘要、目标价和年度利润口径
- 形成卖方预期与市场定价章节
- `summaries.core_view` 保存面向最终报告的精炼观点摘要

注意：

- 若存在 `data_source`，应优先保留 `data_source=0` 的样本作为公司报告主样本
- `data_source!=0` 的记录可能混入行业、策略或其他公司内容，不应直接进入近期研报样本表
- 最终报告不应直接截断原始 `summary`；若要展示研报观点，应优先读取 `summaries.core_view`

## 9. `peers_financials.json` / `peers_instruments.json`

可选文件。

用途：

- 若已提供可比公司列表，可对比最新收入、利润和可用利润率水平

## 10. `announcement_raw.json`

可选文件。

典型字段：

- `info_date`
- `title`
- `info_type`
- `media`
- `file_type`
- `announcement_link`

用途：

- 保留近期正式财报、主要经营数据公告、业绩说明会等原文链接
- 为财报前预览补充管理层近期沟通与经营线索

## 11. `announcement_extracts.json`

可选文件，可由 `earnings-preview/scripts/extract_announcements.py` 生成。

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

用途：

- `raw_sections` 保存较长原文段落，供后续总结使用
- `summaries` 保存可直接进入最终报告的精炼片段
- `company_intro` / `management_discussion` / `outlook` 主要面向年报、半年报正文；季报和临时公告默认以链接留痕为主
- 若源站拦截或 PDF 不可读，也必须保留失败状态和原文链接

## 解析约定

- 所有文件都允许 `{"data": [...]}`、`{"data": {...}}`、`[...]`、`{...}` 四种包装方式
- 同一股票同一季度若存在多条财务记录，脚本会按 `info_date` 选择最新披露版本
- 若卖方预期文件缺失，报告必须明确说明“未提供卖方口径数据”
- 若公告提炼文件缺失，报告仍应保留相关公告原文链接

## 12. `web_search_findings.json`

可选文件。

典型字段：

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
- `event_date`（可选）
- `expected_window`（可选）

用途：

- 补充目标季度预计披露日与电话会安排
- 补充近期行业动态和公司新闻
- 为财报预览提供时间窗口与前瞻背景，但不替代财务、价格和一致预期主数据

允许的 `finding_type`：

- `earnings_release_date`
- `conference_call`
- `industry_context`
- `company_news`

允许的 `source_type`：

- `official`
- `government`
- `association`
- `authoritative_media`
- `general_news`
- `inference`

置信度上限：

- `official`: `5`
- `government`: `4`
- `association`: `4`
- `authoritative_media`: `4`
- `general_news`: `3`
- `inference`: `1`
