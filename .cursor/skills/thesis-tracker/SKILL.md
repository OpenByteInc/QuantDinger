---
name: rq-thesis-tracker
description: |
  创建模板驱动的投资论文跟踪报告，系统化跟踪核心观点、关键支柱、资本回报、催化剂和风险信号。
  `RQData CLI` 负责财务、价格、估值、公告、分红和股东结构等结构化主数据；若需要补充公司新闻、管理层变化、行业趋势、竞争格局或分析师观点，可额外使用 `web_search` 获取实时信息，并先落为结构化 JSON，再由 thesis-tracker/scripts/generate_report.py 以客户可读方式纳入最终正文。

  务必使用此技能当用户：
  - 明确请求投资论文、thesis、投资逻辑、论文跟踪
  - 想建立或更新投资框架、验证关键假设、更新观点
  - 需要追踪催化剂、里程碑、资本回报或信念度变化
  - 需要把既有 thesis 和最新数据做系统化对照

  不适用场景：
  - 首次覆盖深度研究 -> initiating-coverage
  - 财报发布后的单次点评 -> earnings-analysis
  - 只需简单投资建议且不需要完整跟踪报告
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by thesis-tracker/assets/template.md."
---

# RQ 股票研究 - 投资论文跟踪

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、占位符填充和结构校验
- skill 必须自包含，不能依赖仓库级 `utils`
- 不能保留 `[XX]`、`[需要补充]`、`[原因1]` 这类占位文本
- 若提供 `thesis_definition.json`，报告必须优先按用户 thesis 验证；若未提供，则明确说明使用默认财务质量框架
- Thesis 跟踪必须可验证、可反驳；每条关键支柱都应对应可观测指标和反证条件
- 客户稿不得暴露文件名、字段名、`LLM`、`skill` 或内部 workflow 术语
- 缺少数据时必须明确写“无数据 / 无事件 / 未验证 / 未提供 thesis 定义”，不能留空

## 数据源分工

### `RQData CLI` 负责

- 财务、ROE、价格、估值、分红和股东结构
- 公司公告及已实现催化剂
- Thesis 跟踪的结构化主数据和量化验证读数

### `web_search` 负责

- 公司最新新闻、管理层变化、治理动态
- 行业趋势、竞争对手变化、供需链条和主题演化
- 分析师观点变化或网络搜索结果中的预期线索

### `web_search` 禁止替代的内容

- 财务、估值、价格、分红、股东结构和公告主数据
- Thesis 支柱是否通过的核心量化判断
- 任何本应由 RQData 提供的结构化金融数据

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 公司动态、管理层变化、治理事件
- 行业趋势、竞争格局、分析师观点变化
- RQData 无法直接提供的实时定性语境

落盘要求：

- 所有网络搜索结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把搜索草稿或内部笔记直接塞进报告
- 若未提供该文件，报告仍可交付，但相关网络搜索结果部分必须保持“未验证”边界
- 即便提供了 `web_search_findings.json`，它也只能补充 thesis 验证和风险语境，不能直接改写量化结论

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 先完整收集所有数据，再开始分析和更新 thesis，禁止边收集边写结论
- `[MUST-2]` 价格、财务、估值、公告、分红和股东结构必须来自 `RQData CLI`
- `[MUST-3]` 公司新闻、管理层变化和行业趋势必须通过 `web_search` 获取实时信息，不能依赖训练记忆
- `[MUST-4]` Thesis 必须可证伪；每条支柱都必须有明确的验证规则和反证条件
- `[MUST-5]` 反证证据必须与支持证据同等严格记录，不能只保留有利信息
- `[MUST-6]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-7]` 客户稿不得暴露 `LLM`、`skill`、文件名、JSON 字段名或内部 workflow 术语
- `[MUST-8]` 低置信度网络搜索结果不能单独触发核心 thesis 改写
- `[MUST-9]` 若高优先级公告存在 `announcement_link`，正文必须保留原文链接

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般新闻源，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低等级
- 低置信度信息只能作为观察线索，不能单独推翻或强化核心 thesis
- 推断类文字不得标成高置信度

## 图表 / 图片需求

当前实现以表格和结构化追踪说明完成最小可交付版本，但 thesis-tracker 仍必须明确证据与反证展示要求。

- 图表/表格名称：Thesis 支柱证据表
- 目的：逐条展示支柱、当前读数、验证规则与状态
- 使用的数据文件：`thesis_definition.json`、`latest_financials.json`、`historical_financials.json`、`roe.json`
- 关键字段：`pillars[*]`、`revenue`、`net_profit`、`cash_from_operating_activities`、`return_on_equity_weighted_average`
- 建议形式：表格
- 回答问题：核心 thesis 当前还有哪些支柱成立
- 放置位置：`## 关键支柱验证`
- 若图表缺失：不得缺席，必须以表格保留

