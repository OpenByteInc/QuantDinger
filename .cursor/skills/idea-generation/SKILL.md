---
name: rq-idea-generation
description: |
  创建模板驱动的投资创意生成报告，基于真实股票池与财务指标完成系统化量化筛选，并在必要时结合主题/政策/行业实时信息做第二阶段验证，输出价值、成长、质量三类候选及其跟踪重点。
  `RQData CLI` 负责股票池、公司元数据、财务与估值因子主数据；`web_search` 只在需要补充主题验证、政策催化或行业动态时使用，且必须先落为结构化 JSON，再由当前 LLM 基于结构化快照和 `assets/template.md` 回写客户可读正文，最后渲染 HTML。

  务必使用此技能当用户：
  - 明确请求投资创意、idea generation、找投资机会、系统化选股
  - 需要对一个股票池做价值 / 成长 / 质量筛选
  - 想快速形成一份带量化依据的候选清单
  - 需要把筛选规则、候选结果和跟踪重点整理成正式报告

  不适用场景：
  - 单个公司首次深度覆盖 -> initiating-coverage
  - 财报发布后的单家公司分析 -> earnings-analysis
  - 只要一句话推荐且不需要完整报告
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by idea-generation/assets/template.md."
---

# RQ 股票研究 - 投资创意生成

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> LLM 生成正文 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、结构化快照生成、占位符填充和结构校验
- Python 只输出结构化 facts / tables / thresholds，不在代码里硬写推荐语、主题判断或客户可读结论
- 报告必须严格使用真实 `rqdata` 原始文件，不得在代码里硬写候选名单、日期、行业结论或投资判断
- 候选名单只代表“进入下一轮研究池的线索”，不代表最终投资结论
- 缺少数据时必须明确写“无数据 / 未启用该策略 / 未验证”，不能留空

## 数据源分工

### `RQData CLI` 负责

- 股票池、公司名称、行业归属
- 财务快照、历史财务、同比增速
- ROE、市值、PE、PB 等定量筛选指标
- 候选排序、覆盖率、行业分布、交叉命中等结构化事实

### `web_search` 负责

- 量化结果出来后的主题验证
- 行业景气、政策变化、监管动态
- 候选池相关的最新新闻、会议、产业催化

### `web_search` 禁止替代的内容

- 财务数字、估值指标、股票池和公司基础信息
- 正式公告、价格、成交额、一致预期等结构化金融数据
- 任何候选排序、阈值判断和基础量化筛选结果

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 某条主题线索是否存在最新政策或产业催化
- 候选集中行业的景气验证和关键争议点
- 候选公司是否存在影响研究优先级的最新公开事件

落盘要求：

- 所有 `web_search` 结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把自然语言草稿直接塞进报告
- `generate_report.py` 不直接消费该文件；当前 LLM 应在回写 `idea_screening_snapshot.json -> summaries.*` 前读取它
- 若未做 `web_search`，报告必须保持“量化候选池”定位，不能伪装成已完成主题验证

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 所有定量筛选指标必须来自 `RQData CLI`
- `[MUST-2]` `web_search` 只用于第二阶段主题验证，不能替代量化主数据
- `[MUST-3]` 金额类数据在正文和表格中必须换算为“亿元”等客户可读口径
- `[MUST-4]` 候选池不是最终结论；正文必须写清筛选逻辑、催化剂线索和关键风险，不能把候选直接写成“推荐买入”
- `[MUST-5]` `idea_screening_snapshot.json -> summaries.*` 必须由当前 LLM 直接回写；Python 不负责代写正文
- `[MUST-6]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-7]` 若某策略未启用或未形成候选，仍要由 LLM 明确写清该章节当前用途和缺口，不能留空
- `[MUST-8]` 低置信度外部信息不得改写量化筛选结论或候选优先级
- `[MUST-9]` 最终输出必须严格来自模板，不得在脚本中自由拼写整篇报告

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般媒体或二手整理，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算窗口、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低值
- 推断、主题映射或媒体传闻不得标成高置信度
- 低置信度信息只能作为跟踪线索，不能直接升级为核心投资判断

## 图表 / 图片需求

当前脚本以表格交付最小可用版本，但本 skill 仍必须定义达标报告所需的视觉载体；若未生成图表，必须由等价表格降级，不能省略关键问题。

