---
name: rq-catalyst-calendar
description: |
  创建模板驱动的催化剂日历报告，追踪覆盖股票池未来 30 天的重要事件和催化剂。
  使用 RQData CLI 获取财报、分红、公告等结构化公司事件；当需要补充宏观、行业会议或政策催化时，可额外使用 `web_search` 获取实时信息，并先落为结构化 JSON，再统一生成 Markdown 和 HTML 报告。

  务必使用此技能当用户：
  - 明确请求催化剂日历、事件日历、earnings calendar、upcoming events
  - 想了解接下来一段时间的重要事件、本周/本月重点日期
  - 需要追踪股票池的重要催化剂
  - 需要把公司事件与宏观 / 行业催化放进同一份时间轴报告

  不适用场景：
  - 单个公司财报深度分析 -> earnings-preview / earnings-analysis
  - 行业整体分析 -> sector-overview
  - 只需回答单一事实且不需要完整日历报告
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by catalyst-calendar/assets/template.md."
---

# RQ 股票研究 - 催化剂日历

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只负责归一化、校验和填充固定占位符，不负责自由撰写长段正文
- `RQData CLI` 是公司结构化事件主源；`web_search` 只能补充 CLI 无法直接提供的宏观 / 行业 / 会议信息
- 所有结论必须数据驱动，不能写死日期、季度、股票代码、公司名称
- 没有精确事件日期时，只能输出预计窗口，不能伪造具体日期
- 每个关键事件、关键判断和跟踪建议都必须标注数据来源和置信度

## 数据源分工

### `RQData CLI` 负责

- 财报相关日期、业绩快报后续正式披露窗口
- 分红、股权登记、除权除息
- 上市公司公告、股东大会、董事会 / 监事会决议、资本运作公告
- 基础证券信息、股票池、公司名称映射

### `web_search` 负责

- 宏观数据发布日期和政策会议日程
- 行业会议、论坛、展会、监管征求意见、主题催化
- RQData CLI 无法直接提供、但会影响覆盖股票池的实时外部催化

### `web_search` 禁止替代的内容

- 财务数据、估值、股价、成交量等结构化市场数据
- 上市公司公告、分红、股东大会、正式财报发布日期
- 任何可以直接通过 RQData CLI 或交易所 / 公司官方披露获得的结构化公司事件

## `web_search` 使用规则

详细检索口径、结构化字段、来源等级、落盘示例和执行要求见 [references/web_search.md](references/web_search.md)。

### 允许的补充内容

- 宏观：CPI / PPI / GDP / PMI / LPR / 利率会议 / 政策发布时间
- 行业：会议、论坛、主题展会、监管政策日程、行业统计发布时间
- 主题催化：影响股票池的跨公司事件，例如补贴政策窗口、行业标准落地、重要招标或牌照审批时点

### 结构化落盘要求

通过 `web_search` 获取的信息，必须先写入 `web_search_events.json`。

说明：

- `event_scope` 仅允许 `macro` 或 `industry`
- `date_type=exact` 时必须提供 `event_date`
- `date_type=estimated_window` 时必须提供 `window_start` 与 `window_end`
- `web_search` 获取的预计窗口事件，置信度不得高于 `3`

### fallback 规则

1. 优先使用当前环境原生可用的 `web_search` 工具。
2. 若无原生 `web_search`，fallback 到当前环境已配置的联网检索工具。
3. 若仍无法联网：
   - 不得伪造实时宏观 / 行业事件
   - 不得用训练记忆补日期
   - 在报告中明确写出该部分缺失
   - 相关判断降为最低可交付置信度，或直接标为未验证

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 公司结构化事件必须优先来自 `RQData CLI`
- `[MUST-2]` `web_search` 不得替代财报、分红、公告、股东大会等公司正式事件主源
- `[MUST-3]` 公告分类必须基于标题语义和原文校验，不能依赖不透明数字分类码
- `[MUST-4]` 没有精确日期时只能写预计窗口，不能把窗口伪装成确定日期
- `[MUST-5]` 所有关键事件都要写 `数据来源：XXX，置信度X`
- `[MUST-6]` 预计窗口、未验证信息和推断信息不得作为核心高确信度结论
- `[MUST-7]` 同一事件要做去重、来源校验和日期语义校验，不能把披露日误写成事件发生日
- `[MUST-8]` 若过去公告已明确未来日期，必须按实际事件日纳入日历，同时保留原披露日和原文链接
- `[MUST-9]` 最终输出必须严格来自模板，不得在脚本中自由拼写整篇报告

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府部门、行业协会、官方机构、权威财经媒体，且时间和内容明确
- `3`：一般新闻源或二手整理，但出处明确且与其他来源一致；或 `web_search` 的预计窗口事件
- `2`：单一来源、细节不完整、日期仍待核实
- `1`：推断、估算窗口、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低值
- `web_search` 的预计窗口事件最高只能标 `3`
- 低置信度信息不能成为高影响事项的唯一依据