- 图表/表格名称：反证条件表
- 目的：明确每条支柱的失效条件和下一步观察重点
- 使用的数据文件：`thesis_definition.json`
- 关键字段：`pillars[*].falsifier`
- 建议形式：表格或条目列表
- 回答问题：哪些事实会直接削弱或推翻 thesis
- 放置位置：`## 关键支柱验证`
- 若图表缺失：必须以条目列表降级

- 图表/表格名称：催化剂跟踪表
- 目的：区分已实现催化剂、待兑现催化剂和网络搜索结果验证线索
- 使用的数据文件：`announcement_raw.json`、`dividend.json`、`web_search_findings.json`
- 关键字段：`title`、`info_date`、`announcement_link`、`expected_window`、`why_relevant`
- 建议形式：日历表或追踪表
- 回答问题：后续哪些事件最可能验证或证伪 thesis
- 放置位置：`## 催化剂跟踪`
- 若图表缺失：必须保留结构化追踪表

## 目标产出

- 报告长度：5-8 页
- 目标字数：2,000-3,000 字
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
thesis-tracker/
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

- `thesis_definition.json`：可选，自定义 thesis 定义
- `instrument_meta.json`
- `latest_financials.json`
- `historical_financials.json`
- `roe.json`
- `price_6m.json`
- `hs300_6m.json`
- `pe_ratio.json`
- `pb_ratio.json`
- `dividend.json`
- `shareholder_top10.json`
- `announcement_raw.json`
- `web_search_findings.json`（可选）

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
ORDER_BOOK_ID="${ORDER_BOOK_ID:-601318.XSHG}"
PRICE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=180)).isoformat())
PY
)"
ANNOUNCEMENT_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=30)).isoformat())
PY
)"
DIVIDEND_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=450)).isoformat())
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
ROE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=400)).isoformat())
PY
)"

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/thesis_tracker}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/thesis_tracker_${ORDER_BOOK_ID}_${REPORT_DATE}.md}"
```

### 步骤 2：可选定义 thesis

若用户已经有明确 thesis，推荐在数据目录中提供 `thesis_definition.json`：

```json
{
  "thesis_name": "中国平安综合金融与资本回报修复",
  "core_view": "寿险改革与资本回报延续推动估值修复。",
  "confidence_label": "中高",
  "holding_period": "12个月",
  "position_date": "2025-10-29",
  "current_position": "核心跟踪",
  "target_price": {
    "value": 210,
    "currency": "CNY"
  },
  "pillars": [
    {
      "name": "收入规模继续扩张",
      "metric": "latest_revenue_yoy_pct",
      "operator": ">=",
      "threshold": 1.0,
      "falsifier": "若收入同比连续跌破 1%，则该支柱失效"
    },
    {
      "name": "归母净利润保持增长",
      "metric": "latest_net_profit_yoy_pct",
      "operator": ">=",
      "threshold": 5.0,
      "falsifier": "若利润同比回落至 5% 以下，则 thesis 需要下修"
    },
    {
      "name": "ROE维持双位数",
      "metric": "latest_roe",
      "operator": ">=",
      "threshold": 10.0
    },
    {
      "name": "现金创造强于利润",
      "metric": "latest_cash_profit_ratio",
      "operator": ">=",
      "threshold": 1.0
    }
  ],
  "planned_catalysts": [
    {
      "title": "年度利润分配执行",
      "expected_window": "2026-04至2026-06",
      "expected_impact": "验证资本回报改善是否兑现"
    }
  ],
  "risk_items": [
    {
      "title": "资本市场波动拖累投资收益",
      "initial_assessment": "中",
      "monitor": "关注利润弹性与估值波动",
      "response": "跟踪利润兑现与估值再定价节奏"
    }
  ]
}
```

如果没有该文件，脚本会自动退化为“默认财务质量框架”，并在报告中明确标注。

### 步骤 3：采集结构化主数据

```bash
mkdir -p "$DATA_DIR"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"]
}" --format json > "$DATA_DIR/instrument_meta.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fields\": [\"revenue\", \"net_profit\", \"cash_from_operating_activities\"],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/historical_financials.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fields\": [\"revenue\", \"net_profit\", \"cash_from_operating_activities\"],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/latest_financials.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$ROE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/roe.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"pe_ratio\",
  \"start_date\": \"$ROE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/pe_ratio.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"pb_ratio\",
  \"start_date\": \"$ROE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/pb_ratio.json"

rqdata stock cn price --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\", \"total_turnover\"],
  \"adjust_type\": \"post\"
}" --format json > "$DATA_DIR/price_6m.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"000300.XSHG\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/hs300_6m.json"

rqdata stock cn dividend --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$DIVIDEND_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/dividend.json"

rqdata stock cn shareholder-top10 --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$DIVIDEND_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/shareholder_top10.json"