- 图表名称：股票池覆盖与行业分布
- 图表目的：说明股票池规模、最新财报季度覆盖和行业集中情况
- 使用的数据文件：`stock_pool.json`、`instrument_meta.json`、`financials_history.json`
- 关键字段：`order_book_id`、`quarter`、`sector_code_name` / `industry_name`
- 建议图表类型：柱状图或分布表
- 回答问题：当前筛选结果是否受样本覆盖或行业集中度影响
- 放置位置：`## 股票池与筛选框架`
- 若图表缺失：保留覆盖率表、财报季度分布表、行业分布表

- 图表名称：指标看板与候选对比
- 图表目的：比较全池中位数与各策略候选的关键指标
- 使用的数据文件：`idea_screening_snapshot.json`
- 关键字段：`universe.median_metrics`、`strategies.*.median_metrics`、`strategies.*.candidate_count`
- 建议图表类型：分组柱状图、雷达图或对比表
- 回答问题：价值 / 成长 / 质量三组候选的主要特征差异是什么
- 放置位置：`## 指标看板`
- 若图表缺失：保留指标看板说明与候选表格

- 图表名称：交叉命中与风险暴露
- 图表目的：展示多策略共振程度与候选集中风险
- 使用的数据文件：`idea_screening_snapshot.json`
- 关键字段：`overlap.display_candidates`、`risk_flags.*`
- 建议图表类型：交叉矩阵、热力表或风险摘要表
- 回答问题：哪些候选值得优先进入下一轮研究，当前结果的主要结构性风险是什么
- 放置位置：`## 候选组合与交叉验证`、`## 风险与跟踪重点`
- 若图表缺失：保留交叉命中表和风险事实表

## 目标产出

- 报告长度：8-12 页
- 正文目标：约 `3000-5000` 中文字符
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
idea-generation/
├── SKILL.md
├── scripts/
│   ├── build_screening_snapshot.py
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
- `latest_financials.json`
- `financials_history.json`
- `roe.json`
- `market_cap.json`
- `pe_ratio.json`
- `pb_ratio.json`
- `web_search_findings.json`（可选，仅供 LLM 回写 summary 前读取）

这些文件都允许以下结构：

- `{ "data": [...] }`
- `{ "data": { ... } }`
- `[...]`
- `{ ... }`

其中：

- `stock_pool.json` 用于定义股票池
- `instrument_meta.json` 用于公司名称和行业字段补充
- `latest_financials.json` 用于读取每只股票最近一期财务快照
- `financials_history.json` 用于计算同比增长
- `roe.json`、`market_cap.json`、`pe_ratio.json`、`pb_ratio.json` 用于估值与质量筛选
- `web_search_findings.json` 只作为第二阶段主题验证输入，不参与 Python 打分或排序

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
TOP_N="${TOP_N:-10}"
STRATEGY="${STRATEGY:-all}"
START_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year - 2}q1")
PY
)"
END_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year}q4")
PY
)"

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/idea_generation}"
SNAPSHOT_JSON="${SNAPSHOT_JSON:-$DATA_DIR/idea_screening_snapshot.json}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/idea_generation_${STRATEGY}_${REPORT_DATE}.md}"
```

### 步骤 2：采集全市场股票池

```bash
mkdir -p "$DATA_DIR"

rqdata stock cn list --payload "{
  \"date\": \"$REPORT_DATE\",
  \"type\": \"CS\"
}" --format json > "$DATA_DIR/stock_pool.json"
```

### 步骤 3：分批采集基础信息、财务和估值指标

```bash
python3 - "$DATA_DIR" "$REPORT_DATE" "$START_QUARTER" "$END_QUARTER" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
report_date = sys.argv[2]
start_quarter = sys.argv[3]
end_quarter = sys.argv[4]

stock_pool = json.loads(data_dir.joinpath("stock_pool.json").read_text(encoding="utf-8"))
rows = stock_pool if isinstance(stock_pool, list) else stock_pool.get("data", [])
ids = [row["order_book_id"] for row in rows if isinstance(row, dict) and row.get("order_book_id")]

def fetch_batches(command_prefix, payload_builder, output_file):
    collected = []
    for start in range(0, len(ids), 800):
        chunk = ids[start:start + 800]
        payload = json.dumps(payload_builder(chunk), ensure_ascii=False)
        output = subprocess.check_output([*command_prefix, "--payload", payload, "--format", "json"], text=True)
        batch = json.loads(output)
        collected.extend(batch if isinstance(batch, list) else batch.get("data", []))
    data_dir.joinpath(output_file).write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")

