---
name: rq-morning-note
description: |
  创建模板驱动的晨会纪要报告，汇总隔夜公告、最近披露的财务更新、昨日股价表现、盘前宏观/行业语境与今日重点观察名单。
  `RQData CLI` 负责个股行情、公告、财报、分红等结构化主数据；若需要补充宏观政策、海外市场、行业新闻或监管变化，可额外使用 `web_search` 获取实时信息，并先落为结构化 JSON，再由当前脚本纳入最终正文。

  务必使用此技能当用户：
  - 明确请求晨会纪要、morning note、晨会准备、morning meeting
  - 想了解隔夜动态、昨晚发生了什么、overnight developments
  - 询问今天看什么、今日重点、what to watch today
  - 需要盘前关注名单或交易观察

  不适用场景：
  - 单一公司财报深度点评 -> earnings-analysis / earnings-preview
  - 首次覆盖深度研究 -> initiating-coverage
  - 仅需简单新闻摘要且不需要完整报告
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by morning-note/assets/template.md."
---

# RQ 股票研究 - 晨会纪要

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、占位符填充和结构校验
- 晨会纪要必须先完整收集数据，再形成盘前判断，不能边抓数据边写结论
- 不能写死股票池、日期、板块、公司名称、交易结论
- 不能伪造具体盘中时间点；如果数据里没有明确时间，只能写“今日重点关注”
- 缺少数据时必须明确写“无数据/无事件/未验证”，不能留空章节

## 数据源分工

### `RQData CLI` 负责

- 覆盖股票池、公司名称与基础元数据
- 个股价格、成交额、基准指数表现
- 公司公告、财报披露、分红事项

### `web_search` 负责

- 隔夜宏观政策、海外市场、监管动态
- 行业新闻、商品与主题链条的实时语境
- RQData 无法直接提供的盘前网络搜索结果

### `web_search` 禁止替代的内容

- 个股价格、基准指数、公告、财报、分红
- 股票池定义、相对强弱排序
- 任何本应由 RQData 提供的结构化金融主数据

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 宏观与监管动态
- 海外市场与大宗商品线索
- 行业或主题链最新消息

落盘要求：

- 所有网络搜索结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把搜索草稿直接写进报告
- 若未提供该文件，晨会仍可交付，但要保持“结构化盘前纪要”边界
- 即便提供了 `web_search_findings.json`，它也只能补充盘前语境，不能覆盖 RQData 事实层

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 先完整收集所有数据，再开始分析和写报告
- `[MUST-2]` 个股行情、基准、公告、财报和分红必须来自 `RQData CLI`
- `[MUST-3]` 宏观、行业和海外市场等实时网络搜索结果必须通过 `web_search` 获取，不能靠训练记忆补写
- `[MUST-4]` 晨会纪要必须给出明确盘前观点，只总结事件数量视为失败
- `[MUST-5]` 晨会结论必须保持盘前边界，不能伪造盘中时间、成交确认或收盘结论
- `[MUST-6]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-7]` 客户稿不得暴露 `LLM`、`skill`、文件名、JSON 字段名或内部 workflow 术语
- `[MUST-8]` 若高优先级公告存在原文链接，正文应尽量保留追溯入口
- `[MUST-9]` 低置信度网络搜索结果不能单独支撑交易结论

## 确信度评级

- `5`：RQData CLI、上市公司公告、交易所披露、公司官网
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般新闻源，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低等级
- 网络搜索结果若无法确认原始出处，不能标高置信度
- 低置信度信息只能作为“关注线索”，不能直接写成“盘前结论”

## 图表 / 图片需求

晨会纪要当前以短文本和表格为主，不强制图表交付，但仍需明确最小可用的结构化表达。

- 市场回顾：至少保留覆盖池涨跌幅表和基准对照
- 今日重点关注：至少保留 3-5 条可执行关注事项
- 若未来增加图表，优先补充指数/覆盖池相对表现图，不得以图代替事实说明

## 目标产出

- 报告长度：2-3 页
- 目标字数：800-1,500 字
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
morning-note/
├── SKILL.md
├── scripts/
│   └── generate_report.py
├── assets/
│   └── template.md
└── references/
    ├── data_contract.md
    └── web_search.md
```

## 输入文件契约

原始数据目录由 `--data-dir` 指定，脚本会按下列文件名查找输入：

- `stock_pool.json`
- `instrument_meta.json`
- `latest_earnings.json`
- `price_recent.json`
- `hs300_recent.json`
- `dividend_news.json`
- `announcement_raw.json`
- `web_search_findings.json`（可选）

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
LOOKBACK_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=1)).isoformat())
PY
)"
PRICE_LOOKBACK_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=7)).isoformat())
PY
)"
DIVIDEND_LOOKBACK_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=30)).isoformat())
PY
)"
FINANCIAL_START_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year - 1}q1")
PY
)"
FINANCIAL_END_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year}q4")
PY
)"

ORDER_BOOK_IDS_JSON="${ORDER_BOOK_IDS_JSON:-[\"600519.XSHG\",\"000858.XSHE\",\"300750.XSHE\",\"600000.XSHG\"]}"
DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/morning_note}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/morning_note_${REPORT_DATE}.md}"
```

