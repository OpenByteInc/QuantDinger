---
name: rq-initiating-coverage
description: |
  创建模板驱动的首次覆盖研究报告，基于真实财务、股权结构、交易、分红、市场预期、卖方摘要与可比公司数据输出长篇结构化报告。
  `RQData CLI` 负责财务、交易、分红、预期、可比公司与公司基础资料主数据；若需要补充管理层履历、行业规模、竞争格局或政策背景，可额外使用 `web_search` 获取实时定性信息，并先落为结构化 JSON，再由当前报告脚本将其以客户可读方式纳入对应章节。

  务必使用此技能当用户：
  - 明确请求首次覆盖、initiating coverage、深度公司研究、完整公司分析框架
  - 需要为新纳入跟踪的公司建立完整的公司研究报告、对比框架和估值定位
  - 需要公司概况、财务轨迹、股权结构、卖方预期、可比估值等一揽子分析输入

  不适用场景：
  - 财报后单次复盘 -> earnings-analysis
  - 财报前瞻 -> earnings-preview
  - 行业整体研究 -> sector-overview
compatibility: "Requires rqdata CLI, Bash, python3. Report structure is managed by initiating-coverage/assets/template.md."
---

# RQ 股票研究 - 首次覆盖

## 核心原则

- 所有内容必须遵循三阶段流程：数据采集 -> 报告生成 -> HTML 渲染
- `assets/template.md` 是唯一报告模板来源；Python 只做数据归一化、指标计算、占位符填充和结构校验
- Python 可以基于真实数据生成客户可读摘要，但不能硬写脱离数据的主观结论、固定公司故事或静态投资判断
- 当前实现不是 `skills-reference` 中 5 任务 DOCX 工程的逐任务复刻，而是将其压缩为单次自动化长篇报告；但研究深度、来源规范、图表替代表达和证据覆盖不得明显降级
- 最新财报季度必须从真实 `historical_financials.json` 中按 `info_date <= report-date` 自动识别
- `financial-indicator` 必须使用 `factor + start_date/end_date`
- peers 选择必须显式落盘为 `peer_pool.json`，不能在脚本里偷藏固定可比公司列表
- `research_reports.json` 若提供 `data_source` 字段，应将 `0` 视为公司研报主样本；其他来源默认不进入最终正文
- `research_reports.json` 若要进入最终报告，必须先在同一文件内回写 `summaries.core_view` 等客户可读摘要；最终报告不直接截断原始 `summary`
- 缺失数据时必须明确写“无数据 / 未提供 / 未验证”，不能留空

## 数据源分工

### `RQData CLI` 负责

- 公司基础资料、行业口径、股本与十大股东
- 历史财务、ROE、估值、价格、换手率、分红
- 一致预期、目标价、卖方研报结构化字段
- 可比公司池、可比公司财务与估值

### `web_search` 负责

- 管理层公开履历补充
- 行业规模、竞争格局、政策环境与监管变化
- 公司重大新闻、产能 / 产品 / 组织调整等实时定性背景
- RQData 无法直接提供的竞争对手定性描述

### `web_search` 禁止替代的内容

- 财务数据、估值指标、价格、换手率、分红与一致预期
- 可比公司筛选、peer 排名和量化定位
- 任何本应由 RQData 提供的结构化主数据

## `web_search` 使用规则

详细字段、来源等级、落盘示例和 fallback 规则见 [references/web_search.md](references/web_search.md)。

允许补充的内容：

- 管理层履历、重要任职背景、治理事件
- 行业规模、竞争格局、政策动态和监管要求
- 公司近期产品、产能、组织或合作进展

落盘要求：

- 所有 `web_search` 结果必须先写入 `web_search_findings.json`
- 只写结构化记录，不把搜索草稿或碎片化笔记直接塞进报告
- 若未提供该文件，报告仍可交付，但相关定性背景必须保持“未验证”边界
- 即便提供了 `web_search_findings.json`，它也只能补充定性语境，不能改写量化结论

## 硬性规则

以下任一条违反，视为输出失败：

