---
name: rq-sector-overview
description: |
  创建模板驱动的行业概览报告，基于显式行业股票池与真实财务/估值/价格数据完成行业层面的结构化分析。
  使用 RQData CLI 获取股票池、行业分类、财务、估值与区间价格，再由 sector-overview/scripts/generate_report.py 严格按照 `assets/template.md` 生成 Markdown，并在本地可用时渲染 HTML。

  务必使用此技能当用户：
  - 明确请求行业报告、行业概览、sector overview、行业研究
  - 想看某个赛道/板块的整体财务、竞争格局和估值位置
  - 需要行业内公司对比、龙头排序、集中度分析
  - 需要从行业层面筛选潜在投资机会

  不适用场景：
  - 单一公司首次覆盖 -> initiating-coverage
  - 财报后的单公司点评 -> earnings-analysis
  - 只要一句话介绍行业
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by sector-overview/assets/template.md."
---

# RQ 股票研究 - 行业概览

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、占位符填充和结构校验
- skill 必须自包含，不能依赖仓库级 `utils`
- 生成器只消费显式的 `stock_pool.json`，不能在代码里硬写“白酒股池”“新能源股池”
- 缺失数据时必须明确写“无数据/未提供”，不能留空
- 若估值因子文件只有空字符串，必须回溯到最近非空因子日，不能接受“文件有记录但关键章节无数据”的报告
- 最终 Markdown 必须按客户阅读口径呈现，重点输出行业状态判断、龙头梯队、投资线索和跟踪指标，不能把生成过程写进正文
- 每个主要章节都必须保留 `数据来源：RQData，置信度5`

## 数据源分工

### `RQData CLI` 负责

- 行业股票池、财务、估值、价格与基准表现
- 行业内公司对比、集中度和横向排序

### `web_search` 负责

- 市场规模、行业趋势、监管政策
- 竞争格局、并购整合与主题链背景

### `web_search` 禁止替代的内容

- 财务、估值、行情和公告等结构化主数据
- 股票池定义和量化排序结果

## `web_search` 使用规则

详细字段与约束见 [references/web_search.md](references/web_search.md)。

- 所有网络搜索信息必须先写入 `web_search_findings.json`
- 若未提供该文件，报告仍可交付，但只能保持结构化行业概览边界
- 若提供了网络搜索信息，正文必须真正吸收，而不是只多一个 sidecar 文件

## 硬性规则

- `[MUST-1]` 先完整收集所有数据，再开始分析和撰写报告
- `[MUST-2]` 财务、估值和行情数据必须来自 `RQData CLI`
- `[MUST-3]` 市场规模、行业趋势和政策背景必须通过 `web_search` 实时获取，不能依赖训练记忆
- `[MUST-4]` 市值和金额类数据必须统一换算为“亿元”等客户可读口径
- `[MUST-5]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-6]` 客户稿不得暴露文件名、字段名或内部 workflow 术语
- `[MUST-7]` 低置信度网络搜索信息不能单独支撑行业结论或投资线索

## 目标产出

- 报告长度：10-15 页，正文至少达到约 3000 中文字符
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
sector-overview/
├── SKILL.md
├── scripts/
│   └── generate_report.py
├── assets/
│   └── template.md
└── references/
    └── data_contract.md