## 图表 / 图片需求

本 skill 的推荐图示为“催化剂时间轴图”，但允许在图表无法生成时降级为表格，不把图表能力写成当前脚本做不到的强制要求。

- 图表名称：催化剂时间轴图
- 图表目的：把未来 30 天高影响事件按日期铺开，帮助识别事件密集日和连续催化链条
- 使用的数据文件：`announcement_raw.json`、`earnings_express_raw.json`、`latest_financial_raw.json`、`dividend_raw.json`、可选 `web_search_events.json`
- 关键字段：`event_date`、`window_start`、`window_end`、`event_type`、`impact_level`、`company/scope_name`
- 建议图表类型：横向时间轴或甘特式时间带
- 时间范围：`--start-date` 到 `--end-date`
- 图表应回答的问题：未来 30 天哪几天最密集、哪些事件需要提前准备、哪些只有窗口没有精确日期
- 报告放置位置：`## 完整日历视图` 之前或同节内
- 若图表缺失：必须保留 `完整日历视图` 表格和 `日期不确定事件` 表格作为降级交付

## 目标产出

- 报告长度：5-8 页
- 目标字数：2,000-3,000 字
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装 `rq-report-renderer`）
- 报告目录：用户通过 `--data-dir` 和 `--output` 指定，不写死固定路径

## 目录结构

```text
catalyst-calendar/
├── SKILL.md
├── scripts/
│   └── generate_report.py
├── assets/
│   └── template.md
└── references/
    └── data_contract.md
```

## 输入文件契约

原始数据目录由 `--data-dir` 指定，脚本会优先读取下列文件：

- `stock_pool.json`
- `earnings_express_raw.json`
- `latest_financial_raw.json`
- `dividend_raw.json`
- `instrument_meta.json`
- `announcement_raw.json`
- `web_search_events.json`（可选，仅用于宏观 / 行业催化补充）

文件结构默认兼容 RQData CLI 常见输出：

- 顶层为对象，核心数据放在 `data` 字段
- `data` 可以是列表，也可以是单个对象
- 股票字段常见命名：
  - `order_book_id`
  - `ticker`
  - `stock_code`
  - `symbol`
- 公司名字段常见命名：
  - `name`
  - `display_name`
  - `stock_name`
  - `company_name`
  - `symbol`

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
AS_OF_DATE="$(date +%F)"
START_DATE="$AS_OF_DATE"
INDEX_ID="${INDEX_ID:-000300.XSHG}"
ORDER_BOOK_IDS_JSON="${ORDER_BOOK_IDS_JSON:-[\"000001.XSHE\",\"600000.XSHG\"]}"
START_QUARTER="${START_QUARTER:-2025q1}"
END_QUARTER="${END_QUARTER:-2025q4}"
DIVIDEND_LOOKBACK_START="$(python3 - <<'PY'
from datetime import date, timedelta
print((date.today() - timedelta(days=365)).isoformat())
PY
)"
ANNOUNCEMENT_LOOKBACK_START="$(python3 - <<'PY'
from datetime import date, timedelta
print((date.today() - timedelta(days=120)).isoformat())
PY
)"
END_DATE="$(python3 - <<'PY'
from datetime import date, timedelta
print((date.today() + timedelta(days=30)).isoformat())
PY
)"

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/catalyst_calendar}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/catalyst_calendar_${START_DATE}_${END_DATE}.md}"
```

### 步骤 2：采集原始数据

以下文件名是脚本默认识别的输入文件名。字段名需要和实际 RQData 返回保持一致，必要时先用 `rqdata help <subcommand>` 检查。

```bash
mkdir -p "$DATA_DIR"

# 示例：股票池
rqdata index constituents --payload "{
  \"order_book_id\": \"$INDEX_ID\",
  \"date\": \"$AS_OF_DATE\"
}" --format json > "$DATA_DIR/stock_pool.json"

# 示例：业绩快报（以观察起始日为锚点，抓取最近披露的快报）
rqdata stock cn financial-express --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"info_date\": \"$START_DATE\",
  \"interval\": \"4q\"
}" --format json > "$DATA_DIR/earnings_express_raw.json"

# 示例：最新正式财报（用于判断快报对应报告期是否已经被正式财报覆盖）
rqdata stock cn financial --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"fields\": [\"revenue\"],
  \"start_quarter\": \"$START_QUARTER\",
  \"end_quarter\": \"$END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/latest_financial_raw.json"

# 示例：分红相关原始数据
rqdata stock cn dividend --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"start_date\": \"$DIVIDEND_LOOKBACK_START\",
  \"end_date\": \"$END_DATE\"
}" --format json > "$DATA_DIR/dividend_raw.json"

