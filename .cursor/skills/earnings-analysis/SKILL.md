---
name: rq-earnings-analysis
description: |
  创建模板驱动的财报分析报告，在财报披露后基于真实财务数据、市场预期、公告原文链接、卖方研报和股价反应完成结构化复盘。
  `RQData CLI` 负责财务、估值、价格、公告和一致预期主数据；若需要补充财报电话会、管理层动态、行业或政策语境，可额外使用 `web_search` 获取实时信息，并先落为结构化 JSON，再统一生成 Markdown 与 HTML 报告。

  务必使用此技能当用户：
  - 明确请求财报分析、季度业绩点评、earnings update、post-earnings report
  - 想知道财报发布后是超预期、符合预期还是低于预期
  - 需要结合财务数据、市场预期和股价反馈做复盘

  不适用场景：
  - 财报前瞻 -> earnings-preview
  - 首次覆盖 -> initiating-coverage
  - 只要一句话快评
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by earnings-analysis/assets/template.md."
---

# RQ 股票研究 - 财报分析

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、占位符填充和结构校验
- Python 只输出结构化 facts / tables / signal rules，不在代码里硬写观点性结论
- `RQData CLI` 是财务、估值、价格、公告和一致预期的主源；`web_search` 只补充 CLI 无法直接提供的实时外部语境
- 最新财报季度必须从真实 `financial` 数据中按 `info_date <= report-date` 自动识别，不能硬写季度
- “超预期/低于预期”判断必须明确口径，优先使用财报前一致预期和高度相关的卖方点评
- 研报必须做相关性过滤；行业周报、策略报告不能冒充公司点评
- 缺失数据时必须明确写“无数据 / 未提供”，不能留空

## 数据源分工

### `RQData CLI` 负责

- 公司信息、行业归属、历史财务、ROE、估值与股息率
- 财报前后价格反应、成交额变化、基准超额收益
- 一致预期、目标价、卖方研报
- 正式公告、业绩说明会、财报原文链接

### `web_search` 负责

- 财报电话会安排、管理层最新外部表态
- 财报后行业或政策语境
- `RQData CLI` 未直接提供、但会影响财报解读的实时背景信息

### `web_search` 禁止替代的内容

- 财务数字、估值指标、价格数据
- 正式公告、财报披露日、分红、交易所披露
- 一致预期和结构化卖方预测

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 管理层动态、财报电话会、IR 活动安排
- 行业景气、政策变化、监管动态
- 财报后几天内的重要公司新闻或权威媒体解读

落盘要求：

- 所有 `web_search` 结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把自然语言笔记直接塞进报告
- 每条记录都必须保留来源、链接、发布时间、检索时间、相关性说明和置信度

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 所有财务数字必须来自 `RQData CLI`
- `[MUST-2]` `web_search` 不得替代财报、估值、价格、公告和一致预期主源
- `[MUST-3]` 金额类数据必须按可读口径展示；原始“元”金额在正文中应换算为“亿元”
- `[MUST-4]` 必须保留正式公告原文链接；若原文提取失败，也必须保留失败状态和原文链接
- `[MUST-5]` “超预期 / 符合预期 / 低于预期”必须给出口径，不能空喊观点
- `[MUST-6]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-7]` 图表若未生成，必须由等价表格或趋势表完成降级，不能让关键章节失真
- `[MUST-8]` 低置信度外部信息不得改写核心财务判断
- `[MUST-9]` 最终输出必须严格来自模板，不得在脚本中自由拼写整篇报告

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般媒体或二手整理，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算窗口、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低值
- 推断或估计信息不得标成高置信度
- 低置信度信息只能作为补充背景，不能成为财报结论唯一依据

## 图表 / 图片需求

当前脚本以趋势表降级交付，但本 skill 仍必须定义最终报告达标所需的图表需求。