fetch_batches(
    ["rqdata", "stock", "cn", "instruments"],
    lambda chunk: {"order_book_ids": chunk},
    "instrument_meta.json",
)
fetch_batches(
    ["rqdata", "stock", "cn", "financial"],
    lambda chunk: {
        "order_book_ids": chunk,
        "fields": ["revenue", "net_profit", "total_assets", "total_liabilities"],
        "start_quarter": start_quarter,
        "end_quarter": end_quarter,
        "statements": "all",
    },
    "financials_history.json",
)
data_dir.joinpath("latest_financials.json").write_text(
    data_dir.joinpath("financials_history.json").read_text(encoding="utf-8"),
    encoding="utf-8",
)
for factor, output_file in [
    ("return_on_equity_weighted_average", "roe.json"),
    ("market_cap", "market_cap.json"),
    ("pe_ratio", "pe_ratio.json"),
    ("pb_ratio", "pb_ratio.json"),
]:
    fetch_batches(
        ["rqdata", "stock", "cn", "financial-indicator"],
        lambda chunk, current_factor=factor: {
            "order_book_ids": chunk,
            "factor": current_factor,
            "start_date": report_date,
            "end_date": report_date,
        },
        output_file,
    )
PY
```

说明：

- `idea-generation` 默认以全市场 A 股作为筛选覆盖范围，不再优先局限于单一指数成分股
- `financial-indicator` 使用 `factor` 参数，不是 `fields`
- `financials_history.json` 与 `latest_financials.json` 可以复用同一份查询结果，脚本会自动按股票抽取最新季度并计算同比
- 全市场数据量较大，必须按批次抓取，不能把全部 `order_book_id` 一次性塞给单个命令

### 步骤 4：生成结构化筛选快照

```bash
python3 idea-generation/scripts/build_screening_snapshot.py \
  --strategy "$STRATEGY" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --top-n "$TOP_N" \
  --value-pe-median-multiplier "${VALUE_PE_MEDIAN_MULTIPLIER:-1.00}" \
  --value-pb-max "${VALUE_PB_MAX:-1.50}" \
  --value-roe-min "${VALUE_ROE_MIN:-10.0}" \
  --growth-revenue-yoy-min "${GROWTH_REVENUE_YOY_MIN:-15.0}" \
  --growth-profit-yoy-min "${GROWTH_PROFIT_YOY_MIN:-20.0}" \
  --growth-roe-min "${GROWTH_ROE_MIN:-15.0}" \
  --quality-roe-min "${QUALITY_ROE_MIN:-15.0}" \
  --quality-debt-ratio-max "${QUALITY_DEBT_RATIO_MAX:-50.0}" \
  --output "$SNAPSHOT_JSON"