- `[MUST-1]` 财务数据、估值数据、价格数据、分红数据和一致预期必须来自 `RQData CLI`
- `[MUST-2]` `web_search` 只补充定性信息，不能替代结构化金融主数据
- `[MUST-3]` 金额类字段在客户稿中必须换算为“亿元”等可读口径
- `[MUST-4]` peers 必须来自显式落盘并可复核的可比公司池，不能在代码里写死
- `[MUST-5]` 若使用卖方研报，最终报告必须优先消费 `summaries.core_view` 等客户可读摘要，不直接截断原始 `summary`
- `[MUST-6]` 每个关键数据点或关键结论都要标 `数据来源：XXX，置信度X`
- `[MUST-7]` 客户稿不得暴露 `LLM`、`skill`、文件名、JSON 字段名、workflow 术语或内部状态
- `[MUST-8]` 低置信度网络搜索结果不得单独支撑核心投资结论
- `[MUST-9]` 图表若未生成，必须由等价表格、趋势表或对比表完成降级，不得让关键分析断层

## 确信度评级

- `5`：RQData CLI、交易所公告、上市公司官网、官方监管披露
- `4`：政府 / 监管 / 行业协会 / 官方机构、权威财经媒体
- `3`：一般媒体或二手整理，但来源清晰且与其他来源一致
- `2`：单一来源、细节不完整、时点未充分验证
- `1`：推断、估算窗口、未验证信息

使用规则：

- 混合结论的置信度取关键来源中的最低值
- 推断类文字不得标成高置信度
- 低置信度信息只能作为补充背景，不得单独推导出评级或估值判断

## 图表 / 图片需求

当前实现以表格和趋势事实完成最小可交付版本，但首次覆盖报告仍必须定义图表需求；若图表缺失，必须用等价结构化表达降级。

- 图表名称：收入与净利润五年轨迹
- 图表目的：展示 5 年历史财务变化和最新同口径趋势
- 使用的数据文件：`historical_financials.json`
- 关键字段：`quarter`、`revenue`、`net_profit`
- 建议图表类型：柱线组合图
- 回答问题：公司收入和利润的扩张节奏是否稳定
- 放置位置：`## 历史财务轨迹`
- 若图表缺失：保留同口径财务表和近 8 季趋势表

- 图表名称：盈利质量与现金流结构图
- 图表目的：展示毛利率、ROE、资产负债率和现金转化率变化
- 使用的数据文件：`historical_financials.json`、`roe_history.json`
- 关键字段：`gross_profit`、`revenue`、`return_on_equity_weighted_average`、`cash_from_operating_activities`、`total_assets`、`total_liabilities`
- 建议图表类型：折线图或分组柱图
- 回答问题：盈利质量是改善还是弱化，现金流是否跟得上利润
- 放置位置：`## 盈利质量与现金流`
- 若图表缺失：保留质量指标表和现金流对比表

- 图表名称：可比公司估值定位图
- 图表目的：比较目标公司与 peers 的市值、ROE、PE、PB、股息率定位
- 使用的数据文件：`peer_pool.json`、`peer_*`
- 关键字段：`market_cap`、`return_on_equity_weighted_average`、`pe_ratio`、`pb_ratio`、`dividend_yield`
- 建议图表类型：散点图、条形图或对比表
- 回答问题：公司当前估值在可比样本中偏高还是偏低
- 放置位置：`## 可比公司与估值定位`
- 若图表缺失：保留 peer 对比表与中位数偏离表

- 图表名称：股价与股东回报图
- 图表目的：展示目标公司相对基准的股价表现与历史分红
- 使用的数据文件：`price_history.json`、`benchmark_price.json`、`dividend_history.json`
- 关键字段：`close`、`dividend_cash_before_tax`、`declaration_announcement_date`
- 建议图表类型：收益曲线图 + 分红时间轴
- 回答问题：市场历史定价与股东回报特征如何
- 放置位置：`## 交易表现与股东回报`
- 若图表缺失：保留收益表、换手表和分红表

## 目标产出

- 报告长度：10-16 页
- 推荐中文字符数：3500-6000
- 输出文件：
  - Markdown 报告
  - HTML 报告（若本地已安装渲染器）
- 输出目录必须由 `--data-dir` / `--output` 指定，不能写死固定路径

## 目录结构

