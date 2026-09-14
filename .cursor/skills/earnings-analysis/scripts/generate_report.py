#!/usr/bin/env python3
"""Template-driven earnings analysis report generator."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 财报分析报告",
    "## 执行摘要",
    "## 信息截面",
    "## 财报概览",
    "## 市场预期、卖方反馈与价格反应",
    "## 公告原文与管理层表述",
    "## 财务质量与资产负债表",
    "## 投资逻辑更新",
    "## 估值与定位",
    "## 风险提示",
    "## 附录：生成说明",
]

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")
WEB_SOURCE_TYPE_LABELS = {
    "official": "上市公司公告 / 官方网站 / 交易所披露",
    "government": "政府 / 监管 / 行业协会 / 官方机构",
    "association": "政府 / 监管 / 行业协会 / 官方机构",
    "authoritative_media": "权威财经媒体",
    "general_news": "一般新闻源",
    "inference": "分析推断 / 估计窗口 / 未验证信息",
}
WEB_SOURCE_TYPE_MAX_CONFIDENCE = {
    "official": 5,
    "government": 4,
    "association": 4,
    "authoritative_media": 4,
    "general_news": 3,
    "inference": 1,
}
WEB_FINDING_TYPES = {
    "company_news",
    "management_update",
    "earnings_call",
    "industry_context",
    "policy_context",
}
FORECAST_SUFFIXES = ("t", "t1", "t2", "t3")
WEB_FINDING_TYPE_LABELS = {
    "company_news": "公司新闻",
    "management_update": "管理层动态",
    "earnings_call": "业绩交流会",
    "industry_context": "行业语境",
    "policy_context": "政策语境",
}


@dataclass
class QuarterSnapshot:
    quarter: str
    info_date: date
    revenue: Optional[float]
    net_profit: Optional[float]
    gross_profit: Optional[float]
    operating_cash: Optional[float]
    total_assets: Optional[float]
    total_liabilities: Optional[float]


@dataclass
class QuarterDelta:
    revenue: Optional[float]
    net_profit: Optional[float]
    gross_profit: Optional[float]
    operating_cash: Optional[float]


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的财报分析报告")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", help="公司名称，可选")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", required=True, help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出 Markdown 文件路径")
    parser.add_argument("--template", default=str(skill_dir / "assets" / "template.md"), help="Markdown 模板路径")
    parser.add_argument("--no-render", action="store_true", help="不尝试渲染 HTML")
    return parser.parse_args()


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


def extract_named_records(payload: Any, key: str) -> List[Any]:
    if payload is None:
        return []
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return list(payload[key])
    return extract_records(payload)


def parse_iso_date(value: Any) -> Optional[date]:
    if value in (None, "", "null", "0000-00-00"):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    candidates = (
        (text, "%Y-%m-%d"),
        (text, "%Y/%m/%d"),
        (text, "%Y-%m-%d %H:%M:%S"),
        (text, "%Y/%m/%d %H:%M:%S"),
        (text, "%Y-%m-%dT%H:%M:%S"),
        (text, "%Y-%m-%dT%H:%M:%S.%f"),
        (text[:19], "%Y-%m-%d %H:%M:%S"),
        (text[:19], "%Y/%m/%d %H:%M:%S"),
        (text[:19], "%Y-%m-%dT%H:%M:%S"),
        (text[:10], "%Y-%m-%d"),
        (text[:10], "%Y/%m/%d"),
    )
    for raw, fmt in candidates:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value in (None, "", "null"):
        return None
    text = str(value).strip()
    candidates = (
        (text, "%Y-%m-%d %H:%M:%S"),
        (text, "%Y/%m/%d %H:%M:%S"),
        (text, "%Y-%m-%dT%H:%M:%S"),
        (text, "%Y-%m-%dT%H:%M:%S.%f"),
        (text, "%Y-%m-%d"),
        (text[:19], "%Y-%m-%d %H:%M:%S"),
        (text[:19], "%Y/%m/%d %H:%M:%S"),
        (text[:19], "%Y-%m-%dT%H:%M:%S"),
        (text[:10], "%Y-%m-%d"),
    )
    for raw, fmt in candidates:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_quarter_key(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"^(\d{4})q([1-4])$", str(value).strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def previous_quarter(quarter: str) -> Optional[str]:
    key = parse_quarter_key(quarter)
    if not key:
        return None
    year, q = key
    if q == 1:
        return f"{year - 1}q4"
    return f"{year}q{q - 1}"


def yoy_quarter(quarter: str) -> Optional[str]:
    key = parse_quarter_key(quarter)
    if not key:
        return None
    return f"{key[0] - 1}q{key[1]}"


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


def percent_text(value: Optional[float], digits: int = 1, signed: bool = True) -> str:
    if value is None:
        return "无数据"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}%"


def x_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}x"


def billion_yuan_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def yuan_price_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}元"


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def safe_growth(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return (current / base - 1.0) * 100.0


def safe_ratio(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return current / base


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\r", "\n").replace("\u0000", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_text(value: Any, limit: int = 160) -> str:
    text = normalize_text(value).replace("\n", " ")
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def trim_sentence_end(text: str) -> str:
    return text.rstrip("。；;!！?？ ")


def normalize_web_source_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "official_site": "official",
        "official_website": "official",
        "gov": "government",
        "media": "general_news",
        "news": "general_news",
    }
    normalized = aliases.get(text, text)
    if normalized not in WEB_SOURCE_TYPE_LABELS:
        raise ValueError(f"web_search source_type 不受支持: {value}")
    return normalized


def parse_confidence(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"置信度必须是 1-5 的整数: {value}") from exc
    if not 1 <= score <= 5:
        raise ValueError(f"置信度必须在 1-5 之间: {value}")
    return score


def source_confidence_label(source: str, confidence: int) -> str:
    return f"{source}，置信度{confidence}"


def format_data_source_line(source_pairs: Sequence[Tuple[str, int]], *, limit: int = 6) -> str:
    labels: List[str] = []
    for source, confidence in source_pairs:
        label = source_confidence_label(source, confidence)
        if label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    if not labels:
        return "*数据来源：暂无可展示来源*"
    return f"*数据来源：{'；'.join(labels)}*"


def announcement_status_label(extract_row: Optional[Dict[str, Any]]) -> str:
    if not extract_row:
        return "保留原文链接，未抽取正文"

    extract_status = str(extract_row.get("extract_status") or "").strip()
    fetch_status = str(extract_row.get("fetch_status") or "").strip()

    if extract_status == "ok":
        return "已提炼关键片段"
    if extract_status == "partial":
        return "提炼到部分片段"
    if extract_status == "no_sections":
        return "未形成可直接引用片段"
    if extract_status in {"skipped_non_annual_interim", "not_started", "未提取"}:
        return "保留原文链接，未抽取正文"
    if extract_status == "unsupported" or fetch_status == "unsupported_file_type":
        return "文件格式暂不支持提炼"
    if extract_status == "fetch_failed":
        return "原文暂不可读，保留链接"
    if extract_status.startswith("pdf_parse_failed"):
        return "原文解析失败，保留链接"
    if fetch_status and fetch_status not in {"ok", "skipped"}:
        return "原文获取受限，保留链接"
    return "保留原文链接供查阅"


def normalize_web_search_findings(records: List[Any], report_date: date) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        required_fields = [
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
        ]
        missing = [field for field in required_fields if item.get(field) in (None, "")]
        if missing:
            raise ValueError(f"web_search_findings.json 第 {index + 1} 条缺少字段: {', '.join(missing)}")

        source_type = normalize_web_source_type(item.get("source_type"))
        confidence = parse_confidence(item.get("confidence"))
        max_confidence = WEB_SOURCE_TYPE_MAX_CONFIDENCE[source_type]
        finding_type = str(item.get("finding_type") or "").strip()
        if finding_type not in WEB_FINDING_TYPES:
            raise ValueError(f"web_search finding_type 不受支持: {finding_type or '空'}")

        published_at = parse_iso_date(item.get("published_at"))
        retrieved_at = parse_iso_date(item.get("retrieved_at"))
        if not published_at or not retrieved_at:
            raise ValueError(f"web_search_findings.json 第 {index + 1} 条的 published_at / retrieved_at 非法")
        if published_at > retrieved_at:
            raise ValueError(f"web_search_findings.json 第 {index + 1} 条的 published_at 晚于 retrieved_at")
        if published_at > report_date:
            continue

        stance = str(item.get("stance") or "neutral").strip().lower()
        if stance not in {"positive", "neutral", "negative"}:
            raise ValueError(f"web_search stance 仅支持 positive / neutral / negative: {stance}")

        findings.append(
            {
                "query": str(item.get("query") or "").strip(),
                "source_name": str(item.get("source_name") or "").strip(),
                "source_type": source_type,
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "published_at": published_at,
                "retrieved_at": retrieved_at,
                "summary": compact_text(item.get("summary") or "", 220),
                "why_relevant": compact_text(item.get("why_relevant") or "", 200),
                "confidence": min(confidence, max_confidence),
                "finding_type": finding_type,
                "subject": str(item.get("subject") or item.get("scope_name") or "外部补充").strip(),
                "stance": stance,
            }
        )
    findings.sort(key=lambda row: (row["published_at"], row["confidence"]), reverse=True)
    return findings


def extract_section_display_text(extract_row: Dict[str, Any], key: str, limit: int = 220) -> str:
    summaries = extract_row.get("summaries") if isinstance(extract_row.get("summaries"), dict) else {}
    legacy_sections = extract_row.get("sections") if isinstance(extract_row.get("sections"), dict) else {}
    raw_sections = extract_row.get("raw_sections") if isinstance(extract_row.get("raw_sections"), dict) else {}

    if summaries:
        value = summaries.get(key)
        if value not in (None, ""):
            return compact_text(value, limit)
        return ""

    # Legacy fallback only applies to pre-upgrade files that do not contain raw_sections/summaries.
    if not raw_sections and legacy_sections:
        value = legacy_sections.get(key)
        if value not in (None, ""):
            return compact_text(value, limit)
    return ""


def markdown_link(label: str, url: Optional[str]) -> str:
    if not url:
        return label
    return f"[{label}]({url})"


def to_single_quarter(snapshot: QuarterSnapshot, prev_snapshot: Optional[QuarterSnapshot]) -> QuarterDelta:
    quarter = parse_quarter_key(snapshot.quarter)
    if quarter is None:
        return QuarterDelta(snapshot.revenue, snapshot.net_profit, snapshot.gross_profit, snapshot.operating_cash)
    if quarter[1] == 1 or prev_snapshot is None:
        return QuarterDelta(snapshot.revenue, snapshot.net_profit, snapshot.gross_profit, snapshot.operating_cash)
    return QuarterDelta(
        revenue=(snapshot.revenue - prev_snapshot.revenue) if snapshot.revenue is not None and prev_snapshot.revenue is not None else None,
        net_profit=(snapshot.net_profit - prev_snapshot.net_profit) if snapshot.net_profit is not None and prev_snapshot.net_profit is not None else None,
        gross_profit=(snapshot.gross_profit - prev_snapshot.gross_profit) if snapshot.gross_profit is not None and prev_snapshot.gross_profit is not None else None,
        operating_cash=(snapshot.operating_cash - prev_snapshot.operating_cash) if snapshot.operating_cash is not None and prev_snapshot.operating_cash is not None else None,
    )


def dedupe_financial_records(records: List[Any], stock: str, report_date: date) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        if normalize_ticker(item) != stock:
            continue
        quarter = str(item.get("quarter") or "").lower()
        info_date = parse_iso_date(item.get("info_date"))
        if not parse_quarter_key(quarter) or info_date is None or info_date > report_date:
            continue
        current = deduped.get(quarter)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        if current is None or current_date is None or info_date >= current_date:
            deduped[quarter] = item
    return [deduped[key] for key in sorted(deduped.keys(), key=lambda item: parse_quarter_key(item) or (0, 0))]


def build_snapshot(record: Dict[str, Any]) -> QuarterSnapshot:
    return QuarterSnapshot(
        quarter=str(record.get("quarter") or "").lower(),
        info_date=parse_iso_date(record.get("info_date")) or date.min,
        revenue=float_or_none(record.get("revenue")),
        net_profit=float_or_none(record.get("net_profit")),
        gross_profit=float_or_none(record.get("gross_profit")),
        operating_cash=float_or_none(record.get("cash_from_operating_activities")),
        total_assets=float_or_none(record.get("total_assets")),
        total_liabilities=float_or_none(record.get("total_liabilities")),
    )


def latest_factor_value(records: List[Any], stock: str, field_name: str, report_date: date) -> Optional[float]:
    best: Optional[Tuple[date, float]] = None
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("datetime"))
        value = float_or_none(item.get(field_name))
        if field_name == "dividend_yield" and value is not None:
            value = value / 100.0
        if event_date is None or event_date > report_date or value is None:
            continue
        if best is None or event_date >= best[0]:
            best = (event_date, value)
    return best[1] if best else None


def build_price_points(records: List[Any], stock: str) -> List[Tuple[date, float, Optional[float], Optional[float]]]:
    points = []
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        volume = float_or_none(item.get("volume"))
        turnover = float_or_none(item.get("total_turnover"))
        if event_date is None or close is None:
            continue
        points.append((event_date, close, volume, turnover))
    points.sort(key=lambda pair: pair[0])
    return points


def build_benchmark_points(records: List[Any]) -> List[Tuple[date, float]]:
    points = []
    for item in records:
        if not isinstance(item, dict):
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        if event_date is None or close is None:
            continue
        points.append((event_date, close))
    points.sort(key=lambda pair: pair[0])
    return points


def reaction_return(points: Sequence[Tuple[date, float, Optional[float], Optional[float]]], event_date: date, trading_days_after: int) -> Optional[float]:
    before = None
    after_index = None
    for idx, point in enumerate(points):
        if point[0] < event_date:
            before = point
        elif point[0] >= event_date and after_index is None:
            after_index = idx
    if before is None or after_index is None:
        return None
    target_index = after_index + trading_days_after - 1
    if target_index >= len(points) or before[1] == 0:
        return None
    return (points[target_index][1] / before[1] - 1.0) * 100.0


def benchmark_return(points: Sequence[Tuple[date, float]], event_date: date, trading_days_after: int) -> Optional[float]:
    before = None
    after_index = None
    for idx, point in enumerate(points):
        if point[0] < event_date:
            before = point
        elif point[0] >= event_date and after_index is None:
            after_index = idx
    if before is None or after_index is None:
        return None
    target_index = after_index + trading_days_after - 1
    if target_index >= len(points) or before[1] == 0:
        return None
    return (points[target_index][1] / before[1] - 1.0) * 100.0


def median_turnover(points: Sequence[Tuple[date, float, Optional[float], Optional[float]]], before_event: bool, event_date: date) -> Optional[float]:
    values = []
    for point_date, _, _, turnover in points:
        if turnover is None:
            continue
        if before_event and point_date < event_date:
            values.append(turnover)
        if not before_event and point_date >= event_date:
            values.append(turnover)
    return float(median(values)) if values else None


def choose_latest_by_create_time(records: List[Any], predicate) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[datetime, Dict[str, Any]]] = None
    for item in records:
        if not isinstance(item, dict) or not predicate(item):
            continue
        event_dt = parse_iso_datetime(item.get("create_tm") or item.get("create_time") or item.get("date"))
        if event_dt is None:
            continue
        if best is None or event_dt >= best[0]:
            best = (event_dt, item)
    return best[1] if best else None


def consensus_year_map(record: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Optional[float]]]:
    if not record:
        return {}
    report_year_raw = record.get("report_year_t")
    try:
        base_year = int(str(report_year_raw or date.today().year))
    except ValueError:
        base_year = date.today().year
    year_map: Dict[int, Dict[str, Optional[float]]] = {}
    for field_prefix, key in (
        ("comp_con_operating_revenue", "revenue"),
        ("comp_con_net_profit", "profit"),
        ("comp_con_eps", "eps"),
    ):
        for offset, suffix in enumerate(FORECAST_SUFFIXES):
            field_name = f"{field_prefix}_{suffix}"
            if field_name not in record:
                continue
            year_map.setdefault(base_year + offset, {})[key] = float_or_none(record.get(field_name))
    if "con_targ_price" in record:
        year_map.setdefault(base_year, {})["target_price"] = float_or_none(record.get("con_targ_price"))
    return year_map


def first_available_consensus_year(record: Optional[Dict[str, Any]]) -> Optional[int]:
    year_map = consensus_year_map(record)
    available_years = [
        year
        for year, metrics in sorted(year_map.items())
        if any(metrics.get(key) is not None for key in ("revenue", "profit", "eps"))
    ]
    return available_years[0] if available_years else None


def consensus_field_value(record: Optional[Dict[str, Any]], prefix: str, target_year: int) -> Optional[float]:
    field_key_map = {
        "comp_con_operating_revenue": "revenue",
        "comp_con_net_profit": "profit",
        "comp_con_eps": "eps",
    }
    metric_key = field_key_map[prefix]
    year_map = consensus_year_map(record)
    metrics = year_map.get(target_year)
    if not metrics:
        return None
    return metrics.get(metric_key)


def company_aliases(info_record: Optional[Dict[str, Any]], stock: str) -> List[str]:
    aliases = [stock, stock.split(".")[0]]
    if info_record:
        symbol = str(info_record.get("symbol") or "").strip()
        abbrev = str(info_record.get("abbrev_symbol") or "").strip()
        if symbol:
            aliases.extend([symbol, symbol.replace("股份", "")])
        if abbrev:
            aliases.extend([abbrev, abbrev.replace(" ", "")])
        office = str(info_record.get("office_address") or "").strip()
        province = str(info_record.get("province") or "").strip()
        if office:
            aliases.append(office)
        if province:
            aliases.append(province)
    seen = set()
    result = []
    for alias in aliases:
        alias = alias.strip()
        if alias and alias not in seen:
            seen.add(alias)
            result.append(alias)
    return result


def filter_related_reports(records: List[Any], aliases: Sequence[str], report_date: date, event_date: date) -> List[Dict[str, Any]]:
    related = []
    lower_aliases = [alias.lower() for alias in aliases]
    strong_aliases = [alias for alias in lower_aliases if len(alias) >= 4 or re.search(r"\d", alias)]
    stock_code = aliases[1].lower() if len(aliases) > 1 else ""
    excluded_title_tokens = ("行业周报", "周报", "月报", "专题", "策略", "宏观", "晨报", "行业点评合集", "持仓分析", "行业配置", "板块")
    for item in records:
        if not isinstance(item, dict):
            continue
        create_date = parse_iso_date(item.get("create_tm") or item.get("create_time") or item.get("date"))
        if create_date is None or create_date < event_date or create_date > report_date:
            continue
        title = str(item.get("report_title") or "")
        summary = str(item.get("summary") or "")
        title_lower = title.lower()
        summary_lower = summary.lower()
        report_main_id = str(item.get("report_main_id") or "").lower()
        title_hit = any(alias in title_lower for alias in strong_aliases)
        summary_hit = any(alias in summary_lower for alias in strong_aliases)
        report_main_id_hit = bool(stock_code) and stock_code in report_main_id
        company_style_title = any(token in title_lower for token in ("点评", "results", "earnings", "季报", "年报", "三季报", "中报", "q1", "q2", "q3", "q4"))
        generic_title = any(token.lower() in title_lower for token in excluded_title_tokens)
        if generic_title and not title_hit:
            continue
        if title_hit or report_main_id_hit or (summary_hit and company_style_title):
            related.append(item)
    related.sort(key=lambda item: str(item.get("create_tm") or item.get("create_time") or item.get("date") or ""), reverse=True)
    return related


def announcement_score(title: str, info_type: str, event_date: date, report_date: date, info_date: Optional[date]) -> int:
    score = 0
    if "定期报告" in info_type:
        score += 100
    if re.search(r"(年报|年度报告|半年报|半年度报告|一季报|第一季度报告|三季报|第三季度报告)", title):
        score += 80
    if re.search(r"(主要经营数据|业绩说明会|业绩发布会)", title):
        score += 50
    if re.search(r"(董事会|监事会|法律意见书|独立财务顾问)", title):
        score -= 20
    if re.search(r"(激励计划|股票期权|限制性股票|行权|归属|注销|作废)", title):
        score -= 40
    if info_date:
        distance = abs((info_date - event_date).days)
        score += max(0, 20 - distance)
        if info_date > report_date:
            score -= 40
    return score


def select_relevant_announcements(records: List[Any], stock: str, event_date: date, report_date: date) -> List[Dict[str, Any]]:
    candidates = []
    lower_bound = event_date - timedelta(days=10)
    upper_bound = report_date + timedelta(days=5)
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        info_date = parse_iso_date(item.get("info_date") or item.get("date") or item.get("create_tm"))
        if info_date is None or info_date < lower_bound or info_date > upper_bound:
            continue
        title = str(item.get("title") or "")
        info_type = str(item.get("info_type") or "")
        score = announcement_score(title, info_type, event_date, report_date, info_date)
        if score <= 0:
            continue
        row = dict(item)
        row["_score"] = score
        candidates.append(row)
    candidates.sort(key=lambda item: (item["_score"], str(item.get("info_date") or "")), reverse=True)
    deduped = []
    seen = set()
    for item in candidates:
        key = (str(item.get("info_date") or ""), str(item.get("title") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= 5:
            break
    return deduped


def validate_dataset(latest_snapshot: Optional[QuarterSnapshot], price_points: Sequence[Tuple[date, float, Optional[float], Optional[float]]], consensus_records: List[Any]) -> None:
    issues = []
    if latest_snapshot is None:
        issues.append("未识别到 report-date 之前的最新财报季度")
    if len(price_points) < 5:
        issues.append("股价窗口覆盖不足")
    if not consensus_records:
        issues.append("一致预期数据为空")
    if issues:
        raise ValueError("数据质量校验失败：" + "；".join(issues))


def try_render_html(md_path: Path) -> Optional[Path]:
    html_path = md_path.with_suffix(".html")
    renderer_binary = shutil.which("rq-report-renderer")
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
            subprocess.run(["python3", str(repo_renderer), str(md_path), str(html_path)], check=True, capture_output=True, text=True)
            print(f"✅ HTML 报告已生成：{html_path}")
            return html_path
        except subprocess.CalledProcessError as exc:
            print(f"警告：仓库内渲染器执行失败：{exc}")

    print("警告：未找到可用的 HTML 渲染器，保留 Markdown 输出")
    return None


def render_template(template_text: str, replacements: Dict[str, str]) -> str:
    report_text = template_text
    for token, value in replacements.items():
        report_text = report_text.replace(f"[[{token}]]", value)
    leftovers = sorted(set(TOKEN_RE.findall(report_text)))
    if leftovers:
        raise ValueError(f"模板占位符未完全替换：{', '.join(leftovers)}")
    for heading in REQUIRED_HEADINGS:
        if heading not in report_text:
            raise ValueError(f"模板缺少必需章节：{heading}")
    return report_text


def main() -> None:
    args = parse_args()
    report_date = date.fromisoformat(args.report_date)
    data_dir = Path(args.data_dir).expanduser()

    company_records = extract_records(read_json_file(data_dir / "company_info.json"))
    industry_records = extract_records(read_json_file(data_dir / "industry.json"))
    financial_records = extract_records(read_json_file(data_dir / "historical_financials.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe_history.json"))
    market_cap_records = extract_records(read_json_file(data_dir / "market_cap.json"))
    pe_records = extract_records(read_json_file(data_dir / "pe_ratio.json"))
    pb_records = extract_records(read_json_file(data_dir / "pb_ratio.json"))
    dividend_records = extract_records(read_json_file(data_dir / "dividend_yield.json"))
    price_records = extract_records(read_json_file(data_dir / "price_window.json"))
    benchmark_records = extract_records(read_json_file(data_dir / "benchmark_window.json"))
    consensus_records = extract_records(read_json_file(data_dir / "consensus.json"))
    research_records = extract_records(read_json_file(data_dir / "research_reports.json"))
    announcement_records = extract_records(read_json_file(data_dir / "announcement_raw.json"))
    announcement_extract_records = extract_named_records(read_json_file(data_dir / "announcement_extracts.json"), "records")
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))

    company_info = next((item for item in company_records if isinstance(item, dict) and normalize_ticker(item) == args.stock), None)
    company_name = args.company or normalize_name(company_info or {}) or args.stock
    aliases = company_aliases(company_info, args.stock)

    deduped = dedupe_financial_records(financial_records, args.stock, report_date)
    latest_snapshot = build_snapshot(deduped[-1]) if deduped else None
    price_points = build_price_points(price_records, args.stock)
    validate_dataset(latest_snapshot, price_points, consensus_records)
    assert latest_snapshot is not None
    web_findings = normalize_web_search_findings(web_search_records, report_date)

    snapshot_map = {item["quarter"]: build_snapshot(item) for item in deduped}
    prev_snapshot = snapshot_map.get(previous_quarter(latest_snapshot.quarter) or "")
    yoy_snapshot = snapshot_map.get(yoy_quarter(latest_snapshot.quarter) or "")
    yoy_prev_snapshot = snapshot_map.get(previous_quarter(yoy_snapshot.quarter) or "") if yoy_snapshot else None
    target_year = int(latest_snapshot.quarter[:4])
    event_date = latest_snapshot.info_date

    benchmark_points = build_benchmark_points(benchmark_records)

    consensus_pre = choose_latest_by_create_time(consensus_records, lambda item: parse_iso_date(item.get("date")) and parse_iso_date(item.get("date")) < event_date)
    consensus_post = choose_latest_by_create_time(consensus_records, lambda item: parse_iso_date(item.get("date")) and parse_iso_date(item.get("date")) <= report_date)
    pre_consensus_year = first_available_consensus_year(consensus_pre) or target_year
    post_consensus_year = first_available_consensus_year(consensus_post) or target_year
    related_reports = filter_related_reports(research_records, aliases, report_date, event_date)
    selected_announcements = select_relevant_announcements(announcement_records, args.stock, event_date, report_date)
    announcement_extract_index = {}
    for row in announcement_extract_records:
        if isinstance(row, dict):
            link_key = str(row.get("announcement_link") or "")
            title_key = str(row.get("title") or "")
            if link_key:
                announcement_extract_index[link_key] = row
            if title_key:
                announcement_extract_index[title_key] = row

    revenue_cons_pre = consensus_field_value(consensus_pre, "comp_con_operating_revenue", pre_consensus_year)
    profit_cons_pre = consensus_field_value(consensus_pre, "comp_con_net_profit", pre_consensus_year)
    eps_cons_pre = consensus_field_value(consensus_pre, "comp_con_eps", pre_consensus_year)
    revenue_cons_post = consensus_field_value(consensus_post, "comp_con_operating_revenue", post_consensus_year)
    profit_cons_post = consensus_field_value(consensus_post, "comp_con_net_profit", post_consensus_year)
    eps_cons_post = consensus_field_value(consensus_post, "comp_con_eps", post_consensus_year)
    target_price_pre = float_or_none((consensus_pre or {}).get("con_targ_price"))
    target_price_post = float_or_none((consensus_post or {}).get("con_targ_price"))

    market_cap = latest_factor_value(market_cap_records, args.stock, "market_cap", report_date)
    pe_ratio = latest_factor_value(pe_records, args.stock, "pe_ratio", report_date)
    pb_ratio = latest_factor_value(pb_records, args.stock, "pb_ratio", report_date)
    dividend_yield = latest_factor_value(dividend_records, args.stock, "dividend_yield", report_date)
    latest_roe = latest_factor_value(roe_records, args.stock, "return_on_equity_weighted_average", report_date)

    revenue_yoy = safe_growth(latest_snapshot.revenue, yoy_snapshot.revenue if yoy_snapshot else None)
    profit_yoy = safe_growth(latest_snapshot.net_profit, yoy_snapshot.net_profit if yoy_snapshot else None)
    revenue_qoq = safe_growth(latest_snapshot.revenue, prev_snapshot.revenue if prev_snapshot else None)
    profit_qoq = safe_growth(latest_snapshot.net_profit, prev_snapshot.net_profit if prev_snapshot else None)
    gross_margin = safe_ratio(latest_snapshot.gross_profit, latest_snapshot.revenue)
    gross_margin = gross_margin * 100.0 if gross_margin is not None else None
    yoy_gross_margin = safe_ratio(yoy_snapshot.gross_profit if yoy_snapshot else None, yoy_snapshot.revenue if yoy_snapshot else None)
    yoy_gross_margin = yoy_gross_margin * 100.0 if yoy_gross_margin is not None else None
    gross_margin_change = (gross_margin - yoy_gross_margin) if gross_margin is not None and yoy_gross_margin is not None else None
    net_margin = safe_ratio(latest_snapshot.net_profit, latest_snapshot.revenue)
    net_margin = net_margin * 100.0 if net_margin is not None else None
    debt_ratio = safe_ratio(latest_snapshot.total_liabilities, latest_snapshot.total_assets)
    debt_ratio = debt_ratio * 100.0 if debt_ratio is not None else None
    cash_conversion = safe_ratio(latest_snapshot.operating_cash, latest_snapshot.net_profit)
    revenue_progress = safe_ratio(latest_snapshot.revenue, revenue_cons_pre)
    revenue_progress = revenue_progress * 100.0 if revenue_progress is not None else None
    profit_progress = safe_ratio(latest_snapshot.net_profit, profit_cons_pre)
    profit_progress = profit_progress * 100.0 if profit_progress is not None else None
    same_consensus_year = pre_consensus_year == post_consensus_year
    revenue_cons_delta = safe_growth(revenue_cons_post, revenue_cons_pre) if same_consensus_year else None
    profit_cons_delta = safe_growth(profit_cons_post, profit_cons_pre) if same_consensus_year else None
    eps_cons_delta = safe_growth(eps_cons_post, eps_cons_pre) if same_consensus_year else None
    target_price_delta = safe_growth(target_price_post, target_price_pre)
    price_1d = reaction_return(price_points, event_date, 1)
    price_3d = reaction_return(price_points, event_date, 3)
    price_5d = reaction_return(price_points, event_date, 5)
    benchmark_1d = benchmark_return(benchmark_points, event_date, 1)
    benchmark_3d = benchmark_return(benchmark_points, event_date, 3)
    benchmark_5d = benchmark_return(benchmark_points, event_date, 5)
    excess_1d = price_1d - benchmark_1d if price_1d is not None and benchmark_1d is not None else None
    excess_3d = price_3d - benchmark_3d if price_3d is not None and benchmark_3d is not None else None
    excess_5d = price_5d - benchmark_5d if price_5d is not None and benchmark_5d is not None else None
    turnover_pre = median_turnover(price_points, True, event_date)
    turnover_post = median_turnover(price_points, False, event_date)
    turnover_change = safe_ratio(turnover_post, turnover_pre)

    latest_single = to_single_quarter(latest_snapshot, prev_snapshot)
    yoy_single = to_single_quarter(yoy_snapshot, yoy_prev_snapshot) if yoy_snapshot else QuarterDelta(None, None, None, None)
    latest_single_gross_margin = safe_ratio(latest_single.gross_profit, latest_single.revenue)
    latest_single_gross_margin = latest_single_gross_margin * 100.0 if latest_single_gross_margin is not None else None
    yoy_single_gross_margin = safe_ratio(yoy_single.gross_profit, yoy_single.revenue)
    yoy_single_gross_margin = yoy_single_gross_margin * 100.0 if yoy_single_gross_margin is not None else None
    latest_single_revenue_yoy = safe_growth(latest_single.revenue, yoy_single.revenue)
    latest_single_profit_yoy = safe_growth(latest_single.net_profit, yoy_single.net_profit)
    latest_single_cash_yoy = safe_growth(latest_single.operating_cash, yoy_single.operating_cash)
    latest_single_margin_change = (latest_single_gross_margin - yoy_single_gross_margin) if latest_single_gross_margin is not None and yoy_single_gross_margin is not None else None

    related_target_prices = [float_or_none(item.get("targ_price")) for item in related_reports]
    related_target_prices = [value for value in related_target_prices if value is not None]
    related_target_price_median = float(median(related_target_prices)) if related_target_prices else None

    industry_info = next((item for item in industry_records if isinstance(item, dict) and normalize_ticker(item) == args.stock), None)
    industry_text = " - ".join(
        str(industry_info.get(field) or "")
        for field in ("first_industry_name", "second_industry_name", "third_industry_name")
        if industry_info and industry_info.get(field)
    ) or "未提供"
    listed_date = parse_iso_date((company_info or {}).get("listed_date"))
    office_address = str((company_info or {}).get("office_address") or "未提供")

    trend_rows = []
    single_quarter_rows = []
    recent_snapshots = [build_snapshot(item) for item in deduped[-8:]]
    for snapshot in reversed(recent_snapshots):
        prev_for_snapshot = snapshot_map.get(previous_quarter(snapshot.quarter) or "")
        yoy_for_snapshot = snapshot_map.get(yoy_quarter(snapshot.quarter) or "")
        yoy_prev_for_snapshot = snapshot_map.get(previous_quarter(yoy_for_snapshot.quarter) or "") if yoy_for_snapshot else None
        single_snapshot = to_single_quarter(snapshot, prev_for_snapshot)
        yoy_single_snapshot = to_single_quarter(yoy_for_snapshot, yoy_prev_for_snapshot) if yoy_for_snapshot else QuarterDelta(None, None, None, None)
        trend_rows.append(
            [
                snapshot.quarter.upper(),
                billion_yuan_text(snapshot.revenue),
                billion_yuan_text(snapshot.net_profit),
                percent_text(safe_growth(snapshot.revenue, yoy_for_snapshot.revenue if yoy_for_snapshot else None)),
                percent_text(safe_growth(snapshot.net_profit, yoy_for_snapshot.net_profit if yoy_for_snapshot else None)),
            ]
        )
        single_quarter_rows.append(
            [
                snapshot.quarter.upper(),
                billion_yuan_text(single_snapshot.revenue),
                billion_yuan_text(single_snapshot.net_profit),
                percent_text(safe_growth(single_snapshot.revenue, yoy_single_snapshot.revenue)),
                percent_text(safe_growth(single_snapshot.net_profit, yoy_single_snapshot.net_profit)),
            ]
        )

    expectation_text = "符合预期"
    if excess_3d is not None:
        if excess_3d >= 3:
            expectation_text = "正向反馈"
        elif excess_3d <= -3:
            expectation_text = "负向反馈"

    web_source_pairs = [(row["source_name"], row["confidence"]) for row in web_findings]
    external_negative_findings = [row for row in web_findings if row["stance"] == "negative"]
    external_context_rows = [
        [
            row["published_at"].isoformat(),
            row["subject"],
            WEB_FINDING_TYPE_LABELS.get(row["finding_type"], row["finding_type"]),
            row["summary"],
            source_confidence_label(row["source_name"], row["confidence"]),
        ]
        for row in web_findings[:6]
    ]
    external_context_lines = [
        f"- `{row['published_at'].isoformat()}` **{row['subject']}**：{trim_sentence_end(row['summary'])}。相关性：{trim_sentence_end(row['why_relevant'])}。来源：{row['source_name']}（置信度{row['confidence']}，{markdown_link('链接', row['url'])}）"
        for row in web_findings[:6]
    ] or ["- 当前未补充外部实时信息。"]

    if same_consensus_year:
        consensus_exec_line = (
            f"- 财报前后 `{pre_consensus_year}` 年净利润一致预期为 {billion_yuan_text(profit_cons_pre)} -> "
            f"{billion_yuan_text(profit_cons_post)}，变化 {percent_text(profit_cons_delta)}；一致目标价为 "
            f"{yuan_price_text(target_price_pre)} -> {yuan_price_text(target_price_post)}。"
        )
        revenue_expectation_line = (
            f"- 财报前后 `{pre_consensus_year}` 年收入一致预期为 {billion_yuan_text(revenue_cons_pre)} -> "
            f"{billion_yuan_text(revenue_cons_post)}，变化 {percent_text(revenue_cons_delta)}。"
        )
        profit_expectation_line = (
            f"- 财报前后 `{pre_consensus_year}` 年净利润一致预期为 {billion_yuan_text(profit_cons_pre)} -> "
            f"{billion_yuan_text(profit_cons_post)}，变化 {percent_text(profit_cons_delta)}。"
        )
        revenue_row_label = f"收入一致预期（{pre_consensus_year}E）"
        profit_row_label = f"净利润一致预期（{pre_consensus_year}E）"
        eps_row_label = f"EPS 一致预期（{pre_consensus_year}E）"
    else:
        consensus_exec_line = (
            f"- 财报前 `{pre_consensus_year}` 年净利润一致预期为 {billion_yuan_text(profit_cons_pre)}；财报后口径已滚动至 "
            f"`{post_consensus_year}` 年，对应净利润一致预期为 {billion_yuan_text(profit_cons_post)}；一致目标价为 "
            f"{yuan_price_text(target_price_pre)} -> {yuan_price_text(target_price_post)}。"
        )
        revenue_expectation_line = (
            f"- 财报前口径对应 `{pre_consensus_year}` 年收入一致预期 {billion_yuan_text(revenue_cons_pre)}；财报后最新口径已滚动至 "
            f"`{post_consensus_year}` 年，对应收入一致预期 {billion_yuan_text(revenue_cons_post)}。"
        )
        profit_expectation_line = (
            f"- 财报前口径对应 `{pre_consensus_year}` 年净利润一致预期 {billion_yuan_text(profit_cons_pre)}；财报后最新口径已滚动至 "
            f"`{post_consensus_year}` 年，对应净利润一致预期 {billion_yuan_text(profit_cons_post)}。"
        )
        revenue_row_label = f"收入一致预期（{pre_consensus_year}E -> {post_consensus_year}E）"
        profit_row_label = f"净利润一致预期（{pre_consensus_year}E -> {post_consensus_year}E）"
        eps_row_label = f"EPS 一致预期（{pre_consensus_year}E -> {post_consensus_year}E）"

    info_panel = "\n".join(
        [
            format_table(
                ["维度", "当前值"],
                [
                    ["最新财报季度", latest_snapshot.quarter.upper()],
                    ["披露日", event_date.isoformat()],
                    ["行业", industry_text],
                    ["上市日期", listed_date.isoformat() if listed_date else "未提供"],
                    ["办公地址", office_address],
                    ["总市值", billion_yuan_text(market_cap)],
                    ["PE / PB / 股息率", f"{x_text(pe_ratio, 1)} / {x_text(pb_ratio)} / {percent_text(dividend_yield, signed=False)}"],
                    [f"财报前净利润一致预期（{pre_consensus_year}E）", billion_yuan_text(profit_cons_pre)],
                    [f"财报后净利润一致预期（{post_consensus_year}E）", billion_yuan_text(profit_cons_post)],
                    ["高度相关研报样本", f"{len(related_reports)} 条"],
                    ["相关公告样本", f"{len(selected_announcements)} 条"],
                ],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    exec_summary = "\n".join(
        [
            f"- 公司 **{company_name}**（`{args.stock}`）最新可分析财报季度为 `{latest_snapshot.quarter}`，披露日为 `{event_date.isoformat()}`，行业归属为 `{industry_text}`。",
            f"- 累计口径收入 / 净利润为 {billion_yuan_text(latest_snapshot.revenue)} / {billion_yuan_text(latest_snapshot.net_profit)}，同比 {percent_text(revenue_yoy)} / {percent_text(profit_yoy)}，环比 {percent_text(revenue_qoq)} / {percent_text(profit_qoq)}。",
            f"- 单季度口径收入 / 净利润为 {billion_yuan_text(latest_single.revenue)} / {billion_yuan_text(latest_single.net_profit)}，同比 {percent_text(latest_single_revenue_yoy)} / {percent_text(latest_single_profit_yoy)}。",
            f"- 毛利率 / 净利率 / 现金转化率 / 资产负债率分别为 {percent_text(gross_margin, signed=False)} / {percent_text(net_margin, signed=False)} / {x_text(cash_conversion)} / {percent_text(debt_ratio, signed=False)}。",
            consensus_exec_line,
            f"- 财报后 1D / 3D / 5D 个股收益为 {percent_text(price_1d)} / {percent_text(price_3d)} / {percent_text(price_5d)}，3D 相对沪深300 超额收益为 {percent_text(excess_3d)}；反馈标签为 `{expectation_text}`。",
            f"- 相关卖方研报样本 {len(related_reports)} 条，相关公告样本 {len(selected_announcements)} 条。",
            f"- 外部实时补充信息 {len(web_findings)} 条，主要用于补足管理层动态、业绩交流会、行业和政策语境。",
            "",
            format_data_source_line([("RQData", 5), *web_source_pairs]),
        ]
    )

    earnings_overview = "\n".join(
        [
            f"- 最新累计收入 / 净利润分别为 {billion_yuan_text(latest_snapshot.revenue)} / {billion_yuan_text(latest_snapshot.net_profit)}，对应净利率 {percent_text(net_margin, signed=False)}。",
            f"- 可比同期为 `{yoy_snapshot.quarter.upper() if yoy_snapshot else '无数据'}`，上一季度为 `{prev_snapshot.quarter.upper() if prev_snapshot else '无数据'}`。",
            f"- 若拆为单季度口径，本期收入 / 净利润 / 经营现金流为 {billion_yuan_text(latest_single.revenue)} / {billion_yuan_text(latest_single.net_profit)} / {billion_yuan_text(latest_single.operating_cash)}。",
            "",
            format_table(
                ["指标", latest_snapshot.quarter.upper(), yoy_snapshot.quarter.upper() if yoy_snapshot else "可比同期", "同比/变化", prev_snapshot.quarter.upper() if prev_snapshot else "上一季度", "环比/变化"],
                [
                    ["营业收入", billion_yuan_text(latest_snapshot.revenue), billion_yuan_text(yoy_snapshot.revenue if yoy_snapshot else None), percent_text(revenue_yoy), billion_yuan_text(prev_snapshot.revenue if prev_snapshot else None), percent_text(revenue_qoq)],
                    ["净利润", billion_yuan_text(latest_snapshot.net_profit), billion_yuan_text(yoy_snapshot.net_profit if yoy_snapshot else None), percent_text(profit_yoy), billion_yuan_text(prev_snapshot.net_profit if prev_snapshot else None), percent_text(profit_qoq)],
                    ["毛利率", percent_text(gross_margin, signed=False), percent_text(yoy_gross_margin, signed=False), percent_text(gross_margin_change), "无数据", "无数据"],
                    ["ROE", percent_text(latest_roe, signed=False), "无数据", "无数据", "无数据", "无数据"],
                ],
            ),
            "",
            "### 单季度口径复盘",
            format_table(
                ["指标", latest_snapshot.quarter.upper() + " 单季", (yoy_snapshot.quarter.upper() if yoy_snapshot else "可比同期") + " 单季", "同比/变化"],
                [
                    ["营业收入", billion_yuan_text(latest_single.revenue), billion_yuan_text(yoy_single.revenue), percent_text(latest_single_revenue_yoy)],
                    ["净利润", billion_yuan_text(latest_single.net_profit), billion_yuan_text(yoy_single.net_profit), percent_text(latest_single_profit_yoy)],
                    ["经营现金流", billion_yuan_text(latest_single.operating_cash), billion_yuan_text(yoy_single.operating_cash), percent_text(latest_single_cash_yoy)],
                    ["单季毛利率", percent_text(latest_single_gross_margin, signed=False), percent_text(yoy_single_gross_margin, signed=False), percent_text(latest_single_margin_change)],
                ],
            ),
            "",
            "### 预期完成度",
            format_table(
                ["口径", "财报前一致预期", "当前累计值", "完成度"],
                [
                    ["收入", billion_yuan_text(revenue_cons_pre), billion_yuan_text(latest_snapshot.revenue), percent_text(revenue_progress, signed=False)],
                    ["净利润", billion_yuan_text(profit_cons_pre), billion_yuan_text(latest_snapshot.net_profit), percent_text(profit_progress, signed=False)],
                    ["EPS", yuan_price_text(eps_cons_pre), yuan_price_text(eps_cons_post), percent_text(eps_cons_delta)],
                ],
            ),
            "",
            "### 近八个季度累计趋势",
            format_table(["季度", "收入", "净利润", "收入同比", "净利润同比"], trend_rows),
            "",
            "### 近八个季度单季趋势",
            format_table(["季度", "单季收入", "单季净利润", "单季收入同比", "单季净利润同比"], single_quarter_rows),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    report_rows = []
    report_summary_lines = []
    for item in related_reports[:8]:
        report_year = str(item.get("fiscal_year") or "")
        net_profit_forecast = float_or_none(item.get("net_profit_t")) if report_year == str(target_year) else None
        report_rows.append(
            [
                str(item.get("create_tm") or item.get("create_time") or item.get("date") or "无数据")[:10],
                str(item.get("institute") or "无数据"),
                compact_text(item.get("report_title") or "无标题", 50),
                billion_yuan_text(net_profit_forecast),
                yuan_price_text(float_or_none(item.get("targ_price"))),
            ]
        )
        summary = compact_text(item.get("summary"), 180)
        report_summary_lines.append(
            f"- `{str(item.get('create_tm') or item.get('create_time') or item.get('date') or '')[:10]}` **{item.get('institute') or '无数据'}**：{item.get('report_title') or '无标题'}。摘要：{summary or '无摘要'}"
        )
    if not report_rows:
        report_rows = [["无高度相关研报", "-", "-", "-", "-"]]
        report_summary_lines = ["- 报告期附近未识别到高度相关的公司财报点评。"]

    expectation_and_reaction = "\n".join(
        [
            revenue_expectation_line,
            profit_expectation_line,
            f"- 财报前后一致目标价为 {yuan_price_text(target_price_pre)} -> {yuan_price_text(target_price_post)}，变化 {percent_text(target_price_delta)}；成交额中位数倍数为 {x_text(turnover_change)}。",
            "",
            format_table(
                ["口径", "财报前", "财报后", "变化"],
                [
                    [revenue_row_label, billion_yuan_text(revenue_cons_pre), billion_yuan_text(revenue_cons_post), percent_text(revenue_cons_delta)],
                    [profit_row_label, billion_yuan_text(profit_cons_pre), billion_yuan_text(profit_cons_post), percent_text(profit_cons_delta)],
                    [eps_row_label, yuan_price_text(eps_cons_pre), yuan_price_text(eps_cons_post), percent_text(eps_cons_delta)],
                    ["一致目标价", yuan_price_text(target_price_pre), yuan_price_text(target_price_post), percent_text(target_price_delta)],
                ],
            ),
            "",
            "### 价格反馈看板",
            format_table(
                ["窗口", "个股收益", "基准收益", "超额收益", "成交额变化"],
                [
                    ["1D", percent_text(price_1d), percent_text(benchmark_1d), percent_text(excess_1d), x_text(turnover_change)],
                    ["3D", percent_text(price_3d), percent_text(benchmark_3d), percent_text(excess_3d), x_text(turnover_change)],
                    ["5D", percent_text(price_5d), percent_text(benchmark_5d), percent_text(excess_5d), x_text(turnover_change)],
                ],
            ),
            "",
            "### 卖方研报口径",
            format_table(["日期", "机构", "标题", f"{target_year}E 净利润", "目标价"], report_rows),
            "",
            "### 研报摘要摘录",
            *report_summary_lines,
            "",
            "### 外部实时补充信息",
            format_table(["日期", "主题", "类型", "摘要", "来源/置信度"], external_context_rows or [["无数据", "-", "-", "-", "-"]]),
            "",
            *external_context_lines,
            "",
            format_data_source_line([("RQData", 5), *web_source_pairs]),
        ]
    )

    announcement_rows = []
    announcement_detail_lines = []
    management_lines = []
    company_intro_lines = []
    risk_snippet_lines = []
    outlook_lines = []
    extract_status_notes = []
    has_annual_or_interim_extract_source = False
    for item in selected_announcements:
        link = str(item.get("announcement_link") or "")
        extract_row = announcement_extract_index.get(link) or announcement_extract_index.get(str(item.get("title") or ""))
        announcement_rows.append(
            [
                str(item.get("info_date") or "无数据"),
                compact_text(item.get("title") or "无标题", 42),
                str(item.get("info_type") or "无数据"),
                str(item.get("media") or "无数据"),
                announcement_status_label(extract_row),
                markdown_link("原文", link),
            ]
        )
        announcement_detail_lines.append(
            f"- `{item.get('info_date') or '无数据'}` {item.get('title') or '无标题'}；类型：{item.get('info_type') or '无数据'}；来源：{item.get('media') or '无数据'}；{markdown_link('原文', link)}"
        )
        if extract_row:
            if bool(extract_row.get("is_annual_or_interim_report")):
                has_annual_or_interim_extract_source = True
            company_intro = extract_section_display_text(extract_row, "company_intro", 220)
            management_discussion = extract_section_display_text(extract_row, "management_discussion", 220)
            risk_warning = extract_section_display_text(extract_row, "risk_warning", 220)
            outlook = extract_section_display_text(extract_row, "outlook", 220)
            if company_intro:
                company_intro_lines.append(f"- `{item.get('title')}`：{company_intro}")
            if management_discussion:
                management_lines.append(f"- `{item.get('title')}`：{management_discussion}")
            if risk_warning:
                risk_snippet_lines.append(f"- `{item.get('title')}`：{risk_warning}")
            if outlook:
                outlook_lines.append(f"- `{item.get('title')}`：{outlook}")
            fetch_status = str(extract_row.get("fetch_status") or "")
            extract_status = str(extract_row.get("extract_status") or "")
            if fetch_status and fetch_status not in ("ok", "skipped"):
                extract_status_notes.append(f"- `{item.get('title')}`：正文未完整获取，报告保留原文链接供查阅。")
            if extract_status and extract_status not in ("ok", "未提取", "skipped_non_annual_interim"):
                extract_status_notes.append(f"- `{item.get('title')}`：正文未形成可直接引用片段，报告保留原文链接供查阅。")
    if not announcement_rows:
        announcement_rows = [["无相关公告", "-", "-", "-", "-", "-"]]
        announcement_detail_lines = ["- 报告期附近未识别到与本次财报直接相关的公告。"]
    if not company_intro_lines:
        if announcement_extract_records and not has_annual_or_interim_extract_source:
            company_intro_lines = ["- 本次相关公告以季报或临时公告为主，未单列公司介绍片段。"]
        elif announcement_extract_records:
            company_intro_lines = ["- 当前未形成可直接引用的公司介绍片段。"]
        else:
            company_intro_lines = ["- 当前未获取可直接引用的公司介绍片段。"]
    if not management_lines:
        if announcement_extract_records and not has_annual_or_interim_extract_source:
            management_lines = ["- 本次相关公告以季报或临时公告为主，未单列管理层表述片段。"]
        elif announcement_extract_records:
            management_lines = ["- 当前未形成可直接引用的管理层表述片段。"]
        else:
            management_lines = ["- 当前未获取可直接引用的管理层表述片段。"]
    if not risk_snippet_lines:
        if announcement_extract_records and has_annual_or_interim_extract_source:
            risk_snippet_lines = ["- 年报正文未提供可直接引用的风险提示片段。"]
        else:
            risk_snippet_lines = ["- 当前未获取可直接引用的风险提示片段。"]
    if not outlook_lines:
        if announcement_extract_records and not has_annual_or_interim_extract_source:
            outlook_lines = ["- 本次相关公告以季报或临时公告为主，未单列经营展望片段。"]
        elif announcement_extract_records:
            outlook_lines = ["- 当前未形成可直接引用的经营展望片段。"]
        else:
            outlook_lines = ["- 当前未获取可直接引用的经营展望片段。"]
    if not extract_status_notes and selected_announcements and not announcement_extract_records:
        extract_status_notes = [
            "- 本节保留相关公告原文链接，供进一步查阅。"
        ]
    elif not extract_status_notes and announcement_extract_records:
        if not has_annual_or_interim_extract_source:
            extract_status_notes = [
                "- 相关公告以季报及临时公告为主，本节以公告链接和关键信息为主。"
            ]
        else:
            extract_status_notes = [
                "- 本节优先引用年报或半年报正文中的管理层表述与经营展望，其他公告保留原文链接备查。"
            ]

    announcement_section = "\n".join(
        [
            format_table(["日期", "标题", "类型", "来源", "正文说明", "链接"], announcement_rows),
            "",
            "### 公告清单",
            *announcement_detail_lines,
            "",
            "### 公司介绍片段",
            *company_intro_lines,
            "",
            "### 管理层表述片段",
            *management_lines,
            "",
            "### 风险提示片段",
            *risk_snippet_lines,
            "",
            "### 经营展望片段",
            *outlook_lines,
            "",
            "### 补充说明",
            *extract_status_notes,
            "",
            "*数据来源：RQData（公告链接 / 可选原文提取），置信度5*",
        ]
    )

    roe_rows = []
    for item in roe_records:
        if not isinstance(item, dict) or normalize_ticker(item) != args.stock:
            continue
        event_day = parse_iso_date(item.get("date"))
        roe_value = float_or_none(item.get("return_on_equity_weighted_average"))
        if event_day is None or event_day > report_date or roe_value is None:
            continue
        roe_rows.append((event_day, roe_value))
    roe_rows = sorted(roe_rows, key=lambda pair: pair[0])[-5:]
    roe_table_rows = [[row[0].isoformat(), percent_text(row[1], signed=False)] for row in roe_rows] or [["无数据", "无数据"]]

    financial_quality = "\n".join(
        [
            f"- 经营现金流 / 现金转化率 / 总资产 / 总负债分别为 {billion_yuan_text(latest_snapshot.operating_cash)} / {x_text(cash_conversion)} / {billion_yuan_text(latest_snapshot.total_assets)} / {billion_yuan_text(latest_snapshot.total_liabilities)}。",
            f"- 资产负债率 / 最新 ROE / 当前 PB 分别为 {percent_text(debt_ratio, signed=False)} / {percent_text(latest_roe, signed=False)} / {x_text(pb_ratio)}。",
            "",
            format_table(
                ["维度", "当前值", "备注"],
                [
                    ["经营现金流", billion_yuan_text(latest_snapshot.operating_cash), "累计口径"],
                    ["现金转化率", x_text(cash_conversion), "经营现金流 / 净利润"],
                    ["总资产", billion_yuan_text(latest_snapshot.total_assets), "累计口径"],
                    ["总负债", billion_yuan_text(latest_snapshot.total_liabilities), "累计口径"],
                    ["资产负债率", percent_text(debt_ratio, signed=False), "总负债 / 总资产"],
                    ["最新 ROE", percent_text(latest_roe, signed=False), "最近非空因子日"],
                    ["当前 PB", x_text(pb_ratio), "最近非空因子日"],
                ],
            ),
            "",
            "### 近期 ROE 取值",
            format_table(["日期", "ROE"], roe_table_rows),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    growth_pillar = "通过" if (revenue_yoy is not None and revenue_yoy > 0 and profit_yoy is not None and profit_yoy > 0) else "未通过"
    quality_pillar = "通过" if cash_conversion is not None and cash_conversion >= 1.0 and gross_margin_change is not None and gross_margin_change >= -1.0 else "未通过"
    market_pillar = "通过" if excess_3d is not None and excess_3d >= 0 else "未通过"
    announcement_pillar = "通过" if selected_announcements else "未通过"
    external_pillar = "通过" if web_findings else "未通过"
    thesis_update = "\n".join(
        [
            format_table(
                ["验证支柱", "结果", "证据"],
                [
                    ["增长口径", growth_pillar, f"收入同比 {percent_text(revenue_yoy)}；净利润同比 {percent_text(profit_yoy)}"],
                    ["质量口径", quality_pillar, f"毛利率变动 {percent_text(gross_margin_change)}；现金转化率 {x_text(cash_conversion)}"],
                    ["市场反馈", market_pillar, f"3D 超额收益 {percent_text(excess_3d)}；成交额倍数 {x_text(turnover_change)}"],
                    ["公告链路", announcement_pillar, f"相关公告 {len(selected_announcements)} 条；正文读取结果 {len(announcement_extract_records)} 条"],
                    ["外部语境", external_pillar, f"外部补充信息 {len(web_findings)} 条"],
                ],
            ),
            "",
            "### 规则口径",
            "- 增长口径：收入同比 > 0 且净利润同比 > 0。",
            "- 质量口径：现金转化率 >= 1x 且毛利率同比变动 >= -1pct。",
            "- 市场反馈：3D 超额收益 >= 0。",
            "- 公告链路：报告期附近存在正式财报/主要经营数据/业绩说明会公告。",
            "- 外部语境：只补充 RQData 无法直接提供的管理层动态、行业和政策背景，不替代财务与公告主源。",
            "",
            "### 外部语境摘录",
            *external_context_lines[:4],
            "",
            format_data_source_line([("RQData", 5), *web_source_pairs]),
        ]
    )

    valuation_section = "\n".join(
        [
            f"- 当前市值约为 {billion_yuan_text(market_cap)}，PE / PB / 股息率分别约为 {x_text(pe_ratio, 1)} / {x_text(pb_ratio)} / {percent_text(dividend_yield, signed=False)}。",
            f"- 财报后一致目标价为 {yuan_price_text(target_price_post)}；高度相关研报目标价中位数为 {yuan_price_text(related_target_price_median)}。",
            "",
            format_table(
                ["指标", "当前值", "附注"],
                [
                    ["总市值", billion_yuan_text(market_cap), "最近非空因子日"],
                    ["PE", x_text(pe_ratio, 1), "最近非空因子日"],
                    ["PB", x_text(pb_ratio), "最近非空因子日"],
                    ["股息率", percent_text(dividend_yield, signed=False), "bps 已换算为百分比"],
                    ["财报前一致目标价", yuan_price_text(target_price_pre), "一致预期快照"],
                    ["财报后一致目标价", yuan_price_text(target_price_post), "一致预期快照"],
                    ["研报目标价中位数", yuan_price_text(related_target_price_median), "高度相关财报点评样本"],
                ],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    risk_rows = []
    if profit_progress is not None and profit_progress < 75:
        risk_rows.append(["全年兑现口径", f"净利润完成度 {percent_text(profit_progress, signed=False)}", "一致预期 vs 当前累计值", "RQData 财报 + consensus"])
    if gross_margin_change is not None and gross_margin_change < 0:
        risk_rows.append(["盈利能力口径", f"毛利率同比变动 {percent_text(gross_margin_change)}", "累计毛利率对比", "RQData 财报"])
    if excess_3d is not None and excess_3d < 0:
        risk_rows.append(["市场反馈口径", f"3D 超额收益 {percent_text(excess_3d)}", "财报后价格反应", "RQData 价格"])
    if profit_cons_delta is not None and profit_cons_delta < 0:
        risk_rows.append(["预期修正口径", f"净利润一致预期变化 {percent_text(profit_cons_delta)}", "财报前后快照对比", "RQData consensus"])
    for row in external_negative_findings[:2]:
        risk_rows.append(
            [
                "外部实时口径",
                compact_text(row["summary"], 60),
                row["why_relevant"],
                source_confidence_label(row["source_name"], row["confidence"]),
            ]
        )
    for line in risk_snippet_lines:
        if line.startswith("- `"):
            risk_rows.append(["原文风险提示", compact_text(line.replace("- ", ""), 60), "财报原文片段", "公告原文提取"])
            break
    if not risk_rows:
        risk_rows = [["未识别到新增显著风险", "无数据", "无数据", "RQData"]]

    risk_section = "\n".join(
        [
            format_table(["风险维度", "当前证据", "口径", "来源"], risk_rows),
            "",
            format_data_source_line([("RQData", 5), *web_source_pairs]),
        ]
    )

    appendix = "\n".join(
        [
            f"- 报告日期：{args.report_date}；公司：{company_name}（{args.stock}）；最新财报季度：{latest_snapshot.quarter}；披露日：{event_date.isoformat()}。",
            f"- 行业：{industry_text}；上市日期：{listed_date.isoformat() if listed_date else '未提供'}；办公地址：{office_address}。",
            f"- 一致预期口径使用财报披露前最后一条快照与报告日最新快照；本次读取的财报前年份为 {pre_consensus_year} 年，财报后滚动年份为 {post_consensus_year} 年。",
            "- Q2、Q3、Q4 等累计披露口径已拆分为单季度口径，避免将累计值误读为单季变化。",
            "- 股息率原始值为 bps，文中已统一换算为百分比。",
            "- 公告章节优先引用年报或半年报正文片段，其他公告保留原文链接供查阅。",
            f"- 外部实时补充信息：{len(web_findings)} 条；仅用于补充管理层动态、行业和政策语境，不替代 RQData 财务、预期和公告主源。",
            "",
            format_data_source_line([("RQData", 5), *web_source_pairs]),
        ]
    )

    template_text = Path(args.template).read_text(encoding="utf-8")
    report_text = render_template(
        template_text,
        {
            "REPORT_DATE": args.report_date,
            "COMPANY_NAME": company_name,
            "STOCK_CODE": args.stock,
            "LATEST_QUARTER": latest_snapshot.quarter.upper(),
            "EVENT_DATE": event_date.isoformat(),
            "EXEC_SUMMARY": exec_summary,
            "INFO_PANEL": info_panel,
            "EARNINGS_OVERVIEW": earnings_overview,
            "EXPECTATION_AND_REACTION": expectation_and_reaction,
            "ANNOUNCEMENT_SECTION": announcement_section,
            "FINANCIAL_QUALITY": financial_quality,
            "THESIS_UPDATE": thesis_update,
            "VALUATION_SECTION": valuation_section,
            "RISK_SECTION": risk_section,
            "APPENDIX": appendix,
        },
    )

    output_path = Path(args.output).expanduser() if args.output else data_dir / f"earnings_analysis_{args.stock}_{args.report_date}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
