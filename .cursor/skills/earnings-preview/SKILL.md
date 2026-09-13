---
name: rq-earnings-preview
description: |
  创建模板驱动的财报预览报告，在财报发布前基于历史财务、近期股价、卖方一致预期、研报口径与网络搜索结果搭建可追踪的预判框架。
  `RQData CLI` 负责历史财务、价格、一致预期、研报与公告主数据；若需要补充目标季度预计披露日、电话会安排或近期行业动态，可额外使用 `web_search` 获取实时信息，并先落为结构化 JSON，再由 earnings-preview/scripts/generate_report.py 以客户可读方式纳入正文。

  务必使用此技能当用户：
  - 明确请求财报预览、earnings preview、pre-earnings、财报前瞻
  - 想知道财报前看什么、哪些指标最关键
  - 需要结合市场预期和近期股价定位财报前 setup
  - 需要在财报前形成一份结构化关注清单和情景分析

  不适用场景：
  - 财报发布后的复盘分析 -> earnings-analysis
  - 首次覆盖深度研究 -> initiating-coverage
  - 只问一句“什么时候发财报”
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by earnings-preview/assets/template.md."
---

# RQ 股票研究 - 财报预览

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、占位符填充和结构校验
- skill 必须自包含，不能依赖仓库级 `utils`
- 财报预览重在“预测框架”，不是把历史财务复述一遍
- 报告必须显式区分“历史事实”“市场预期”“分析师估算”
- 市场预期应优先来自 `stock cn consensus` 与 `stock cn research-reports`，不得伪造 consensus
- 目标季度预计披露日、电话会和近期行业动态若需要写入正文，必须来自 `web_search`，不能靠训练记忆补写
- 缺失数据时必须明确写“无数据 / 未提供 / 未验证”，不能留空

## 数据源分工

### `RQData CLI` 负责

- 公司信息、行业、历史财务、ROE、价格与成交额
- 一致预期、研报、目标价和公告主数据
- 历史股价反应、卖方分歧和预测锚点

### `web_search` 负责

- 目标季度预计披露日
- 电话会 / 业绩会安排
- 近期行业动态、政策变化和公司新闻

### `web_search` 禁止替代的内容

- 财务、价格、估值、公告、卖方预期和一致预期主数据
- 情景分析的核心数值框架
- 任何本应由 RQData 提供的结构化金融数据

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 目标季度预计披露日和电话会安排
- 财报前近期行业与政策动态
- 与本次财报预览直接相关的公司新闻

落盘要求：

- 所有网络搜索结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把搜索草稿直接塞进报告
- 若未提供该文件，报告仍可交付，但涉及时间安排和近期动态的部分必须保持“未验证”边界
- 即便提供了 `web_search_findings.json`，它也只能补充预测背景，不能替代卖方预期与历史数据

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 历史财务数据、分析师预期、研报和公告必须来自 `RQData CLI`
- `[MUST-2]` 目标季度预计披露日必须通过 `web_search` 获取实时信息，不能使用训练记忆
- `[MUST-3]` 金额类数据必须统一转换为“亿元”等客户可读口径
- `[MUST-4]` `consensus` 的 `t1/t2/t3` 是年度 forward buckets，不得直接伪装成目标季度单季预测
- `[MUST-5]` 情景分析必须包含乐观 / 中性 / 悲观三个情景及对应股价反应区间
- `[MUST-6]` 研报若进入正文，必须优先消费 `summaries.core_view` 等客户可读摘要层
- `[MUST-7]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-8]` 客户稿不得暴露 `LLM`、`skill`、文件名、JSON 字段名或内部 workflow 术语
- `[MUST-9]` 低置信度网络搜索结果不能单独支撑交易结论或发布日期结论

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般新闻源，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低等级
- 预测与情景分析统一标注为“分析师估算，置信度4”
- 低置信度网络搜索结果只能作为时间安排或行业背景线索

## 图表 / 图片需求

当前实现以结构化表格完成最小可交付版本，但财报预览仍需明确图表 / 表格需求。

- 图表 / 表格名称：共识预测表
- 目的：展示历史已披露季度与卖方年度预期口径
- 使用的数据文件：`historical_financials.json`、`consensus.json`、`research_reports.json`
- 关键字段：`revenue`、`net_profit`、`con_targ_price`、`comp_con_*`、`net_profit_t`
- 建议形式：表格
- 回答问题：市场当前预期高还是低、预期是否分歧
- 放置位置：`## 卖方预期与市场定价`
- 若图表缺失：必须以表格保留

- 图表 / 表格名称：三情景预测表
- 目的：展示乐观 / 中性 / 悲观三情景和预期市场反应
- 使用的数据文件：`historical_financials.json`、`price_recent.json`
- 关键字段：`revenue`、`net_profit`、`gross_profit` / `profit_from_operation`（如有）或可回退的利润率口径、`close`
- 建议形式：表格
- 回答问题：财报前风险收益比如何
- 放置位置：`## 情景分析与市场反应`
- 若图表缺失：必须以表格保留