```text
initiating-coverage/
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

- `company_info.json`
- `industry.json`
- `shares.json`
- `shareholder_top10.json`
- `historical_financials.json`
- `roe_history.json`
- `market_cap.json`
- `pe_ratio.json`
- `pb_ratio.json`
- `dividend_yield.json`
- `price_history.json`
- `turnover_history.json`
- `benchmark_price.json`
- `dividend_history.json`
- `consensus.json`
- `research_reports.json`
- `peer_pool.json`
- `peer_company_info.json`
- `peer_industry.json`
- `peer_latest_financials.json`
- `peer_roe.json`
- `peer_market_cap.json`
- `peer_pe_ratio.json`
- `peer_pb_ratio.json`
- `peer_dividend_yield.json`
- `web_search_findings.json`（可选）

完整字段说明见 [references/data_contract.md](references/data_contract.md)。

## 工作流

### 步骤 1：准备参数

```bash
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
ORDER_BOOK_ID="${ORDER_BOOK_ID:-600519.XSHG}"
BENCHMARK_ORDER_BOOK_ID="${BENCHMARK_ORDER_BOOK_ID:-000300.XSHG}"
PRICE_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=365 * 3 + 30)).isoformat())
PY
)"
CONSENSUS_START_DATE="$(python3 - <<PY
from datetime import date, timedelta
report_date = date.fromisoformat("${REPORT_DATE}")
print((report_date - timedelta(days=210)).isoformat())
PY
)"
HISTORY_START_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year - 5}q1")
PY
)"
HISTORY_END_QUARTER="$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f"{report_date.year}q4")
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

DATA_DIR="${DATA_DIR:-$HOME/rq_equities_reports/initiating_coverage}"
OUTPUT_MD="${OUTPUT_MD:-$DATA_DIR/initiating_coverage_${ORDER_BOOK_ID}_${REPORT_DATE}.md}"
mkdir -p "$DATA_DIR"
```

### 步骤 2：采集目标公司基础数据

```bash
rqdata stock cn instruments --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"]
}" --format json > "$DATA_DIR/company_info.json"

rqdata stock cn industry --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"date\": \"$REPORT_DATE\",
  \"level\": 0,
  \"source\": \"citics_2019\"
}" --format json > "$DATA_DIR/industry.json"

rqdata stock cn shares --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$CONSENSUS_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/shares.json"

rqdata stock cn shareholder-top10 --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$CONSENSUS_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"start_rank\": 1,
  \"end_rank\": 10
}" --format json > "$DATA_DIR/shareholder_top10.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"fields\": [
    \"revenue\",
    \"net_profit\",
    \"gross_profit\",
    \"total_assets\",
    \"total_liabilities\",
    \"total_equity\",
    \"cash_from_operating_activities\",
    \"cash_flow_from_investing_activities\",
    \"cash_flow_from_financing_activities\"
  ],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/historical_financials.json"
```

### 步骤 3：采集目标公司估值、交易、分红与卖方数据

```bash
rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/roe_history.json"

for factor in market_cap pe_ratio pb_ratio dividend_yield; do
  rqdata stock cn financial-indicator --payload "{
    \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
    \"factor\": \"$factor\",
    \"start_date\": \"$FACTOR_DATE\",
    \"end_date\": \"$FACTOR_DATE\"
  }" --format json > "$DATA_DIR/${factor}.json"
done

rqdata stock cn price --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\", \"volume\", \"total_turnover\", \"high\", \"low\"],
  \"adjust_type\": \"post\"
}" --format json > "$DATA_DIR/price_history.json"

rqdata stock cn turnover-rate --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/turnover_history.json"

rqdata index price --payload "{
  \"order_book_ids\": [\"$BENCHMARK_ORDER_BOOK_ID\"],
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"fields\": [\"close\"]
}" --format json > "$DATA_DIR/benchmark_price.json"

rqdata stock cn dividend --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$(python3 - <<PY
from datetime import date
report_date = date.fromisoformat("${REPORT_DATE}")
print(f\"{report_date.year - 5}-01-01\")
PY
)\",
  \"end_date\": \"$REPORT_DATE\"
}" --format json > "$DATA_DIR/dividend_history.json"