```

## 输入文件契约

原始数据目录由 `--data-dir` 指定，脚本会按下列文件名查找输入：

- `sector_definition.json`：可选
- `stock_pool.json`
- `instrument_meta.json`
- `industry_map.json`
- `historical_financials.json`
- `latest_financials.json`
- `roe.json`
- `market_cap.json`
- `pe_ratio.json`
- `pb_ratio.json`
- `dividend_yield.json`
- `price_period.json`
- `benchmark_price.json`

这些文件都允许以下结构：

- `{ "data": [...] }`
- `{ "data": { ... } }`
- `[...]`
- `{ ... }`

其中：

- `stock_pool.json` 定义行业股票池
- `sector_definition.json` 推荐用于记录行业名、分类级别、分类来源和基准指数
- `industry_map.json` 用于补充一级/二级/三级行业名
- `historical_financials.json` / `latest_financials.json` 用于行业财务结构与同比分析
- `roe.json`、`market_cap.json`、`pe_ratio.json`、`pb_ratio.json`、`dividend_yield.json` 用于竞争格局和估值分析
- `price_period.json` / `benchmark_price.json` 用于区间表现与相对收益

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
INDUSTRY_NAME="${INDUSTRY_NAME:-白酒}"
INDUSTRY_LEVEL="${INDUSTRY_LEVEL:-third}"
INDUSTRY_VALUE="${INDUSTRY_VALUE:-白酒}"
INDUSTRY_SOURCE="${INDUSTRY_SOURCE:-citics_2019}"
BENCHMARK_ORDER_BOOK_ID="${BENCHMARK_ORDER_BOOK_ID:-000300.XSHG}"
PRICE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=180)).isoformat())
PY
)"
FACTOR_DATE="$(python3 - <<PY
import json
import subprocess
from datetime import date, timedelta

def has_non_empty_factor(day: str) -> bool:
    payload = json.dumps({
        "order_book_ids": ["600519.XSHG", "000858.XSHE"],
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
for _ in range(10):
    if has_non_empty_factor(cursor.isoformat()):
        print(cursor.isoformat())
        break
    cursor -= timedelta(days=1)
else:
    raise SystemExit("最近 10 个自然日都未找到非空 market_cap 因子日期")
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

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/sector_overview}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/sector_overview_${INDUSTRY_NAME}_${REPORT_DATE}.md}"
```

### 步骤 2：生成行业股票池

推荐先产出行业定义文件：

```json
{
  "industry_name": "白酒",
  "industry_level": "third",
  "industry_value": "白酒",
  "industry_source": "citics_2019",
  "benchmark_order_book_id": "000300.XSHG"
}
```

保存为 `$DATA_DIR/sector_definition.json` 后执行：

```bash
mkdir -p "$DATA_DIR"

rqdata stock cn list --payload "{
  \"date\": \"$FACTOR_DATE\",
  \"type\": \"CS\"
}" --format json > "$DATA_DIR/stock_list.json"

python3 - "$DATA_DIR/stock_list.json" "$DATA_DIR/industry_map.json" "$DATA_DIR/stock_pool.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

stock_list_path = Path(sys.argv[1])
industry_map_path = Path(sys.argv[2])
stock_pool_path = Path(sys.argv[3])
definition_path = stock_list_path.parent / "sector_definition.json"
definition = json.loads(definition_path.read_text()) if definition_path.exists() else {}

industry_level = definition.get("industry_level", "third")
industry_value = definition.get("industry_value")
industry_source = definition.get("industry_source", "citics_2019")
if not industry_value:
    raise SystemExit("sector_definition.json 缺少 industry_value")

payload = json.loads(stock_list_path.read_text())
items = payload if isinstance(payload, list) else payload.get("data", [])
order_book_ids = [item["order_book_id"] for item in items if isinstance(item, dict) and item.get("order_book_id")]

industry_rows = []
for start in range(0, len(order_book_ids), 800):
    chunk = order_book_ids[start:start + 800]
    cmd = [
        "rqdata", "stock", "cn", "industry",
        "--payload", json.dumps({
            "order_book_ids": chunk,
            "date": definition.get("factor_date"),
            "level": 0,
            "source": industry_source,
        }, ensure_ascii=False),
        "--format", "json",
    ]
    industry_rows.extend(json.loads(subprocess.check_output(cmd, text=True)))

industry_map_path.write_text(json.dumps(industry_rows, ensure_ascii=False, indent=2), encoding="utf-8")

field_map = {
    "first": "first_industry_name",
    "second": "second_industry_name",
    "third": "third_industry_name",
}
field_name = field_map[industry_level]
selected = [row for row in industry_rows if isinstance(row, dict) and row.get(field_name) == industry_value]
stock_pool_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
PY
```