rqdata stock cn announcement --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$ANNOUNCEMENT_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/announcement_raw.json"
```

说明：

- `financial-indicator` 使用 `factor` 参数，不是 `fields`
- `roe.json` 建议抓一个较宽的日期区间，脚本会自动选最新可用值
- `historical_financials.json` 和 `latest_financials.json` 允许复用同一份原始数据，脚本内部会自动抽取最新报告期
- `shareholder_top10.json` 建议至少覆盖最近 4-6 个季度，便于观察稳定性
- `announcement_raw.json` 建议至少回看 30 天，用于识别最近已实现催化剂

### 步骤 3.5：可选的公司 / 管理层 / 行业语境补充

当用户需要更完整的 thesis 验证背景时，可执行该步骤。

- 使用 `web_search` 补充公司新闻、管理层变化、行业趋势、竞争格局或分析师观点变化
- 结果必须写入 `web_search_findings.json`
- 这类结果只补充 thesis 验证、催化剂跟踪和风险监控语境，不能替代量化主数据

### 步骤 4：生成 Markdown 报告

```bash
python3 thesis-tracker/scripts/generate_report.py \
  --stock "$ORDER_BOOK_ID" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

常用参数：

- `--stock`：股票代码
- `--company`：公司名称，可选；若缺失则优先从 `instrument_meta.json` 读取
- `--data-dir`：原始 JSON 数据目录
- `--report-date`：报告日期，默认当天
- `--output`：输出 Markdown 路径
- `--template`：自定义模板路径，默认 `thesis-tracker/assets/template.md`
- `--no-render`：不生成 HTML

### 步骤 5：渲染 HTML

脚本会优先尝试调用本地安装的 `rq-report-renderer`，若未安装则回退到仓库内 `report-renderer/scripts/render_report.py`；两者都不可用时保留 Markdown 并打印警告。

## 阶段门控

### Gate 1：Thesis 主定义与量化主数据齐备

- 股票、财务、价格、估值、公告、分红和股东结构主数据存在
- 若用户提供自定义 thesis，其关键支柱和目标价可解析
- 即使没有网络搜索结果，报告也能完成基础 thesis 跟踪

### Gate 2：支柱验证与反证条件完成

- 每条支柱都能映射到真实指标
- 每条支柱都有验证规则和反证条件
- 支持证据与反证线索都已进入正文

### Gate 3：可选网络搜索结果完成

- 若启用网络搜索结果补充，`web_search_findings.json` 已落盘
- 字段完整、来源等级可解释
- 网络搜索结果仅用于公司 / 管理层 / 行业 / 竞争 / 分析师跟踪信息

### Gate 4：成稿完成

- Markdown 已生成
- 若本地渲染器存在，HTML 已生成
- 客户稿不暴露内部术语
- 长度、章节、来源标注和 thesis 追踪表均达标

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[AS_OF_TIME]]`
  - `[[COMPANY_NAME]]`
  - `[[STOCK_CODE]]`
  - `[[THESIS_SOURCE]]`
  - `[[EXEC_SUMMARY]]`
  - `[[THESIS_SNAPSHOT]]`
  - `[[PILLAR_VERIFICATION]]`
  - `[[MARKET_PERFORMANCE]]`
  - `[[CAPITAL_RETURN]]`
  - `[[CATALYST_TRACKING]]`
  - `[[RISK_MONITORING]]`
  - `[[UPDATE_LOG]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- 论文快照必须落到 thesis 名称、当前价/目标价、信念度和仓位/周期等交付层信息
- 关键支柱必须引用真实指标，不得保留空白观点
- 每条支柱都必须能回答 4 个问题：
  - 当前读数是什么
  - 验证规则是什么
  - 反证条件是什么
  - 当前状态是通过还是未通过
- 若无自定义 thesis 文件，必须明确写明“采用默认财务质量框架”
- 催化剂和风险必须优先引用真实公告、分红和价格表现
- 若启用 `web_search`，正文必须真实吸收公司 / 管理层 / 行业 / 竞争 / 分析师语境，而不是只多一个 sidecar JSON
- 高优先级催化剂若存在 `announcement_link`，正文必须保留原文链接
- 风险监控不能只剩几条泛化 bullet，应至少包含风险矩阵或风险详解
- 不得残留 `[XX]`、`[原因1]`、`[催化剂1]`、旧路径或内部字段名

## 阶段验收清单

- [ ] Markdown 文件存在
- [ ] 若本地渲染器存在则 HTML 文件存在
- [ ] 模板占位符无残留
- [ ] 主章节完整
- [ ] 报告达到 5-8 页最低可交付标准
- [ ] 关键数据点与关键结论带 `数据来源：XXX，置信度X`
- [ ] Thesis 支柱包含验证规则和反证条件
- [ ] 若使用 `web_search`，其内容已真正进入最终报告且未越权替代 RQData 主数据
- [ ] 高优先级催化剂保留原文链接

## 常见错误

- 直接在 Python 中写死“核心观点 / 催化剂 / 风险”占位文字
- 把 `financial-indicator` 错当成 `fields` 接口使用
- 不去重 `financial` 的复权 / 重述记录，导致同一季度重复计算
- 把低信号公告当作核心催化剂
- 只记录支持 thesis 的证据，不记录反证条件和失效线索
- 把网络搜索结果直接写成核心结论，却没有结构化来源落盘
- 继续依赖旧版 `~/.claude/skills/...` 路径来组织 skill 内部文件