rqdata stock cn consensus --payload "{
  \"order_book_ids\": [\"$ORDER_BOOK_ID\"],
  \"start_date\": \"$CONSENSUS_START_DATE\",
  \"end_date\": \"$REPORT_DATE\",
  \"report_range\": 3
}" --format json > "$DATA_DIR/consensus.json"
```

先根据财务数据识别最新季度与预测年份：

```bash
read LATEST_QUARTER LATEST_YEAR NEXT_YEAR <<EOF
$(python3 - "$DATA_DIR/historical_financials.json" "$REPORT_DATE" <<'PY'
import json
import sys
from datetime import date

payload = json.load(open(sys.argv[1], "r", encoding="utf-8"))
report_date = date.fromisoformat(sys.argv[2])

records = payload if isinstance(payload, list) else payload.get("data", [])
best = None
for item in records:
    if not isinstance(item, dict):
        continue
    quarter = str(item.get("quarter") or "").lower()
    info_date = str(item.get("info_date") or "")[:10]
    if not quarter or not info_date:
        continue
    if info_date > report_date.isoformat():
        continue
    if best is None or quarter > best:
        best = quarter

if not best:
    raise SystemExit("未识别到最新财报季度")
year = int(best[:4])
print(best, year, year + 1)
PY
)
EOF

python3 - "$DATA_DIR" "$ORDER_BOOK_ID" "$LATEST_YEAR" "$NEXT_YEAR" "$CONSENSUS_START_DATE" "$REPORT_DATE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
stock = sys.argv[2]
years = [sys.argv[3], sys.argv[4]]
start_date = sys.argv[5]
end_date = sys.argv[6]

rows = []
for year in years:
    payload = json.dumps({
        "order_book_ids": [stock],
        "fiscal_year": year,
        "start_date": start_date,
        "end_date": end_date,
        "date_rule": "create_tm",
    }, ensure_ascii=False)
    output = subprocess.check_output(
        ["rqdata", "stock", "cn", "research-reports", "--payload", payload, "--format", "json"],
        text=True,
    )
    data = json.loads(output)
    rows.extend(data if isinstance(data, list) else data.get("data", []))

data_dir.joinpath("research_reports.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY
```

若 `research_reports.json` 需要进入最终正文，应先在原记录内补齐客户可读摘要：

- 摘要输入位置：`research_reports.json -> records[].summary`
- 摘要回写位置：`research_reports.json -> records[].summaries.core_view`
- 仅 `data_source=0` 且公司直接相关的记录可以进入最终报告

### 步骤 3.5：可选的管理层 / 行业 / 竞争语境补充

当用户需要更完整的首次覆盖定性背景时，可执行该步骤。

- 使用 `web_search` 补充管理层、行业规模、政策环境、竞争格局或公司最新重大动态
- 结果必须写入 `web_search_findings.json`
- 这类结果只补充定性语境，不参与 Python 量化计算或 peer 排序

### 步骤 4：生成可比公司池并采集 peers 数据

先生成全市场股票列表和行业映射：

```bash
rqdata stock cn list --payload "{
  \"date\": \"$FACTOR_DATE\",
  \"type\": \"CS\"
}" --format json > "$DATA_DIR/stock_list.json"

python3 - "$DATA_DIR" "$ORDER_BOOK_ID" "$REPORT_DATE" "$FACTOR_DATE" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
target = sys.argv[2]
report_date = sys.argv[3]
factor_date = sys.argv[4]

target_industry = json.loads(data_dir.joinpath("industry.json").read_text(encoding="utf-8"))
target_rows = target_industry if isinstance(target_industry, list) else target_industry.get("data", [])
target_row = next(item for item in target_rows if isinstance(item, dict) and item.get("order_book_id") == target)
target_third = target_row.get("third_industry_name")
target_second = target_row.get("second_industry_name")

stock_list = json.loads(data_dir.joinpath("stock_list.json").read_text(encoding="utf-8"))
stock_rows = stock_list if isinstance(stock_list, list) else stock_list.get("data", [])
ids = [row["order_book_id"] for row in stock_rows if isinstance(row, dict) and row.get("order_book_id")]

industry_rows = []
for start in range(0, len(ids), 800):
    chunk = ids[start:start + 800]
    payload = json.dumps({
        "order_book_ids": chunk,
        "date": report_date,
        "level": 0,
        "source": "citics_2019",
    }, ensure_ascii=False)
    output = subprocess.check_output(
        ["rqdata", "stock", "cn", "industry", "--payload", payload, "--format", "json"],
        text=True,
    )
    batch = json.loads(output)
    industry_rows.extend(batch if isinstance(batch, list) else batch.get("data", []))