- 图表名称：收入与净利润趋势图
- 图表目的：展示近 8 个季度累计与单季收入、净利润变化
- 使用的数据文件：`historical_financials.json`
- 关键字段：`quarter`、`revenue`、`net_profit`
- 建议图表类型：双轴折线或柱线组合
- 回答问题：增长趋势是否延续，单季拐点是否出现
- 放置位置：`## 财报概览`
- 若图表缺失：保留累计趋势表和单季趋势表

- 图表名称：盈利能力与质量图
- 图表目的：展示毛利率、ROE、现金转化率和资产负债率变化
- 使用的数据文件：`historical_financials.json`、`roe_history.json`
- 关键字段：`gross_profit`、`revenue`、`return_on_equity_weighted_average`、`cash_from_operating_activities`、`total_assets`、`total_liabilities`
- 建议图表类型：折线图或分组柱状图
- 回答问题：财务质量是在改善还是恶化
- 放置位置：`## 财务质量与资产负债表`
- 若图表缺失：保留财务质量表和 ROE 取值表

- 图表名称：预期修正与价格反应图
- 图表目的：展示财报前后预期变化与 1D / 3D / 5D 股价反馈
- 使用的数据文件：`consensus.json`、`price_window.json`、`benchmark_window.json`
- 关键字段：`comp_con_*`、`con_targ_price`、`close`
- 建议图表类型：对比柱图 + 收益曲线
- 回答问题：市场是否把这次财报解读为正面还是负面
- 放置位置：`## 市场预期、卖方反馈与价格反应`
- 若图表缺失：保留预期对比表、价格反馈看板和超额收益表

## 目标产出

- 报告长度：8-12 页
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
earnings-analysis/
├── SKILL.md
├── scripts/
│   ├── extract_announcements.py
│   └── generate_report.py
├── assets/
│   └── template.md
└── references/
    ├── data_contract.md
    └── web_search.md
```

## 输入文件契约

原始数据目录由 `--data-dir` 指定，脚本会按下列文件名查找输入：

- `company_info.json`
- `industry.json`
- `historical_financials.json`
- `roe_history.json`
- `market_cap.json`
- `pe_ratio.json`
- `pb_ratio.json`
- `dividend_yield.json`
- `price_window.json`
- `benchmark_window.json`
- `consensus.json`
- `research_reports.json`
- `announcement_raw.json`
- `announcement_extracts.json`（可选）
- `web_search_findings.json`（可选）

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-2026-04-07}"
ORDER_BOOK_ID="${ORDER_BOOK_ID:-600519.XSHG}"
PRICE_WINDOW_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=45)).isoformat())
PY
)"
CONSENSUS_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=90)).isoformat())
PY
)"
FACTOR_DATE="$(python3 - <<PY
import json
import subprocess
from datetime import date, timedelta

def has_non_empty_factor(day: str) -> bool:
    payload = json.dumps({
        "order_book_ids": ["${ORDER_BOOK_ID}"],
        "factor": "market_cap",
        "start_date": day,
        "end_date": day,
    }, ensure_ascii=False)
    rows = json.loads(subprocess.check_output(
        ["rqdata", "stock", "cn", "financial-indicator", "--payload", payload, "--format", "json"],
        text=True,
    ))
    return any(isinstance(row, dict) and row.get("market_cap") not in (None, "", "null") for row in rows)

cursor = date.fromisoformat("${REPORT_DATE}")
for _ in range(15):
    if has_non_empty_factor(cursor.isoformat()):
        print(cursor.isoformat())
        break
    cursor -= timedelta(days=1)
else:
    raise SystemExit("最近 15 个自然日都未找到非空 market_cap 因子日期")
PY
)"
HISTORY_START_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year - 2}q1")
PY
)"
HISTORY_END_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year}q4")
PY
)"

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/earnings_analysis}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/earnings_analysis_${ORDER_BOOK_ID}_${REPORT_DATE}.md}"
```

### 步骤 2：采集公司、财务、估值与价格数据