# 示例：基础信息（用于补公司名称）
rqdata stock cn instruments --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON
}" --format json > "$DATA_DIR/instrument_meta.json"

# 示例：公告数据（用于精确识别财报、股东大会、利润分配等事件）
rqdata stock cn announcement --payload "{
  \"order_book_ids\": $ORDER_BOOK_IDS_JSON,
  \"start_date\": \"$ANNOUNCEMENT_LOOKBACK_START\",
  \"end_date\": \"$END_DATE\"
}" --format json > "$DATA_DIR/announcement_raw.json"
```

### 步骤 2B：补充 `web_search` 宏观 / 行业事件

仅当需要补充实时宏观或行业催化，且这些信息无法直接通过 `RQData CLI` 获得时，才执行此步骤。

执行要求：

- 先检索，再整理，最后落为 `web_search_events.json`
- 每条记录都要保留来源、链接、发布时间、检索时间和相关性说明
- 不得把检索到的新闻标题直接当作结构化事件，必须手工或脚本抽取 `event_date` 或 `window_start/window_end`

### 步骤 3：生成 Markdown 报告

```bash
python3 catalyst-calendar/scripts/generate_report.py \
  --data-dir "$DATA_DIR" \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --output "$OUTPUT_MD"
```

常用参数：

- `--data-dir`：原始 JSON 数据目录
- `--start-date`：观察窗口开始日期
- `--end-date`：观察窗口结束日期
- `--output`：输出 Markdown 路径
- `--template`：自定义模板路径，默认 `catalyst-calendar/assets/template.md`
- `--report-date`：报告日期，默认当天
- `--no-render`：不生成 HTML

### 步骤 4：渲染 HTML

脚本默认会尝试使用本地安装的 `rq-report-renderer` 渲染 HTML。如果本地没有可用渲染器，脚本会保留 Markdown 并打印警告。

## 阶段门控

### 阶段 1：数据采集完成标准

- RQData 原始文件齐全，或缺失原因已明确记录
- `announcement_raw.json` 的时间范围包含观察起始日前至少 120 天
- 若使用 `web_search`，`web_search_events.json` 已按结构化字段落盘
- 未验证日期、模糊窗口和推断信息已被单独标识

### 阶段 2：报告生成完成标准

- 所有未来事件都完成去重
- 公告披露日与实际事件日已做语义区分
- 预计窗口事件未被写成精确日期
- 模板占位符全部替换完成
- 关键事件后都有 `数据来源：XXX，置信度X`

### 阶段 3：交付完成标准

- Markdown 报告生成成功
- 若渲染器可用，HTML 报告生成成功
- 关键章节齐全
- 若图表缺失，已通过表格完成降级交付

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 模板占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[START_DATE]]`
  - `[[END_DATE]]`
  - `[[COVERAGE_SCOPE]]`
  - `[[EXEC_SUMMARY]]`
  - `[[CALENDAR_TABLE]]`
  - `[[EVENT_TYPE_SECTIONS]]`
  - `[[HIGH_IMPACT_SECTIONS]]`
  - `[[RECENT_DISCLOSED_SECTIONS]]`
  - `[[ACTION_SECTION]]`
  - `[[ESTIMATED_EVENT_SECTIONS]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- 每个关键数据块后必须有来源说明
- 不得出现硬编码示例值残留
- 不得将预计窗口写成精确日期
- 高影响事件必须给出事件依据和关注点
- 若过去公告已明确未来日期，正文应按实际事件日展示，而不是按公告披露日展示
- 公告处理应先做标题筛选；只有高概率携带未来日期或期间的公告才需要读取原文
- 正文必须保留可追溯的公告原文链接
- 宏观 / 行业事件若来自 `web_search`，必须保留来源名称、URL、发布时间和检索时间
- 无事件时必须明确写出“无数据 / 无事件”，不能输出空章节

## 阶段验收清单

- [ ] 数据采集 -> 报告生成 -> HTML 渲染三阶段都按顺序执行
- [ ] `RQData CLI` 与 `web_search` 的边界没有混用
- [ ] `web_search` 事件已写入结构化 JSON，而不是散落在自然语言笔记里
- [ ] 公司事件与宏观 / 行业事件都保留来源和置信度
- [ ] 事件去重、来源校验、日期语义校验已完成
- [ ] 时间轴图若未生成，表格降级路径仍能支撑报告质量
- [ ] Markdown / HTML 输出达到 5-8 页目标的可交付质量

## 常见错误

- 直接在 Python 里拼接整篇报告正文
- 用固定季度映射伪造财报发布日期
- 把十大股东数据误当作股东大会日期来源
- 把分红预案、股权登记、除权除息混成一个事件
- 使用网络搜索替代 `RQData` 的正式公司事件
- 用新闻发布时间代替真实事件发生日期
- 使用固定输出目录而不是 `--data-dir` / `--output`