data_dir.joinpath("industry_universe.json").write_text(
    json.dumps(industry_rows, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

third_matches = [row for row in industry_rows if isinstance(row, dict) and row.get("third_industry_name") == target_third]
second_matches = [row for row in industry_rows if isinstance(row, dict) and row.get("second_industry_name") == target_second]
selected = third_matches if len(third_matches) >= 6 else second_matches

candidate_ids = []
seen = set()
for row in selected:
    stock = row.get("order_book_id")
    if not stock or stock in seen:
        continue
    seen.add(stock)
    candidate_ids.append(stock)

payload = json.dumps({
    "order_book_ids": candidate_ids,
    "factor": "market_cap",
    "start_date": factor_date,
    "end_date": factor_date,
}, ensure_ascii=False)
output = subprocess.check_output(
    ["rqdata", "stock", "cn", "financial-indicator", "--payload", payload, "--format", "json"],
    text=True,
)
factor_rows = json.loads(output)

market_cap = {}
for row in factor_rows:
    if isinstance(row, dict) and row.get("order_book_id") and row.get("market_cap") not in (None, "", "null"):
        market_cap[row["order_book_id"]] = float(row["market_cap"])

peer_rows = []
for stock in candidate_ids:
    if stock not in market_cap:
        continue
    peer_rows.append({
        "order_book_id": stock,
        "selection_level": "third" if stock in {item.get("order_book_id") for item in third_matches} else "second",
        "market_cap": market_cap[stock],
    })

peer_rows.sort(key=lambda item: item["market_cap"], reverse=True)
peer_rows = peer_rows[:6]

if target not in {item["order_book_id"] for item in peer_rows} and target in market_cap:
    peer_rows = [{"order_book_id": target, "selection_level": "target", "market_cap": market_cap[target]}] + peer_rows[:5]

data_dir.joinpath("peer_pool.json").write_text(
    json.dumps(peer_rows, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
PY
```

再采集 peers 元数据与最新快照：

```bash
PEER_IDS="$(python3 - "$DATA_DIR/peer_pool.json" <<'PY'
import json
import sys
rows = json.load(open(sys.argv[1], "r", encoding="utf-8"))
print(json.dumps([row["order_book_id"] for row in rows], ensure_ascii=False))
PY
)"

rqdata stock cn instruments --payload "{
  \"order_book_ids\": $PEER_IDS
}" --format json > "$DATA_DIR/peer_company_info.json"

rqdata stock cn industry --payload "{
  \"order_book_ids\": $PEER_IDS,
  \"date\": \"$REPORT_DATE\",
  \"level\": 0,
  \"source\": \"citics_2019\"
}" --format json > "$DATA_DIR/peer_industry.json"

rqdata stock cn financial --payload "{
  \"order_book_ids\": $PEER_IDS,
  \"fields\": [
    \"revenue\",
    \"net_profit\",
    \"gross_profit\",
    \"total_assets\",
    \"total_liabilities\",
    \"cash_from_operating_activities\"
  ],
  \"start_quarter\": \"$HISTORY_START_QUARTER\",
  \"end_quarter\": \"$HISTORY_END_QUARTER\",
  \"statements\": \"all\"
}" --format json > "$DATA_DIR/peer_latest_financials.json"

rqdata stock cn financial-indicator --payload "{
  \"order_book_ids\": $PEER_IDS,
  \"factor\": \"return_on_equity_weighted_average\",
  \"start_date\": \"$PRICE_START_DATE\",
  \"end_date\": \"$FACTOR_DATE\"
}" --format json > "$DATA_DIR/peer_roe.json"

for factor in market_cap pe_ratio pb_ratio dividend_yield; do
  rqdata stock cn financial-indicator --payload "{
    \"order_book_ids\": $PEER_IDS,
    \"factor\": \"$factor\",
    \"start_date\": \"$FACTOR_DATE\",
    \"end_date\": \"$FACTOR_DATE\"
  }" --format json > "$DATA_DIR/peer_${factor}.json"