```bash
mkdir -p "$DATA_DIR"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"]
}" --format json > "$DATA_DIR/company_info.json"

rqdata stock cn industry --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"date\": \"$REPORT_DATE\",
  \"level\": 0,
  \"source\": \"citics_2019\"
}" --format json > "$DATA_DIR/industry.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fields\": [\"revenue\", \"net_profit\", \"gross_profit\", \"cash_from_operating_activities\", \"total_assets\", \"total_liabilities\"],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/historical_financials.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$CONSENSUS_START\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/roe_history.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"market_cap\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/market_cap.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"pe_ratio\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/pe_ratio.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"pb_ratio\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/pb_ratio.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"dividend_yield\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/dividend_yield.json"

rqdata stock cn price --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_WINDOW_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\", \"volume\", \"total_turnover\"],
  \"adjust_type\": \"post\"
}" --format json > "$DATA_DIR/price_window.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"000300.XSHG\"],
  \"start_date\": \"$PRICE_WINDOW_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/benchmark_window.json"
```

### 步骤 3：采集市场预期、研报与公告

```bash
rqdata stock cn consensus --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$CONSENSUS_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"report_range\": 3
}" --format json > "$DATA_DIR/consensus.json"

TARGET_FISCAL_YEAR="$(python3 - "$DATA_DIR/historical_financials.json" "$REPORT_DATE" <<'PY'
import json
import sys
from datetime import date, datetime
from pathlib import Path

def parse_iso_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).date()
        except ValueError:
            continue
    return None

rows = json.loads(Path(sys.argv[1]).read_text())
report_date = date.fromisoformat(sys.argv[2])
best = None
for row in rows:
    info_date = parse_iso_date(row.get("info_date"))
    quarter = row.get("quarter")
    if not info_date or info_date > report_date or not quarter:
        continue
    if best is None or quarter > best:
        best = quarter
print(best[:4] if best else report_date.year)
PY
)"

rqdata stock cn research-reports --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fiscal_year\": \"$TARGET_FISCAL_YEAR\",
  \"start_date\": \"$CONSENSUS_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"date_rule\": \"create_tm\"
}" --format json > "$DATA_DIR/research_reports.json"

ANNOUNCEMENT_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=45)).isoformat())
PY
)"

rqdata stock cn announcement --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$ANNOUNCEMENT_START\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/announcement_raw.json"
```

### 步骤 4：提取公告原文片段

```bash
python3 earnings-analysis/scripts/extract_announcements.py \
  --stock "$ORDER_BOOK_ID" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE"
```

要求：

- 若公告源站可读，会生成 `announcement_extracts.json`
- `announcement_extracts.json` 保留 `raw_sections`、`summaries`、失败状态和原文链接
- 年报 / 中报正文优先抽取 `company_intro`、`management_discussion`、`risk_warning`、`outlook`
- 若源站阻断或 PDF 不可读，也不能静默丢失

### 步骤 4.5：当前 LLM 回写 `announcement_extracts.json` summary

- 直接读取 `records[].raw_sections.*`
- 回写 `records[].summaries.*`
- 不额外创建 `announcement_summaries.json`
- `summaries.*` 必须是客户可读表述，不得直接复制 `raw_sections.*` 原文、不允许粘贴 PDF 抽取碎片
- 这一步必须由当前 LLM 完成；`generate_report.py` 不负责代写公告正文描述
- 若年报 / 中报已有可用 `raw_sections.*`，但 `summaries.*` 仍为空或只是原文截断，视为 workflow 未完成，不进入成稿阶段

### 步骤 5：补充 `web_search` 实时信息

仅当需要补充财报电话会、管理层外部表态、行业或政策语境时，才执行此步骤。

- 检索结果必须先写入 `web_search_findings.json`
- 只能补充 `RQData CLI` 直接缺失的信息
- 不得把新闻稿当作财务主数据

### 步骤 6：生成结构化 Markdown 草稿