```

### 步骤 4.5：可选的主题 / 催化剂验证

当用户明确要求主题扫描，或量化结果需要实时外部语境时，才执行这一步。

- 先用 `web_search` 收集主题、行业、政策或候选公司最新事件
- 结果写入 `web_search_findings.json`
- 不得把 `web_search` 结果直接喂给 Python 做打分、排序或阈值判断
- 若没有执行这一步，最终报告应保持“量化候选池 + 后续验证建议”的口径

### 步骤 5：当前 LLM 直接回写 summary

运行完快照脚本后，当前 LLM 必须直接读取 `"$SNAPSHOT_JSON"`，并在存在时一并读取 `"$DATA_DIR/web_search_findings.json"`，基于结构化数据和 [template.md](assets/template.md) 回写：

- `summaries.exec_summary`
- `summaries.universe_overview`
- `summaries.metric_scoreboard`
- `summaries.value_section`
- `summaries.growth_section`
- `summaries.quality_section`
- `summaries.overlap_section`
- `summaries.risk_section`
- `summaries.appendix`

要求：

- 这些 summary 必须是客户可读正文，不得写“LLM 总结”“脚本生成”“流程说明”
- 文字结论只允许由当前 LLM 基于快照事实和可选 `web_search_findings.json` 生成
- 未启用策略也要写清楚“本轮未启用，但保留该视角作为对照 / 后续扩池方向”
- 若只重复表格字段、输出项目符号堆砌或泄露文件名 / `summaries.*` / workflow 术语，视为未完成

### 步骤 6：渲染最终 Markdown 报告

常用参数：

- `--data-dir`：原始 JSON 数据目录
- `--snapshot`：显式指定结构化快照路径；默认读取 `--data-dir/idea_screening_snapshot.json`
- `--output`：输出 Markdown 路径
- `--template`：自定义模板路径，默认 `idea-generation/assets/template.md`
- `--no-render`：不生成 HTML
- 快照阶段阈值参数全部显式传入，例如 `--growth-revenue-yoy-min`、`--quality-roe-min`；这些阈值由用户决定，脚本只负责执行

### 步骤 7：渲染 HTML

脚本会优先尝试调用本地安装的 `rq-report-renderer`，若未安装则回退到仓库内 `report-renderer/scripts/render_report.py`；仍不可用时保留 Markdown 并打印警告。

## 阶段门控

### Gate 1：数据采集完成

- 原始 JSON 文件齐全
- 股票池、财务、ROE、PE、PB 都有真实记录
- 没有用固定股票名单或手填候选替代数据采集

### Gate 2：结构化快照完成

- `idea_screening_snapshot.json` 已生成
- `strategies.*.thresholds` 记录了本次真实参数
- `summaries.*` 仍为空，说明 Python 没有越界代写正文

### Gate 3：LLM 正文完成

- `summaries.*` 已全部回写
- 正文是客户可读内容，不是字段复读或内部流程描述
- 若使用 `web_search`，其信息只体现在总结与跟踪建议，不改写量化主结论

### Gate 4：交付完成

- Markdown 已生成
- 若本地渲染器可用，HTML 已生成
- 报告长度、章节、来源标注和风险提示均达标

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[STRATEGY]]`
  - `[[EXEC_SUMMARY]]`
  - `[[UNIVERSE_OVERVIEW]]`
  - `[[UNIVERSE_FACTS]]`
  - `[[QUARTER_TABLE]]`
  - `[[SECTOR_TABLE]]`
  - `[[METRIC_SCOREBOARD]]`
  - `[[METRIC_TABLE]]`
  - `[[VALUE_SUMMARY]]`
  - `[[VALUE_FACTS]]`
  - `[[VALUE_TABLE]]`
  - `[[GROWTH_SUMMARY]]`
  - `[[GROWTH_FACTS]]`
  - `[[GROWTH_TABLE]]`
  - `[[QUALITY_SUMMARY]]`
  - `[[QUALITY_FACTS]]`
  - `[[QUALITY_TABLE]]`
  - `[[OVERLAP_SUMMARY]]`
  - `[[OVERLAP_FACTS]]`
  - `[[OVERLAP_TABLE]]`
  - `[[RISK_SUMMARY]]`
  - `[[RISK_FACTS]]`
  - `[[APPENDIX_SUMMARY]]`
  - `[[APPENDIX_FACTS]]`

## 报告质量要求

- 完整包含模板中的主章节
- 候选股票必须来自真实股票池与真实财务 / 估值指标
- 不得残留 `[XX]`、`[需要补充]`、`[股票A]` 这类占位文本
- 文本必须说明筛选规则、覆盖范围、候选意义和关键风险，不能只有空表格
- 每个策略都要说明“启用 / 未启用”和结果数量
- 不能把量化候选直接写成确定性投资结论，必须保留“候选池 / 下一轮验证”边界
- 建议正文达到 `3000-5000` 中文字符；单策略模式也应保留足够的筛选框架、预览和跟踪说明，不能退化成简表

## 阶段验收清单

- [ ] `idea_screening_snapshot.json` 成功生成，且 `summaries.*` 完整回写
- [ ] Markdown 文件存在
- [ ] 若本地渲染器存在则 HTML 文件存在
- [ ] 模板占位符无残留
- [ ] 主章节完整
- [ ] 报告长度达到 8-12 页的最低可交付标准
- [ ] 关键数据点与关键结论带 `数据来源：XXX，置信度X`
- [ ] 至少一个启用策略生成非空候选表
- [ ] 未启用策略章节仍有合格说明，不是空白
- [ ] 若使用 `web_search`，相关记录已落盘且未越权替代 RQData 主数据

## 常见错误

- 把 `financial-indicator` 返回字段错误地当成统一 `value`
- 直接在 Python 里硬写“推荐股票名单”或评论句，而不是让当前 LLM 基于快照生成正文
- 使用固定日期、固定季度、固定输出路径
- 只输出表格，不解释规则、覆盖率和风险
- 把候选池直接写成确定性结论，忽略第二阶段验证
- 继续依赖旧版 `~/.claude/skills/...` 或仓库级 `utils`
