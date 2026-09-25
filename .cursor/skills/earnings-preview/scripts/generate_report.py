#!/usr/bin/env python3
"""Template-driven earnings preview report generator."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median, pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 财报预览报告",
    "## 执行摘要",
    "## 历史经营与财务趋势",
    "## 预览季度预测框架",
    "## 卖方预期与市场定价",
    "## 公告与管理层线索",
    "## 情景分析与市场反应",
    "## 财报前交易定位",
    "## 关键风险与验证点",
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
    "earnings_release_date",
    "conference_call",
    "industry_context",
    "company_news",
}

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")


class ExternalFinding(dict):
    pass


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的财报预览报告")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", help="公司名称，可选")
    parser.add_argument("--quarter", required=True, help="目标预览季度，例如 2026q1")
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

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_quarter_key(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"^(\d{4})q([1-4])$", str(value).strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def quarter_to_string(value: Tuple[int, int]) -> str:
    return f"{value[0]}q{value[1]}"


def previous_quarter(quarter: str) -> Optional[str]:
    key = parse_quarter_key(quarter)
    if not key:
        return None
    year, q = key
    if q == 1:
        return f"{year - 1}q4"
    return f"{year}q{q - 1}"


def same_quarter_last_year(quarter: str) -> Optional[str]:
    key = parse_quarter_key(quarter)
    if not key:
        return None
    return f"{key[0] - 1}q{key[1]}"


def quarter_distance(from_quarter: str, to_quarter: str) -> Optional[int]:
    from_key = parse_quarter_key(from_quarter)
    to_key = parse_quarter_key(to_quarter)
    if not from_key or not to_key:
        return None
    return (to_key[0] - from_key[0]) * 4 + (to_key[1] - from_key[1])


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


def extract_web_search_findings(records: Sequence[Any], report_date: date) -> List[ExternalFinding]:
    findings: List[ExternalFinding] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        published_at = parse_iso_date(item.get("published_at"))
        if published_at is None or published_at > report_date:
            continue
        event_date = parse_iso_date(item.get("event_date"))
        findings.append(
            ExternalFinding(
                source_name=str(item.get("source_name") or "网络搜索来源").strip(),
                title=clean_text(item.get("title")),
                published_at=published_at,
                summary=clean_text(item.get("summary")).rstrip("。；;!！?？"),
                why_relevant=clean_text(item.get("why_relevant")).rstrip("。；;!！?？"),
                confidence=int(float_or_none(item.get("confidence")) or 0),
                finding_type=str(item.get("finding_type") or "").strip(),
                url=str(item.get("url") or "").strip(),
                event_date=event_date.isoformat() if event_date else "",
                expected_window=str(item.get("expected_window") or "").strip(),
            )
        )
    findings.sort(key=lambda item: (item["published_at"], item["confidence"]), reverse=True)
    return findings


def is_company_report_record(record: Dict[str, Any]) -> bool:
    data_source = float_or_none(record.get("data_source"))
    if data_source is None:
        return True
    return abs(data_source) < 1e-9


def billion_yuan_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def percent_text(value: Optional[float], digits: int = 1, signed: bool = True) -> str:
    if value is None:
        return "无数据"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}%"


def x_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}x"


def yuan_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}元"


def compact_text(value: Any, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def median_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return float(median(valid))


def stdev_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if len(valid) < 2:
        return None
    return float(pstdev(valid))


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def safe_margin(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator) * 100.0


def select_margin_metric(rows: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    candidates = [
        ("gross_margin", "毛利率"),
        ("operating_margin", "营业利润率"),
        ("net_margin", "净利率"),
    ]
    for field, label in candidates:
        if any(item.get(field) is not None for item in rows):
            return field, label
    return "net_margin", "净利率"


def dedupe_financial_records(records: List[Any], stock: str, report_date: date) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        item_date = parse_iso_date(item.get("info_date"))
        if item_date and item_date > report_date:
            continue
        quarter = str(item.get("quarter") or "").lower()
        if not parse_quarter_key(quarter):
            continue
        current = deduped.get(quarter)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        if current is None or (item_date and (current_date is None or item_date >= current_date)):
            deduped[quarter] = item
    return [deduped[key] for key in sorted(deduped.keys(), key=lambda q: parse_quarter_key(q) or (0, 0))]


def latest_factor_series(records: List[Any], stock: str, field_name: str) -> List[Tuple[date, float]]:
    series: Dict[date, float] = {}
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date"))
        value = float_or_none(item.get(field_name))
        if event_date is None or value is None:
            continue
        series[event_date] = value
    return sorted(series.items(), key=lambda pair: pair[0])


def build_financial_trend(records: List[Any], stock: str, report_date: date) -> List[Dict[str, Any]]:
    ordered = dedupe_financial_records(records, stock, report_date)
    by_quarter = {str(item.get("quarter") or "").lower(): item for item in ordered}
    trend: List[Dict[str, Any]] = []
    for item in ordered:
        quarter = str(item.get("quarter") or "").lower()
        revenue = float_or_none(item.get("revenue"))
        net_profit = float_or_none(item.get("net_profit"))
        gross_profit = float_or_none(item.get("gross_profit"))
        operating_profit = float_or_none(item.get("profit_from_operation"))
        operating_expense = float_or_none(item.get("operating_expense"))
        cash_flow = float_or_none(item.get("cash_from_operating_activities"))
        yoy_record = by_quarter.get(same_quarter_last_year(quarter) or "")

        yoy_revenue = float_or_none(yoy_record.get("revenue")) if yoy_record else None
        yoy_profit = float_or_none(yoy_record.get("net_profit")) if yoy_record else None
        revenue_yoy = ((revenue / yoy_revenue - 1.0) * 100.0) if revenue is not None and yoy_revenue not in (None, 0) else None
        profit_yoy = ((net_profit / yoy_profit - 1.0) * 100.0) if net_profit is not None and yoy_profit not in (None, 0) else None
        gross_margin = safe_margin(gross_profit, revenue)
        operating_margin = safe_margin(operating_profit, revenue)
        net_margin = safe_margin(net_profit, revenue)
        expense_ratio = ((operating_expense / revenue) * 100.0) if operating_expense is not None and revenue not in (None, 0) else None
        cash_conversion = (cash_flow / net_profit) if cash_flow is not None and net_profit not in (None, 0) else None

        trend.append(
            {
                "quarter": quarter,
                "info_date": item.get("info_date"),
                "revenue": revenue,
                "net_profit": net_profit,
                "gross_margin": gross_margin,
                "operating_margin": operating_margin,
                "net_margin": net_margin,
                "expense_ratio": expense_ratio,
                "cash_flow": cash_flow,
                "cash_conversion": cash_conversion,
                "revenue_yoy": revenue_yoy,
                "profit_yoy": profit_yoy,
            }
        )
    return trend


def attach_roe(trend: List[Dict[str, Any]], roe_series: List[Tuple[date, float]]) -> None:
    for item in trend:
        info_date = parse_iso_date(item.get("info_date"))
        matched = None
        for event_date, value in roe_series:
            if info_date and event_date <= info_date:
                matched = value
        item["roe"] = matched


def forecast_target_quarter(trend: List[Dict[str, Any]], target_quarter: str) -> Dict[str, Any]:
    by_quarter = {item["quarter"]: item for item in trend}
    latest = trend[-1] if trend else {}
    anchor_quarter = same_quarter_last_year(target_quarter)
    anchor = by_quarter.get(anchor_quarter or "")
    previous = by_quarter.get(previous_quarter(target_quarter) or "")
    recent = trend[-4:]
    margin_field, margin_label = select_margin_metric(recent or trend)

    revenue_yoy_base = median_or_none(item.get("revenue_yoy") for item in recent)
    profit_yoy_base = median_or_none(item.get("profit_yoy") for item in recent)
    margin_base = median_or_none(item.get(margin_field) for item in recent)
    cash_conversion_base = median_or_none(item.get("cash_conversion") for item in recent)
    expense_ratio_base = median_or_none(item.get("expense_ratio") for item in recent)

    anchor_revenue = anchor.get("revenue") if anchor else latest.get("revenue")
    anchor_profit = anchor.get("net_profit") if anchor else latest.get("net_profit")
    predicted_revenue = (
        anchor_revenue * (1.0 + (revenue_yoy_base or 0.0) / 100.0) if anchor_revenue is not None else None
    )
    predicted_profit = (
        anchor_profit * (1.0 + (profit_yoy_base or 0.0) / 100.0) if anchor_profit is not None else None
    )
    predicted_cash = (
        predicted_profit * cash_conversion_base if predicted_profit is not None and cash_conversion_base is not None else None
    )

    return {
        "target_quarter": target_quarter,
        "latest_quarter": latest.get("quarter") or "无数据",
        "anchor_quarter": anchor_quarter or "无数据",
        "anchor_revenue": anchor_revenue,
        "anchor_profit": anchor_profit,
        "predicted_revenue": predicted_revenue,
        "predicted_profit": predicted_profit,
        "margin_field": margin_field,
        "margin_label": margin_label,
        "predicted_margin": margin_base,
        "predicted_cash_conversion": cash_conversion_base,
        "predicted_expense_ratio": expense_ratio_base,
        "predicted_cash_flow": predicted_cash,
        "revenue_yoy_base": revenue_yoy_base,
        "profit_yoy_base": profit_yoy_base,
        "latest": latest,
        "previous": previous,
    }


def build_price_snapshot(stock_records: List[Any], index_records: List[Any], stock: str) -> Dict[str, Any]:
    def to_series(records: List[Any], asset: Optional[str] = None) -> List[Tuple[date, float, Optional[float]]]:
        result = []
        for item in records:
            if not isinstance(item, dict):
                continue
            if asset is not None and normalize_ticker(item) != asset:
                continue
            event_date = parse_iso_date(item.get("datetime") or item.get("date"))
            close = float_or_none(item.get("close"))
            turnover = float_or_none(item.get("total_turnover"))
            if event_date is None or close is None:
                continue
            result.append((event_date, close, turnover))
        result.sort(key=lambda row: row[0])
        return result

    stock_series = to_series(stock_records, stock)
    index_series = to_series(index_records)
    stock_return = None
    index_return = None
    excess_return = None
    realized_vol = None
    current_price = None
    avg_turnover_20 = None
    avg_turnover_prev20 = None

    if len(stock_series) >= 2:
        stock_return = (stock_series[-1][1] / stock_series[0][1] - 1.0) * 100.0
        current_price = stock_series[-1][1]
        returns = []
        for idx in range(1, len(stock_series)):
            prev = stock_series[idx - 1][1]
            curr = stock_series[idx][1]
            if prev:
                returns.append((curr / prev - 1.0) * 100.0)
        if len(returns) >= 10:
            realized_vol = stdev_or_none(returns[-20:])
        turnovers = [row[2] for row in stock_series if row[2] is not None]
        if len(turnovers) >= 20:
            avg_turnover_20 = sum(turnovers[-20:]) / 20.0
        if len(turnovers) >= 40:
            avg_turnover_prev20 = sum(turnovers[-40:-20]) / 20.0

    if len(index_series) >= 2:
        index_return = (index_series[-1][1] / index_series[0][1] - 1.0) * 100.0
    if stock_return is not None and index_return is not None:
        excess_return = stock_return - index_return

    return {
        "stock_return": stock_return,
        "index_return": index_return,
        "excess_return": excess_return,
        "realized_vol": realized_vol,
        "current_price": current_price,
        "avg_turnover_20": avg_turnover_20,
        "avg_turnover_prev20": avg_turnover_prev20,
    }


def build_consensus_snapshot(records: List[Any], stock: str) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[date, Dict[str, Any]]] = None
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("create_tm"))
        if event_date is None:
            continue
        if best is None or event_date >= best[0]:
            best = (event_date, item)
    return best[1] if best else None


def build_company_aliases(company_records: List[Any], stock: str, company_name: str) -> List[str]:
    aliases = [company_name, company_name.replace("股份有限公司", ""), company_name.replace("股份", ""), stock, stock.split(".")[0]]
    for item in company_records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        for field in ("display_name", "name", "stock_name", "company_name", "abbrev_symbol", "symbol"):
            value = str(item.get(field) or "").strip()
            if value:
                aliases.extend([value, value.replace("股份有限公司", ""), value.replace("股份", ""), value.replace(" ", "")])
    deduped = []
    seen = set()
    for alias in aliases:
        alias = alias.strip()
        if alias and alias not in seen:
            deduped.append(alias)
            seen.add(alias)
    return deduped


def pick_web_findings(findings: Sequence[ExternalFinding], finding_type: str) -> List[ExternalFinding]:
    return [item for item in findings if item.get("finding_type") == finding_type]


def build_earnings_event_context(findings: Sequence[ExternalFinding]) -> str:
    release_items = pick_web_findings(findings, "earnings_release_date")
    call_items = pick_web_findings(findings, "conference_call")
    if release_items:
        item = release_items[0]
        event_text = item.get("event_date") or item.get("expected_window") or "未验证"
        suffix = f"；电话会：{call_items[0].get('event_date') or call_items[0].get('expected_window') or '未验证'}" if call_items else ""
        return f"{event_text}（数据来源：{item.get('source_name')}，置信度{item.get('confidence')}）{suffix}"
    if call_items:
        item = call_items[0]
        event_text = item.get("event_date") or item.get("expected_window") or "未验证"
        return f"电话会：{event_text}（数据来源：{item.get('source_name')}，置信度{item.get('confidence')}）"
    return "未验证"


def build_network_search_block(findings: Sequence[ExternalFinding], heading: str, limit: int = 4) -> List[str]:
    if not findings:
        return []
    label_map = {
        "earnings_release_date": "预计披露日",
        "conference_call": "电话会",
        "industry_context": "行业动态",
        "company_news": "公司新闻",
    }
    lines = [heading]
    for item in findings[:limit]:
        event_hint = item.get("event_date") or item.get("expected_window") or item["published_at"].isoformat()
        lines.append(
            f"- **{event_hint} / {label_map.get(item['finding_type'], '网络搜索结果')} / {item['source_name']}**："
            f"{item['title']}。{item['summary']}。对本次预览的意义：{item['why_relevant']}。"
            f" *数据来源：{item['source_name']}，置信度{item['confidence']}*"
        )
    return lines


def filter_related_reports(records: List[Any], stock: str, aliases: Sequence[str], report_date: date) -> List[Dict[str, Any]]:
    related = []
    lower_aliases = [alias.lower() for alias in aliases]
    strong_aliases = [alias for alias in lower_aliases if len(alias) >= 4 or re.search(r"\d", alias)]
    stock_code = stock.split(".")[0].lower()
    excluded_title_tokens = (
        "行业周报",
        "周报",
        "月报",
        "专题",
        "策略",
        "宏观",
        "晨报",
        "行业点评合集",
        "行业动态",
        "动态点评",
        "持仓分析",
        "行业配置",
        "板块",
        "金股",
        "组合",
    )
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        if not is_company_report_record(item):
            continue
        create_date = parse_iso_date(item.get("create_tm") or item.get("create_time") or item.get("date"))
        if create_date is None or create_date > report_date:
            continue
        title = str(item.get("report_title") or "")
        summary = str(item.get("summary") or "")
        title_lower = title.lower()
        summary_lower = summary.lower()
        report_main_id = str(item.get("report_main_id") or "").lower()
        title_hit = any(alias in title_lower for alias in strong_aliases)
        summary_hit = any(alias in summary_lower for alias in strong_aliases)
        report_main_id_hit = bool(stock_code) and stock_code in report_main_id
        company_style_title = any(token in title_lower for token in ("点评", "results", "earnings", "季报", "年报", "三季报", "中报", "q1", "q2", "q3", "q4", "更新", "预览", "前瞻"))
        generic_title = any(token.lower() in title_lower for token in excluded_title_tokens)
        if generic_title and not title_hit:
            continue
        if title_hit or (summary_hit and company_style_title) or (report_main_id_hit and (title_hit or summary_hit or company_style_title)):
            related.append(item)
    related.sort(key=lambda item: str(item.get("create_tm") or item.get("date") or ""), reverse=True)
    return related


def build_research_snapshot(records: List[Any], stock: str, aliases: Sequence[str], report_date: date) -> Dict[str, Any]:
    filtered = filter_related_reports(records, stock, aliases, report_date)
    filtered.sort(key=lambda item: str(item.get("create_tm") or item.get("date") or ""), reverse=True)
    display_reports = [item for item in filtered if extract_research_display_summary(item)]

    target_prices = [float_or_none(item.get("targ_price")) for item in filtered]
    target_prices = [value for value in target_prices if value is not None]
    profit_estimates = [float_or_none(item.get("net_profit_t")) for item in filtered]
    profit_estimates = [value for value in profit_estimates if value is not None]

    return {
        "count": len(filtered),
        "reports": filtered[:8],
        "display_reports": display_reports[:8],
        "target_price_median": median_or_none(target_prices),
        "target_price_min": min(target_prices) if target_prices else None,
        "target_price_max": max(target_prices) if target_prices else None,
        "profit_t_median": median_or_none(profit_estimates),
        "profit_t_std": stdev_or_none(profit_estimates),
        "institutes": sorted({str(item.get("institute") or "").strip() for item in filtered if item.get("institute")}),
    }


def announcement_score(title: str, info_type: str, report_date: date, info_date: Optional[date]) -> int:
    score = 0
    if "定期报告" in info_type:
        score += 100
    if re.search(r"(年报|年度报告|半年报|半年度报告|一季报|第一季度报告|三季报|第三季度报告)", title):
        score += 80
    if re.search(r"(主要经营数据|业绩说明会|业绩发布会|投资者关系活动记录表|调研活动信息)", title):
        score += 50
    if re.search(r"(摘要|英文版|英文摘要)", title):
        score -= 40
    if re.search(r"(董事会|监事会|法律意见书|独立财务顾问|章程|投票制实施细则)", title):
        score -= 20
    if re.search(r"(激励计划|股票期权|限制性股票|行权|归属|注销|作废)", title):
        score -= 40
    if info_date is not None:
        distance = (report_date - info_date).days
        if distance < 0:
            score -= 100
        else:
            score += max(0, 120 - distance) // 6
    return score


def target_quarter_report_tokens(target_quarter: str) -> List[str]:
    key = parse_quarter_key(target_quarter)
    if not key:
        return []
    year, quarter = key
    quarter_map = {
        1: [f"{year}年第一季度报告", f"{year}年一季报", f"{year}q1", f"{year}Q1"],
        2: [f"{year}年半年度报告", f"{year}年半年报", f"{year}年中报", f"{year}q2", f"{year}Q2"],
        3: [f"{year}年第三季度报告", f"{year}年三季报", f"{year}q3", f"{year}Q3"],
        4: [f"{year}年年度报告", f"{year}年年报", f"{year}q4", f"{year}Q4"],
    }
    return quarter_map.get(quarter, [])


def is_target_quarter_periodic_report(title: str, target_quarter: str) -> bool:
    text = clean_text(title)
    if not text:
        return False
    if not re.search(r"(报告|季报|年报|中报|业绩快报|业绩预告)", text):
        return False
    return any(token in text for token in target_quarter_report_tokens(target_quarter))


def select_relevant_announcements(
    records: List[Any], stock: str, report_date: date, target_quarter: str
) -> List[Dict[str, Any]]:
    candidates = []
    lower_bound = report_date - timedelta(days=240)
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        info_date = parse_iso_date(item.get("info_date") or item.get("date") or item.get("create_tm"))
        if info_date is None or info_date < lower_bound or info_date > report_date:
            continue
        title = str(item.get("title") or "")
        info_type = str(item.get("info_type") or "")
        if is_target_quarter_periodic_report(title, target_quarter):
            continue
        score = announcement_score(title, info_type, report_date, info_date)
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
        if len(deduped) >= 6:
            break
    return deduped


def extract_section_display_text(extract_row: Dict[str, Any], key: str, limit: int = 220) -> str:
    summaries = extract_row.get("summaries") if isinstance(extract_row.get("summaries"), dict) else {}
    legacy_sections = extract_row.get("sections") if isinstance(extract_row.get("sections"), dict) else {}
    raw_sections = extract_row.get("raw_sections") if isinstance(extract_row.get("raw_sections"), dict) else {}
    if summaries:
        value = summaries.get(key)
        if value not in (None, ""):
            return compact_text(value, limit)
        return ""
    if not raw_sections and legacy_sections:
        value = legacy_sections.get(key)
        if value not in (None, ""):
            return compact_text(value, limit)
    return ""


def build_peer_snapshot(financial_records: List[Any], peer_meta_records: List[Any]) -> Dict[str, Any]:
    meta_map = {
        normalize_ticker(item): normalize_name(item) or normalize_ticker(item)
        for item in peer_meta_records
        if isinstance(item, dict) and normalize_ticker(item)
    }
    rows = []
    seen: Dict[str, Dict[str, Any]] = {}
    for item in financial_records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        quarter = str(item.get("quarter") or "").lower()
        if not stock or not parse_quarter_key(quarter):
            continue
        key = (stock, quarter)
        current = seen.get(key)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        item_date = parse_iso_date(item.get("info_date"))
        if current is None or (item_date and (current_date is None or item_date >= current_date)):
            seen[key] = item
    latest_by_stock: Dict[str, Dict[str, Any]] = {}
    for item in seen.values():
        stock = normalize_ticker(item)
        current = latest_by_stock.get(stock)
        current_key = parse_quarter_key(str(current.get("quarter") or "").lower()) if current else None
        item_key = parse_quarter_key(str(item.get("quarter") or "").lower())
        if current is None or (item_key and (current_key is None or item_key >= current_key)):
            latest_by_stock[stock] = item

    for stock, item in latest_by_stock.items():
        revenue = float_or_none(item.get("revenue"))
        gross_profit = float_or_none(item.get("gross_profit"))
        operating_profit = float_or_none(item.get("profit_from_operation"))
        net_profit = float_or_none(item.get("net_profit"))
        gross_margin = safe_margin(gross_profit, revenue)
        operating_margin = safe_margin(operating_profit, revenue)
        net_margin = safe_margin(net_profit, revenue)
        rows.append(
            {
                "name": meta_map.get(stock, stock),
                "order_book_id": stock,
                "quarter": str(item.get("quarter") or ""),
                "revenue": revenue,
                "net_profit": net_profit,
                "gross_margin": gross_margin,
                "operating_margin": operating_margin,
                "net_margin": net_margin,
            }
        )
    margin_field, margin_label = select_margin_metric(rows)
    return {
        "count": len(rows),
        "rows": sorted(rows, key=lambda item: item["name"]),
        "revenue_median": median_or_none(item.get("revenue") for item in rows),
        "net_profit_median": median_or_none(item.get("net_profit") for item in rows),
        "margin_field": margin_field,
        "margin_label": margin_label,
        "margin_median": median_or_none(item.get(margin_field) for item in rows),
    }


def build_scenarios(forecast: Dict[str, Any], price_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_band = max(2.0, (price_snapshot.get("realized_vol") or 2.0) * 1.4)
    scenarios = [
        {"name": "乐观", "rev_delta": 5.0, "profit_delta": 8.0, "margin_delta": 1.0, "reaction": f"+{base_band:.1f}% ~ +{base_band * 2.0:.1f}%", "probability": "25%"},
        {"name": "中性", "rev_delta": 0.0, "profit_delta": 0.0, "margin_delta": 0.0, "reaction": f"-{base_band / 2.0:.1f}% ~ +{base_band / 2.0:.1f}%", "probability": "50%"},
        {"name": "悲观", "rev_delta": -5.0, "profit_delta": -8.0, "margin_delta": -1.0, "reaction": f"-{base_band * 2.0:.1f}% ~ -{base_band:.1f}%", "probability": "25%"},
    ]
    rows = []
    for scenario in scenarios:
        revenue = (
            forecast["anchor_revenue"] * (1.0 + ((forecast.get("revenue_yoy_base") or 0.0) + scenario["rev_delta"]) / 100.0)
            if forecast.get("anchor_revenue") is not None
            else None
        )
        profit = (
            forecast["anchor_profit"] * (1.0 + ((forecast.get("profit_yoy_base") or 0.0) + scenario["profit_delta"]) / 100.0)
            if forecast.get("anchor_profit") is not None
            else None
        )
        margin = (
            (forecast.get("predicted_margin") or 0.0) + scenario["margin_delta"]
            if forecast.get("predicted_margin") is not None
            else None
        )
        rows.append(
            {
                "name": scenario["name"],
                "revenue": revenue,
                "profit": profit,
                "margin": margin,
                "reaction": scenario["reaction"],
                "probability": scenario["probability"],
            }
        )
    return rows


def clean_summary(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def extract_research_display_summary(item: Dict[str, Any]) -> str:
    summaries = item.get("summaries") if isinstance(item.get("summaries"), dict) else {}
    for key in ("core_view", "report_summary", "investment_takeaway"):
        value = summaries.get(key)
        if value not in (None, ""):
            return clean_summary(value)
    for key in ("report_summary", "llm_summary"):
        value = item.get(key)
        if value not in (None, ""):
            return clean_summary(value)
    return ""


def markdown_link(label: str, url: Optional[str]) -> str:
    if not url:
        return label
    return f"[{label}]({url})"


def align_price_to_target(current_price: Optional[float], target_price: Optional[float]) -> Optional[float]:
    if current_price is None or target_price in (None, 0):
        return current_price
    candidates = [current_price, current_price / 10.0, current_price / 100.0]
    scored = []
    for candidate in candidates:
        if candidate <= 0:
            continue
        scored.append((abs(math.log(candidate / target_price)), candidate))
    if not scored:
        return current_price
    return min(scored, key=lambda item: item[0])[1]


def build_exec_summary(
    company: str,
    target_quarter: str,
    industry_name: str,
    trend: List[Dict[str, Any]],
    forecast: Dict[str, Any],
    price_snapshot: Dict[str, Any],
    research_snapshot: Dict[str, Any],
    consensus_snapshot: Optional[Dict[str, Any]],
    web_findings: Sequence[ExternalFinding],
) -> str:
    latest = trend[-1] if trend else {}
    margin_label = forecast.get("margin_label", "利润率")
    lines = [
        f"- {company} 所属行业为 `{industry_name}`，当前预览目标季度为 `{target_quarter}`；最新已披露季度为 `{latest.get('quarter', '无数据')}`。",
        f"- 基于最近 4 个已披露季度的同比中位数，基准情形下我们预计营收 {billion_yuan_text(forecast.get('predicted_revenue'))}、净利润 {billion_yuan_text(forecast.get('predicted_profit'))}、{margin_label} {percent_text(forecast.get('predicted_margin'), signed=False)}。",
        f"- 财报前 3 个月股价涨跌幅为 {percent_text(price_snapshot.get('stock_return'))}，相对沪深300超额收益为 {percent_text(price_snapshot.get('excess_return'))}；20 日日收益波动率约为 {percent_text(price_snapshot.get('realized_vol'), signed=False)}。",
    ]
    if research_snapshot["count"]:
        lines.append(
            f"- 近 120 天共检索到 {research_snapshot['count']} 篇相关研报，卖方目标价中位数为 {yuan_text(research_snapshot.get('target_price_median'))}，"
            f"年度净利润口径中位数为 {billion_yuan_text(research_snapshot.get('profit_t_median'))}。"
        )
    if consensus_snapshot:
        lines.append(
            f"- 最新一致预期快照日期为 {consensus_snapshot.get('date') or '无数据'}，目标价为 {yuan_text(float_or_none(consensus_snapshot.get('con_targ_price')))}。"
        )
    release_items = pick_web_findings(web_findings, "earnings_release_date")
    if release_items:
        item = release_items[0]
        lines.append(
            f"- 网络搜索结果显示目标季度预计披露时间为 {item.get('event_date') or item.get('expected_window') or '未验证'}，"
            f"来源 {item.get('source_name')}；可用于安排财报前窗口与仓位节奏。"
        )
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    if web_findings:
        lines.append(
            f"*补充网络搜索结果：{web_findings[0].get('source_name')} 等 {len(web_findings)} 个来源，"
            f"置信度{min(item.get('confidence', 0) for item in web_findings)}-{max(item.get('confidence', 0) for item in web_findings)}*"
        )
    return "\n".join(lines)


def build_announcement_section(
    selected_announcements: List[Dict[str, Any]],
    announcement_extract_records: List[Any],
    web_findings: Sequence[ExternalFinding],
) -> str:
    intro_lines: List[str] = []
    release_items = pick_web_findings(web_findings, "earnings_release_date")
    call_items = pick_web_findings(web_findings, "conference_call")
    context_items = pick_web_findings(web_findings, "industry_context") + pick_web_findings(web_findings, "company_news")
    if release_items or call_items or context_items:
        intro_lines.extend(build_network_search_block(release_items + call_items + context_items, "### 网络搜索结果"))
        intro_lines.append("")

    if not selected_announcements:
        return "\n".join(
            intro_lines + [
                "- 报告期前未识别到与本次财报预览直接相关的重点公告。",
                "",
                "*数据来源：RQData（公告链接 / 可选原文提取），置信度5*",
            ]
        )

    extract_index: Dict[str, Dict[str, Any]] = {}
    for row in announcement_extract_records:
        if not isinstance(row, dict):
            continue
        link_key = str(row.get("announcement_link") or "")
        title_key = str(row.get("title") or "")
        if link_key:
            extract_index[link_key] = row
        if title_key:
            extract_index[title_key] = row

    announcement_rows = []
    detail_lines = []
    company_intro_lines = []
    management_lines = []
    outlook_lines = []
    risk_lines = []
    note_lines = []
    has_annual_or_interim_extract_source = False

    for item in selected_announcements:
        link = str(item.get("announcement_link") or "")
        extract_row = extract_index.get(link) or extract_index.get(str(item.get("title") or ""))
        extract_status = "链接可查"
        if extract_row:
            if bool(extract_row.get("is_annual_or_interim_report")):
                has_annual_or_interim_extract_source = True
            company_intro = extract_section_display_text(extract_row, "company_intro", 220)
            management_discussion = extract_section_display_text(extract_row, "management_discussion", 220)
            risk_warning = extract_section_display_text(extract_row, "risk_warning", 220)
            outlook = extract_section_display_text(extract_row, "outlook", 220)
            if any((company_intro, management_discussion, risk_warning, outlook)):
                extract_status = "已提炼关键片段"
            elif str(extract_row.get("fetch_status") or "") not in ("", "ok", "skipped"):
                extract_status = "原文获取不完整"
            elif str(extract_row.get("extract_status") or "") not in ("", "ok", "未提取", "skipped_non_annual_interim"):
                extract_status = "未形成可引用片段"
            if company_intro:
                company_intro_lines.append(f"- `{item.get('title')}`：{company_intro}")
            if management_discussion:
                management_lines.append(f"- `{item.get('title')}`：{management_discussion}")
            if risk_warning:
                risk_lines.append(f"- `{item.get('title')}`：{risk_warning}")
            if outlook:
                outlook_lines.append(f"- `{item.get('title')}`：{outlook}")
            if extract_status != "已提炼关键片段" and extract_status != "链接可查":
                note_lines.append(f"- `{item.get('title')}`：{extract_status}，报告保留原文链接供查阅。")

        announcement_rows.append(
            [
                str(item.get("info_date") or "无数据"),
                compact_text(item.get("title") or "无标题", 42),
                str(item.get("info_type") or "无数据"),
                str(item.get("media") or "无数据"),
                extract_status,
                markdown_link("原文", link),
            ]
        )
        detail_lines.append(
            f"- `{item.get('info_date') or '无数据'}` {item.get('title') or '无标题'}；类型：{item.get('info_type') or '无数据'}；来源：{item.get('media') or '无数据'}；{markdown_link('原文', link)}"
        )

    if not company_intro_lines:
        company_intro_lines = ["- 当前未形成可直接引用的公司背景片段。"] if has_annual_or_interim_extract_source else ["- 本次相关公告以季度更新或临时公告为主，未单列公司背景片段。"]
    if not management_lines:
        management_lines = ["- 当前未形成可直接引用的管理层表述片段。"] if has_annual_or_interim_extract_source else ["- 本次相关公告以季度更新或临时公告为主，未单列管理层表述片段。"]
    if not outlook_lines:
        outlook_lines = ["- 当前未形成可直接引用的经营展望片段。"] if has_annual_or_interim_extract_source else ["- 本次相关公告以季度更新或临时公告为主，未单列经营展望片段。"]
    if not risk_lines:
        risk_lines = ["- 当前未获取可直接引用的风险提示片段。"]
    if not note_lines:
        note_lines = ["- 本节优先引用正式财报和近期经营沟通中的关键表述，其他公告保留原文链接备查。"]

    return "\n".join(
        intro_lines + [
            format_table(["日期", "标题", "类型", "来源", "原文情况", "链接"], announcement_rows),
            "",
            "### 公告清单",
            *detail_lines,
            "",
            "### 公司背景片段",
            *company_intro_lines,
            "",
            "### 管理层近期表述",
            *management_lines,
            "",
            "### 经营展望与验证点",
            *outlook_lines,
            "",
            "### 风险与关注点",
            *risk_lines,
            "",
            "### 补充说明",
            *note_lines,
            "",
            "*数据来源：RQData（公告链接 / 可选原文提取），置信度5*",
        ]
    )


def build_historical_trend_section(trend: List[Dict[str, Any]]) -> str:
    latest = trend[-1] if trend else {}
    recent = trend[-4:]
    margin_field, margin_label = select_margin_metric(recent or trend)
    recent_revenue_yoy = median_or_none(item.get("revenue_yoy") for item in recent)
    recent_profit_yoy = median_or_none(item.get("profit_yoy") for item in recent)
    recent_margins = [item.get(margin_field) for item in recent if item.get(margin_field) is not None]
    margin_min = min(recent_margins) if recent_margins else None
    margin_max = max(recent_margins) if recent_margins else None
    lines = [
        f"- 最新已披露季度 `{latest.get('quarter', '无数据')}` 的营收为 {billion_yuan_text(latest.get('revenue'))}，净利润为 {billion_yuan_text(latest.get('net_profit'))}，营收同比 {percent_text(latest.get('revenue_yoy'))}，净利润同比 {percent_text(latest.get('profit_yoy'))}。",
        f"- 最近 4 个季度{margin_label}中位数约为 {percent_text(median_or_none(item.get(margin_field) for item in recent), signed=False)}，"
        f"现金转化率中位数约为 {x_text(median_or_none(item.get('cash_conversion') for item in recent))}。",
        f"- 最近 4 个季度 ROE 中位数约为 {percent_text(median_or_none(item.get('roe') for item in recent), signed=False)}，费用率中位数约为 {percent_text(median_or_none(item.get('expense_ratio') for item in recent), signed=False)}。",
        "",
    ]
    headers = ["季度", "营收", "营收同比", "净利润", "净利润同比", margin_label, "费用率", "现金转化率", "ROE"]
    rows = [
        [
            item["quarter"],
            billion_yuan_text(item.get("revenue")),
            percent_text(item.get("revenue_yoy")),
            billion_yuan_text(item.get("net_profit")),
            percent_text(item.get("profit_yoy")),
            percent_text(item.get(margin_field), signed=False),
            percent_text(item.get("expense_ratio"), signed=False),
            x_text(item.get("cash_conversion")),
            percent_text(item.get("roe"), signed=False),
        ]
        for item in trend[-8:]
    ]
    lines.append(format_table(headers, rows))
    lines.extend(
        [
            "",
            "### 历史观察要点",
            f"- 最近 4 个季度营收同比中位数约为 {percent_text(recent_revenue_yoy)}，净利润同比中位数约为 {percent_text(recent_profit_yoy)}。",
            (
                f"- 最近 4 个季度{margin_label}区间约为 {percent_text(margin_min, signed=False)} 至 {percent_text(margin_max, signed=False)}，"
                "说明利润率波动相对可控。"
                if margin_min is not None and margin_max is not None
                else f"- {margin_label}样本不足，暂时无法稳定刻画利润率波动区间。"
            ),
            "- 现金转化率持续高于 1x，意味着经营现金流对利润仍有较强覆盖，财报前应重点验证这一点是否延续。",
        ]
    )
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_forecast_section(forecast: Dict[str, Any], target_quarter: str) -> str:
    gap = quarter_distance(forecast.get("latest_quarter") or "", target_quarter)
    margin_label = forecast.get("margin_label", "利润率")
    lines = [
        f"- 预测锚点优先使用去年同期 `{forecast.get('anchor_quarter', '无数据')}` 与最近 4 个季度同比中位数；如果去年同期缺失，则退化为最近一期数据。",
        f"- 基准收入同比假设为 {percent_text(forecast.get('revenue_yoy_base'))}，基准利润同比假设为 {percent_text(forecast.get('profit_yoy_base'))}；"
        f"对应 {target_quarter} 的营收预测为 {billion_yuan_text(forecast.get('predicted_revenue'))}，净利润预测为 {billion_yuan_text(forecast.get('predicted_profit'))}。",
        f"- 基准{margin_label}假设为 {percent_text(forecast.get('predicted_margin'), signed=False)}，基准现金转化率假设为 {x_text(forecast.get('predicted_cash_conversion'))}。"
        if forecast.get("predicted_margin") is not None
        else "- 当前缺少足够的利润率/现金流历史，无法完整构建利润率框架。",
    ]
    if gap is not None:
        lines.append(f"- 最新已披露季度与目标季度之间相隔 {gap} 个季度；间隔越长，预测不确定性越高。")

    headers = ["指标", "基准预测", "主要依据"]
    rows = [
        ["营收", billion_yuan_text(forecast.get("predicted_revenue")), f"去年同期 `{forecast.get('anchor_quarter', '无数据')}` 与最近 4 季度营收同比中位数"],
        ["净利润", billion_yuan_text(forecast.get("predicted_profit")), f"去年同期 `{forecast.get('anchor_quarter', '无数据')}` 与最近 4 季度净利润同比中位数"],
        [margin_label, percent_text(forecast.get("predicted_margin"), signed=False), f"最近 4 季度{margin_label}中位数"],
        ["现金转化率", x_text(forecast.get("predicted_cash_conversion")), "最近 4 季度经营现金流/净利润中位数"],
    ]
    lines.extend(
        [
            "",
            format_table(headers, rows),
            "",
            "### 本次财报最值得验证的三个点",
            f"- **收入节奏**：实际营收是否接近 {billion_yuan_text(forecast.get('predicted_revenue'))}，以及同比增速是否明显偏离 {percent_text(forecast.get('revenue_yoy_base'))} 这一中枢。",
            f"- **利润兑现**：实际净利润是否接近 {billion_yuan_text(forecast.get('predicted_profit'))}，利润弹性是否高于收入弹性。",
            f"- **利润率与现金流**：{margin_label}是否守住 {percent_text(forecast.get('predicted_margin'), signed=False)} 一线，现金转化率是否仍接近 {x_text(forecast.get('predicted_cash_conversion'))}。",
            "",
            "*数据来源：RQData（历史数据），预测为分析师估算，置信度4*",
        ]
    )
    return "\n".join(lines)


def build_expectation_section(
    consensus_snapshot: Optional[Dict[str, Any]],
    research_snapshot: Dict[str, Any],
    peer_snapshot: Dict[str, Any],
) -> str:
    lines = []
    if consensus_snapshot:
        lines.extend(
            [
                f"- 最新一致预期快照日期为 {consensus_snapshot.get('date') or '无数据'}，目标价为 {yuan_text(float_or_none(consensus_snapshot.get('con_targ_price')))}。",
                f"- RQData consensus forward buckets 显示：营收 t1/t2/t3 分别为 "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_operating_revenue_t1')))} / "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_operating_revenue_t2')))} / "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_operating_revenue_t3')))}；"
                f"净利润 t1/t2/t3 分别为 "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_net_profit_t1')))} / "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_net_profit_t2')))} / "
                f"{billion_yuan_text(float_or_none(consensus_snapshot.get('comp_con_net_profit_t3')))}。"
            ]
        )
    else:
        lines.append("- 当前未识别到有效一致预期快照。")

    if research_snapshot["count"]:
        if research_snapshot.get("target_price_median") is not None:
            target_text = (
                f"目标价中位数为 {yuan_text(research_snapshot.get('target_price_median'))}，区间为 "
                f"{yuan_text(research_snapshot.get('target_price_min'))} 至 {yuan_text(research_snapshot.get('target_price_max'))}。"
            )
        else:
            target_text = "样本中未给出有效目标价，更多体现为年度利润和推荐口径。"
        lines.append(
            f"- 近 120 天卖方研报共 {research_snapshot['count']} 篇，覆盖机构包括 {('、'.join(research_snapshot['institutes'][:6]) or '无数据')}；"
            f"{target_text}"
        )
        lines.append(
            f"- 研报年度净利润口径中位数为 {billion_yuan_text(research_snapshot.get('profit_t_median'))}，"
            f"离散度约为 {billion_yuan_text(research_snapshot.get('profit_t_std'))}。"
        )
        lines.append("- 这些研报口径是年度维度校准，不应直接等同于目标季度单季预期，但可以帮助判断全年乐观假设是否已经被市场提前计入。")
        lines.append("")
        if research_snapshot.get("display_reports"):
            lines.append("### 近期研报样本")
            for item in research_snapshot["display_reports"][:5]:
                lines.append(
                    f"- **{item.get('institute') or '未知机构'} / {item.get('create_tm') or item.get('date') or '无日期'}**："
                    f"`{item.get('report_title') or '未命名研报'}`；"
                    f"目标价 {yuan_text(float_or_none(item.get('targ_price')))}，"
                    f"净利润口径 {billion_yuan_text(float_or_none(item.get('net_profit_t')))}；"
                    f"观点提要：{extract_research_display_summary(item)}"
                )
    else:
        lines.append("- 近期无可用卖方研报样本。")

    if peer_snapshot["count"]:
        peer_margin_label = peer_snapshot.get("margin_label", "利润率")
        peer_margin_field = peer_snapshot.get("margin_field", "net_margin")
        lines.extend(
            [
                "",
                "### 可比公司校准",
                f"- 已提供 {peer_snapshot['count']} 家可比公司最近一期数据；营收中位数为 {billion_yuan_text(peer_snapshot.get('revenue_median'))}，"
                f"净利润中位数为 {billion_yuan_text(peer_snapshot.get('net_profit_median'))}，{peer_margin_label}中位数为 {percent_text(peer_snapshot.get('margin_median'), signed=False)}。",
                "",
                format_table(
                    ["可比公司", "最新季度", "营收", "净利润", peer_margin_label],
                    [
                        [
                            f"{item['name']}<br>`{item['order_book_id']}`",
                            item["quarter"],
                            billion_yuan_text(item.get("revenue")),
                            billion_yuan_text(item.get("net_profit")),
                            percent_text(item.get(peer_margin_field), signed=False),
                        ]
                        for item in peer_snapshot["rows"][:5]
                    ],
                ),
            ]
        )
    else:
        lines.extend(["", "### 可比公司校准", "- 未提供可比公司数据，本轮仅基于卖方预期与公司自身历史序列做校准。"])

    lines.extend(["", "*数据来源：RQData，置信度5*"])
    return "\n".join(lines)


def build_scenario_section(scenarios: List[Dict[str, Any]], margin_label: str) -> str:
    headers = ["情景", "营收", "净利润", margin_label, "预期市场反应", "概率"]
    rows = [
        [
            item["name"],
            billion_yuan_text(item.get("revenue")),
            billion_yuan_text(item.get("profit")),
            percent_text(item.get("margin"), signed=False),
            item["reaction"],
            item["probability"],
        ]
        for item in scenarios
    ]
    lines = [
        "- 乐观/中性/悲观情景以基准同比假设为中心上下浮动，并用最近 20 个交易日的 realized volatility 估算财报日反应区间。",
        "",
        format_table(headers, rows),
        "",
        "*数据来源：RQData（历史数据/价格数据），情景分析为分析师估算，置信度4*",
    ]
    return "\n".join(lines)


def build_trading_setup_section(
    price_snapshot: Dict[str, Any],
    research_snapshot: Dict[str, Any],
    consensus_snapshot: Optional[Dict[str, Any]],
    margin_label: str,
) -> str:
    implied_target = research_snapshot.get("target_price_median")
    if implied_target is None and consensus_snapshot is not None:
        implied_target = float_or_none(consensus_snapshot.get("con_targ_price"))
    current_price = align_price_to_target(price_snapshot.get("current_price"), implied_target)
    target_upside = ((implied_target / current_price - 1.0) * 100.0) if implied_target is not None and current_price not in (None, 0) else None
    turnover_change = (
        ((price_snapshot["avg_turnover_20"] / price_snapshot["avg_turnover_prev20"] - 1.0) * 100.0)
        if price_snapshot.get("avg_turnover_20") is not None and price_snapshot.get("avg_turnover_prev20") not in (None, 0)
        else None
    )

    if price_snapshot.get("excess_return") is not None and price_snapshot["excess_return"] > 10 and (target_upside is None or target_upside < 15):
        stance = f"财报前预期已不低，若没有更强的利润与{margin_label}惊喜，股价容错率偏低。"
    elif price_snapshot.get("excess_return") is not None and price_snapshot["excess_return"] < -5 and (target_upside is None or target_upside > 10):
        stance = "财报前市场预期相对保守，若利润/现金流不差于基准情景，存在修复空间。"
    else:
        stance = "当前更接近中性 setup，重点看财报是否打破市场已有预期区间。"

    lines = [
        f"- 近 3 个月股价表现为 {percent_text(price_snapshot.get('stock_return'))}，沪深300 同期为 {percent_text(price_snapshot.get('index_return'))}，超额收益为 {percent_text(price_snapshot.get('excess_return'))}。",
        f"- 当前价格约为 {yuan_text(current_price, 2)}；"
        + (
            f"基于卖方/一致预期目标价的隐含空间约为 {percent_text(target_upside)}。"
            if target_upside is not None
            else "当前缺少有效目标价，无法计算隐含空间。"
        ),
        f"- 近 20 日成交额相对前 20 日变化约为 {percent_text(turnover_change)}，20 日日收益波动率约为 {percent_text(price_snapshot.get('realized_vol'), signed=False)}。",
        f"- **定位判断**：{stance}",
        "- 财报前如果再出现明显超额收益扩张，而卖方目标价和一致预期没有同步抬升，通常意味着市场已经开始抢跑。",
        "- 如果实际财报只能满足基准情景而无法带来新的全年上修线索，短期更容易出现“兑现式”波动。",
        "",
        "*数据来源：RQData，置信度5*",
    ]
    return "\n".join(lines)


def build_risk_section(
    target_quarter: str,
    trend: List[Dict[str, Any]],
    forecast: Dict[str, Any],
    price_snapshot: Dict[str, Any],
    research_snapshot: Dict[str, Any],
    web_findings: Sequence[ExternalFinding],
) -> str:
    latest_quarter = trend[-1]["quarter"] if trend else "无数据"
    gap = quarter_distance(latest_quarter, target_quarter)
    margin_field = forecast.get("margin_field", "net_margin")
    margin_label = forecast.get("margin_label", "利润率")
    margin_vol = stdev_or_none(item.get(margin_field) for item in trend[-4:])
    lines = []
    if gap is not None and gap >= 2:
        lines.append(f"- **信息滞后风险**：最新财报仅到 `{latest_quarter}`，距离目标季度 `{target_quarter}` 已有 {gap} 个季度，预测误差会明显放大。")
    if margin_vol is not None and margin_vol > 2.0:
        lines.append(f"- **{margin_label}波动风险**：最近 4 个季度{margin_label}标准差约为 {percent_text(margin_vol, signed=False)}，利润弹性可能高于收入弹性。")
    if price_snapshot.get("realized_vol") is not None and price_snapshot["realized_vol"] > 2.5:
        lines.append(f"- **交易波动风险**：近 20 日日收益波动率约为 {percent_text(price_snapshot['realized_vol'], signed=False)}，财报日放大波动的概率较高。")
    if research_snapshot.get("profit_t_std") is not None and research_snapshot["profit_t_std"] > 0:
        lines.append(f"- **卖方分歧风险**：年度净利润口径离散度约为 {billion_yuan_text(research_snapshot['profit_t_std'])}，说明市场预期并不集中。")
    release_items = pick_web_findings(web_findings, "earnings_release_date")
    if not release_items:
        lines.append(f"- **时间安排未验证风险**：当前未通过网络搜索结果确认 `{target_quarter}` 的预计披露日，财报前节奏判断需保守处理。")
    elif release_items[0].get("confidence", 0) <= 3:
        lines.append("- **预计披露日置信度风险**：当前预计披露日来自较低置信度网络搜索结果，需等待公司或交易所进一步确认。")
    if not lines:
        lines.append("- 当前未识别到突出的新增风险，但仍需重点验证利润率、现金流和市场预期差。")
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_appendix(
    args: argparse.Namespace,
    company: str,
    industry_name: str,
    counts: Dict[str, int],
    earnings_event_context: str,
    web_findings: Sequence[ExternalFinding],
) -> str:
    lines = [
        f"- 报告日期：{args.report_date}；公司：{company}（{args.stock}）；目标季度：{args.quarter}。",
        f"- 行业：{industry_name}；预计披露信息：{earnings_event_context}。",
        f"- 历史趋势采用财务 PIT 数据去重口径，预测与情景分析为分析师估算。",
        "- 卖方预期部分优先采用一致预期、财报点评和公司高相关研报，尽量避免低相关样本干扰。",
        "- 公告章节优先引用正式财报和近期经营沟通中的管理层表述，其他公告保留原文链接供查阅。",
        (
            f"- 本次共纳入 {len(web_findings)} 条网络搜索结果，用于补充预计披露日、电话会或行业动态。"
            if web_findings
            else "- 本次未纳入网络搜索结果，正文不对预计披露日和电话会做确定性表述。"
        ),
        f"- 本次样本载入：财务 {counts['historical_financials']} 条、股价 {counts['price_recent']} 条、研报 {counts['research_reports']} 条、公告 {counts['announcement_raw']} 条、网络搜索结果 {counts['web_search_findings']} 条。",
        "",
        "*数据来源：RQData，置信度5*",
    ]
    return "\n".join(lines)


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
            subprocess.run(["python3", str(repo_renderer), str(md_path), str(html_path)], check=True, capture_output=True, text=True)
            print(f"✅ HTML 报告已生成：{html_path}")
            return html_path
        except subprocess.CalledProcessError as exc:
            print(f"警告：仓库内 report-renderer 渲染失败：{exc}")

    print("警告：未找到可用的 HTML 渲染器，保留 Markdown 输出")
    return None


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()
    report_date = parse_iso_date(args.report_date) or date.today()

    company_records = extract_records(read_json_file(data_dir / "company_info.json"))
    industry_records = extract_records(read_json_file(data_dir / "industry.json"))
    historical_financials = extract_records(read_json_file(data_dir / "historical_financials.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe_history.json"))
    price_recent = extract_records(read_json_file(data_dir / "price_recent.json"))
    hs300_recent = extract_records(read_json_file(data_dir / "hs300_recent.json"))
    consensus_records = extract_records(read_json_file(data_dir / "consensus.json"))
    research_records = extract_records(read_json_file(data_dir / "research_reports.json"))
    announcement_records = extract_records(read_json_file(data_dir / "announcement_raw.json"))
    announcement_extract_records = extract_named_records(read_json_file(data_dir / "announcement_extracts.json"), "records")
    peers_financials = extract_records(read_json_file(data_dir / "peers_financials.json"))
    peers_instruments = extract_records(read_json_file(data_dir / "peers_instruments.json"))
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))
    validate_web_search_records(web_search_records)
    web_findings = extract_web_search_findings(web_search_records, report_date)

    company_name = args.company
    if not company_name:
        for item in company_records:
            if isinstance(item, dict) and normalize_ticker(item) == args.stock:
                company_name = normalize_name(item)
                if company_name:
                    break
    company_name = company_name or args.stock

    industry_name = "无数据"
    for item in industry_records:
        if isinstance(item, dict) and normalize_ticker(item) == args.stock:
            industry_name = str(item.get("first_industry_name") or "无数据")
            break

    trend = build_financial_trend(historical_financials, args.stock, report_date)
    if not trend:
        raise ValueError("historical_financials.json 中缺少可用的历史财务数据。")

    attach_roe(trend, latest_factor_series(roe_records, args.stock, "return_on_equity_weighted_average"))
    forecast = forecast_target_quarter(trend, args.quarter.lower())
    price_snapshot = build_price_snapshot(price_recent, hs300_recent, args.stock)
    consensus_snapshot = build_consensus_snapshot(consensus_records, args.stock)
    aliases = build_company_aliases(company_records, args.stock, company_name)
    research_snapshot = build_research_snapshot(research_records, args.stock, aliases, report_date)
    selected_announcements = select_relevant_announcements(announcement_records, args.stock, report_date, args.quarter.lower())
    peer_snapshot = build_peer_snapshot(peers_financials, peers_instruments)
    scenarios = build_scenarios(forecast, price_snapshot)
    earnings_event_context = build_earnings_event_context(web_findings)

    exec_summary = build_exec_summary(company_name, args.quarter.lower(), industry_name, trend, forecast, price_snapshot, research_snapshot, consensus_snapshot, web_findings)
    historical_trend = build_historical_trend_section(trend)
    forecast_framework = build_forecast_section(forecast, args.quarter.lower())
    expectation_section = build_expectation_section(consensus_snapshot, research_snapshot, peer_snapshot)
    announcement_section = build_announcement_section(selected_announcements, announcement_extract_records, web_findings)
    scenario_section = build_scenario_section(scenarios, forecast.get("margin_label", "利润率"))
    trading_setup = build_trading_setup_section(price_snapshot, research_snapshot, consensus_snapshot, forecast.get("margin_label", "利润率"))
    risk_section = build_risk_section(args.quarter.lower(), trend, forecast, price_snapshot, research_snapshot, web_findings)
    appendix = build_appendix(
        args,
        company_name,
        industry_name,
        {
            "company_info": len(company_records),
            "industry": len(industry_records),
            "historical_financials": len(historical_financials),
            "roe_history": len(roe_records),
            "price_recent": len(price_recent),
            "hs300_recent": len(hs300_recent),
            "consensus": len(consensus_records),
            "research_reports": len(research_records),
            "announcement_raw": len(announcement_records),
            "peers_financials": len(peers_financials),
            "peers_instruments": len(peers_instruments),
            "web_search_findings": len(web_search_records),
        },
        earnings_event_context,
        web_findings,
    )

    template_text = Path(args.template).read_text(encoding="utf-8")
    report_text = render_template(
        template_text,
        {
            "REPORT_DATE": args.report_date,
            "COMPANY_NAME": company_name,
            "STOCK_CODE": args.stock,
            "TARGET_QUARTER": args.quarter.lower(),
            "EARNINGS_EVENT_CONTEXT": earnings_event_context,
            "EXEC_SUMMARY": exec_summary,
            "HISTORICAL_TREND": historical_trend,
            "FORECAST_FRAMEWORK": forecast_framework,
            "EXPECTATION_SECTION": expectation_section,
            "ANNOUNCEMENT_SECTION": announcement_section,
            "SCENARIO_SECTION": scenario_section,
            "TRADING_SETUP": trading_setup,
            "RISK_SECTION": risk_section,
            "APPENDIX": appendix,
        },
    )

    output_path = Path(args.output).expanduser() if args.output else data_dir / f"earnings_preview_{args.stock}_{args.quarter}_{args.report_date}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