## 目标产出

- 报告长度：5-8 页
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
earnings-preview/
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
- `price_recent.json`
- `hs300_recent.json`
- `consensus.json`
- `research_reports.json`
- `announcement_raw.json`：可选，建议提供
- `announcement_extracts.json`：可选
- `peers_financials.json`：可选
- `peers_instruments.json`：可选
- `web_search_findings.json`：可选，但若正文要写预计披露日 / 电话会 / 行业动态，则应提供

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
ORDER_BOOK_ID="${ORDER_BOOK_ID:-600519.XSHG}"
TARGET_QUARTER="${TARGET_QUARTER:-2026q1}"
PRICE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=90)).isoformat())
PY
)"
REPORT_LOOKBACK_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=120)).isoformat())
PY
)"
ROE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=540)).isoformat())
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
TARGET_FISCAL_YEAR="$(python3 - <<PY
print("${TARGET_QUARTER}"[:4])
PY
)"
PREV_FISCAL_YEAR="$(python3 - <<PY
print(str(int("${TARGET_QUARTER}"[:4]) - 1))
PY
)"

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/earnings_preview}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/earnings_preview_${ORDER_BOOK_ID}_${TARGET_QUARTER}_${REPORT_DATE}.md}"
PEER_STOCKS_JSON="${PEER_STOCKS_JSON:-[]}"
```

### 步骤 2：采集公司、行业与历史财务

```bash
mkdir -p "$DATA_DIR"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"]
}" --format json > "$DATA_DIR/company_info.json"

rqdata stock cn industry --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/industry.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fields\": [\"revenue\", \"net_profit\", \"gross_profit\", \"operating_expense\", \"cash_from_operating_activities\"],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/historical_financials.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$ROE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/roe_history.json"
```

### 步骤 3：采集市场表现与预期数据

```bash
rqdata stock cn price --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\", \"volume\", \"total_turnover\"],
  \"adjust_type\": \"none\"
}" --format json > "$DATA_DIR/price_recent.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"000300.XSHG\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/hs300_recent.json"

rqdata stock cn consensus --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$REPORT_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"report_range\": 3
}" --format json > "$DATA_DIR/consensus.json"

rqdata stock cn research-reports --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fiscal_year\": \"$TARGET_FISCAL_YEAR\",
  \"start_date\": \"$REPORT_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"date_rule\": \"create_tm\"
}" --format json > "$DATA_DIR/research_reports_current_year.json"

rqdata stock cn research-reports --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fiscal_year\": \"$PREV_FISCAL_YEAR\",
  \"start_date\": \"$REPORT_LOOKBACK_START\",
  \"end_date\": \"$REPORT_DATE\",
  \"date_rule\": \"create_tm\"
}" --format json > "$DATA_DIR/research_reports_prev_year.json"

python3 - "$DATA_DIR/research_reports_prev_year.json" "$DATA_DIR/research_reports_current_year.json" "$DATA_DIR/research_reports.json" <<'PY'
import json
import sys
from pathlib import Path

merged = []
for path in sys.argv[1:3]:
    payload = json.loads(Path(path).read_text())
    items = payload if isinstance(payload, list) else payload.get("data", [])
    if isinstance(items, list):
        merged.extend(items)
Path(sys.argv[3]).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
PY

ANNOUNCEMENT_START="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=240)).isoformat())
PY
)"

rqdata stock cn announcement --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$ANNOUNCEMENT_START\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/announcement_raw.json"
```

### 步骤 3.5：网络搜索目标季度时间安排与近期行业动态

当正文需要写目标季度预计披露日、电话会或近期行业动态时，应执行该步骤。

- 使用 `web_search` 获取目标季度预计披露日
- 可选补充电话会安排、近期行业动态和公司新闻
- 结果必须写入 `web_search_findings.json`

### 步骤 4：可选采集可比公司数据

若已有明确可比公司列表，可补充：

```bash
if [ "$PEER_STOCKS_JSON" != "[]" ]; then
  rqdata stock cn instruments --payload "{
    \"order_book_ids\": $PEER_STOCKS_JSON
  }" --format json > "$DATA_DIR/peers_instruments.json"

  rqdata stock cn financial --payload "{
    \"order_book_ids\": $PEER_STOCKS_JSON,
    \"fields\": [\"revenue\", \"net_profit\", \"gross_profit\"],
    \"start_quarter\": \"$HISTORY_START_QUARTER\",
    \"end_quarter\": \"$HISTORY_END_QUARTER\",
    \"statements\": \"all\"
  }" --format json > "$DATA_DIR/peers_financials.json"