done
```

### 步骤 5：生成 Markdown / HTML

```bash
python3 initiating-coverage/scripts/generate_report.py \
  --stock "$ORDER_BOOK_ID" \
  --data-dir "$DATA_DIR" \
  --report-date "$REPORT_DATE" \
  --output "$OUTPUT_MD"
```

## 阶段门控

### Gate 1：公司与财务主数据齐备

- 已识别最新财报季度
- 价格历史、一致预期、peer 主数据齐全
- 公司、股东、财务、估值、分红、交易文件存在

### Gate 2：卖方与可比样本整理完成

- `research_reports.json` 已抓取且公司直接相关样本可识别
- 若需要进入最终报告，`summaries.core_view` 已补齐
- `peer_pool.json` 已生成，且可比公司数量足以支撑对比

### Gate 3：可选网络搜索结果完成

- 若启用定性补充，`web_search_findings.json` 已落盘
- 字段完整、来源等级可解释
- 网络搜索结果仅用于管理层 / 行业 / 政策 / 竞争背景，不替代量化主数据

### Gate 4：成稿完成

- Markdown 已生成
- 若本地渲染器存在，HTML 已生成
- 客户稿不暴露内部术语
- 长度、章节、来源标注和结构化对比均达标

## 模板规则

- 报告必须严格基于 [template.md](assets/template.md) 生成
- 占位符采用 `[[TOKEN]]` 语法，不使用 Jinja
- 当前模板仅允许以下占位符：
  - `[[REPORT_DATE]]`
  - `[[COMPANY_NAME]]`
  - `[[STOCK_CODE]]`
  - `[[EXEC_SUMMARY]]`
  - `[[COMPANY_PROFILE]]`
  - `[[OWNERSHIP_SECTION]]`
  - `[[FINANCIAL_TRAJECTORY]]`
  - `[[QUALITY_AND_CASHFLOW]]`
  - `[[EXPECTATION_AND_SELLSIDE]]`
  - `[[PEER_AND_VALUATION]]`
  - `[[TRADING_AND_DIVIDEND]]`
  - `[[RISK_SECTION]]`
  - `[[APPENDIX]]`

## 报告质量要求

- 报告必须包含模板中的所有一级章节
- 必须同时覆盖：
  - 公司基本信息与股权结构
  - 历史财务轨迹
  - 现金流与资产负债表
  - 市场预期与卖方摘录
  - 可比公司与估值定位
  - 交易表现与股东回报
  - 风险提示与口径说明
- 报告必须达到首次覆盖的最低研究深度，不能退化成“财务 + peer 简表”
- 最低研究深度至少应回答 4 类问题：
  - 公司是什么、处于什么行业位置、覆盖边界在哪里
  - 近 5 年财务轨迹和最近单季度经营节奏如何变化
  - 当前市场预期、卖方口径与可比估值把公司放在什么位置
  - 后续持续跟踪时最需要盯住哪些风险与验证点
- 若启用了网络搜索结果补充，正文至少要出现“管理层 / 行业 / 政策 / 公司动态 / 竞争格局”中的一类实质信息，而不是只多一个 sidecar 文件
- 不允许残留 `[[PLACEHOLDER]]`
- 不允许在正文中暴露内部流程描述、文件名、字段名或脚本术语
- Markdown 输出必须与模板章节和数据契约保持一致

## 阶段验收清单

- [ ] Markdown 文件存在
- [ ] 若本地渲染器存在则 HTML 文件存在
- [ ] 模板占位符无残留
- [ ] 主章节完整
- [ ] 报告长度达到 10-16 页的最低可交付标准
- [ ] 关键数据点与关键结论带 `数据来源：XXX，置信度X`
- [ ] peers 来自显式生成且可复核的可比公司池
- [ ] 若使用卖方摘要，正文消费的是客户可读摘要层
- [ ] 若使用 `web_search`，其内容已真正进入最终报告且未越权替代 RQData 主数据

## 常见错误

- 把 `financial-indicator` 返回字段错误地当成统一 `value`
- 在脚本里硬写固定 peers 或固定公司结论
- 把原始卖方 `summary` 直接截断贴进客户稿
- 把 `web_search` 结果写成主结论，反而压过 RQData 主数据
- 在正文里出现文件名、JSON 字段名或内部执行语言
- 使用固定日期、固定季度、固定输出路径
