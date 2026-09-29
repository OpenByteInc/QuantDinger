#!/usr/bin/env python3
"""Template-driven thesis tracker report generator."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 投资论文跟踪报告",
    "## 执行摘要",
    "## 论文快照",
    "## 关键支柱验证",
    "## 股价与相对收益回顾",
    "## 资本回报与股东结构",
    "## 催化剂跟踪",
    "## 风险监控",
    "## 更新日志",
    "## 附录：口径说明",
]
WEB_SOURCE_CONFIDENCE_CEILING = {
    "official": 5,
    "government": 4,
    "association": 4,
    "authoritative_media": 4,
    "general_news": 3,
    "inference": 1,
}
WEB_ALLOWED_FINDING_TYPES = {
    "company_news",
    "management_change",
    "industry_trend",
    "competition_context",
    "analyst_view",
}

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")


@dataclass
class PillarResult:
    name: str
    metric: str
    actual_value: Optional[float]
    actual_text: str
    expectation_text: str
    falsifier_text: str
    passed: bool
    rationale: str


@dataclass
class CatalystItem:
    title: str
    event_date: date
    category: str
    priority: int
    announcement_link: Optional[str]
    media: str
    info_type: str


@dataclass
class ExternalFinding:
    source_name: str
    title: str
    published_at: date
    summary: str
    why_relevant: str
    confidence: int
    finding_type: str


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的投资论文跟踪报告")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", help="公司名称，可选")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", default=date.today().isoformat(), help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出 Markdown 文件路径")
    parser.add_argument("--template", default=str(skill_dir / "assets" / "template.md"), help="Markdown 模板路径")
    parser.add_argument("--no-render", action="store_true", help="不尝试渲染 HTML")
    return parser.parse_args()


def parse_iso_date(value: Any) -> Optional[date]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    candidates = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    text = text[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    text = str(value).strip()
    if not text:
        return None

    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def extract_records(payload: Any) -> List[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "data" in payload:
            data = payload["data"]
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
            return []
        return [payload]
    return []


def pick_first(record: Dict[str, Any], fields: Sequence[str]) -> Any:
    for field in fields:
        if field in record and record[field] not in (None, ""):
            return record[field]
    return None


def normalize_ticker(record: Dict[str, Any]) -> str:
    value = pick_first(record, TICKER_FIELDS)
    return str(value).strip() if value not in (None, "") else ""


def normalize_name(record: Dict[str, Any]) -> str:
    value = pick_first(record, NAME_FIELDS)
    return str(value).strip() if value not in (None, "") else ""


def float_or_none(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_link(value: Any) -> Optional[str]:
    if value in (None, "", "null"):
        return None
    text = str(value).strip()
    return text or None


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def validate_web_search_records(records: Sequence[Any]) -> None:
    if not records:
        return
    required_fields = {
        "query",
        "source_name",
        "source_type",
        "title",
        "url",
        "published_at",
        "retrieved_at",
        "summary",
        "why_relevant",
        "confidence",
        "finding_type",
    }
    issues: List[str] = []
    for idx, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            issues.append(f"第 {idx} 条网络搜索结果记录不是对象")
            continue
        missing = [field for field in required_fields if item.get(field) in (None, "", "null")]
        if missing:
            issues.append(f"第 {idx} 条网络搜索结果记录缺少字段：{', '.join(missing)}")
        source_type = str(item.get("source_type") or "").strip()
        if source_type not in WEB_SOURCE_CONFIDENCE_CEILING:
            issues.append(f"第 {idx} 条网络搜索结果记录来源类型非法：{source_type or '空'}")
        confidence = float_or_none(item.get("confidence"))
        ceiling = WEB_SOURCE_CONFIDENCE_CEILING.get(source_type)
        if confidence is None:
            issues.append(f"第 {idx} 条网络搜索结果记录缺少置信度")
        elif ceiling is not None and confidence > ceiling:
            issues.append(f"第 {idx} 条网络搜索结果记录置信度 {confidence:g} 超过来源上限 {ceiling}")
        finding_type = str(item.get("finding_type") or "").strip()
        if finding_type not in WEB_ALLOWED_FINDING_TYPES:
            issues.append(f"第 {idx} 条网络搜索结果记录 finding_type 非法：{finding_type or '空'}")
    if issues:
        raise ValueError("网络搜索结果校验失败：" + "；".join(issues))


def extract_external_findings(records: Sequence[Any]) -> List[ExternalFinding]:
    findings: List[ExternalFinding] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        published_at = parse_iso_date(item.get("published_at"))
        if published_at is None:
            continue
        findings.append(
            ExternalFinding(
                source_name=str(item.get("source_name") or "网络搜索来源").strip(),
                title=clean_text(item.get("title")),
                published_at=published_at,
                summary=clean_text(item.get("summary")).rstrip("。；;!！?？"),
                why_relevant=clean_text(item.get("why_relevant")).rstrip("。；;!！?？"),
                confidence=int(float_or_none(item.get("confidence")) or 0),
                finding_type=str(item.get("finding_type") or "").strip(),
            )
        )
    findings.sort(key=lambda item: (item.published_at, item.confidence), reverse=True)
    return findings


def percent_text(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:+.{digits}f}%"


def unsigned_percent_text(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}%"


def ratio_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}x"


def yuan_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def price_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value:.2f}元"


def format_link_markdown(url: Optional[str], label: str = "原文") -> str:
    if not url:
        return ""
    return f"[{label}]({url})"


def choose_latest_by_date(records: Iterable[Dict[str, Any]], date_fields: Sequence[str]) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[date, Dict[str, Any]]] = None
    for record in records:
        event_date = None
        for field in date_fields:
            event_date = parse_iso_date(record.get(field))
            if event_date:
                break
        if event_date is None:
            continue
        if best is None or event_date > best[0]:
            best = (event_date, record)
    return best[1] if best else None


def dedupe_financial_records(records: List[Any], stock: str, report_date: date) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        if normalize_ticker(item) != stock:
            continue
        item_date = parse_iso_date(item.get("info_date"))
        if item_date and item_date > report_date:
            continue
        quarter = str(item.get("quarter") or "")
        if not quarter:
            continue
        current = deduped.get(quarter)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        if current is None or (item_date and (current_date is None or item_date >= current_date)):
            deduped[quarter] = item
    return [deduped[key] for key in sorted(deduped.keys())]


def find_yoy_record(records: Sequence[Dict[str, Any]], latest_quarter: str) -> Optional[Dict[str, Any]]:
    match = re.match(r"^(\d{4})q([1-4])$", latest_quarter)
    if not match:
        return None
    yoy_quarter = f"{int(match.group(1)) - 1}q{match.group(2)}"
    for record in records:
        if record.get("quarter") == yoy_quarter:
            return record
    return None


def load_thesis_definition(path: Path) -> Optional[Dict[str, Any]]:
    payload = read_json_file(path)
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def latest_roe_value(records: List[Any], stock: str) -> Optional[float]:
    best_date: Optional[date] = None
    best_value: Optional[float] = None
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date"))
        value = float_or_none(item.get("return_on_equity_weighted_average"))
        if event_date is None or value is None:
            continue
        if best_date is None or event_date >= best_date:
            best_date = event_date
            best_value = value
    return best_value


def latest_factor_value(records: List[Any], stock: str, field: str, report_date: date) -> Optional[float]:
    best_date: Optional[date] = None
    best_value: Optional[float] = None
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("datetime"))
        value = float_or_none(item.get(field))
        if event_date is None or value is None or event_date > report_date:
            continue
        if best_date is None or event_date >= best_date:
            best_date = event_date
            best_value = value
    return best_value


def factor_range(records: List[Any], stock: str, field: str, report_date: date) -> Tuple[Optional[float], Optional[float]]:
    values: List[float] = []
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("datetime"))
        value = float_or_none(item.get(field))
        if event_date is None or value is None or event_date > report_date:
            continue
        values.append(value)
    if not values:
        return None, None
    return min(values), max(values)


def build_as_of_time(report_date: date, *collections: List[Any]) -> str:
    latest_dt: Optional[datetime] = None
    for collection in collections:
        for item in collection:
            if not isinstance(item, dict):
                continue
            for field in (
                "create_tm",
                "datetime",
                "info_date",
                "date",
                "report_date",
                "advance_date",
                "ex_dividend_date",
                "end_date",
                "published_at",
                "retrieved_at",
            ):
                current = parse_iso_datetime(item.get(field))
                if current and current.date() <= report_date and (latest_dt is None or current > latest_dt):
                    latest_dt = current
    if latest_dt:
        if latest_dt.time() == datetime.min.time():
            return latest_dt.strftime("%Y-%m-%d")
        return latest_dt.strftime("%Y-%m-%d %H:%M:%S")
    return report_date.isoformat()


def calculate_price_return(records: List[Any], stock: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    series: Dict[date, Tuple[float, Optional[float]]] = {}
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        if event_date is None or close is None:
            continue
        series[event_date] = (close, float_or_none(item.get("total_turnover")))
    ordered = sorted(series.items(), key=lambda pair: pair[0])
    if len(ordered) < 2:
        return None, None, None
    first_close = ordered[0][1][0]
    last_close, turnover = ordered[-1][1]
    if not first_close:
        return None, None, turnover
    return (last_close / first_close - 1.0) * 100.0, last_close, turnover


def calculate_index_return(records: List[Any]) -> Optional[float]:
    series: Dict[date, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        if event_date is None or close is None:
            continue
        series[event_date] = close
    ordered = sorted(series.items(), key=lambda pair: pair[0])
    if len(ordered) < 2:
        return None
    first_close = ordered[0][1]
    last_close = ordered[-1][1]
    if not first_close:
        return None
    return (last_close / first_close - 1.0) * 100.0


def latest_dividend(records: List[Any], stock: str) -> Optional[Dict[str, Any]]:
    matched = [item for item in records if isinstance(item, dict) and normalize_ticker(item) == stock]
    return choose_latest_by_date(matched, ("advance_date", "declaration_announcement_date", "ex_dividend_date", "payable_date"))


def shareholder_summary(records: List[Any], stock: str) -> Dict[str, Optional[float]]:
    periods: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        period = str(item.get("end_date") or "")
        if period:
            periods.setdefault(period, []).append(item)
    if not periods:
        return {
            "latest_period": None,
            "top1": None,
            "top10": None,
            "previous_period": None,
            "previous_top10": None,
        }
    ordered_periods = sorted(periods.keys())
    latest_period = ordered_periods[-1]
    previous_period = ordered_periods[-2] if len(ordered_periods) >= 2 else None

    def summarize(period: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
        if not period:
            return None, None
        items = periods[period]
        top1 = None
        total = 0.0
        count = 0
        for item in items:
            pct = float_or_none(item.get("hold_percent_total"))
            rank = str(item.get("rank") or "")
            if pct is None:
                continue
            count += 1
            total += pct
            if rank == "1":
                top1 = pct
        return top1, total if count else None

    top1, top10 = summarize(latest_period)
    _, previous_top10 = summarize(previous_period)
    return {
        "latest_period": latest_period,
        "top1": top1,
        "top10": top10,
        "previous_period": previous_period,
        "previous_top10": previous_top10,
    }


LOW_SIGNAL_KEYWORDS = (
    "独立董事述职报告",
    "内部控制审计报告",
    "内部控制评价报告",
    "可持续发展报告",
    "履职情况报告",
    "履职情况评估报告",
    "履行监督职责情况报告",
    "投资者保护工作报告",
    "已审财务报表",
    "专项报告",
    "H股公告",
    "审计报告",
    "独立性自查",
    "独立性专项意见",
    "管理办法",
)

CATEGORY_PRIORITY = {
    "财报披露": 0,
    "资本回报": 1,
    "经营催化": 2,
    "治理事项": 3,
}


def classify_announcement(title: str) -> Optional[str]:
    if any(keyword in title for keyword in LOW_SIGNAL_KEYWORDS):
        return None

    rules = [
        ("财报披露", ("年度报告", "年报", "半年报", "季报", "业绩报告", "业绩发布会")),
        ("资本回报", ("利润分配", "分红", "派息", "回购")),
        ("治理事项", ("股东大会", "续聘会计师事务所")),
        ("经营催化", ("提质增效", "核心人员持股计划", "长期服务计划", "实施方案", "投资者保护")),
    ]
    for category, keywords in rules:
        if any(keyword in title for keyword in keywords):
            return category
    return None


def catalyst_title_priority(title: str, category: str) -> int:
    high_signal_rules = [
        (0, ("年度报告", "年报")),
        (1, ("利润分配", "分红", "派息", "回购")),
        (2, ("业绩报告", "业绩快报", "业绩预告", "业绩发布会")),
        (3, ("半年报", "季报")),
        (4, ("核心人员持股计划", "长期服务计划")),
        (5, ("董事会", "监事会", "股东大会", "续聘会计师事务所")),
    ]
    for priority, keywords in high_signal_rules:
        if any(keyword in title for keyword in keywords):
            return priority
    return 10 + CATEGORY_PRIORITY.get(category, 9)


def extract_catalysts(records: List[Any], stock: str) -> List[CatalystItem]:
    items: List[CatalystItem] = []
    seen = set()
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        category = classify_announcement(title)
        if category is None:
            continue
        event_date = parse_iso_date(item.get("info_date"))
        if event_date is None:
            continue
        key = (title, event_date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            CatalystItem(
                title=title,
                event_date=event_date,
                category=category,
                priority=catalyst_title_priority(title, category),
                announcement_link=normalize_link(item.get("announcement_link")),
                media=str(item.get("media") or "未知来源"),
                info_type=str(item.get("info_type") or "未分类"),
            )
        )
    items.sort(key=lambda item: (-item.event_date.toordinal(), item.priority, item.title))
    return items


def format_target_price(target: Any) -> str:
    if isinstance(target, dict):
        value = target.get("value")
        currency = target.get("currency") or ""
        if value not in (None, ""):
            return f"{value} {currency}".strip()
    if target not in (None, ""):
        return str(target)
    return "未提供"


def numeric_target_price(target: Any) -> Optional[float]:
    if isinstance(target, dict):
        return float_or_none(target.get("value"))
    return float_or_none(target)


def catalyst_expected_impact(item: CatalystItem) -> str:
    if item.category == "财报披露":
        return "验证利润、现金流与分红是否支持 thesis 延续"
    if item.category == "资本回报":
        return "观察股东回报与资金配置是否继续改善"
    if item.category == "经营催化":
        return "观察经营执行与管理层激励是否强化兑现路径"
    if item.category == "治理事项":
        return "观察治理安排是否带来新的资本回报或经营催化"
    return "观察事件是否触发预期修正"


def metric_lookup(financial_records: Sequence[Dict[str, Any]], roe_value: Optional[float], price_return: Optional[float], excess_return: Optional[float], ownership: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    latest = financial_records[-1] if financial_records else None
    latest_quarter = str(latest.get("quarter")) if latest else ""
    yoy = find_yoy_record(financial_records, latest_quarter) if latest_quarter else None

    latest_revenue = float_or_none(latest.get("revenue")) if latest else None
    latest_profit = float_or_none(latest.get("net_profit")) if latest else None
    latest_cash = float_or_none(latest.get("cash_from_operating_activities")) if latest else None
    yoy_revenue = float_or_none(yoy.get("revenue")) if yoy else None
    yoy_profit = float_or_none(yoy.get("net_profit")) if yoy else None

    revenue_yoy = ((latest_revenue / yoy_revenue - 1.0) * 100.0) if latest_revenue is not None and yoy_revenue not in (None, 0) else None
    profit_yoy = ((latest_profit / yoy_profit - 1.0) * 100.0) if latest_profit is not None and yoy_profit not in (None, 0) else None
    cash_profit_ratio = (latest_cash / latest_profit) if latest_cash is not None and latest_profit not in (None, 0) else None

    return {
        "latest_revenue_yoy_pct": revenue_yoy,
        "latest_net_profit_yoy_pct": profit_yoy,
        "latest_cash_profit_ratio": cash_profit_ratio,
        "latest_roe": roe_value,
        "price_return_6m_pct": price_return,
        "excess_return_6m_pct": excess_return,
        "top1_holder_pct": ownership.get("top1"),
        "top10_holder_pct": ownership.get("top10"),
    }


def evaluate_rule(value: Optional[float], operator: str, threshold: float) -> bool:
    if value is None:
        return False
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    return False


def default_pillars(metrics: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    return [
        {"name": "收入规模继续扩张", "metric": "latest_revenue_yoy_pct", "operator": ">=", "threshold": 0.0, "rationale": "默认框架要求收入同比不转负。"},
        {"name": "归母净利润保持增长", "metric": "latest_net_profit_yoy_pct", "operator": ">=", "threshold": 0.0, "rationale": "默认框架要求利润同比延续正增长。"},
        {"name": "ROE维持双位数", "metric": "latest_roe", "operator": ">=", "threshold": 10.0, "rationale": "默认框架要求资本效率保持双位数。"},
        {"name": "现金创造不弱于利润", "metric": "latest_cash_profit_ratio", "operator": ">=", "threshold": 1.0, "rationale": "默认框架要求经营现金流覆盖利润。"},
    ]


def actual_text_for_metric(metric: str, value: Optional[float]) -> str:
    if metric.endswith("_pct") or metric == "latest_roe":
        return percent_text(value)
    if metric.endswith("_ratio"):
        return ratio_text(value)
    return percent_text(value)


def expectation_text(metric: str, operator: str, threshold: float) -> str:
    if metric.endswith("_pct") or metric == "latest_roe":
        return f"{operator} {threshold:.1f}%"
    if metric.endswith("_ratio"):
        return f"{operator} {threshold:.2f}x"
    return f"{operator} {threshold}"


def default_falsifier_text(metric: str, operator: str, threshold: float) -> str:
    threshold_text = expectation_text(metric, "=", threshold).replace("= ", "")
    if operator in {">", ">="}:
        return f"若该指标回落至 {threshold_text} 以下，则该支柱失效。"
    if operator in {"<", "<="}:
        return f"若该指标升至 {threshold_text} 以上，则该支柱失效。"
    return f"若该指标显著偏离 {threshold_text}，则该支柱需要重估。"


def build_pillar_results(thesis_definition: Optional[Dict[str, Any]], metrics: Dict[str, Optional[float]]) -> Tuple[str, List[PillarResult]]:
    source = "用户提供的投资逻辑定义" if thesis_definition else "默认财务质量框架"
    pillar_defs = thesis_definition.get("pillars") if thesis_definition else None
    if not isinstance(pillar_defs, list) or not pillar_defs:
        pillar_defs = default_pillars(metrics)

    results: List[PillarResult] = []
    for item in pillar_defs:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric") or "")
        operator = str(item.get("operator") or ">=")
        threshold = float_or_none(item.get("threshold"))
        name = str(item.get("name") or metric or "未命名支柱")
        value = metrics.get(metric)
        if threshold is None or metric not in metrics:
            result = PillarResult(
                name=name,
                metric=metric,
                actual_value=value,
                actual_text=actual_text_for_metric(metric, value),
                expectation_text="规则无效",
                falsifier_text=str(item.get("falsifier") or "支柱定义缺少有效的反证条件。"),
                passed=False,
                rationale=str(item.get("rationale") or "支柱定义缺少有效 metric/threshold。"),
            )
        else:
            result = PillarResult(
                name=name,
                metric=metric,
                actual_value=value,
                actual_text=actual_text_for_metric(metric, value),
                expectation_text=expectation_text(metric, operator, threshold),
                falsifier_text=str(item.get("falsifier") or default_falsifier_text(metric, operator, threshold)),
                passed=evaluate_rule(value, operator, threshold),
                rationale=str(item.get("rationale") or "无额外说明"),
            )
        results.append(result)
    return source, results


def conviction_label(passed_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "低"
    ratio = passed_count / total_count
    if ratio >= 0.75:
        return "高"
    if ratio >= 0.5:
        return "中"
    return "低"


def finding_type_label(finding_type: str) -> str:
    labels = {
        "company_news": "公司动态",
        "management_change": "管理层变化",
        "industry_trend": "行业趋势",
        "competition_context": "竞争格局",
        "analyst_view": "分析师观点",
    }
    return labels.get(finding_type, "网络搜索结果")


def build_external_context_lines(
    findings: Sequence[ExternalFinding],
    heading: str,
    limit: int = 4,
) -> List[str]:
    if not findings:
        return []
    lines = [heading]
    for item in findings[:limit]:
        lines.append(
            f"- **{item.published_at.isoformat()} {item.source_name} / {finding_type_label(item.finding_type)}**："
            f"{item.title}。{item.summary}。与投资逻辑的关系：{item.why_relevant}。"
            f" *数据来源：{item.source_name}，置信度{item.confidence}*"
        )
    return lines


def render_template(template_text: str, replacements: Dict[str, str]) -> str:
    report_text = template_text
    for token, value in replacements.items():
        report_text = report_text.replace(f"[[{token}]]", value)
    unresolved = sorted(set(TOKEN_RE.findall(report_text)))
    if unresolved:
        raise ValueError(f"模板占位符未完全替换：{', '.join(unresolved)}")
    for heading in REQUIRED_HEADINGS:
        if heading not in report_text:
            raise ValueError(f"模板缺少必需章节：{heading}")
    return report_text


def try_render_html(md_path: Path) -> Optional[Path]:
    renderer_binary = shutil.which("rq-report-renderer")
    html_path = md_path.with_suffix(".html")
    if renderer_binary:
        try:
            subprocess.run([renderer_binary, str(md_path), str(html_path)], check=True, capture_output=True, text=True)
            print(f"✅ HTML 报告已生成：{html_path}")
            return html_path
        except subprocess.CalledProcessError as exc:
            print(f"警告：rq-report-renderer 渲染失败：{exc}")

    repo_renderer = Path(__file__).resolve().parents[2] / "report-renderer" / "scripts" / "render_report.py"
    if repo_renderer.exists():
        try:
            subprocess.run(
                ["python3", str(repo_renderer), str(md_path), str(html_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"✅ HTML 报告已生成：{html_path}")
            return html_path
        except subprocess.CalledProcessError as exc:
            print(f"警告：仓库内 report-renderer 渲染失败：{exc}")

    print("警告：未找到可用的 HTML 渲染器，保留 Markdown 输出")
    return None


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()
    report_date = date.fromisoformat(args.report_date)

    thesis_definition = load_thesis_definition(data_dir / "thesis_definition.json")
    instrument_records = extract_records(read_json_file(data_dir / "instrument_meta.json"))
    historical_financials = extract_records(read_json_file(data_dir / "historical_financials.json"))
    latest_financials = extract_records(read_json_file(data_dir / "latest_financials.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe.json"))
    price_records = extract_records(read_json_file(data_dir / "price_6m.json"))
    hs300_records = extract_records(read_json_file(data_dir / "hs300_6m.json"))
    pe_records = extract_records(read_json_file(data_dir / "pe_ratio.json"))
    pb_records = extract_records(read_json_file(data_dir / "pb_ratio.json"))
    dividend_records = extract_records(read_json_file(data_dir / "dividend.json"))
    shareholder_records = extract_records(read_json_file(data_dir / "shareholder_top10.json"))
    announcement_records = extract_records(read_json_file(data_dir / "announcement_raw.json"))
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))
    validate_web_search_records(web_search_records)
    network_search_findings = extract_external_findings(web_search_records)

    company_name = args.company
    if not company_name:
        for record in instrument_records:
            if isinstance(record, dict) and normalize_ticker(record) == args.stock:
                company_name = normalize_name(record)
                break
    company_name = company_name or args.stock

    financial_history = dedupe_financial_records(historical_financials, args.stock, report_date)
    financial_latest = dedupe_financial_records(latest_financials, args.stock, report_date)
    base_financials = financial_latest or financial_history

    latest_record = base_financials[-1] if base_financials else None
    latest_quarter = str(latest_record.get("quarter")) if latest_record else "无数据"
    yoy_record = find_yoy_record(base_financials, latest_quarter) if latest_record else None

    price_return, latest_price, latest_turnover = calculate_price_return(price_records, args.stock)
    benchmark_return = calculate_index_return(hs300_records)
    excess_return = (price_return - benchmark_return) if price_return is not None and benchmark_return is not None else None
    roe_value = latest_roe_value(roe_records, args.stock)
    latest_pe = latest_factor_value(pe_records, args.stock, "pe_ratio", report_date)
    latest_pb = latest_factor_value(pb_records, args.stock, "pb_ratio", report_date)
    pe_min, pe_max = factor_range(pe_records, args.stock, "pe_ratio", report_date)
    pb_min, pb_max = factor_range(pb_records, args.stock, "pb_ratio", report_date)
    ownership = shareholder_summary(shareholder_records, args.stock)
    latest_dividend_record = latest_dividend(dividend_records, args.stock)
    catalysts = extract_catalysts(announcement_records, args.stock)
    as_of_time = build_as_of_time(
        report_date,
        latest_financials,
        historical_financials,
        roe_records,
        pe_records,
        pb_records,
        price_records,
        hs300_records,
        dividend_records,
        shareholder_records,
        announcement_records,
        web_search_records,
    )

    metrics = metric_lookup(base_financials, roe_value, price_return, excess_return, ownership)
    thesis_source, pillars = build_pillar_results(thesis_definition, metrics)
    high_conf_network_findings = [item for item in network_search_findings if item.confidence >= 4]
    low_conf_network_findings = [item for item in network_search_findings if item.confidence <= 3]

    passed_count = sum(1 for item in pillars if item.passed)
    conviction = conviction_label(passed_count, len(pillars))
    target_price_value = numeric_target_price((thesis_definition or {}).get("target_price"))
    target_price_text = format_target_price((thesis_definition or {}).get("target_price"))
    target_upside = ((target_price_value / latest_price - 1.0) * 100.0) if target_price_value not in (None, 0) and latest_price not in (None, 0) else None
    initial_confidence = str((thesis_definition or {}).get("confidence_label") or "未提供")
    core_view = str((thesis_definition or {}).get("core_view") or "未提供用户自定义投资逻辑，以下按默认财务质量框架跟踪。")
    holding_period = str((thesis_definition or {}).get("holding_period") or "未提供")
    position_date = str((thesis_definition or {}).get("position_date") or "未提供")
    current_position = str((thesis_definition or {}).get("current_position") or (thesis_definition or {}).get("position") or "未提供")
    thesis_name = str((thesis_definition or {}).get("thesis_name") or f"{company_name} 默认跟踪框架")

    latest_revenue = float_or_none(latest_record.get("revenue")) if latest_record else None
    latest_profit = float_or_none(latest_record.get("net_profit")) if latest_record else None
    latest_cash = float_or_none(latest_record.get("cash_from_operating_activities")) if latest_record else None

    exec_summary_lines = [
        f"当前跟踪对象为 {company_name}（{args.stock}），本次沿用 thesis `{thesis_name}`。最新识别报告期为 {latest_quarter}，"
        f"共验证 {len(pillars)} 项关键支柱，其中通过 {passed_count} 项，当前信念度评估为 `{conviction}`。",
        f"当前价为 {price_text(latest_price)}，目标价为 {target_price_text}，静态空间约 {percent_text(target_upside)}；"
        f"最近 6 个月股价表现为 {percent_text(price_return)}，相对沪深300 的超额收益为 {percent_text(excess_return)}。",
        f"最近 ROE 为 {unsigned_percent_text(roe_value)}，经营现金流/净利润为 {ratio_text(metrics['latest_cash_profit_ratio'])}；"
        f"当前 PE / PB 约为 {ratio_text(latest_pe, 1)} / {ratio_text(latest_pb)}。",
        (
            f"最近 30 天筛选出 {len(catalysts)} 条高相关度公告催化，资本回报方面最新分红记录为 `{latest_dividend_record.get('quarter')}`，"
            f"前十大股东合计持股比例约 {unsigned_percent_text(ownership['top10'], 2)}。"
            if latest_dividend_record
            else f"最近 30 天筛选出 {len(catalysts)} 条高相关度公告催化，当前股东结构期末为 `{ownership['latest_period'] or '无数据'}`。"
        ),
    ]
    if network_search_findings:
        exec_summary_lines.append(
            f"本次同时纳入 {len(network_search_findings)} 条网络搜索结果，主要用于补充公司动态、管理层变化或行业趋势验证；"
            "这些结果只作为补充证据，不直接替代 RQData 主数据。"
        )
    exec_summary_lines.extend(
        [
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )
    if network_search_findings:
        exec_summary_lines.append(
            f"*补充网络搜索结果：{network_search_findings[0].source_name}"
            f" 等 {len(network_search_findings)} 个来源，置信度"
            f"{min(item.confidence for item in network_search_findings)}-{max(item.confidence for item in network_search_findings)}*"
        )
    exec_summary = "\n".join(exec_summary_lines)

    snapshot_lines = [
        f"- **论文名称**：{thesis_name}",
        f"- **核心观点**：{core_view}",
        f"- **最新报告期**：{latest_quarter}",
        f"- **建仓/起始日期**：{position_date}",
        f"- **当前持仓/关注仓位**：{current_position}",
        f"- **持有周期**：{holding_period}",
        f"- **当前价 / 目标价 / 空间**：{price_text(latest_price)} / {target_price_text} / {percent_text(target_upside)}",
        f"- **目标价**：{target_price_text}",
        f"- **初始信念度**：{initial_confidence}",
        f"- **当前信念度**：{conviction}",
        f"- **当前估值（PE / PB）**：{ratio_text(latest_pe, 1)} / {ratio_text(latest_pb)}",
        f"- **逻辑定义来源**：{thesis_source}",
        "",
        "*数据来源：RQData，置信度5*",
    ]
    thesis_snapshot = "\n".join(snapshot_lines)

    pillar_lines = [
        "| 支柱 | 实际值 | 验证规则 | 反证条件 | 状态 | 说明 |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in pillars:
        status = "通过" if item.passed else "未通过"
        pillar_lines.append(
            f"| {item.name} | {item.actual_text} | {item.expectation_text} | {item.falsifier_text} | {status} | {item.rationale} |"
        )
    if latest_record and yoy_record:
        pillar_lines.append("")
        pillar_lines.append(
            f"最新报告期 {latest_quarter} 对应营收 {yuan_text(latest_revenue)}、净利润 {yuan_text(latest_profit)}、经营现金流 {yuan_text(latest_cash)}；"
            f"对比去年同期后，营收同比 {percent_text(metrics['latest_revenue_yoy_pct'])}，净利润同比 {percent_text(metrics['latest_net_profit_yoy_pct'])}。"
        )
    pillar_lines.append("")
    pillar_lines.append("### 支柱详解")
    for idx, item in enumerate(pillars, start=1):
        pillar_lines.extend(
            [
                f"- **支柱{idx}：{item.name}**",
                f"  原始假设/验证逻辑：{item.rationale}",
                f"  验证规则：{item.expectation_text}",
                f"  反证条件：{item.falsifier_text}",
                f"  当前读数：{item.actual_text}",
                f"  结论：{'当前继续成立' if item.passed else '当前未完全成立，需要继续跟踪'}",
            ]
        )
    pillar_lines.append("")
    pillar_lines.append("*数据来源：RQData，置信度5*")
    pillar_verification = "\n".join(pillar_lines)

    market_lines = [
        f"- **最新股价**：{price_text(latest_price)}",
        f"- **目标价 / 静态空间**：{target_price_text} / {percent_text(target_upside)}",
        f"- **6个月股价回报**：{percent_text(price_return)}",
        f"- **沪深300回报**：{percent_text(benchmark_return)}",
        f"- **超额收益**：{percent_text(excess_return)}",
        f"- **最近成交额**：{yuan_text(latest_turnover)}" if latest_turnover is not None else "- **最近成交额**：无数据",
        f"- **当前 PE / 历史区间**：{ratio_text(latest_pe, 1)} / {ratio_text(pe_min, 1)} - {ratio_text(pe_max, 1)}",
        f"- **当前 PB / 历史区间**：{ratio_text(latest_pb)} / {ratio_text(pb_min)} - {ratio_text(pb_max)}",
        "",
        "| 指标 | 当前值 | 说明 |",
        "| --- | ---: | --- |",
        f"| 当前价 | {price_text(latest_price)} | 市场最新交易价格 |",
        f"| 目标价 | {target_price_text} | thesis 预设目标价 |",
        f"| 目标空间 | {percent_text(target_upside)} | 目标价相对当前价的静态空间 |",
        f"| PE | {ratio_text(latest_pe, 1)} | 当前盈利估值水平 |",
        f"| PB | {ratio_text(latest_pb)} | 当前资产/资本回报估值水平 |",
        "",
    ]
    if excess_return is not None:
        if excess_return >= 0:
            market_lines.append("股价相对基准保持超额收益，说明 thesis 至少没有被市场完全否定，当前更需要验证估值修复是否仍有基本面支撑。")
        else:
            market_lines.append("股价相对基准为负超额收益，说明 thesis 需要更严格地接受数据、催化剂和估值三方面的再验证。")
    market_lines.append("")
    market_lines.append("*数据来源：RQData，置信度5*")
    market_performance = "\n".join(market_lines)

    capital_lines = []
    if latest_dividend_record:
        round_lot = latest_dividend_record.get("round_lot") or "10"
        capital_lines.extend(
            [
                f"- **最新分红记录**：{latest_dividend_record.get('quarter')}，每 {round_lot} 股税前现金分红 {latest_dividend_record.get('dividend_cash_before_tax')}。",
                f"- **预案公告日**：{latest_dividend_record.get('advance_date') or '无数据'}",
                f"- **除权除息日**：{latest_dividend_record.get('ex_dividend_date') or '无数据'}",
            ]
        )
    else:
        capital_lines.append("- **最新分红记录**：无数据")

    capital_lines.extend(
        [
            f"- **最新股东结构期末**：{ownership['latest_period'] or '无数据'}",
            f"- **第一大股东持股比例**：{unsigned_percent_text(ownership['top1'], digits=2)}",
            f"- **前十大股东合计持股比例**：{unsigned_percent_text(ownership['top10'], digits=2)}",
        ]
    )
    if ownership["top10"] is not None and ownership["previous_top10"] is not None:
        change = ownership["top10"] - ownership["previous_top10"]
        capital_lines.append(
            f"- **与上一期前十大股东集中度变化**：{percent_text(change, digits=2)}（上一期为 {ownership['previous_period']}）。"
        )
    capital_lines.extend(
        [
            "",
            "| 观察项 | 当前值 | 说明 |",
            "| --- | ---: | --- |",
            f"| 第一大股东持股比例 | {unsigned_percent_text(ownership['top1'], 2)} | 观察控制权稳定性 |",
            f"| 前十大股东合计持股 | {unsigned_percent_text(ownership['top10'], 2)} | 观察筹码集中度 |",
            f"| 最新分红季度 | {latest_dividend_record.get('quarter') if latest_dividend_record else '无数据'} | 观察资本回报节奏 |",
        ]
    )
    capital_lines.append("")
    capital_lines.append("*数据来源：RQData，置信度5*")
    capital_return = "\n".join(capital_lines)

    catalyst_lines = ["### 催化剂日历"]
    if catalysts:
        catalyst_lines.extend(
            [
                "| 日期 | 催化剂 | 预期影响 | 实际结果 | 状态 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in catalysts[:8]:
            catalyst_lines.append(
                f"| {item.event_date.isoformat()} | `{item.title}` | {catalyst_expected_impact(item)} | {item.category} / {item.info_type} | 已披露 |"
            )
    else:
        catalyst_lines.append("- 最近 30 天未识别到高相关度公告催化。")

    catalyst_lines.append("")
    catalyst_lines.append("### 已实现/已披露催化剂详解")
    if catalysts:
        for item in catalysts[:6]:
            link_text = format_link_markdown(item.announcement_link)
            catalyst_lines.append(
                f"- **{item.event_date.isoformat()} `{item.title}`**：归类为{item.category}，来源 {item.media}；"
                f"{catalyst_expected_impact(item)}" + (f" {link_text}" if link_text else "")
            )
    else:
        catalyst_lines.append("- 最近 30 天未识别到可跟踪的高优先级公告催化。")

    catalyst_lines.append("")
    if high_conf_network_findings:
        catalyst_lines.extend(build_external_context_lines(high_conf_network_findings, "### 网络搜索结果"))
        catalyst_lines.append("")
    elif network_search_findings:
        catalyst_lines.extend(build_external_context_lines(network_search_findings, "### 网络搜索结果"))
        catalyst_lines.append("")
    if low_conf_network_findings:
        catalyst_lines.extend(build_external_context_lines(low_conf_network_findings, "### 需二次验证的网络搜索结果", limit=3))
        catalyst_lines.append("")

    catalyst_lines.append("### 待验证/待兑现催化剂")
    planned = (thesis_definition or {}).get("planned_catalysts")
    if isinstance(planned, list) and planned:
        catalyst_lines.extend(
            [
                "| 预计窗口 | 催化剂 | 预期影响 | 状态 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in planned:
            if not isinstance(item, dict):
                continue
            catalyst_lines.append(
                f"| {item.get('expected_window', '未提供')} | {item.get('title', '未命名催化剂')} | {item.get('expected_impact', '等待后续公告/财报验证')} | 待兑现 |"
            )
    elif latest_dividend_record:
        catalyst_lines.append("- **资本回报兑现**：跟踪利润分配方案后续执行节奏以及除权除息安排。")
        catalyst_lines.append("- **下一次定期披露**：等待下一季报/中报验证利润与现金流延续性。")
    else:
        catalyst_lines.append("- **下一次定期披露**：等待下一次财报窗口验证关键支柱是否延续。")
    catalyst_lines.append("")
    catalyst_lines.append("*数据来源：RQData，置信度5*")
    catalyst_tracking = "\n".join(catalyst_lines)

    risk_rows: List[Tuple[str, str, str, str, str]] = []
    risk_items = (thesis_definition or {}).get("risk_items")
    if isinstance(risk_items, list) and risk_items:
        for item in risk_items:
            if not isinstance(item, dict):
                continue
            risk_rows.append(
                (
                    str(item.get("title") or "未命名风险"),
                    str(item.get("initial_assessment") or "中"),
                    str(item.get("monitor") or "未提供监控要点"),
                    "↑" if excess_return is not None and excess_return < 0 else "→",
                    str(item.get("response") or "结合下一次财报、公告与价格表现继续验证"),
                )
            )
    else:
        if excess_return is not None and excess_return < 0:
            risk_rows.append(("市场验证风险", "中", "股价最近 6 个月跑输沪深300，需要更高频验证 thesis。", "↑", "缩短验证节奏，优先跟踪价格与催化剂背离。"))
        failed_pillars = [item for item in pillars if not item.passed]
        if failed_pillars:
            names = "、".join(item.name for item in failed_pillars)
            risk_rows.append(("支柱失效风险", "中高", f"以下支柱尚未通过验证：{names}。", "↑", "等待下一次财报与公告继续验证。"))
        if low_conf_network_findings:
            risk_rows.append(("网络搜索结果确认风险", "中", "部分网络搜索结果来自低置信度来源，只能作为跟踪线索。", "→", "优先等待公告、财报或更高等级来源交叉验证。"))
        if not risk_rows:
            risk_rows.append(("暂无突出新增风险", "低", "当前未识别到新的显著风险信号。", "→", "继续跟踪下一次定期披露和资本回报执行。"))

    risk_lines = [
        "### 风险矩阵",
        "| 风险 | 原始评估 | 当前状态 | 趋势 | 应对措施 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for title, initial_state, current_state, trend, response in risk_rows:
        risk_lines.append(f"| {title} | {initial_state} | {current_state} | {trend} | {response} |")
    risk_lines.append("")
    risk_lines.append("### 风险详解")
    for title, initial_state, current_state, trend, response in risk_rows:
        risk_lines.append(f"- **{title}**：原始评估 `{initial_state}`，当前状态为 {current_state}；趋势 `{trend}`，当前应对为 {response}。")
    risk_lines.append("")
    risk_lines.append("*数据来源：RQData，置信度5*")
    risk_monitoring = "\n".join(risk_lines)

    update_lines = [
        f"- **{report_date.isoformat()} 更新**：最新跟踪报告期为 `{latest_quarter}`，共验证 {len(pillars)} 项支柱，通过 {passed_count} 项，当前信念度更新为 `{conviction}`。",
        f"- **价格与估值**：当前价 {price_text(latest_price)}，目标价 {target_price_text}，静态空间 {percent_text(target_upside)}；当前 PE / PB 为 {ratio_text(latest_pe, 1)} / {ratio_text(latest_pb)}。",
        f"- **催化剂增量**：最近 30 天识别出 {len(catalysts)} 条高相关度公告催化，最高优先级事项为 `{catalysts[0].title}`。" if catalysts else "- **催化剂增量**：最近 30 天未识别到新增高优先级催化。",
        (
            f"- **资本回报与股东结构**：最新分红记录为 `{latest_dividend_record.get('quarter')}`，前十大股东合计持股比例约 {unsigned_percent_text(ownership['top10'], 2)}。"
            if latest_dividend_record
            else f"- **股东结构**：最新股东结构期末为 `{ownership['latest_period'] or '无数据'}`，前十大股东合计持股比例约 {unsigned_percent_text(ownership['top10'], 2)}。"
        ),
    ]
    if network_search_findings:
        update_lines.append(
            f"- **网络搜索结果增量**：本次补充 {len(network_search_findings)} 条网络搜索结果，最新一条为 `{network_search_findings[0].title}`。"
        )
    update_lines.extend(
        [
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )
    update_log = "\n".join(update_lines)

    appendix_lines = [
        f"- 报告日期为 {report_date.isoformat()}，信息截面为 {as_of_time}。",
        f"- 投资逻辑定义来源：{thesis_source}。",
        "- 财务数据按季度去重，保留同一季度最新披露版本。",
        "- 公告部分会过滤低信号治理/例行披露，优先保留财报、资本回报、经营催化与治理关键节点，并保留高优先级事项的原文链接。",
        (
            f"- 本次共纳入 {len(network_search_findings)} 条网络搜索结果，用于补充公司动态、管理层变化和行业趋势验证。"
            if network_search_findings
            else "- 本次未纳入网络搜索结果，报告保持 RQData 驱动的基础 thesis 跟踪口径。"
        ),
        "",
        "### 数据覆盖情况",
        f"- 逻辑定义：{'已提供用户自定义逻辑' if thesis_definition else '未提供用户自定义逻辑'}",
        f"- 公司基础信息：{len(instrument_records)} 条记录",
        f"- 最新财务样本：{len(latest_financials)} 条记录",
        f"- 历史财务样本：{len(historical_financials)} 条记录",
        f"- ROE 样本：{len(roe_records)} 条记录",
        f"- 股价样本：{len(price_records)} 条记录",
        f"- 基准指数样本：{len(hs300_records)} 条记录",
        f"- PE 样本：{len(pe_records)} 条记录",
        f"- PB 样本：{len(pb_records)} 条记录",
        f"- 分红样本：{len(dividend_records)} 条记录",
        f"- 股东结构样本：{len(shareholder_records)} 条记录",
        f"- 公告样本：{len(announcement_records)} 条记录",
        f"- 网络搜索结果样本：{len(web_search_records)} 条记录",
    ]
    appendix = "\n".join(appendix_lines)

    template_path = Path(args.template).expanduser()
    report_text = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "REPORT_DATE": report_date.isoformat(),
            "AS_OF_TIME": as_of_time,
            "COMPANY_NAME": company_name,
            "STOCK_CODE": args.stock,
            "THESIS_SOURCE": thesis_source,
            "EXEC_SUMMARY": exec_summary,
            "THESIS_SNAPSHOT": thesis_snapshot,
            "PILLAR_VERIFICATION": pillar_verification,
            "MARKET_PERFORMANCE": market_performance,
            "CAPITAL_RETURN": capital_return,
            "CATALYST_TRACKING": catalyst_tracking,
            "RISK_MONITORING": risk_monitoring,
            "UPDATE_LOG": update_log,
            "APPENDIX": appendix,
        },
    )

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = data_dir / f"thesis_tracker_{args.stock}_{report_date.isoformat()}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