fi
```

### 步骤 5：整理研报观点摘要（必须）

说明：

- 不新增额外摘要文件，直接在 `research_reports.json` 原记录上回写摘要
- 摘要输入位置：`research_reports.json -> records[].summary`
- 摘要回写位置：`research_reports.json -> records[].summaries.core_view`
- 仅处理当前股票、且 `data_source=0` 的公司报告主样本
- 摘要应为客户可读表述，压缩原始研报的核心判断、盈利预期变化、估值或关键观察点
- 最终报告只消费这些摘要，不直接展示或截断原始 `summary`

### 步骤 6：提取公告原文片段（可选但推荐）

```bash
python3 earnings-preview/scripts/extract_announcements.py \
  --stock "$ORDER_BOOK_ID" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE"
```

说明：

- 该步骤会从近期正式财报、主要经营数据、业绩说明会等公告中提取可复用正文片段
- `announcement_extracts.json` 采用两层结构：
  - `raw_sections`：保存较长原文段落，仅作为后续总结输入
  - `summaries`：保存面向最终报告的精炼总结
- `公司背景 / 管理层表述 / 经营展望` 主要针对年报、半年报正文；季报和临时公告保留原文链接为主
- 若源站阻断或 PDF 不可读，也必须保留失败状态和原文链接

### 步骤 7：生成 Markdown 报告

```bash
python3 earnings-preview/scripts/generate_report.py \
  --stock "$ORDER_BOOK_ID" \
  --quarter "$TARGET_QUARTER" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

### 步骤 8：渲染 HTML

脚本会优先尝试调用本地安装的 `rq-report-renderer`；若未安装则回退到仓库内 `report-renderer/scripts/render_report.py`；两者都不可用时才保留 Markdown 并打印警告。

## 阶段门控

### Gate 1：历史与预期主数据齐备

- 历史财务、股价、卖方预期主数据存在
- 历史事实和市场预期可以明确分层
- 即使没有网络搜索结果，也能形成基础财报预览框架

### Gate 2：预测框架完成

- 已识别最新已披露季度
- 已形成目标季度基准预测
- 三情景和股价反应区间可解释

### Gate 3：可选网络搜索结果完成

- 若启用网络搜索结果，`web_search_findings.json` 已落盘
- 预计披露日和电话会若进入正文，来源等级可解释
- 网络搜索结果只补充时间安排和行业背景，不越权替代主数据

### Gate 4：成稿完成

- Markdown 已生成
- 若本地渲染器存在，HTML 已生成
- 客户稿不暴露内部术语
- 长度、章节、来源标注和预测框架达标

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[COMPANY_NAME]]`
  - `[[STOCK_CODE]]`
  - `[[TARGET_QUARTER]]`
  - `[[EARNINGS_EVENT_CONTEXT]]`
  - `[[EXEC_SUMMARY]]`
  - `[[HISTORICAL_TREND]]`
  - `[[FORECAST_FRAMEWORK]]`
  - `[[EXPECTATION_SECTION]]`
  - `[[ANNOUNCEMENT_SECTION]]`
  - `[[SCENARIO_SECTION]]`
  - `[[TRADING_SETUP]]`
  - `[[RISK_SECTION]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- 必须显式区分历史数据、卖方预期和分析师预测
- 不能只写“市场预期高/低”，必须给出对应数据
- 必须回答 4 个预测核心问题：
  - 目标季度预计看什么
  - 市场当前预期是什么
  - 乐观 / 中性 / 悲观三情景分别长什么样
  - 财报前交易定位偏向抢跑、保守还是中性
- 若正文写了预计披露日或电话会，必须来自网络搜索结果
- 若提供公告数据，正文必须保留原文链接；若提供提炼结果，应优先展示管理层和经营展望片段
- 近期研报样本若进入正文，必须展示整理后的客户可读摘要，不得直接截断原始 `summary`
- 不得残留 `[XX]`、`[需要补充]`、`[风险1]` 这类占位文本

## 阶段验收清单

- [ ] Markdown 文件存在
- [ ] 若本地渲染器存在则 HTML 文件存在
- [ ] 模板占位符无残留
- [ ] 主章节完整
- [ ] 预测章节和情景章节带有“分析师估算，置信度4”
- [ ] 市场预期章节确实引用了 `consensus.json` 或 `research_reports.json`
- [ ] 若存在相关公告，正文保留原文链接
- [ ] 若启用 `web_search`，正文真实出现网络搜索结果而不是只多一个 sidecar JSON

## 常见错误

- 把 `consensus` 当作季度预期直接使用，却没有说明它是 forward annual buckets
- 写“市场预期”“交易建议”却没有任何研报、目标价或价格行为依据
- 目标季度预计披露日直接靠训练记忆补写
- 使用固定季度、固定日期或固定同业名单
- 把网络搜索结果直接写成主结论，反而压过 RQData 主数据