```bash
python3 earnings-analysis/scripts/generate_report.py \
  --stock "$ORDER_BOOK_ID" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

说明：

- 该脚本生成结构化事实草稿，包含表格、信号、研报摘录、公告原文链接和可选外部实时补充信息
- 不应在 Python 里写“因此看多 / 看空”这类分析句
- 若已产出 `announcement_extracts.json`，报告只消费其中 `summaries`
- 不允许在报告生成阶段根据 `raw_sections` 现写客户可读公告摘要；`summaries` 缺失时只能明确提示缺失

### 步骤 7：渲染 HTML

脚本会优先尝试本地安装的 `rq-report-renderer`，若不可用则回退到仓库内 `report-renderer/scripts/render_report.py`；两者都不可用时保留 Markdown 并打印警告。

## 阶段门控

### 阶段 1：数据采集完成标准

- 财务、价格、估值、一致预期、研报和公告原始文件齐全
- 最新财报季度已按 `info_date <= report-date` 自动识别
- 若使用 `web_search`，`web_search_findings.json` 已结构化落盘

### 阶段 2：研究核查完成标准

- 已核查财报季度、披露日、价格窗口、预期快照和相关公告
- 已核查 `announcement_extracts.json -> summaries.*` 为当前 LLM 回写的客户可读摘要，不是原文截断
- 已确认卖方研报相关性，不包含行业周报 / 策略报告污染
- 公告原文提取成功或失败状态都已保留

### 阶段 3：成稿完成标准

- 模板占位符全部替换
- 关键章节完整
- 关键结论后有来源和置信度
- 公告章节中的文字描述仅来自 `announcement_extracts.json -> summaries.*`
- 图表未生成时，表格降级仍能覆盖趋势、质量和价格反应

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[COMPANY_NAME]]`
  - `[[STOCK_CODE]]`
  - `[[LATEST_QUARTER]]`
  - `[[EVENT_DATE]]`
  - `[[EXEC_SUMMARY]]`
  - `[[INFO_PANEL]]`
  - `[[EARNINGS_OVERVIEW]]`
  - `[[EXPECTATION_AND_REACTION]]`
  - `[[ANNOUNCEMENT_SECTION]]`
  - `[[FINANCIAL_QUALITY]]`
  - `[[THESIS_UPDATE]]`
  - `[[VALUATION_SECTION]]`
  - `[[RISK_SECTION]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- 关键结论必须基于真实财报、预期和价格反应数据
- “超预期 / 符合预期 / 低于预期”必须给出口径
- `research_reports.json` 中的 `summary` 必须作为卖方文字解释层输出
- 公告原文链接必须保留
- 公告正文描述必须先由当前 LLM 回写到 `announcement_extracts.json -> summaries.*`
- `dividend_yield` 原始值单位为 bps，报告中必须换算为百分比
- 若最新财报季度、关键财务指标或价格窗口缺失，生成器应直接失败
- 若财报前后一致预期字段在 `*_t` 为空、但在更靠后的 forecast slot 非空，生成器必须继续核查并读取实际可用口径，不能机械把财报后预期写成“无数据”
- 若使用 `web_search`，必须保留来源名称、链接、发布时间和检索时间

## 阶段验收清单

- [ ] 数据采集 -> 报告生成 -> HTML 渲染三阶段都按顺序执行
- [ ] `RQData CLI` 与 `web_search` 的边界没有混用
- [ ] 财报原文、预期对比、价格反应都有真实数据支撑
- [ ] `announcement_extracts.json -> summaries.*` 已由当前 LLM 回写成客户可读内容，而不是原文截断
- [ ] 卖方研报做过相关性过滤
- [ ] 公告原文链接和提取状态都保留
- [ ] 图表未生成时，表格降级仍覆盖核心问题
- [ ] Markdown / HTML 报告达到 8-12 页目标质量

## 常见错误

- 使用仓库级 `utils/detect_latest_quarter.py`
- 把 `financial-indicator` 误写成 `fields + start_quarter/end_quarter`
- 财报后分析却没有财报前预期口径
- 看到 `consensus` 的 `*_t` 为空，就直接把财报后预期写成“无数据”，没有继续核查 `*_t1 / *_t2`
- 把行业周报、策略报告直接当成公司财报点评写进正文
- 用 `web_search` 替代财报、估值、价格、公告和一致预期主源