说明：

- 脚本会把全 A 股按批查询行业分类，再过滤出目标赛道
- `sector_definition.json` 建议额外写入 `factor_date`，与下游估值/价格查询保持一致
- `factor_date` 必须是最近非空因子日，不是“最近交易日”四个字就算完成
- 如果上游已经有明确成分股，也可以直接提供 `stock_pool.json`

### 步骤 3：采集行业财务、估值与价格数据

```bash
STOCK_POOL_JSON="$(python3 - "$DATA_DIR/stock_pool.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
items = payload if isinstance(payload, list) else payload.get("data", [])
order_book_ids = []
for item in items:
    if isinstance(item, dict) and item.get("order_book_id"):
        order_book_ids.append(item["order_book_id"])
print(json.dumps(order_book_ids, ensure_ascii=False))
PY
)"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON
}" --format json > "$DATA_DIR/instrument_meta.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"fields\": [\"revenue\", \"net_profit\", \"gross_profit\", \"total_assets\", \"total_liabilities\", \"cash_from_operating_activities\"],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/historical_financials.json"

cp "$DATA_DIR/historical_financials.json" "$DATA_DIR/latest_financials.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/roe.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"factor\": \"market_cap\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/market_cap.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"factor\": \"pe_ratio\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/pe_ratio.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"factor\": \"pb_ratio\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/pb_ratio.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"factor\": \"dividend_yield\",
  \"start_date\": \"$FACTOR_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/dividend_yield.json"

rqdata stock cn price --payload "{
  \"order_book_ids\": $STOCK_POOL_JSON,
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$FACTOR_DATE\",
  \"fields\": [\"close\"],
  \"adjust_type\": \"post\"
}" --format json > "$DATA_DIR/price_period.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"$BENCHMARK_ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$FACTOR_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/benchmark_price.json"
```

### 步骤 4：生成 Markdown 报告

```bash
python3 sector-overview/scripts/generate_report.py \
  --industry "$INDUSTRY_NAME" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

兼容旧入口：

```bash
python3 sector-overview/generate_report.py \
  --industry "$INDUSTRY_NAME" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

常用参数：

- `--industry`：行业名称，例如 `白酒`
- `--data-dir`：原始 JSON 数据目录
- `--report-date`：报告日期，默认当天
- `--output`：输出 Markdown 路径
- `--template`：自定义模板路径，默认 `sector-overview/assets/template.md`
- `--no-render`：不生成 HTML

### 步骤 5：渲染 HTML

脚本会优先尝试调用本地安装的 `rq-report-renderer`，若不存在则回退到仓库内的 `report-renderer/scripts/render_report.py`；两者都不可用时保留 Markdown 并打印警告。

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[INDUSTRY_NAME]]`
  - `[[EXEC_SUMMARY]]`
  - `[[SECTOR_SCOPE]]`
  - `[[FINANCIAL_STRUCTURE]]`
  - `[[COMPETITION]]`
  - `[[VALUATION]]`
  - `[[PERFORMANCE]]`
  - `[[OPPORTUNITIES]]`
  - `[[RISK_SECTION]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 完整包含模板中的主章节
- 行业结论必须基于显式股票池和真实财务/估值/价格数据
- 不得残留 `[XX]`、`[公司A]`、`[行业机会]` 这类占位文本
- 必须显式说明股票池覆盖数、主导财报季度和集中度
- 机会筛选不能只列公司名，必须说明估值/盈利/回报依据
- `dividend_yield` 原始值单位为 bps，报告中必须换算为百分比后再展示
- 若市值、ROE、PE/PB 因子整体为空，生成器应直接失败，而不是输出残缺章节

## 常见错误

- 在代码里硬写“白酒/新能源/银行”股票池
- 使用固定 `2024q3`、固定 `2024-12-31`
- 把“最近交易日”误当成“最近非空因子日”，导致估值文件虽有记录却全是空值
- 继续依赖旧版 `utils` 或公共 `sector_analysis` 模块