### 步骤 2：采集结构化主数据

```bash
mkdir -p "$DATA_DIR"

printf '{"data": %s}\n' "$ORDER_BOOK_IDS_JSON" > "$DATA_DIR/stock_pool.json"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON
}" --format json > "$DATA_DIR/instrument_meta.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"fields\": [\"revenue\", \"net_profit\"],
  \"start_quarter\": \"$FINANCIAL_START_QUARTER\",
  \"end_quarter\": \"$FINANCIAL_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/latest_earnings.json"

rqdata stock cn price --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"start_date\": \"$PRICE_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\", \"total_turnover\"],
  \"adjust_type\": \"post\"
}" --format json > "$DATA_DIR/price_recent.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"000300.XSHG\"],
  \"start_date\": \"$PRICE_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/hs300_recent.json"

rqdata stock cn dividend --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"start_date\": \"$DIVIDEND_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/dividend_news.json"

rqdata stock cn announcement --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"start_date\": \"$LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/announcement_raw.json"
```

说明：

- `latest_earnings.json` 建议抓取近 1-2 年全部季度，脚本会按 `report_date` 过滤到隔夜窗口
- `price_recent.json` 与 `hs300_recent.json` 需要至少包含两个交易日，脚本才可计算涨跌幅
- `announcement_raw.json` 是隔夜动态的首选来源
- 缺少某个文件时脚本会跳过对应模块，但不会伪造内容

### 步骤 2.5：可选的宏观 / 行业 / 海外市场补充

当用户需要更完整的盘前语境时，可执行该步骤。

- 使用 `web_search` 补充宏观政策、监管动态、海外市场、商品或行业新闻
- 结果必须写入 `web_search_findings.json`
- 这类信息只补充盘前语境，不替代个股公告、财报和价格事实

### 步骤 3：生成 Markdown 报告

```bash
python3 morning-note/scripts/generate_report.py \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --lookback-start "$LOOKBACK_START" \
  --output "$OUTPUT_MD"
```

### 步骤 4：渲染 HTML

脚本会优先尝试调用本地安装的 `rq-report-renderer`，若未安装则回退到仓库内 `report-renderer/scripts/render_report.py`；两者都不可用时保留 Markdown 并打印警告。

## 阶段门控

### Gate 1：结构化主数据齐备

- 股票池、公司名、价格和基准数据存在
- 公告、财报、分红数据至少有一类可用
- 晨会正文不依赖网络搜索结果也能交付

### Gate 2：盘前判断准备完成

- 已完成昨日市场回顾和隔夜事件筛选
- 执行摘要能回答“核心观点 / 盘前定位 / 持仓建议”
- 今日重点关注来自真实事件或真实相对强弱

### Gate 3：可选网络搜索结果完成

- 若启用网络搜索结果补充，`web_search_findings.json` 已落盘
- 字段完整、来源等级可解释
- 网络搜索结果仅用于宏观 / 行业 / 政策 / 海外市场信息

### Gate 4：成稿完成

- Markdown 已生成
- 若本地渲染器存在，HTML 已生成
- 客户稿不暴露内部术语
- 长度、章节、来源标注和盘前观点均达标

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[AS_OF_TIME]]`
  - `[[LOOKBACK_START]]`
  - `[[COVERAGE_SCOPE]]`
  - `[[EXEC_SUMMARY]]`
  - `[[OVERNIGHT_DEVELOPMENTS]]`
  - `[[MARKET_RECAP]]`
  - `[[WATCHLIST]]`
  - `[[TRADE_OBSERVATIONS]]`
  - `[[RISK_ALERTS]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- `执行摘要` 必须能落到“核心观点 / 盘前定位 / 持仓建议”层面，不能只是事件计数
- 隔夜动态必须优先引用真实公告/财报/分红记录
- 若启用 `web_search`，正文必须真实吸收网络搜索结果，而不是只多一个 sidecar JSON
- 股价回顾必须基于真实价格数据，不能写固定涨跌幅示例
- 今日关注必须来自真实事件或真实相对强弱，不得伪造盘中时间
- 风险提示必须来自真实数据覆盖情况、市场强弱或事件集中度，不能写空泛套话
- 不得残留模板示例值、股票池示例值、旧路径或内部字段名

## 阶段验收清单

- [ ] Markdown 文件存在
- [ ] 若本地渲染器存在则 HTML 文件存在
- [ ] 模板占位符无残留
- [ ] 主章节完整
- [ ] 数据来源说明存在
- [ ] 执行摘要包含核心观点 / 盘前定位 / 持仓建议
- [ ] 若高优先级公告存在原文链接，正文保留了追溯入口
- [ ] 若使用 `web_search`，其内容已真正进入最终报告且未越权替代 RQData 主数据

## 常见错误

- 直接在 Python 中写死整篇晨会正文
- 把固定股票池、固定板块叙述当作通用逻辑
- 伪造 `09:30 / 10:00 / 14:00` 等盘中时间点
- 把网络搜索结果写成既成事实，却没有结构化来源落盘
- 在正文中暴露文件名、JSON 字段名或内部执行语言
- 数据不足时输出空标题或模板示例文本
