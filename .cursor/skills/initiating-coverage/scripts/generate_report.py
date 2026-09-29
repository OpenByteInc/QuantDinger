#!/usr/bin/env python3
"""Template-driven initiating coverage report generator."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 首次覆盖研究报告",
    "## 执行摘要",
    "## 公司概况与覆盖边界",
    "## 股权结构与治理画像",
    "## 历史财务轨迹",
    "## 盈利质量与现金流",
    "## 市场预期与卖方口径",
    "## 可比公司与估值定位",
    "## 交易表现与股东回报",
    "## 风险提示",
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
    "management_update",
    "industry_context",
    "policy_context",
    "company_news",
    "competition_context",
}

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")


@dataclass
class QuarterSnapshot:
    quarter: str
    info_date: date
    revenue: Optional[float]
    net_profit: Optional[float]
    gross_profit: Optional[float]
    operating_cash: Optional[float]
    investing_cash: Optional[float]
    financing_cash: Optional[float]
    total_assets: Optional[float]
    total_liabilities: Optional[float]
    total_equity: Optional[float]


@dataclass
class QuarterDelta:
    revenue: Optional[float]
    net_profit: Optional[float]
    gross_profit: Optional[float]
    operating_cash: Optional[float]
    investing_cash: Optional[float]
    financing_cash: Optional[float]


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的首次覆盖研究报告")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", help="公司名称，可选")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", default=date.today().isoformat(), help="报告日期 (YYYY-MM-DD)")
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
            return datetime.strptime(text[: len(fmt)], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, "", "null"):
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text[: len(fmt)], fmt)
        except ValueError:
            continue
    return None


def parse_quarter_key(value: str) -> Optional[Tuple[int, int]]:
    match = re.match(r"^(\d{4})q([1-4])$", str(value).strip().lower())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def quarter_sort_key(value: str) -> Tuple[int, int]:
    return parse_quarter_key(value) or (0, 0)


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


def billion_yuan_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def yuan_price_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}元"


def shares_text(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿股"


def percent_text(value: Optional[float], digits: int = 1, signed: bool = True) -> str:
    if value is None:
        return "无数据"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}%"


def x_text(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "无数据"
    return f"{value:.{digits}f}x"


def count_text(value: int) -> str:
    return f"{value}家"


def ratio_or_none(numerator: Optional[float], denominator: Optional[float], multiplier: float = 100.0) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def safe_growth(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return (current / base - 1.0) * 100.0


def cagr(values: Sequence[Tuple[int, Optional[float]]]) -> Optional[float]:
    usable = [(year, value) for year, value in values if value not in (None, 0)]
    if len(usable) < 2:
        return None
    start_year, start_value = usable[0]
    end_year, end_value = usable[-1]
    periods = end_year - start_year
    if periods <= 0 or start_value in (None, 0) or end_value is None:
        return None
    return ((end_value / start_value) ** (1 / periods) - 1.0) * 100.0


def median_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return float(median(valid))


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def shorten_text(text: Any, limit: int = 90) -> str:
    if text in (None, "", "null"):
        return "无摘要"
    value = re.sub(r"\s+", " ", str(text)).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def clean_summary(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


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


def format_web_context_lines(records: Sequence[Any], limit: int = 4) -> List[str]:
    ranked: List[Dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        ranked.append(
            {
                "published_at": parse_iso_date(item.get("published_at")),
                "source_name": str(item.get("source_name") or "网络搜索来源").strip(),
                "title": clean_summary(item.get("title")),
                "summary": clean_summary(item.get("summary")),
                "why_relevant": clean_summary(item.get("why_relevant")),
                "confidence": int(float_or_none(item.get("confidence")) or 0),
            }
        )
    ranked.sort(key=lambda item: (item["published_at"] or date.min, item["confidence"]), reverse=True)
    lines: List[str] = []
    for item in ranked[:limit]:
        published = item["published_at"].isoformat() if item["published_at"] else "日期未披露"
        summary = item["summary"].rstrip("。；;!！?？")
        relevance = item["why_relevant"].rstrip("。；;!！?？")
        lines.append(
            f"- `{published}` {item['source_name']}：{item['title']}。{summary}。与本次覆盖的关系：{relevance}。"
            f" *数据来源：{item['source_name']}，置信度{item['confidence']}*"
        )
    return lines


def is_company_report_source(record: Dict[str, Any]) -> bool:
    data_source = float_or_none(record.get("data_source"))
    if data_source is None:
        return True
    return abs(data_source) < 1e-9


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


def dedupe_financial_records(records: List[Any], stock_ids: Sequence[str], report_date: date) -> List[Dict[str, Any]]:
    stock_set = set(stock_ids)
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        quarter = str(item.get("quarter") or "").lower()
        info_date = parse_iso_date(item.get("info_date"))
        if stock not in stock_set or not parse_quarter_key(quarter) or info_date is None or info_date > report_date:
            continue
        key = (stock, quarter)
        current = deduped.get(key)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        if current is None or (current_date is None or info_date >= current_date):
            deduped[key] = item
    return sorted(deduped.values(), key=lambda item: (normalize_ticker(item), quarter_sort_key(str(item.get("quarter") or "").lower())))


def build_snapshot(record: Dict[str, Any]) -> QuarterSnapshot:
    return QuarterSnapshot(
        quarter=str(record.get("quarter") or "").lower(),
        info_date=parse_iso_date(record.get("info_date")) or date.min,
        revenue=float_or_none(record.get("revenue")),
        net_profit=float_or_none(record.get("net_profit")),
        gross_profit=float_or_none(record.get("gross_profit")),
        operating_cash=float_or_none(record.get("cash_from_operating_activities")),
        investing_cash=float_or_none(record.get("cash_flow_from_investing_activities")),
        financing_cash=float_or_none(record.get("cash_flow_from_financing_activities")),
        total_assets=float_or_none(record.get("total_assets")),
        total_liabilities=float_or_none(record.get("total_liabilities")),
        total_equity=float_or_none(record.get("total_equity")),
    )


def to_single_quarter(snapshot: QuarterSnapshot, prev_snapshot: Optional[QuarterSnapshot]) -> QuarterDelta:
    key = parse_quarter_key(snapshot.quarter)
    if key is None or key[1] == 1 or prev_snapshot is None:
        return QuarterDelta(
            revenue=snapshot.revenue,
            net_profit=snapshot.net_profit,
            gross_profit=snapshot.gross_profit,
            operating_cash=snapshot.operating_cash,
            investing_cash=snapshot.investing_cash,
            financing_cash=snapshot.financing_cash,
        )
    return QuarterDelta(
        revenue=(snapshot.revenue - prev_snapshot.revenue) if snapshot.revenue is not None and prev_snapshot.revenue is not None else None,
        net_profit=(snapshot.net_profit - prev_snapshot.net_profit) if snapshot.net_profit is not None and prev_snapshot.net_profit is not None else None,
        gross_profit=(snapshot.gross_profit - prev_snapshot.gross_profit) if snapshot.gross_profit is not None and prev_snapshot.gross_profit is not None else None,
        operating_cash=(snapshot.operating_cash - prev_snapshot.operating_cash) if snapshot.operating_cash is not None and prev_snapshot.operating_cash is not None else None,
        investing_cash=(snapshot.investing_cash - prev_snapshot.investing_cash) if snapshot.investing_cash is not None and prev_snapshot.investing_cash is not None else None,
        financing_cash=(snapshot.financing_cash - prev_snapshot.financing_cash) if snapshot.financing_cash is not None and prev_snapshot.financing_cash is not None else None,
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


def latest_factor_map(records: List[Any], stock_ids: Sequence[str], field_name: str, report_date: date) -> Dict[str, float]:
    result = {}
    for stock in stock_ids:
        value = latest_factor_value(records, stock, field_name, report_date)
        if value is not None:
            result[stock] = value
    return result


def build_close_points(records: List[Any], stock: str) -> List[Tuple[date, float, Optional[float]]]:
    points = []
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        turnover = float_or_none(item.get("total_turnover"))
        if event_date is None or close is None:
            continue
        points.append((event_date, close, turnover))
    points.sort(key=lambda pair: pair[0])
    return points


def build_index_points(records: List[Any]) -> List[Tuple[date, float]]:
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


def build_turnover_points(records: List[Any], stock: str) -> List[Tuple[date, Optional[float], Optional[float], Optional[float]]]:
    points = []
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("tradedate") or item.get("date"))
        if event_date is None:
            continue
        points.append(
            (
                event_date,
                float_or_none(item.get("today")),
                float_or_none(item.get("week")),
                float_or_none(item.get("month")),
            )
        )
    points.sort(key=lambda pair: pair[0])
    return points


def nearest_price_at_or_before(points: Sequence[Tuple[date, float]], target: date) -> Optional[Tuple[date, float]]:
    chosen = None
    for point_date, close in points:
        if point_date <= target:
            chosen = (point_date, close)
        else:
            break
    return chosen


def trailing_return(points: Sequence[Tuple[date, float]], end_date: date, lookback_days: int) -> Optional[float]:
    if not points:
        return None
    end_point = nearest_price_at_or_before(points, end_date)
    start_point = nearest_price_at_or_before(points, end_date - timedelta(days=lookback_days))
    if end_point is None or start_point is None or start_point[1] == 0:
        return None
    return (end_point[1] / start_point[1] - 1.0) * 100.0


def trailing_turnover_stats(points: Sequence[Tuple[date, Optional[float], Optional[float], Optional[float]]], end_date: date) -> Dict[str, Optional[float]]:
    filtered = [point for point in points if point[0] <= end_date and point[1] is not None]
    if not filtered:
        return {"latest": None, "median_20": None, "median_60": None}
    latest = filtered[-1][1]
    values = [point[1] for point in filtered if point[1] is not None]
    return {
        "latest": latest,
        "median_20": float(median(values[-20:])) if len(values) >= 1 else None,
        "median_60": float(median(values[-60:])) if len(values) >= 1 else None,
    }


def latest_share_row(records: List[Any], stock: str, report_date: date) -> Optional[Dict[str, Any]]:
    best = None
    best_date = None
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        event_date = parse_iso_date(item.get("date"))
        if event_date is None or event_date > report_date:
            continue
        if best_date is None or event_date >= best_date:
            best = item
            best_date = event_date
    return best


def latest_top10(records: List[Any], stock: str, report_date: date) -> List[Dict[str, Any]]:
    latest_key: Optional[Tuple[date, date]] = None
    rows: List[Dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        end_date = parse_iso_date(item.get("end_date"))
        info_date = parse_iso_date(item.get("info_date"))
        if end_date is None or info_date is None or info_date > report_date:
            continue
        key = (end_date, info_date)
        if latest_key is None or key > latest_key:
            latest_key = key
            rows = [item]
        elif key == latest_key:
            rows.append(item)
    rows.sort(key=lambda item: int(float_or_none(item.get("rank")) or 999))
    return rows


def choose_latest_record_by_cutoff(records: List[Any], cutoff: date) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[datetime, Dict[str, Any]]] = None
    for item in records:
        if not isinstance(item, dict):
            continue
        event_dt = parse_timestamp(item.get("create_tm") or item.get("date"))
        event_date = parse_iso_date(item.get("date") or item.get("create_tm"))
        if event_dt is None or event_date is None or event_date > cutoff:
            continue
        if best is None or event_dt >= best[0]:
            best = (event_dt, item)
    return best[1] if best else None


def consensus_year_map(record: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Optional[float]]]:
    if not record:
        return {}
    base_year = int(str(record.get("report_year_t") or date.today().year))
    year_map: Dict[int, Dict[str, Optional[float]]] = {}
    for field_prefix, key in (
        ("comp_con_operating_revenue", "revenue"),
        ("comp_con_net_profit", "profit"),
        ("comp_con_eps", "eps"),
    ):
        for suffix, year in (("t", base_year), ("t1", base_year + 1), ("t2", base_year + 2), ("t3", base_year + 3)):
            field_name = f"{field_prefix}_{suffix}"
            if field_name not in record:
                continue
            year_map.setdefault(year, {})[key] = float_or_none(record.get(field_name))
    if "con_targ_price" in record:
        year_map.setdefault(base_year, {})["target_price"] = float_or_none(record.get("con_targ_price"))
    return year_map


def company_aliases(info_record: Optional[Dict[str, Any]], stock: str) -> List[str]:
    aliases = [stock, stock.split(".")[0]]
    if info_record:
        symbol = str(info_record.get("symbol") or "").strip()
        abbrev = str(info_record.get("abbrev_symbol") or "").strip()
        if symbol:
            aliases.extend([symbol, symbol.replace("股份", ""), symbol.replace("有限公司", "")])
        if abbrev:
            aliases.append(abbrev)
    result: List[str] = []
    seen = set()
    for alias in aliases:
        value = alias.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def filter_related_reports(records: List[Any], aliases: Sequence[str], report_date: date) -> List[Dict[str, Any]]:
    lower_aliases = [alias.lower() for alias in aliases]
    stock_code = aliases[1].lower() if len(aliases) > 1 else ""
    related = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if not is_company_report_source(item):
            continue
        create_date = parse_iso_date(item.get("create_tm") or item.get("date"))
        if create_date is None or create_date > report_date or create_date < report_date - timedelta(days=210):
            continue
        title = str(item.get("report_title") or "")
        summary = str(item.get("summary") or "")
        report_main_id = str(item.get("report_main_id") or "").lower()
        title_lower = title.lower()
        summary_lower = summary.lower()
        title_hit = any(alias in title_lower for alias in lower_aliases if len(alias) >= 2)
        summary_hit = any(alias in summary_lower for alias in lower_aliases if len(alias) >= 4)
        if title_hit or (stock_code and report_main_id == stock_code) or (summary_hit and any(token in title_lower for token in ("点评", "coverage", "initiation", "深度", "年报", "季报"))):
            display_summary = extract_research_display_summary(item)
            if not display_summary:
                continue
            related.append({**item, "_display_summary": display_summary})
    related.sort(key=lambda item: str(item.get("create_tm") or item.get("date") or ""), reverse=True)
    return related


def latest_by_stock(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in records:
        stock = normalize_ticker(item)
        current = result.get(stock)
        current_key = quarter_sort_key(str(current.get("quarter") or "").lower()) if current else (0, 0)
        item_key = quarter_sort_key(str(item.get("quarter") or "").lower())
        if current is None or item_key >= current_key:
            result[stock] = item
    return result


def dominant_quarter(latest_records: Dict[str, Dict[str, Any]]) -> Optional[str]:
    counts = Counter(str(item.get("quarter") or "").lower() for item in latest_records.values() if item.get("quarter"))
    return counts.most_common(1)[0][0] if counts else None


def build_name_map(records: List[Any]) -> Dict[str, str]:
    result = {}
    for item in records:
        if isinstance(item, dict):
            stock = normalize_ticker(item)
            if stock:
                result[stock] = normalize_name(item) or stock
    return result


def build_industry_map(records: List[Any]) -> Dict[str, Dict[str, str]]:
    result = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock:
            result[stock] = {
                "first": str(item.get("first_industry_name") or "无数据"),
                "second": str(item.get("second_industry_name") or "无数据"),
                "third": str(item.get("third_industry_name") or "无数据"),
            }
    return result


def build_peer_rows(
    stock_ids: Sequence[str],
    instrument_records: List[Any],
    industry_records: List[Any],
    financial_records: List[Any],
    roe_records: List[Any],
    market_cap_records: List[Any],
    pe_records: List[Any],
    pb_records: List[Any],
    dividend_records: List[Any],
    report_date: date,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    names = build_name_map(instrument_records)
    industries = build_industry_map(industry_records)
    financial_deduped = dedupe_financial_records(financial_records, stock_ids, report_date)
    latest_records = latest_by_stock(financial_deduped)
    comparison_quarter = dominant_quarter(latest_records)
    yoy_q = yoy_quarter(comparison_quarter or "")
    by_stock_quarter = {(normalize_ticker(item), str(item.get("quarter") or "").lower()): item for item in financial_deduped}

    roe_map = latest_factor_map(roe_records, stock_ids, "return_on_equity_weighted_average", report_date)
    market_cap_map = latest_factor_map(market_cap_records, stock_ids, "market_cap", report_date)
    pe_map = latest_factor_map(pe_records, stock_ids, "pe_ratio", report_date)
    pb_map = latest_factor_map(pb_records, stock_ids, "pb_ratio", report_date)
    dividend_map = latest_factor_map(dividend_records, stock_ids, "dividend_yield", report_date)

    rows: List[Dict[str, Any]] = []
    for stock in stock_ids:
        latest_record = latest_records.get(stock)
        if latest_record is None:
            continue
        latest_quarter = str(latest_record.get("quarter") or "").lower()
        if comparison_quarter and latest_quarter != comparison_quarter:
            continue
        yoy_record = by_stock_quarter.get((stock, yoy_q or ""))
        revenue = float_or_none(latest_record.get("revenue"))
        profit = float_or_none(latest_record.get("net_profit"))
        gross_profit = float_or_none(latest_record.get("gross_profit"))
        assets = float_or_none(latest_record.get("total_assets"))
        liabilities = float_or_none(latest_record.get("total_liabilities"))
        gross_margin = ratio_or_none(gross_profit, revenue)
        debt_ratio = ratio_or_none(liabilities, assets)
        rows.append(
            {
                "stock": stock,
                "name": names.get(stock, stock),
                "industry": industries.get(stock, {}),
                "quarter": latest_quarter,
                "revenue": revenue,
                "profit": profit,
                "revenue_yoy": safe_growth(revenue, float_or_none(yoy_record.get("revenue")) if yoy_record else None),
                "profit_yoy": safe_growth(profit, float_or_none(yoy_record.get("net_profit")) if yoy_record else None),
                "gross_margin": gross_margin,
                "debt_ratio": debt_ratio,
                "roe": roe_map.get(stock),
                "market_cap": market_cap_map.get(stock),
                "pe": pe_map.get(stock),
                "pb": pb_map.get(stock),
                "dividend_yield": dividend_map.get(stock),
            }
        )
    rows.sort(key=lambda item: (item.get("market_cap") is None, -(item.get("market_cap") or 0.0)))
    return rows, comparison_quarter


def yearly_dividend_rows(records: List[Any], stock: str, report_date: date, limit: int = 5) -> List[Dict[str, Any]]:
    grouped: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"cash": 0.0, "events": 0, "round_lot": None, "latest_declaration": None})
    for item in records:
        if not isinstance(item, dict) or normalize_ticker(item) != stock:
            continue
        declaration_date = parse_iso_date(item.get("declaration_announcement_date") or item.get("ex_dividend_date"))
        if declaration_date is None or declaration_date > report_date:
            continue
        year = declaration_date.year
        grouped[year]["cash"] += float_or_none(item.get("dividend_cash_before_tax")) or 0.0
        grouped[year]["events"] += 1
        grouped[year]["round_lot"] = item.get("round_lot") or grouped[year]["round_lot"]
        if grouped[year]["latest_declaration"] is None or declaration_date > grouped[year]["latest_declaration"]:
            grouped[year]["latest_declaration"] = declaration_date
    rows = []
    for year in sorted(grouped.keys(), reverse=True)[:limit]:
        payload = grouped[year]
        rows.append(
            {
                "year": year,
                "cash": payload["cash"],
                "events": payload["events"],
                "round_lot": payload["round_lot"],
                "latest_declaration": payload["latest_declaration"],
            }
        )
    return rows


def validate_dataset(
    latest_snapshot: Optional[QuarterSnapshot],
    price_points: Sequence[Tuple[date, float, Optional[float]]],
    consensus_records: List[Any],
    peer_rows: Sequence[Dict[str, Any]],
) -> None:
    issues = []
    if latest_snapshot is None:
        issues.append("未识别到最新财报季度")
    if len(price_points) < 120:
        issues.append("价格历史覆盖不足")
    if not consensus_records:
        issues.append("一致预期数据为空")
    if len(peer_rows) < 3:
        issues.append("可比公司覆盖不足")
    if issues:
        raise ValueError("数据质量校验失败：" + "；".join(issues))


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
            print(f"警告：仓库内 render_report.py 执行失败：{exc}")

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
    share_records = extract_records(read_json_file(data_dir / "shares.json"))
    shareholder_records = extract_records(read_json_file(data_dir / "shareholder_top10.json"))
    financial_records = extract_records(read_json_file(data_dir / "historical_financials.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe_history.json"))
    market_cap_records = extract_records(read_json_file(data_dir / "market_cap.json"))
    pe_records = extract_records(read_json_file(data_dir / "pe_ratio.json"))
    pb_records = extract_records(read_json_file(data_dir / "pb_ratio.json"))
    dividend_yield_records = extract_records(read_json_file(data_dir / "dividend_yield.json"))
    price_records = extract_records(read_json_file(data_dir / "price_history.json"))
    turnover_records = extract_records(read_json_file(data_dir / "turnover_history.json"))
    benchmark_records = extract_records(read_json_file(data_dir / "benchmark_price.json"))
    dividend_history_records = extract_records(read_json_file(data_dir / "dividend_history.json"))
    consensus_records = extract_records(read_json_file(data_dir / "consensus.json"))
    research_records = extract_records(read_json_file(data_dir / "research_reports.json"))
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))
    peer_pool_records = extract_records(read_json_file(data_dir / "peer_pool.json"))
    peer_company_records = extract_records(read_json_file(data_dir / "peer_company_info.json"))
    peer_industry_records = extract_records(read_json_file(data_dir / "peer_industry.json"))
    peer_financial_records = extract_records(read_json_file(data_dir / "peer_latest_financials.json"))
    peer_roe_records = extract_records(read_json_file(data_dir / "peer_roe.json"))
    peer_market_cap_records = extract_records(read_json_file(data_dir / "peer_market_cap.json"))
    peer_pe_records = extract_records(read_json_file(data_dir / "peer_pe_ratio.json"))
    peer_pb_records = extract_records(read_json_file(data_dir / "peer_pb_ratio.json"))
    peer_dividend_records = extract_records(read_json_file(data_dir / "peer_dividend_yield.json"))

    company_info = next((item for item in company_records if isinstance(item, dict) and normalize_ticker(item) == args.stock), None)
    company_name = args.company or normalize_name(company_info or {}) or args.stock
    industry_info = next((item for item in industry_records if isinstance(item, dict) and normalize_ticker(item) == args.stock), None)

    deduped_financials = dedupe_financial_records(financial_records, [args.stock], report_date)
    snapshots = [build_snapshot(item) for item in deduped_financials]
    latest_snapshot = snapshots[-1] if snapshots else None
    snapshot_map = {snapshot.quarter: snapshot for snapshot in snapshots}
    prev_snapshot = snapshot_map.get(previous_quarter(latest_snapshot.quarter) or "") if latest_snapshot else None
    yoy_snapshot = snapshot_map.get(yoy_quarter(latest_snapshot.quarter) or "") if latest_snapshot else None
    yoy_prev_snapshot = snapshot_map.get(previous_quarter(yoy_snapshot.quarter) or "") if yoy_snapshot else None

    price_points_raw = build_close_points(price_records, args.stock)
    price_points = [(point_date, close) for point_date, close, _ in price_points_raw]
    benchmark_points = build_index_points(benchmark_records)
    turnover_points = build_turnover_points(turnover_records, args.stock)

    peer_ids = []
    seen_peer_ids = set()
    for item in peer_pool_records:
        if isinstance(item, str):
            stock = item.strip()
        elif isinstance(item, dict):
            stock = normalize_ticker(item)
        else:
            continue
        if stock and stock not in seen_peer_ids:
            seen_peer_ids.add(stock)
            peer_ids.append(stock)
    if args.stock not in seen_peer_ids:
        peer_ids.insert(0, args.stock)

    peer_rows, comparison_quarter = build_peer_rows(
        peer_ids,
        peer_company_records,
        peer_industry_records,
        peer_financial_records,
        peer_roe_records,
        peer_market_cap_records,
        peer_pe_records,
        peer_pb_records,
        peer_dividend_records,
        report_date,
    )

    validate_web_search_records(web_search_records)
    validate_dataset(latest_snapshot, price_points_raw, consensus_records, peer_rows)
    assert latest_snapshot is not None

    latest_single = to_single_quarter(latest_snapshot, prev_snapshot)
    yoy_single = to_single_quarter(yoy_snapshot, yoy_prev_snapshot) if yoy_snapshot else QuarterDelta(None, None, None, None, None, None)
    latest_market_cap = latest_factor_value(market_cap_records, args.stock, "market_cap", report_date)
    latest_pe = latest_factor_value(pe_records, args.stock, "pe_ratio", report_date)
    latest_pb = latest_factor_value(pb_records, args.stock, "pb_ratio", report_date)
    latest_dividend_yield = latest_factor_value(dividend_yield_records, args.stock, "dividend_yield", report_date)
    latest_roe = latest_factor_value(roe_records, args.stock, "return_on_equity_weighted_average", report_date)
    latest_share = latest_share_row(share_records, args.stock, report_date)
    latest_top10_rows = latest_top10(shareholder_records, args.stock, report_date)
    aliases = company_aliases(company_info, args.stock)
    related_reports = filter_related_reports(research_records, aliases, report_date)

    consensus_latest = choose_latest_record_by_cutoff(consensus_records, report_date)
    consensus_prior = choose_latest_record_by_cutoff(consensus_records, report_date - timedelta(days=60))
    consensus_latest_map = consensus_year_map(consensus_latest)
    consensus_prior_map = consensus_year_map(consensus_prior)
    forecast_years = sorted(
        year for year, payload in consensus_latest_map.items() if any(payload.get(key) is not None for key in ("revenue", "profit", "eps"))
    )[:2]
    if not forecast_years:
        forecast_years = sorted(consensus_prior_map.keys())[:2]

    same_quarter_history = []
    latest_quarter_key = parse_quarter_key(latest_snapshot.quarter)
    if latest_quarter_key:
        target_q = latest_quarter_key[1]
        for snapshot in snapshots:
            key = parse_quarter_key(snapshot.quarter)
            if key and key[1] == target_q:
                same_quarter_history.append(snapshot)
    same_quarter_history = same_quarter_history[-5:]

    revenue_cagr = cagr([(int(snapshot.quarter[:4]), snapshot.revenue) for snapshot in same_quarter_history])
    profit_cagr = cagr([(int(snapshot.quarter[:4]), snapshot.net_profit) for snapshot in same_quarter_history])
    gross_margin = ratio_or_none(latest_snapshot.gross_profit, latest_snapshot.revenue)
    yoy_gross_margin = ratio_or_none(yoy_snapshot.gross_profit, yoy_snapshot.revenue) if yoy_snapshot else None
    latest_single_gross_margin = ratio_or_none(latest_single.gross_profit, latest_single.revenue)
    yoy_single_gross_margin = ratio_or_none(yoy_single.gross_profit, yoy_single.revenue)
    debt_ratio = ratio_or_none(latest_snapshot.total_liabilities, latest_snapshot.total_assets)
    yoy_debt_ratio = ratio_or_none(yoy_snapshot.total_liabilities, yoy_snapshot.total_assets) if yoy_snapshot else None
    cash_conversion = ratio_or_none(latest_snapshot.operating_cash, latest_snapshot.net_profit, 1.0)
    single_cash_conversion = ratio_or_none(latest_single.operating_cash, latest_single.net_profit, 1.0)
    revenue_yoy = safe_growth(latest_snapshot.revenue, yoy_snapshot.revenue if yoy_snapshot else None)
    profit_yoy = safe_growth(latest_snapshot.net_profit, yoy_snapshot.net_profit if yoy_snapshot else None)
    revenue_qoq = safe_growth(latest_snapshot.revenue, prev_snapshot.revenue if prev_snapshot else None)
    profit_qoq = safe_growth(latest_snapshot.net_profit, prev_snapshot.net_profit if prev_snapshot else None)
    single_revenue_yoy = safe_growth(latest_single.revenue, yoy_single.revenue)
    single_profit_yoy = safe_growth(latest_single.net_profit, yoy_single.net_profit)

    close_price = price_points[-1][1] if price_points else None
    return_windows = [
        ("1M", 30),
        ("3M", 90),
        ("6M", 180),
        ("1Y", 365),
        ("3Y", 365 * 3),
    ]
    stock_returns = {label: trailing_return(price_points, report_date, days) for label, days in return_windows}
    benchmark_returns = {label: trailing_return(benchmark_points, report_date, days) for label, days in return_windows}
    excess_returns = {
        label: (stock_returns[label] - benchmark_returns[label])
        if stock_returns[label] is not None and benchmark_returns[label] is not None
        else None
        for label, _ in return_windows
    }
    turnover_stats = trailing_turnover_stats(turnover_points, report_date)

    top10_total = sum(float_or_none(item.get("hold_percent_total")) or 0.0 for item in latest_top10_rows)
    top3_total = sum(float_or_none(item.get("hold_percent_total")) or 0.0 for item in latest_top10_rows[:3])
    top1_total = float_or_none(latest_top10_rows[0].get("hold_percent_total")) if latest_top10_rows else None
    total_shares = float_or_none((latest_share or {}).get("total"))
    circulation_a = float_or_none((latest_share or {}).get("circulation_a"))
    free_circulation = float_or_none((latest_share or {}).get("free_circulation"))
    free_float_ratio = ratio_or_none(free_circulation, total_shares)
    circulation_ratio = ratio_or_none(circulation_a, total_shares)

    peer_market_cap_median = median_or_none(row.get("market_cap") for row in peer_rows)
    peer_pe_median = median_or_none(row.get("pe") for row in peer_rows)
    peer_pb_median = median_or_none(row.get("pb") for row in peer_rows)
    peer_dividend_median = median_or_none(row.get("dividend_yield") for row in peer_rows)
    peer_roe_median = median_or_none(row.get("roe") for row in peer_rows)
    target_peer_row = next((row for row in peer_rows if row["stock"] == args.stock), None)

    latest_target_price = float_or_none((consensus_latest or {}).get("con_targ_price"))
    prior_target_price = float_or_none((consensus_prior or {}).get("con_targ_price"))
    target_price_delta = safe_growth(latest_target_price, prior_target_price)
    target_upside = safe_growth(latest_target_price, close_price)

    same_quarter_rows = []
    for snapshot in same_quarter_history:
        roe_point = latest_factor_value(roe_records, args.stock, "return_on_equity_weighted_average", snapshot.info_date)
        same_quarter_rows.append(
            [
                snapshot.quarter,
                snapshot.info_date.isoformat(),
                billion_yuan_text(snapshot.revenue),
                billion_yuan_text(snapshot.net_profit),
                percent_text(ratio_or_none(snapshot.gross_profit, snapshot.revenue), signed=False),
                percent_text(ratio_or_none(snapshot.total_liabilities, snapshot.total_assets), signed=False),
                percent_text(roe_point, signed=False),
                billion_yuan_text(snapshot.operating_cash),
            ]
        )

    latest_compare_rows = [
        [
            "累计口径",
            latest_snapshot.quarter,
            billion_yuan_text(latest_snapshot.revenue),
            billion_yuan_text(latest_snapshot.net_profit),
            percent_text(revenue_yoy),
            percent_text(profit_yoy),
            percent_text(gross_margin, signed=False),
            x_text(cash_conversion),
        ],
        [
            "单季度口径",
            latest_snapshot.quarter,
            billion_yuan_text(latest_single.revenue),
            billion_yuan_text(latest_single.net_profit),
            percent_text(single_revenue_yoy),
            percent_text(single_profit_yoy),
            percent_text(latest_single_gross_margin, signed=False),
            x_text(single_cash_conversion),
        ],
    ]

    ownership_rows = [
        [
            str(item.get("rank") or "无数据"),
            str(item.get("shareholder_name") or "无数据"),
            str(item.get("shareholder_kind") or "无数据"),
            percent_text(float_or_none(item.get("hold_percent_total")), signed=False),
            percent_text(float_or_none(item.get("hold_percent_float")), signed=False),
        ]
        for item in latest_top10_rows[:10]
    ]

    consensus_table_rows = []
    for year in forecast_years:
        latest_payload = consensus_latest_map.get(year, {})
        prior_payload = consensus_prior_map.get(year, {})
        consensus_table_rows.append(
            [
                str(year),
                billion_yuan_text(latest_payload.get("revenue")),
                percent_text(safe_growth(latest_payload.get("revenue"), prior_payload.get("revenue"))),
                billion_yuan_text(latest_payload.get("profit")),
                percent_text(safe_growth(latest_payload.get("profit"), prior_payload.get("profit"))),
                str(latest_payload.get("eps")) if latest_payload.get("eps") is not None else "无数据",
            ]
        )

    report_rows = []
    for item in related_reports[:6]:
        report_rows.append(
            [
                str(parse_iso_date(item.get("create_tm") or item.get("date")) or "无数据"),
                str(item.get("institute") or "无数据"),
                shorten_text(item.get("report_title"), 38),
                yuan_price_text(float_or_none(item.get("targ_price"))),
                billion_yuan_text(float_or_none(item.get("net_profit_t"))),
                shorten_text(item.get("_display_summary"), 72),
            ]
        )

    peer_table_rows = []
    for row in peer_rows[:8]:
        peer_table_rows.append(
            [
                row["name"],
                row["stock"],
                row.get("quarter") or "无数据",
                billion_yuan_text(row.get("market_cap")),
                billion_yuan_text(row.get("revenue")),
                billion_yuan_text(row.get("profit")),
                percent_text(row.get("gross_margin"), signed=False),
                percent_text(row.get("roe"), signed=False),
                x_text(row.get("pe")),
                x_text(row.get("pb")),
                percent_text(row.get("dividend_yield"), signed=False),
            ]
        )

    performance_rows = []
    for label, _ in return_windows:
        performance_rows.append(
            [
                label,
                percent_text(stock_returns[label]),
                percent_text(benchmark_returns[label]),
                percent_text(excess_returns[label]),
            ]
        )

    dividend_rows = []
    for row in yearly_dividend_rows(dividend_history_records, args.stock, report_date, limit=5):
        round_lot = str(row["round_lot"] or "10")
        dividend_rows.append(
            [
                str(row["year"]),
                f"{row['cash']:.2f} 元 / 每{round_lot}股",
                str(row["events"]),
                row["latest_declaration"].isoformat() if row["latest_declaration"] else "无数据",
            ]
        )

    peer_rank_by_market_cap = next((idx + 1 for idx, row in enumerate(peer_rows) if row["stock"] == args.stock), None)
    peer_rank_by_pe = next(
        (idx + 1 for idx, row in enumerate(sorted(peer_rows, key=lambda item: (item.get("pe") is None, -(item.get("pe") or 0.0)))) if row["stock"] == args.stock),
        None,
    )
    peer_rank_by_roe = next(
        (idx + 1 for idx, row in enumerate(sorted(peer_rows, key=lambda item: (item.get("roe") is None, -(item.get("roe") or 0.0)))) if row["stock"] == args.stock),
        None,
    )

    risk_rows = [
        [
            "净利润一致预期 60 天变化",
            percent_text(safe_growth(
                consensus_latest_map.get(forecast_years[0], {}).get("profit") if forecast_years else None,
                consensus_prior_map.get(forecast_years[0], {}).get("profit") if forecast_years else None,
            )),
            "-5%",
            "关注" if (forecast_years and safe_growth(
                consensus_latest_map.get(forecast_years[0], {}).get("profit"),
                consensus_prior_map.get(forecast_years[0], {}).get("profit"),
            ) or 0.0) <= -5 else "正常",
        ],
        [
            "经营现金流 / 净利润",
            x_text(cash_conversion),
            "<0.8x",
            "关注" if cash_conversion is not None and cash_conversion < 0.8 else "正常",
        ],
        [
            "资产负债率同比变化",
            percent_text((debt_ratio - yoy_debt_ratio) if debt_ratio is not None and yoy_debt_ratio is not None else None),
            ">+5pct",
            "关注" if debt_ratio is not None and yoy_debt_ratio is not None and debt_ratio - yoy_debt_ratio > 5 else "正常",
        ],
        [
            "PE 相对 peer 中位数",
            percent_text(safe_growth(latest_pe, peer_pe_median)),
            ">+20%",
            "关注" if latest_pe is not None and peer_pe_median not in (None, 0) and latest_pe > peer_pe_median * 1.2 else "正常",
        ],
        [
            "1Y 相对基准超额收益",
            percent_text(excess_returns["1Y"]),
            "<-10%",
            "关注" if excess_returns["1Y"] is not None and excess_returns["1Y"] < -10 else "正常",
        ],
        [
            "第一大股东持股",
            percent_text(top1_total, signed=False),
            ">50%",
            "关注" if top1_total is not None and top1_total > 50 else "正常",
        ],
    ]

    ownership_highlights = []
    for item in latest_top10_rows[:10]:
        ownership_highlights.append(
            f"- 第 {str(item.get('rank') or '无数据')} 大股东为 `{str(item.get('shareholder_name') or '无数据')}`，股东类别 `{str(item.get('shareholder_kind') or '无数据')}`，占总股本约 {percent_text(float_or_none(item.get('hold_percent_total')), signed=False)}。"
        )

    history_fact_lines = []
    for snapshot in same_quarter_history:
        history_fact_lines.append(
            f"- `{snapshot.quarter}`：收入 {billion_yuan_text(snapshot.revenue)}，净利润 {billion_yuan_text(snapshot.net_profit)}，毛利率 {percent_text(ratio_or_none(snapshot.gross_profit, snapshot.revenue), signed=False)}，经营现金流 {billion_yuan_text(snapshot.operating_cash)}。"
        )

    quarter_fact_lines = []
    for snapshot in snapshots[-8:]:
        prev_for_snapshot = snapshot_map.get(previous_quarter(snapshot.quarter) or "")
        yoy_for_snapshot = snapshot_map.get(yoy_quarter(snapshot.quarter) or "")
        single_snapshot = to_single_quarter(snapshot, prev_for_snapshot)
        quarter_fact_lines.append(
            f"- `{snapshot.quarter}` 累计收入 / 净利润为 {billion_yuan_text(snapshot.revenue)} / {billion_yuan_text(snapshot.net_profit)}，单季度收入 / 净利润约为 {billion_yuan_text(single_snapshot.revenue)} / {billion_yuan_text(single_snapshot.net_profit)}，累计同比约 {percent_text(safe_growth(snapshot.revenue, yoy_for_snapshot.revenue if yoy_for_snapshot else None))} / {percent_text(safe_growth(snapshot.net_profit, yoy_for_snapshot.net_profit if yoy_for_snapshot else None))}。"
        )

    expectation_fact_lines = []
    for year in forecast_years:
        latest_payload = consensus_latest_map.get(year, {})
        prior_payload = consensus_prior_map.get(year, {})
        expectation_fact_lines.append(
            f"- `{year}` 年一致预期收入 / 净利润 / EPS 分别为 {billion_yuan_text(latest_payload.get('revenue'))} / {billion_yuan_text(latest_payload.get('profit'))} / {str(latest_payload.get('eps')) if latest_payload.get('eps') is not None else '无数据'}，较 60 天前变化约 {percent_text(safe_growth(latest_payload.get('revenue'), prior_payload.get('revenue')))} / {percent_text(safe_growth(latest_payload.get('profit'), prior_payload.get('profit')))}。"
        )

    report_fact_lines = []
    for item in related_reports[:6]:
        report_fact_lines.append(
            f"- `{str(parse_iso_date(item.get('create_tm') or item.get('date')) or '无数据')}` `{str(item.get('institute') or '无数据')}` 发布 `{shorten_text(item.get('report_title'), 42)}`，目标价 {yuan_price_text(float_or_none(item.get('targ_price')))}，`net_profit_t` {billion_yuan_text(float_or_none(item.get('net_profit_t')))}，核心观点 `{shorten_text(item.get('_display_summary'), 140)}`。"
        )

    peer_fact_lines = []
    for row in peer_rows[:6]:
        peer_fact_lines.append(
            f"- `{row['name']}`（`{row['stock']}`）最新可比季度 `{row.get('quarter') or '无数据'}`，市值 {billion_yuan_text(row.get('market_cap'))}，收入 / 净利润 {billion_yuan_text(row.get('revenue'))} / {billion_yuan_text(row.get('profit'))}，ROE {percent_text(row.get('roe'), signed=False)}，PE / PB {x_text(row.get('pe'))} / {x_text(row.get('pb'))}。"
        )

    performance_fact_lines = []
    for label, _ in return_windows:
        performance_fact_lines.append(
            f"- `{label}`：公司收益 {percent_text(stock_returns[label])}，基准收益 {percent_text(benchmark_returns[label])}，超额收益 {percent_text(excess_returns[label])}。"
        )

    dividend_fact_lines = []
    for row in yearly_dividend_rows(dividend_history_records, args.stock, report_date, limit=5):
        dividend_fact_lines.append(
            f"- `{row['year']}` 年累计税前派现约 {row['cash']:.2f} 元 / 每{str(row['round_lot'] or '10')}股，分红事件 {row['events']} 次，最新宣告日 `{row['latest_declaration'].isoformat() if row['latest_declaration'] else '无数据'}`。"
        )

    risk_fact_lines = [
        f"- 净利润一致预期 60 天变化约为 {percent_text(safe_growth(consensus_latest_map.get(forecast_years[0], {}).get('profit') if forecast_years else None, consensus_prior_map.get(forecast_years[0], {}).get('profit') if forecast_years else None))}。",
        f"- 经营现金流 / 净利润约为 {x_text(cash_conversion)}，资产负债率约为 {percent_text(debt_ratio, signed=False)}。",
        f"- PE 相对 peer 中位数偏离约 {percent_text(safe_growth(latest_pe, peer_pe_median))}，PB 相对 peer 中位数偏离约 {percent_text(safe_growth(latest_pb, peer_pb_median))}。",
        f"- 过去 1Y / 3Y 相对基准超额收益约为 {percent_text(excess_returns['1Y'])} / {percent_text(excess_returns['3Y'])}。",
        f"- 第一大股东 / 前三大 / 前十大持股比例约为 {percent_text(top1_total, signed=False)} / {percent_text(top3_total, signed=False)} / {percent_text(top10_total, signed=False)}。",
    ]

    industry_text = " - ".join(
        str(industry_info.get(field) or "")
        for field in ("first_industry_name", "second_industry_name", "third_industry_name")
        if industry_info and industry_info.get(field)
    ) or "未提供"

    peer_pool_rows = []
    for item in peer_pool_records[:8]:
        if not isinstance(item, dict):
            continue
        peer_pool_rows.append(
            [
                normalize_ticker(item) or "无数据",
                str(item.get("selection_level") or "无数据"),
                billion_yuan_text(float_or_none(item.get("market_cap"))),
            ]
        )

    exec_summary = "\n".join(
        [
            f"- 公司 **{company_name}**（`{args.stock}`）位于 `{industry_text}`，上市日期 `{str((company_info or {}).get('listed_date') or '无数据')}`；当前识别的最新财报季度为 `{latest_snapshot.quarter}`，披露日 `{latest_snapshot.info_date.isoformat()}`。",
            f"- 最新累计口径收入 / 净利润分别为 {billion_yuan_text(latest_snapshot.revenue)} / {billion_yuan_text(latest_snapshot.net_profit)}，同比 {percent_text(revenue_yoy)} / {percent_text(profit_yoy)}；单季度口径收入 / 净利润约为 {billion_yuan_text(latest_single.revenue)} / {billion_yuan_text(latest_single.net_profit)}。",
            f"- 当前总市值 {billion_yuan_text(latest_market_cap)}，PE / PB / 股息率分别为 {x_text(latest_pe)} / {x_text(latest_pb)} / {percent_text(latest_dividend_yield, signed=False)}；过去 1Y / 3Y 股价收益约为 {percent_text(stock_returns['1Y'])} / {percent_text(stock_returns['3Y'])}。",
            f"- 可比公司池覆盖 {count_text(len(peer_rows))}，可比口径季度为 `{comparison_quarter or latest_snapshot.quarter}`；公司当前市值 / ROE / PE 在可比样本中的排名约为 `{peer_rank_by_market_cap or '无数据'}` / `{peer_rank_by_roe or '无数据'}` / `{peer_rank_by_pe or '无数据'}`。",
            f"- 最新一致预期目标价 {yuan_price_text(latest_target_price)}，相对最新收盘价的静态空间约为 {percent_text(target_upside)}；60 天目标价变化约为 {percent_text(target_price_delta)}。",
            f"- 最新十大股东口径下，前三大 / 前十大持股合计约 {percent_text(top3_total, signed=False)} / {percent_text(top10_total, signed=False)}，自由流通股占总股本比例约 {percent_text(free_float_ratio, signed=False)}。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    external_context_lines = format_web_context_lines(web_search_records)
    company_profile_parts = [
            "本章节聚焦覆盖边界与样本口径，帮助确认公司定位、比较框架与后续章节的分析边界。",
            f"公司基础档案显示，其上市于 `{str((company_info or {}).get('listed_date') or '无数据')}`，办公地址 `{str((company_info or {}).get('office_address') or '无数据')}`，所在省份 `{str((company_info or {}).get('province') or '无数据')}`，行业标签 `{industry_text}`。这些字段用于确认覆盖对象与可比样本锚点。",
            "",
            format_table(
                ["项目", "内容"],
                [
                    ["公司名称", company_name],
                    ["股票代码", args.stock],
                    ["行业归属", industry_text],
                    ["上市日期", str((company_info or {}).get("listed_date") or "无数据")],
                    ["办公地址", str((company_info or {}).get("office_address") or "无数据")],
                    ["省份", str((company_info or {}).get("province") or "无数据")],
                    ["最新财报季度", latest_snapshot.quarter],
                    ["财报披露日", latest_snapshot.info_date.isoformat()],
                    ["当前收盘价", yuan_price_text(close_price)],
                    ["当前总市值", billion_yuan_text(latest_market_cap)],
                ],
            ),
            "",
            f"可比公司按照统一筛选规则生成：优先选择与目标公司同三级行业的上市公司，样本不足时再扩展到二级行业，最终保留 {count_text(len(peer_rows))} 只代表性样本，并按最新可得市值排序。",
            f"当前样本的可比季度为 `{comparison_quarter or latest_snapshot.quarter}`，这样估值与盈利对比尽量保持在同一财务口径下。",
            "",
            format_table(["股票代码", "选择层级", "最新市值"], peer_pool_rows or [["无数据", "无数据", "无数据"]]),
    ]
    if external_context_lines:
        company_profile_parts.extend(
            [
                "",
                "补充行业 / 管理层 / 公司动态语境如下：",
                *external_context_lines,
            ]
        )
    company_profile_parts.extend(
        [
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )
    company_profile = "\n".join(company_profile_parts)

    ownership_section = "\n".join(
        [
            f"最新股本记录日期为 `{str((latest_share or {}).get('date') or '无数据')}`。总股本 / A 股流通股 / 自由流通股分别为 {shares_text(total_shares)} / {shares_text(circulation_a)} / {shares_text(free_circulation)}，流通比例 / 自由流通比例约为 {percent_text(circulation_ratio, signed=False)} / {percent_text(free_float_ratio, signed=False)}。",
            f"最新十大股东口径对应的报告期末为 `{str((latest_top10_rows[0].get('end_date')) if latest_top10_rows else '无数据')}`，前三大 / 前十大持股集中度约为 {percent_text(top3_total, signed=False)} / {percent_text(top10_total, signed=False)}，第一大股东单独持股约为 {percent_text(top1_total, signed=False)}。",
            "以下内容仅复述最新股东结构，不附加治理优劣判断：",
            *ownership_highlights,
            "",
            format_table(["排名", "股东名称", "股东类别", "占总股本", "占流通股"], ownership_rows or [["无数据", "无数据", "无数据", "无数据", "无数据"]]),
            "",
            format_table(
                ["指标", "当前值"],
                [
                    ["总股本", shares_text(total_shares)],
                    ["A 股流通股", shares_text(circulation_a)],
                    ["自由流通股", shares_text(free_circulation)],
                    ["流通比例", percent_text(circulation_ratio, signed=False)],
                    ["自由流通比例", percent_text(free_float_ratio, signed=False)],
                    ["前三大股东合计", percent_text(top3_total, signed=False)],
                    ["前十大股东合计", percent_text(top10_total, signed=False)],
                ],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    financial_trajectory = "\n".join(
        [
            f"财务轨迹采用与最新季度同口径的历史序列进行横向比较。当前最新季度为 `{latest_snapshot.quarter}`，以下表格展示近 5 个同口径季度的收入、利润、毛利率、资产负债率、ROE 与经营现金流。",
            "逐年同口径事实如下：",
            *history_fact_lines,
            "",
            format_table(
                ["季度", "披露日", "收入", "净利润", "毛利率", "资产负债率", "ROE", "经营现金流"],
                same_quarter_rows or [["无数据", "无数据", "无数据", "无数据", "无数据", "无数据", "无数据", "无数据"]],
            ),
            "",
            f"基于同口径序列，收入 CAGR 约为 {percent_text(revenue_cagr)}，净利润 CAGR 约为 {percent_text(profit_cagr)}。最新累计口径收入 / 净利润同比约为 {percent_text(revenue_yoy)} / {percent_text(profit_yoy)}，环比约为 {percent_text(revenue_qoq)} / {percent_text(profit_qoq)}。",
            f"若拆到单季度口径，收入同比约 {percent_text(single_revenue_yoy)}，净利润同比约 {percent_text(single_profit_yoy)}；这一组数据更适合观察短期经营节奏是否出现变化。",
            "最近 8 个已披露季度的累计/单季度口径串联如下：",
            *quarter_fact_lines,
            "",
            format_table(
                ["口径", "季度", "收入", "净利润", "收入同比", "净利润同比", "毛利率", "经营现金流/净利润"],
                latest_compare_rows,
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    quality_and_cashflow = "\n".join(
        [
            f"盈利质量章节只保留结构化指标。累计口径下，毛利率约 {percent_text(gross_margin, signed=False)}，较上年同期变动 {percent_text((gross_margin - yoy_gross_margin) if gross_margin is not None and yoy_gross_margin is not None else None)}；资产负债率约 {percent_text(debt_ratio, signed=False)}，同比变动 {percent_text((debt_ratio - yoy_debt_ratio) if debt_ratio is not None and yoy_debt_ratio is not None else None)}。",
            f"经营现金流 / 净利润约为 {x_text(cash_conversion)}，单季度口径约为 {x_text(single_cash_conversion)}；最新 ROE 约 {percent_text(latest_roe, signed=False)}，peer 中位数约 {percent_text(peer_roe_median, signed=False)}。",
            f"累计经营 / 投资 / 融资现金流分别为 {billion_yuan_text(latest_snapshot.operating_cash)} / {billion_yuan_text(latest_snapshot.investing_cash)} / {billion_yuan_text(latest_snapshot.financing_cash)}；单季度对应值分别为 {billion_yuan_text(latest_single.operating_cash)} / {billion_yuan_text(latest_single.investing_cash)} / {billion_yuan_text(latest_single.financing_cash)}。",
            f"总资产 / 总负债 / 股东权益分别为 {billion_yuan_text(latest_snapshot.total_assets)} / {billion_yuan_text(latest_snapshot.total_liabilities)} / {billion_yuan_text(latest_snapshot.total_equity)}，可用于交叉判断资本结构与现金生成质量。",
            "",
            format_table(
                ["指标", "累计口径", "单季度口径"],
                [
                    ["营业收入", billion_yuan_text(latest_snapshot.revenue), billion_yuan_text(latest_single.revenue)],
                    ["净利润", billion_yuan_text(latest_snapshot.net_profit), billion_yuan_text(latest_single.net_profit)],
                    ["毛利", billion_yuan_text(latest_snapshot.gross_profit), billion_yuan_text(latest_single.gross_profit)],
                    ["经营现金流", billion_yuan_text(latest_snapshot.operating_cash), billion_yuan_text(latest_single.operating_cash)],
                    ["投资现金流", billion_yuan_text(latest_snapshot.investing_cash), billion_yuan_text(latest_single.investing_cash)],
                    ["融资现金流", billion_yuan_text(latest_snapshot.financing_cash), billion_yuan_text(latest_single.financing_cash)],
                    ["总资产", billion_yuan_text(latest_snapshot.total_assets), "不适用"],
                    ["总负债", billion_yuan_text(latest_snapshot.total_liabilities), "不适用"],
                    ["股东权益", billion_yuan_text(latest_snapshot.total_equity), "不适用"],
                ],
            ),
            "",
            format_table(
                ["质量指标", "当前值", "对比基准"],
                [
                    ["毛利率", percent_text(gross_margin, signed=False), percent_text(yoy_gross_margin, signed=False)],
                    ["单季度毛利率", percent_text(latest_single_gross_margin, signed=False), percent_text(yoy_single_gross_margin, signed=False)],
                    ["资产负债率", percent_text(debt_ratio, signed=False), percent_text(yoy_debt_ratio, signed=False)],
                    ["ROE", percent_text(latest_roe, signed=False), percent_text(peer_roe_median, signed=False)],
                    ["经营现金流 / 净利润", x_text(cash_conversion), "1.00x"],
                ],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    expectation_and_sellside = "\n".join(
        [
            "市场预期章节展示一致预期时间序列与相关卖方材料摘要，重点观察盈利预测、目标价与机构表述的变化方向。",
            *expectation_fact_lines,
            "",
            format_table(
                ["预测年份", "最新收入预期", "较60天前", "最新净利润预期", "较60天前", "最新 EPS"],
                consensus_table_rows or [["无数据", "无数据", "无数据", "无数据", "无数据", "无数据"]],
            ),
            "",
            f"最新一致预期目标价为 {yuan_price_text(latest_target_price)}，较 60 天前变动 {percent_text(target_price_delta)}；相对最新收盘价的静态空间约为 {percent_text(target_upside)}。当前纳入正文的卖方材料仅保留与公司直接相关、且已整理成客户可读摘要的样本。",
            "近期相关卖方观点如下：",
            *report_fact_lines,
            "",
            format_table(
                ["日期", "机构", "标题", "目标价", "净利润t", "摘要摘录"],
                report_rows or [["无数据", "无数据", "无数据", "无数据", "无数据", "无数据"]],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    peer_and_valuation = "\n".join(
        [
            f"可比公司章节使用统一规则筛选出的样本与最新同口径财务快照。当前可比季度为 `{comparison_quarter or latest_snapshot.quarter}`，公司当前总市值 / PE / PB / 股息率分别为 {billion_yuan_text(latest_market_cap)} / {x_text(latest_pe)} / {x_text(latest_pb)} / {percent_text(latest_dividend_yield, signed=False)}，对应可比样本中位数为 {billion_yuan_text(peer_market_cap_median)} / {x_text(peer_pe_median)} / {x_text(peer_pb_median)} / {percent_text(peer_dividend_median, signed=False)}。",
            f"若以当前样本排序，公司在可比样本中的市值排名约 `{peer_rank_by_market_cap or '无数据'}`，ROE 排名约 `{peer_rank_by_roe or '无数据'}`，PE 排名约 `{peer_rank_by_pe or '无数据'}`。以下表格用于观察公司在盈利质量、估值和回报维度上的相对位置。",
            "主要可比公司事实摘录：",
            *peer_fact_lines,
            "",
            format_table(
                ["公司", "代码", "季度", "市值", "收入", "净利润", "毛利率", "ROE", "PE", "PB", "股息率"],
                peer_table_rows or [["无数据"] * 11],
            ),
            "",
            format_table(
                ["指标", "公司当前", "Peer 中位数", "相对中位数"],
                [
                    ["总市值", billion_yuan_text(latest_market_cap), billion_yuan_text(peer_market_cap_median), percent_text(safe_growth(latest_market_cap, peer_market_cap_median))],
                    ["ROE", percent_text(latest_roe, signed=False), percent_text(peer_roe_median, signed=False), percent_text((latest_roe - peer_roe_median) if latest_roe is not None and peer_roe_median is not None else None)],
                    ["PE", x_text(latest_pe), x_text(peer_pe_median), percent_text(safe_growth(latest_pe, peer_pe_median))],
                    ["PB", x_text(latest_pb), x_text(peer_pb_median), percent_text(safe_growth(latest_pb, peer_pb_median))],
                    ["股息率", percent_text(latest_dividend_yield, signed=False), percent_text(peer_dividend_median, signed=False), percent_text((latest_dividend_yield - peer_dividend_median) if latest_dividend_yield is not None and peer_dividend_median is not None else None)],
                ],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    trading_and_dividend = "\n".join(
        [
            f"交易表现章节覆盖 1M/3M/6M/1Y/3Y 的绝对收益与相对基准收益。最新收盘价为 {yuan_price_text(close_price)}，最新换手率约 {percent_text(turnover_stats['latest'], signed=False)}，近 20 / 60 个交易日换手率中位数约为 {percent_text(turnover_stats['median_20'], signed=False)} / {percent_text(turnover_stats['median_60'], signed=False)}。",
            "区间收益分解如下：",
            *performance_fact_lines,
            "",
            format_table(
                ["区间", "公司收益", "基准收益", "超额收益"],
                performance_rows,
            ),
            "",
            format_table(
                ["换手指标", "数值"],
                [
                    ["最新日换手率", percent_text(turnover_stats["latest"], signed=False)],
                    ["近20日中位数", percent_text(turnover_stats["median_20"], signed=False)],
                    ["近60日中位数", percent_text(turnover_stats["median_60"], signed=False)],
                    [
                        "最新 / 近60日中位数",
                        x_text((turnover_stats["latest"] / turnover_stats["median_60"]) if turnover_stats["latest"] is not None and turnover_stats["median_60"] not in (None, 0) else None),
                    ],
                ],
            ),
            "",
            "近五年分红记录摘要：",
            *dividend_fact_lines,
            "",
            format_table(
                ["年份", "累计税前派现", "事件数", "最新宣告日"],
                dividend_rows or [["无数据", "无数据", "无数据", "无数据"]],
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    risk_section = "\n".join(
        [
            "以下内容为重点跟踪信号表，用于提示需要持续核查的维度，并不替代对公司基本面的完整判断。",
            *risk_fact_lines,
            "",
            format_table(
                ["监控项", "当前值", "提示阈值", "状态"],
                risk_rows,
            ),
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    appendix = "\n".join(
        [
            f"- 报告日期：`{report_date.isoformat()}`；目标股票：`{args.stock}`；当前识别财报季度：`{latest_snapshot.quarter}`；可比样本数量：`{len(peer_rows)}`。",
            "- 若某项数据缺失，报告会明确展示“无数据/未提供”。",
            "- 研报样本优先保留公司直接相关材料；标题不相关、来源不匹配或未形成客户可读摘要的样本不会进入正文。",
            "- 可比公司表、收益表和风险信号表均基于同一报告日口径生成，适合作为后续持续跟踪的对照基线。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )

    template_text = Path(args.template).read_text(encoding="utf-8")
    replacements = {
        "REPORT_DATE": report_date.isoformat(),
        "COMPANY_NAME": company_name,
        "STOCK_CODE": args.stock,
        "EXEC_SUMMARY": exec_summary,
        "COMPANY_PROFILE": company_profile,
        "OWNERSHIP_SECTION": ownership_section,
        "FINANCIAL_TRAJECTORY": financial_trajectory,
        "QUALITY_AND_CASHFLOW": quality_and_cashflow,
        "EXPECTATION_AND_SELLSIDE": expectation_and_sellside,
        "PEER_AND_VALUATION": peer_and_valuation,
        "TRADING_AND_DIVIDEND": trading_and_dividend,
        "RISK_SECTION": risk_section,
        "APPENDIX": appendix,
    }
    report_text = render_template(template_text, replacements)

    output_path = Path(args.output).expanduser() if args.output else data_dir / f"initiating_coverage_{args.stock}_{report_date.isoformat()}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
