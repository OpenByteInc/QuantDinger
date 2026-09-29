#!/usr/bin/env python3
"""Template-driven sector overview report generator."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 行业概览报告",
    "## 执行摘要",
    "## 行业定义与样本覆盖",
    "## 行业状态判断",
    "## 行业财务与盈利结构",
    "## 竞争格局与集中度",
    "## 估值与股东回报",
    "## 区间表现与相对收益",
    "## 投资线索与跟踪指标",
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
    "market_size_context",
    "trend_context",
    "policy_context",
    "competition_context",
    "mna_context",
}

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的行业概览报告")
    parser.add_argument("--industry", required=True, help="行业名称")
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
            issues.append(f"第 {idx} 条网络搜索信息记录不是对象")
            continue
        missing = [field for field in required_fields if item.get(field) in (None, "", "null")]
        if missing:
            issues.append(f"第 {idx} 条网络搜索信息记录缺少字段：{', '.join(missing)}")
        source_type = str(item.get("source_type") or "").strip()
        if source_type not in WEB_SOURCE_CONFIDENCE_CEILING:
            issues.append(f"第 {idx} 条网络搜索信息记录来源类型非法：{source_type or '空'}")
        confidence = float_or_none(item.get("confidence"))
        ceiling = WEB_SOURCE_CONFIDENCE_CEILING.get(source_type)
        if confidence is None:
            issues.append(f"第 {idx} 条网络搜索信息记录缺少置信度")
        elif ceiling is not None and confidence > ceiling:
            issues.append(f"第 {idx} 条网络搜索信息记录置信度 {confidence:g} 超过来源上限 {ceiling}")
        finding_type = str(item.get("finding_type") or "").strip()
        if finding_type not in WEB_ALLOWED_FINDING_TYPES:
            issues.append(f"第 {idx} 条网络搜索信息记录 finding_type 非法：{finding_type or '空'}")
    if issues:
        raise ValueError("网络搜索信息校验失败：" + "；".join(issues))


def extract_external_findings(records: Sequence[Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        published_at = parse_iso_date(item.get("published_at"))
        if published_at is None:
            continue
        findings.append(
            {
                "source_name": str(item.get("source_name") or "外部来源").strip(),
                "title": clean_text(item.get("title")),
                "published_at": published_at,
                "summary": clean_text(item.get("summary")).rstrip("。；;!！?？"),
                "why_relevant": clean_text(item.get("why_relevant")).rstrip("。；;!！?？"),
                "confidence": int(float_or_none(item.get("confidence")) or 0),
            }
        )
    findings.sort(key=lambda item: (item["published_at"], item["confidence"]), reverse=True)
    return findings


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


def median_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return float(median(valid))


def average_or_none(values: Iterable[Optional[float]]) -> Optional[float]:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return float(sum(valid) / len(valid))


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def count_if(rows: Sequence[Dict[str, Any]], predicate) -> int:
    return sum(1 for item in rows if predicate(item))


def safe_ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return numerator / denominator * 100.0


def format_company_list(items: Sequence[Dict[str, Any]], field_name: str, formatter, limit: int = 5) -> str:
    picked = []
    for item in items[:limit]:
        picked.append(f"{item['name']}（{formatter(item.get(field_name))}）")
    return "、".join(picked) if picked else "无数据"


def ranked_rows(rows: Sequence[Dict[str, Any]], field_name: str, reverse: bool = True, require_positive: bool = False) -> List[Dict[str, Any]]:
    ranked = []
    for item in rows:
        value = item.get(field_name)
        if value is None:
            continue
        if require_positive and value <= 0:
            continue
        ranked.append(item)
    return sorted(ranked, key=lambda item: item.get(field_name) or 0.0, reverse=reverse)


def quoted(value: str) -> str:
    return f"`{value}`" if value else "`无数据`"


def is_special_treatment(item: Dict[str, Any]) -> bool:
    name = str(item.get("name") or "")
    return "ST" in name.upper()


def build_stock_pool(records: List[Any]) -> List[str]:
    order_book_ids: List[str] = []
    seen = set()
    for item in records:
        if isinstance(item, str):
            stock = item.strip()
        elif isinstance(item, dict):
            stock = normalize_ticker(item)
        else:
            continue
        if not stock or stock in seen:
            continue
        seen.add(stock)
        order_book_ids.append(stock)
    return order_book_ids


def build_name_map(records: List[Any], stock_ids: Sequence[str]) -> Dict[str, str]:
    stock_set = set(stock_ids)
    result = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock in stock_set:
            result[stock] = normalize_name(item) or stock
    return result


def build_industry_map(records: List[Any], stock_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    stock_set = set(stock_ids)
    result = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock not in stock_set:
            continue
        result[stock] = {
            "first": str(item.get("first_industry_name") or "无数据"),
            "second": str(item.get("second_industry_name") or "无数据"),
            "third": str(item.get("third_industry_name") or "无数据"),
        }
    return result


def dedupe_financial_records(records: List[Any], stock_ids: Sequence[str]) -> List[Dict[str, Any]]:
    stock_set = set(stock_ids)
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        quarter = str(item.get("quarter") or "").lower()
        if stock not in stock_set or not parse_quarter_key(quarter):
            continue
        key = (stock, quarter)
        current = deduped.get(key)
        current_date = parse_iso_date(current.get("info_date")) if current else None
        item_date = parse_iso_date(item.get("info_date"))
        if current is None or (item_date and (current_date is None or item_date >= current_date)):
            deduped[key] = item
    return list(deduped.values())


def latest_by_stock(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in records:
        stock = normalize_ticker(item)
        current = grouped.get(stock)
        current_key = parse_quarter_key(str(current.get("quarter") or "").lower()) if current else None
        item_key = parse_quarter_key(str(item.get("quarter") or "").lower())
        if current is None or (item_key and (current_key is None or item_key >= current_key)):
            grouped[stock] = item
    return grouped


def latest_factor_map(records: List[Any], stock_ids: Sequence[str], field_name: str) -> Dict[str, float]:
    stock_set = set(stock_ids)
    best: Dict[str, Tuple[date, float]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock not in stock_set:
            continue
        event_date = parse_iso_date(item.get("date") or item.get("datetime"))
        value = float_or_none(item.get(field_name))
        if field_name == "dividend_yield" and value is not None:
            value = value / 100.0
        if event_date is None or value is None:
            continue
        current = best.get(stock)
        if current is None or event_date >= current[0]:
            best[stock] = (event_date, value)
    return {stock: value for stock, (_, value) in best.items()}


def dominant_quarter(latest_records: Dict[str, Dict[str, Any]]) -> Optional[str]:
    counts = Counter(str(item.get("quarter") or "").lower() for item in latest_records.values() if item.get("quarter"))
    return counts.most_common(1)[0][0] if counts else None


def same_quarter_last_year(quarter: str) -> Optional[str]:
    key = parse_quarter_key(quarter)
    if not key:
        return None
    return f"{key[0] - 1}q{key[1]}"


def build_sector_rows(
    stock_ids: Sequence[str],
    instrument_records: List[Any],
    industry_records: List[Any],
    financial_records: List[Any],
    roe_records: List[Any],
    market_cap_records: List[Any],
    pe_records: List[Any],
    pb_records: List[Any],
    dividend_records: List[Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    names = build_name_map(instrument_records, stock_ids)
    industries = build_industry_map(industry_records, stock_ids)
    financial_deduped = dedupe_financial_records(financial_records, stock_ids)
    latest_records = latest_by_stock(financial_deduped)
    dominant = dominant_quarter(latest_records)
    yoy_quarter = same_quarter_last_year(dominant) if dominant else None

    by_stock_quarter = {(normalize_ticker(item), str(item.get("quarter") or "").lower()): item for item in financial_deduped}
    roe_map = latest_factor_map(roe_records, stock_ids, "return_on_equity_weighted_average")
    market_cap_map = latest_factor_map(market_cap_records, stock_ids, "market_cap")
    pe_map = latest_factor_map(pe_records, stock_ids, "pe_ratio")
    pb_map = latest_factor_map(pb_records, stock_ids, "pb_ratio")
    dividend_map = latest_factor_map(dividend_records, stock_ids, "dividend_yield")

    rows: List[Dict[str, Any]] = []
    coverage_counts = Counter(str(item.get("quarter") or "").lower() for item in latest_records.values())
    for stock in stock_ids:
        latest = latest_records.get(stock)
        if latest is None or str(latest.get("quarter") or "").lower() != dominant:
            continue
        yoy = by_stock_quarter.get((stock, yoy_quarter or ""))
        revenue = float_or_none(latest.get("revenue"))
        net_profit = float_or_none(latest.get("net_profit"))
        gross_profit = float_or_none(latest.get("gross_profit"))
        total_assets = float_or_none(latest.get("total_assets"))
        total_liabilities = float_or_none(latest.get("total_liabilities"))
        cash_flow = float_or_none(latest.get("cash_from_operating_activities"))
        yoy_revenue = float_or_none(yoy.get("revenue")) if yoy else None
        yoy_profit = float_or_none(yoy.get("net_profit")) if yoy else None
        revenue_yoy = ((revenue / yoy_revenue - 1.0) * 100.0) if revenue is not None and yoy_revenue not in (None, 0) else None
        profit_yoy = ((net_profit / yoy_profit - 1.0) * 100.0) if net_profit is not None and yoy_profit not in (None, 0) else None
        gross_margin = ((gross_profit / revenue) * 100.0) if gross_profit is not None and revenue not in (None, 0) else None
        net_margin = ((net_profit / revenue) * 100.0) if net_profit is not None and revenue not in (None, 0) else None
        debt_ratio = ((total_liabilities / total_assets) * 100.0) if total_liabilities is not None and total_assets not in (None, 0) else None
        cash_conversion = (cash_flow / net_profit) if cash_flow is not None and net_profit not in (None, 0) else None

        industry_meta = industries.get(stock, {"first": "无数据", "second": "无数据", "third": "无数据"})
        rows.append(
            {
                "order_book_id": stock,
                "name": names.get(stock, stock),
                "quarter": dominant,
                "first_industry": industry_meta["first"],
                "second_industry": industry_meta["second"],
                "third_industry": industry_meta["third"],
                "revenue": revenue,
                "net_profit": net_profit,
                "gross_margin": gross_margin,
                "net_margin": net_margin,
                "debt_ratio": debt_ratio,
                "cash_conversion": cash_conversion,
                "revenue_yoy": revenue_yoy,
                "profit_yoy": profit_yoy,
                "roe": roe_map.get(stock),
                "market_cap": market_cap_map.get(stock),
                "pe": pe_map.get(stock),
                "pb": pb_map.get(stock),
                "dividend_yield": dividend_map.get(stock),
            }
        )

    stats = {
        "pool_size": len(stock_ids),
        "covered_size": len(rows),
        "dominant_quarter": dominant,
        "yoy_quarter": yoy_quarter,
        "quarter_distribution": coverage_counts,
    }
    return rows, stats


def build_return_map(price_records: List[Any], stock_ids: Sequence[str]) -> Dict[str, float]:
    stock_set = set(stock_ids)
    series: Dict[str, Dict[date, float]] = {stock: {} for stock in stock_set}
    for item in price_records:
        if not isinstance(item, dict):
            continue
        stock = normalize_ticker(item)
        if stock not in stock_set:
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        if event_date is None or close is None:
            continue
        series[stock][event_date] = close

    returns: Dict[str, float] = {}
    for stock, points in series.items():
        ordered = sorted(points.items(), key=lambda pair: pair[0])
        if len(ordered) < 2 or ordered[0][1] in (None, 0):
            continue
        returns[stock] = (ordered[-1][1] / ordered[0][1] - 1.0) * 100.0
    return returns


def build_benchmark_return(records: List[Any]) -> Optional[float]:
    points: Dict[date, float] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        event_date = parse_iso_date(item.get("datetime") or item.get("date"))
        close = float_or_none(item.get("close"))
        if event_date is None or close is None:
            continue
        points[event_date] = close
    ordered = sorted(points.items(), key=lambda pair: pair[0])
    if len(ordered) < 2 or ordered[0][1] in (None, 0):
        return None
    return (ordered[-1][1] / ordered[0][1] - 1.0) * 100.0


def sector_summary(rows: Sequence[Dict[str, Any]], returns: Dict[str, float], benchmark_return: Optional[float]) -> Dict[str, Any]:
    total_market_cap = sum(item.get("market_cap") or 0.0 for item in rows)
    total_revenue = sum(item.get("revenue") or 0.0 for item in rows)
    total_profit = sum(item.get("net_profit") or 0.0 for item in rows)
    gross_profit_agg = sum((item.get("gross_margin") or 0.0) / 100.0 * (item.get("revenue") or 0.0) for item in rows if item.get("gross_margin") is not None and item.get("revenue") is not None)
    gross_margin = (gross_profit_agg / total_revenue * 100.0) if total_revenue else None

    sector_return_avg = median_or_none(returns.values())
    excess = sector_return_avg - benchmark_return if sector_return_avg is not None and benchmark_return is not None else None
    positive_count = sum(1 for value in returns.values() if value > 0)
    revenue_positive_count = count_if(rows, lambda item: (item.get("revenue_yoy") or -9999.0) > 0)
    profit_positive_count = count_if(rows, lambda item: (item.get("profit_yoy") or -9999.0) > 0)
    high_roe_count = count_if(rows, lambda item: (item.get("roe") or -9999.0) >= 10.0)
    low_pe_count = count_if(rows, lambda item: item.get("pe") is not None and 0 < item["pe"] <= 15.0)
    dividend_payer_count = count_if(rows, lambda item: (item.get("dividend_yield") or 0.0) > 0)
    loss_making_count = count_if(rows, lambda item: (item.get("net_profit") or 0.0) < 0)
    high_cash_count = count_if(rows, lambda item: (item.get("cash_conversion") or 0.0) >= 1.0)
    positive_pe_values = [item.get("pe") for item in rows if item.get("pe") is not None and item.get("pe") > 0]
    positive_pb_values = [item.get("pb") for item in rows if item.get("pb") is not None and item.get("pb") > 0]
    return {
        "total_market_cap": total_market_cap if total_market_cap else None,
        "total_revenue": total_revenue if total_revenue else None,
        "total_profit": total_profit if total_profit else None,
        "gross_margin": gross_margin,
        "revenue_yoy_median": median_or_none(item.get("revenue_yoy") for item in rows),
        "profit_yoy_median": median_or_none(item.get("profit_yoy") for item in rows),
        "roe_median": median_or_none(item.get("roe") for item in rows),
        "pe_median": median_or_none(positive_pe_values),
        "pb_median": median_or_none(positive_pb_values),
        "dividend_median": median_or_none(item.get("dividend_yield") for item in rows),
        "debt_ratio_median": median_or_none(item.get("debt_ratio") for item in rows),
        "cash_conversion_median": median_or_none(item.get("cash_conversion") for item in rows),
        "sector_return_median": sector_return_avg,
        "benchmark_return": benchmark_return,
        "excess_return": excess,
        "positive_count": positive_count,
        "breadth": (positive_count / len(returns) * 100.0) if returns else None,
        "revenue_positive_count": revenue_positive_count,
        "profit_positive_count": profit_positive_count,
        "high_roe_count": high_roe_count,
        "low_pe_count": low_pe_count,
        "dividend_payer_count": dividend_payer_count,
        "loss_making_count": loss_making_count,
        "high_cash_count": high_cash_count,
        "revenue_positive_ratio": safe_ratio(revenue_positive_count, len(rows)),
        "profit_positive_ratio": safe_ratio(profit_positive_count, len(rows)),
        "high_roe_ratio": safe_ratio(high_roe_count, len(rows)),
        "dividend_payer_ratio": safe_ratio(dividend_payer_count, len(rows)),
        "cash_conversion_ratio": safe_ratio(high_cash_count, len(rows)),
        "pe_average": average_or_none(positive_pe_values),
        "pb_average": average_or_none(positive_pb_values),
    }


def populated_count(rows: Sequence[Dict[str, Any]], field_name: str) -> int:
    return sum(1 for item in rows if item.get(field_name) is not None)


def validate_dataset_quality(
    stock_ids: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    returns: Dict[str, float],
    summary: Dict[str, Any],
) -> None:
    if not rows:
        raise ValueError("主导财报季度样本为空，无法生成行业概览报告。")

    issues: List[str] = []
    market_cap_coverage = populated_count(rows, "market_cap")
    roe_coverage = populated_count(rows, "roe")
    pe_coverage = populated_count(rows, "pe")
    pb_coverage = populated_count(rows, "pb")
    dividend_coverage = populated_count(rows, "dividend_yield")
    return_coverage = len(returns)

    if market_cap_coverage == 0 or summary.get("total_market_cap") is None:
        issues.append("市值因子全部为空；请把 financial-indicator 日期回溯到最近非空交易日后重试。")
    if roe_coverage == 0:
        issues.append("ROE 因子全部为空；当前因子日期不可用。")
    if pe_coverage == 0 and pb_coverage == 0:
        issues.append("PE/PB 因子全部为空；估值章节无法成立。")
    if dividend_coverage == 0:
        issues.append("股息率因子全部为空；股东回报章节无法成立。")
    if return_coverage < max(3, min(len(stock_ids), 5)):
        issues.append("区间价格覆盖不足；相对收益章节无法稳定成立。")

    if issues:
        raise ValueError("数据质量校验失败：" + "；".join(issues))


def concentration(market_caps: Sequence[Optional[float]], top_n: int) -> Optional[float]:
    valid = sorted((value for value in market_caps if value is not None), reverse=True)
    total = sum(valid)
    if total <= 0 or len(valid) < top_n:
        return None
    return sum(valid[:top_n]) / total * 100.0


def classify_operating_state(summary: Dict[str, Any]) -> str:
    revenue_yoy = summary.get("revenue_yoy_median")
    profit_yoy = summary.get("profit_yoy_median")
    if revenue_yoy is None or profit_yoy is None:
        return "数据不足，暂难判断景气阶段"
    if revenue_yoy >= 10 and profit_yoy >= 10:
        return "收入与利润同步扩张，处于景气上行阶段"
    if revenue_yoy >= 0 and profit_yoy < 0:
        return "收入修复领先于利润兑现，处于复苏早期"
    if revenue_yoy < 0 and profit_yoy >= 0:
        return "需求偏弱但盈利仍在修复，处于利润韧性阶段"
    return "收入和利润均承压，处于主动去库存或需求偏弱阶段"


def classify_market_style(summary: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> str:
    breadth = summary.get("breadth")
    excess = summary.get("excess_return")
    cr5 = concentration([item.get("market_cap") for item in rows], 5)
    if breadth is None or excess is None:
        return "市场风格判断不足"
    if excess > 5 and breadth >= 60:
        return "行业整体强于基准，资金参与面较广"
    if excess > 5 and breadth < 60:
        return "行业跑赢基准，但行情主要集中于少数龙头"
    if excess <= 5 and breadth >= 50:
        return "股价表现分散，行业内部轮动多于整体贝塔"
    if cr5 is not None and cr5 >= 70:
        return "市场对龙头更偏好，二线公司尚未形成共振"
    return "行业整体交易热度偏弱，仍需等待基本面验证"


def major_company_line(items: Sequence[Dict[str, Any]], field_name: str, formatter, limit: int = 5) -> str:
    chosen = [item for item in ranked_rows(items, field_name, reverse=True) if not is_special_treatment(item)][:limit]
    return format_company_list(chosen, field_name, formatter, limit=limit)


def build_external_context_block(external_findings: Sequence[Dict[str, Any]], title: str) -> List[str]:
    if not external_findings:
        return []
    lines = [title]
    for item in external_findings[:5]:
        lines.append(
            f"- **{item['published_at'].isoformat()} {item['source_name']}**：{item['title']}。{item['summary']}。"
            f" 对本次行业判断的意义：{item['why_relevant']}。 *数据来源：{item['source_name']}，置信度{item['confidence']}*"
        )
    return lines


def build_exec_summary(
    industry: str,
    stats: Dict[str, Any],
    summary: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
    external_findings: Sequence[Dict[str, Any]] = (),
) -> str:
    top_company = max(rows, key=lambda item: item.get("market_cap") or 0.0) if rows else None
    cr5 = concentration([item.get("market_cap") for item in rows], 5)
    operating_state = classify_operating_state(summary)
    market_style = classify_market_style(summary, rows)
    lines = [
            f"- 一句话判断：`{industry}` 当前呈现“{operating_state}”特征，{market_style}。",
            f"- 样本覆盖：股票池 {stats['pool_size']} 家，进入主导财报季度 {quoted(stats['dominant_quarter'] or '无数据')} 比较口径的公司 {stats['covered_size']} 家，覆盖率约 {percent_text(safe_ratio(stats['covered_size'], stats['pool_size']), signed=False)}。",
            f"- 行业体量：总市值约为 {billion_yuan_text(summary.get('total_market_cap'))}，主导季度合计营收 {billion_yuan_text(summary.get('total_revenue'))}、净利润 {billion_yuan_text(summary.get('total_profit'))}，整体毛利率约为 {percent_text(summary.get('gross_margin'), signed=False)}。",
            f"- 景气读数：营收同比中位数 {percent_text(summary.get('revenue_yoy_median'))}，净利润同比中位数 {percent_text(summary.get('profit_yoy_median'))}，其中营收正增长公司占比 {percent_text(summary.get('revenue_positive_ratio'), signed=False)}，利润正增长公司占比 {percent_text(summary.get('profit_positive_ratio'), signed=False)}。",
            f"- 质量与估值：ROE 中位数 {percent_text(summary.get('roe_median'), signed=False)}，股息率中位数 {percent_text(summary.get('dividend_median'), signed=False)}，正 PE 样本均值 / 中位数分别为 {x_text(summary.get('pe_average'), 1)} / {x_text(summary.get('pe_median'), 1)}。",
            f"- 交易表现：近 180 天行业收益中位数 {percent_text(summary.get('sector_return_median'))}，相对基准超额收益 {percent_text(summary.get('excess_return'))}，上涨家数占比 {percent_text(summary.get('breadth'), signed=False)}，CR5 约为 {percent_text(cr5, signed=False)}。",
            f"- 龙头定位：当前市值龙头为 **{top_company['name']}**（{top_company['order_book_id']}），总市值约 {billion_yuan_text(top_company.get('market_cap'))}。"
            if top_company
            else "- 当前没有足够数据识别行业龙头。",
            "",
            "*数据来源：RQData，置信度5*",
    ]
    if external_findings:
        top_finding = external_findings[0]
        lines.append(
            f"- 网络搜索信息补充：{top_finding['source_name']} 在 {top_finding['published_at'].isoformat()} 提到“{top_finding['title']}”，"
            f"可用于补充行业背景层和景气判断。 *数据来源：{top_finding['source_name']}，置信度{top_finding['confidence']}*"
        )
    return "\n".join(lines)


def build_scope_section(industry: str, sector_definition: Optional[Dict[str, Any]], stats: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> str:
    quarter_lines = [f"- `{quarter}`：{count} 家" for quarter, count in stats["quarter_distribution"].most_common(5)] or ["- 无数据。"]
    first_levels = Counter(item.get("first_industry") for item in rows if item.get("first_industry"))
    second_levels = Counter(item.get("second_industry") for item in rows if item.get("second_industry"))
    third_levels = Counter(item.get("third_industry") for item in rows if item.get("third_industry"))
    top_by_cap = major_company_line(rows, "market_cap", lambda value: billion_yuan_text(value))
    top_by_revenue = major_company_line(rows, "revenue", lambda value: billion_yuan_text(value))
    lines = [
        f"- 本报告使用 `{industry}` 作为行业名称；当前分类来源为 `{(sector_definition or {}).get('industry_source', '未提供')}`，层级为 `{(sector_definition or {}).get('industry_level', '未提供')}`。",
        f"- 股票池总数为 {stats['pool_size']} 家，进入主导季度比较口径的公司为 {stats['covered_size']} 家；未纳入主导季度比较的样本通常是财报期次不同或关键字段缺失。",
        f"- 当前市值前列公司主要包括：{top_by_cap}。",
        f"- 当前营收体量前列公司主要包括：{top_by_revenue}。",
        "",
        "### 主导财报季度分布",
        *quarter_lines,
        "",
        "### 行业分类校验",
        f"- 一级行业最常见分类：`{(first_levels.most_common(1)[0][0] if first_levels else '无数据')}`",
        f"- 二级行业最常见分类：`{(second_levels.most_common(1)[0][0] if second_levels else '无数据')}`",
        f"- 三级行业最常见分类：`{(third_levels.most_common(1)[0][0] if third_levels else '无数据')}`",
        "- 若分类映射为空，通常意味着上游只提供了显式股票池而未附行业层级映射；这不会影响财务和估值统计，但会降低口径复核能力。",
        "",
        "*数据来源：RQData，置信度5*",
    ]
    return "\n".join(lines)


def build_sector_state(
    industry: str,
    summary: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
    external_findings: Sequence[Dict[str, Any]] = (),
) -> str:
    operating_state = classify_operating_state(summary)
    market_style = classify_market_style(summary, rows)
    filtered_rows = [item for item in rows if not is_special_treatment(item)]
    revenue_leaders = format_company_list(ranked_rows(filtered_rows, "revenue_yoy", reverse=True), "revenue_yoy", lambda value: percent_text(value), limit=4)
    profit_leaders = format_company_list(ranked_rows(filtered_rows, "profit_yoy", reverse=True), "profit_yoy", lambda value: percent_text(value), limit=4)
    roe_leaders = format_company_list(ranked_rows(filtered_rows, "roe", reverse=True), "roe", lambda value: percent_text(value, signed=False), limit=4)
    low_pe_names = format_company_list(ranked_rows(filtered_rows, "pe", reverse=False, require_positive=True), "pe", lambda value: x_text(value, 1), limit=4)
    lines = [
        f"- 当前行业状态判断为：**{operating_state}**。",
        f"- 从覆盖样本看，营收正增长公司占比约 {percent_text(summary.get('revenue_positive_ratio'), signed=False)}，利润正增长公司占比约 {percent_text(summary.get('profit_positive_ratio'), signed=False)}，说明 `{industry}` 内部修复并不完全同步。",
        f"- 市场交易层面呈现：**{market_style}**。近 180 天行业收益中位数 {percent_text(summary.get('sector_return_median'))}，超额收益 {percent_text(summary.get('excess_return'))}，上涨广度 {percent_text(summary.get('breadth'), signed=False)}。",
        f"- 质量层面，ROE 不低于 10% 的公司占比约 {percent_text(summary.get('high_roe_ratio'), signed=False)}，现金转化率不低于 1x 的公司占比约 {percent_text(summary.get('cash_conversion_ratio'), signed=False)}，说明行业内部仍可区分出具备盈利兑现能力的经营主体。",
        f"- 回报层面，有股息回报记录的公司占比约 {percent_text(summary.get('dividend_payer_ratio'), signed=False)}；若行业股息率中位数偏低，通常意味着资金更看重成长兑现而非现金回报。",
        f"- 背离观察：行业收入增速与利润增速之间存在 {percent_text((summary.get('revenue_yoy_median') or 0.0) - (summary.get('profit_yoy_median') or 0.0), signed=False)} 的差值，这通常意味着成本、费用或产品结构变化仍在影响利润释放节奏。",
        "",
        "### 关键观察",
        f"- 营收增速领先公司：{revenue_leaders}。",
        f"- 利润修复领先公司：{profit_leaders}。",
        f"- ROE 领先公司：{roe_leaders}。",
        f"- 正 PE 口径下的低估值样本：{low_pe_names}。",
        "- 若后续行业出现“营收正增长占比抬升、利润正增长占比同步抬升、上涨广度扩散”三项共振，通常意味着行业状态从结构性修复转向更广泛的景气改善。",
        "",
        "*数据来源：RQData，置信度5*",
    ]
    if external_findings:
        lines.extend(["", *build_external_context_block(external_findings, "### 外部行业语境")])
    return "\n".join(lines)


def build_financial_section(rows: Sequence[Dict[str, Any]], summary: Dict[str, Any], stats: Dict[str, Any]) -> str:
    top_revenue = sorted(rows, key=lambda item: item.get("revenue") or 0.0, reverse=True)[:10]
    filtered_rows = [item for item in rows if not is_special_treatment(item)]
    revenue_growers = ranked_rows(filtered_rows, "revenue_yoy", reverse=True)[:5]
    profit_growers = ranked_rows(filtered_rows, "profit_yoy", reverse=True)[:5]
    margin_leaders = ranked_rows(filtered_rows, "gross_margin", reverse=True)[:5]
    headers = ["公司", "营收", "营收同比", "净利润", "净利润同比", "毛利率", "现金转化率"]
    table_rows = [
        [
            f"{item['name']}<br>`{item['order_book_id']}`",
            billion_yuan_text(item.get("revenue")),
            percent_text(item.get("revenue_yoy")),
            billion_yuan_text(item.get("net_profit")),
            percent_text(item.get("profit_yoy")),
            percent_text(item.get("gross_margin"), signed=False),
            x_text(item.get("cash_conversion")),
        ]
        for item in top_revenue
    ]
    return "\n".join(
        [
            f"- 主导比较口径为 `{stats['dominant_quarter'] or '无数据'}` 对比 `{stats['yoy_quarter'] or '无数据'}`。",
            f"- 行业营收同比中位数约为 {percent_text(summary.get('revenue_yoy_median'))}，净利润同比中位数约为 {percent_text(summary.get('profit_yoy_median'))}。",
            f"- 行业整体毛利率约为 {percent_text(summary.get('gross_margin'), signed=False)}，资产负债率中位数约为 {percent_text(summary.get('debt_ratio_median'), signed=False)}，现金转化率中位数约为 {x_text(summary.get('cash_conversion_median'))}。",
            f"- 当前亏损样本数量约为 {summary.get('loss_making_count') or 0} 家，反映行业内部仍存在尚未越过盈亏平衡点的公司。",
            "",
            format_table(headers, table_rows),
            "",
            "### 财务结构观察",
            f"- 营收增速领先样本：{format_company_list(revenue_growers, 'revenue_yoy', lambda value: percent_text(value), limit=5)}。",
            f"- 利润增速领先样本：{format_company_list(profit_growers, 'profit_yoy', lambda value: percent_text(value), limit=5)}。",
            f"- 毛利率领先样本：{format_company_list(margin_leaders, 'gross_margin', lambda value: percent_text(value, signed=False), limit=5)}。",
            "- 如果营收同比改善而利润同比未同步，通常意味着产品价格、折旧摊销、研发投放或渠道费用仍在压制利润释放。",
            "- 如果现金转化率显著高于 1x，说明当前利润兑现质量较好；但若极端偏高，也要结合一次性回款或营运资本波动理解。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )


def build_competition_section(rows: Sequence[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    ranked = sorted(rows, key=lambda item: item.get("market_cap") or 0.0, reverse=True)
    filtered_ranked = [item for item in ranked if not is_special_treatment(item)]
    top3 = ranked[:3]
    next5 = ranked[3:8]
    challengers = [item for item in filtered_ranked if (item.get("profit_yoy") or -9999.0) > 20][:5]
    headers = ["公司", "市值", "ROE", "营收", "净利润", "PE", "PB"]
    table_rows = [
        [
            f"{item['name']}<br>`{item['order_book_id']}`",
            billion_yuan_text(item.get("market_cap")),
            percent_text(item.get("roe"), signed=False),
            billion_yuan_text(item.get("revenue")),
            billion_yuan_text(item.get("net_profit")),
            x_text(item.get("pe"), 1),
            x_text(item.get("pb")),
        ]
        for item in ranked[:10]
    ]
    return "\n".join(
        [
            f"- 行业总市值约为 {billion_yuan_text(summary.get('total_market_cap'))}，CR3 / CR5 / CR10 分别约为 "
            f"{percent_text(concentration([item.get('market_cap') for item in ranked], 3), signed=False)} / "
            f"{percent_text(concentration([item.get('market_cap') for item in ranked], 5), signed=False)} / "
            f"{percent_text(concentration([item.get('market_cap') for item in ranked], 10), signed=False)}。",
            f"- ROE 中位数为 {percent_text(summary.get('roe_median'), signed=False)}；市值龙头未必是盈利质量最优，需要结合 ROE 和利润兑现能力交叉判断。",
            "",
            format_table(headers, table_rows),
            "",
            "### 龙头梯队",
            f"- 第一梯队（市值前三）：{format_company_list(top3, 'market_cap', lambda value: billion_yuan_text(value), limit=3)}。",
            f"- 第二梯队（市值第 4-8 名）：{format_company_list(next5, 'market_cap', lambda value: billion_yuan_text(value), limit=5)}。",
            f"- 利润修复较快、可能提升行业话语权的挑战者：{format_company_list(challengers, 'profit_yoy', lambda value: percent_text(value), limit=5)}。",
            "",
            "### 竞争格局观察",
            "- 高集中度行业通常意味着龙头在品牌、渠道或成本端占优，但也意味着估值会更快反映市场共识。",
            "- 若 CR5 很高而二线公司仍能保持正的利润同比，通常说明行业景气在龙头之外也有扩散。",
            "- 若龙头市值集中但利润修复并未同步集中，往往意味着资金先交易确定性，再等待基本面向二线扩散。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )


def build_valuation_section(rows: Sequence[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    filtered_rows = [item for item in rows if not is_special_treatment(item)]
    positive_pe_ranked = ranked_rows(filtered_rows, "pe", reverse=False, require_positive=True)
    high_dividend_ranked = ranked_rows(filtered_rows, "dividend_yield", reverse=True)
    stressed = [
        item for item in rows
        if item.get("pe") is not None and (item["pe"] <= 0 or item["pe"] >= max((summary.get("pe_median") or 0.0) * 2, 60.0))
    ]
    headers = ["公司", "PE", "PB", "股息率", "ROE", "净利润同比"]
    table_rows = [
        [
            f"{item['name']}<br>`{item['order_book_id']}`",
            x_text(item.get("pe"), 1),
            x_text(item.get("pb")),
            percent_text(item.get("dividend_yield"), signed=False),
            percent_text(item.get("roe"), signed=False),
            percent_text(item.get("profit_yoy")),
        ]
        for item in positive_pe_ranked[:12]
    ]
    return "\n".join(
        [
            f"- 行业 PE 中位数约为 {x_text(summary.get('pe_median'), 1)}，PB 中位数约为 {x_text(summary.get('pb_median'))}，股息率中位数约为 {percent_text(summary.get('dividend_median'), signed=False)}。",
            f"- 正 PE 样本均值约为 {x_text(summary.get('pe_average'), 1)}，说明行业估值分布可能存在被少数高估值公司拉宽的现象。",
            "",
            format_table(headers, table_rows),
            "",
            "### 估值与回报观察",
            f"- 低估值样本（正 PE 由低到高）：{format_company_list(positive_pe_ranked, 'pe', lambda value: x_text(value, 1), limit=5)}。",
            f"- 高股息样本：{format_company_list(high_dividend_ranked, 'dividend_yield', lambda value: percent_text(value, signed=False), limit=5)}。",
            f"- 高估值或亏损样本：{format_company_list(stressed, 'pe', lambda value: x_text(value, 1), limit=5)}。",
            "- 低估值且高 ROE 的公司更容易成为行业中的性价比候选，但若利润同比转负，估值折价也可能有基本面原因。",
            "- 高股息率在成熟行业中有参考价值，但仍需结合现金转化率确认分红可持续性。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )


def build_performance_section(rows: Sequence[Dict[str, Any]], returns: Dict[str, float], summary: Dict[str, Any]) -> str:
    ranked = sorted(
        [
            {**item, "period_return": returns.get(item["order_book_id"])}
            for item in rows
            if item["order_book_id"] in returns
        ],
        key=lambda item: item.get("period_return") or -9999.0,
        reverse=True,
    )
    laggards = [item for item in list(reversed(ranked)) if not is_special_treatment(item)][:5]
    headers = ["公司", "区间收益", "ROE", "PE", "净利润同比", "股息率"]
    table_rows = [
        [
            f"{item['name']}<br>`{item['order_book_id']}`",
            percent_text(item.get("period_return")),
            percent_text(item.get("roe"), signed=False),
            x_text(item.get("pe"), 1),
            percent_text(item.get("profit_yoy")),
            percent_text(item.get("dividend_yield"), signed=False),
        ]
        for item in ranked[:12]
    ]
    return "\n".join(
        [
            f"- 行业成分股区间收益中位数约为 {percent_text(summary.get('sector_return_median'))}，基准指数收益约为 {percent_text(summary.get('benchmark_return'))}，行业超额收益约为 {percent_text(summary.get('excess_return'))}。",
            f"- 上涨家数占比约为 {percent_text(summary.get('breadth'), signed=False)}；收益广度能帮助判断行业行情是龙头驱动还是全面扩散。",
            "",
            format_table(headers, table_rows),
            "",
            "### 表现观察",
            f"- 区间涨幅靠前样本：{format_company_list(ranked, 'period_return', lambda value: percent_text(value), limit=5)}。",
            f"- 区间回撤较大样本：{format_company_list(laggards, 'period_return', lambda value: percent_text(value), limit=5)}。",
            "- 如果行业收益显著跑赢基准，但上涨广度有限，通常说明资金集中在少数龙头。",
            "- 如果收益中位数和上涨广度都不错，说明行业贝塔与个股阿尔法都在改善。",
            "- 如果股价表现明显领先而利润同比尚未兑现，往往意味着市场交易的是预期改善而不是当期盈利。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )


def build_investment_framework_section(
    rows: Sequence[Dict[str, Any]],
    returns: Dict[str, float],
    summary: Dict[str, Any],
    external_findings: Sequence[Dict[str, Any]] = (),
) -> str:
    candidates = []
    for item in rows:
        if is_special_treatment(item):
            continue
        value_signal = item.get("pe") is not None and summary.get("pe_median") is not None and item["pe"] <= summary["pe_median"]
        quality_signal = item.get("roe") is not None and summary.get("roe_median") is not None and item["roe"] >= summary["roe_median"]
        growth_signal = (item.get("profit_yoy") or -999) > 0
        yield_signal = item.get("dividend_yield") is not None and summary.get("dividend_median") is not None and item["dividend_yield"] >= summary["dividend_median"]
        score = sum([value_signal, quality_signal, growth_signal, yield_signal])
        if score >= 3:
            candidates.append({**item, "score": score, "period_return": returns.get(item["order_book_id"])})
    candidates.sort(key=lambda item: (-item["score"], -(item.get("roe") or 0.0), item.get("pe") or 9999.0))

    defensive = [
        item for item in candidates
        if (item.get("dividend_yield") or 0.0) >= (summary.get("dividend_median") or 0.0) and (item.get("roe") or -9999.0) >= (summary.get("roe_median") or -9999.0)
    ][:5]
    repair = [
        {**item, "period_return": returns.get(item["order_book_id"])}
        for item in rows
        if not is_special_treatment(item)
        if (item.get("profit_yoy") or -9999.0) > 20
        and (returns.get(item["order_book_id"]) is None or returns.get(item["order_book_id"], 9999.0) <= (summary.get("sector_return_median") or 0.0))
    ]
    repair = sorted(repair, key=lambda item: item.get("profit_yoy") or -9999.0, reverse=True)[:5]
    momentum = [
        {**item, "period_return": returns.get(item["order_book_id"])}
        for item in rows
        if not is_special_treatment(item)
        if returns.get(item["order_book_id"]) is not None
        and returns[item["order_book_id"]] > 0
        and (item.get("profit_yoy") or -9999.0) > 0
    ]
    momentum = sorted(momentum, key=lambda item: item.get("period_return") or -9999.0, reverse=True)[:5]

    headers = ["公司", "得分", "PE", "ROE", "净利润同比", "股息率", "区间收益"]
    table_rows = [
        [
            f"{item['name']}<br>`{item['order_book_id']}`",
            str(item["score"]),
            x_text(item.get("pe"), 1),
            percent_text(item.get("roe"), signed=False),
            percent_text(item.get("profit_yoy")),
            percent_text(item.get("dividend_yield"), signed=False),
            percent_text(item.get("period_return")),
        ]
        for item in candidates[:10]
    ] or [["暂无候选", "-", "-", "-", "-", "-", "-"]]

    lines = [
        "- 核心筛选规则：PE 不高于行业中位数、ROE 不低于行业中位数、净利润同比为正、股息率不低于行业中位数；满足 4 项中的至少 3 项进入优先观察名单。",
        "",
        format_table(headers, table_rows),
        "",
        "### 投资线索",
    ]
    if candidates:
        for item in candidates[:6]:
            lines.append(
                f"- **{item['name']}（{item['order_book_id']}）**：满足 {item['score']} 项条件，"
                f"PE {x_text(item.get('pe'), 1)}、ROE {percent_text(item.get('roe'), signed=False)}、"
                f"净利润同比 {percent_text(item.get('profit_yoy'))}、股息率 {percent_text(item.get('dividend_yield'), signed=False)}。"
            )
    else:
        lines.append("- 当前没有满足至少 3 项条件的公司，说明行业要么整体估值偏高，要么盈利质量尚未全面改善。")
    lines.extend(
        [
            "",
            "### 分组跟踪",
            f"- 防御型配置线索：{format_company_list(defensive, 'dividend_yield', lambda value: percent_text(value, signed=False), limit=5)}。",
            f"- 盈利修复但股价尚未充分反映的样本：{format_company_list(repair, 'profit_yoy', lambda value: percent_text(value), limit=5)}。",
            f"- 景气与股价共振样本：{format_company_list(momentum, 'period_return', lambda value: percent_text(value), limit=5)}。",
            "- 后续跟踪建议优先围绕三类变量展开：利润增速能否延续、ROE 是否稳定高于行业中位数、估值是否在盈利兑现后仍具备吸引力。",
            "",
            "### 跟踪指标清单",
            f"- 基本面指标：营收同比中位数 {percent_text(summary.get('revenue_yoy_median'))}、净利润同比中位数 {percent_text(summary.get('profit_yoy_median'))}、ROE 中位数 {percent_text(summary.get('roe_median'), signed=False)}。",
            f"- 交易指标：区间收益中位数 {percent_text(summary.get('sector_return_median'))}、超额收益 {percent_text(summary.get('excess_return'))}、上涨广度 {percent_text(summary.get('breadth'), signed=False)}。",
            f"- 估值指标：PE 中位数 {x_text(summary.get('pe_median'), 1)}、PB 中位数 {x_text(summary.get('pb_median'))}、股息率中位数 {percent_text(summary.get('dividend_median'), signed=False)}。",
            "- 当基本面改善先于交易指标时，更适合等待验证；当交易指标先于基本面改善时，则要警惕预期交易过满后的回撤压力。",
            "",
            "*数据来源：RQData，置信度5*",
        ]
    )
    if external_findings:
        lines.extend(["", *build_external_context_block(external_findings[:3], "### 外部线索补充")])
    return "\n".join(lines)


def build_risk_section(summary: Dict[str, Any], stats: Dict[str, Any], rows: Sequence[Dict[str, Any]]) -> str:
    lines = []
    if stats["covered_size"] < stats["pool_size"]:
        lines.append(f"- **样本覆盖风险**：股票池 {stats['pool_size']} 家中仅有 {stats['covered_size']} 家进入主导季度比较口径，行业结论仍有覆盖缺口。")
    cr5 = concentration([item.get("market_cap") for item in rows], 5)
    if cr5 is not None and cr5 > 70:
        lines.append(f"- **龙头集中风险**：CR5 约为 {percent_text(cr5, signed=False)}，行业收益和估值容易被少数龙头主导。")
    if summary.get("excess_return") is not None and summary["excess_return"] > 15:
        lines.append(f"- **预期透支风险**：行业近 180 天相对基准超额收益约为 {percent_text(summary['excess_return'])}，短期定价可能已不便宜。")
    if summary.get("profit_yoy_median") is not None and summary["profit_yoy_median"] < 0:
        lines.append(f"- **盈利承压风险**：净利润同比中位数约为 {percent_text(summary['profit_yoy_median'])}，说明行业盈利趋势尚未普遍改善。")
    if not lines:
        lines.append("- 当前未识别到突出的新增风险，但仍需跟踪主导季度财务兑现、估值位置和行业广度变化。")
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_appendix(args: argparse.Namespace, sector_definition: Optional[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    lines = [
        f"- 报告日期为 {args.report_date}，行业名称为 `{args.industry}`。",
        f"- 行业样本来自显式股票池，股票池规模 {stats['pool_size']} 家，进入主导季度比较口径 {stats['covered_size']} 家。",
        f"- 主导财报季度为 `{stats['dominant_quarter'] or '无数据'}`，同比比较季度为 `{stats['yoy_quarter'] or '无数据'}`；行业同比结论基于该口径计算。",
        f"- 当前行业来源为 `{(sector_definition or {}).get('industry_source', '未提供')}`，行业层级为 `{(sector_definition or {}).get('industry_level', '未提供')}`。",
        "- 估值和价格指标应使用最近非空因子日；若最近交易日返回空值，需要向前回溯至可用日期。",
        "- 股息率原始单位为 bps，报告展示时已换算为百分比。",
        "- 本报告旨在提供行业横向比较和跟踪框架，不替代针对单一公司的深度尽调。",
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
            print(f"警告：仓库内 render_report.py 执行失败：{exc}")

    print("警告：未找到可用的 HTML 渲染器，保留 Markdown 输出")
    return None


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir).expanduser()

    sector_definition = read_json_file(data_dir / "sector_definition.json")
    stock_pool_records = extract_records(read_json_file(data_dir / "stock_pool.json"))
    instrument_records = extract_records(read_json_file(data_dir / "instrument_meta.json"))
    industry_records = extract_records(read_json_file(data_dir / "industry_map.json"))
    historical_financials = extract_records(read_json_file(data_dir / "historical_financials.json"))
    latest_financials = extract_records(read_json_file(data_dir / "latest_financials.json"))
    roe_records = extract_records(read_json_file(data_dir / "roe.json"))
    market_cap_records = extract_records(read_json_file(data_dir / "market_cap.json"))
    pe_records = extract_records(read_json_file(data_dir / "pe_ratio.json"))
    pb_records = extract_records(read_json_file(data_dir / "pb_ratio.json"))
    dividend_records = extract_records(read_json_file(data_dir / "dividend_yield.json"))
    price_records = extract_records(read_json_file(data_dir / "price_period.json"))
    benchmark_records = extract_records(read_json_file(data_dir / "benchmark_price.json"))
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))
    validate_web_search_records(web_search_records)
    external_findings = extract_external_findings(web_search_records)

    stock_ids = build_stock_pool(stock_pool_records)
    if not stock_ids:
        raise ValueError("stock_pool.json 中缺少有效股票池。")

    rows, stats = build_sector_rows(
        stock_ids,
        instrument_records,
        industry_records,
        latest_financials or historical_financials,
        roe_records,
        market_cap_records,
        pe_records,
        pb_records,
        dividend_records,
    )
    returns = build_return_map(price_records, stock_ids)
    benchmark_return = build_benchmark_return(benchmark_records)
    summary = sector_summary(rows, returns, benchmark_return)
    validate_dataset_quality(stock_ids, rows, returns, summary)

    exec_summary = build_exec_summary(args.industry, stats, summary, rows, external_findings)
    sector_scope = build_scope_section(args.industry, sector_definition if isinstance(sector_definition, dict) else None, stats, rows)
    sector_state = build_sector_state(args.industry, summary, rows, external_findings)
    financial_structure = build_financial_section(rows, summary, stats)
    competition = build_competition_section(rows, summary)
    valuation = build_valuation_section(rows, summary)
    performance = build_performance_section(rows, returns, summary)
    investment_framework = build_investment_framework_section(rows, returns, summary, external_findings)
    risk_section = build_risk_section(summary, stats, rows)
    appendix = build_appendix(
        args,
        sector_definition if isinstance(sector_definition, dict) else None,
        stats,
    )

    template_text = Path(args.template).read_text(encoding="utf-8")
    report_text = render_template(
        template_text,
        {
            "REPORT_DATE": args.report_date,
            "INDUSTRY_NAME": args.industry,
            "EXEC_SUMMARY": exec_summary,
            "SECTOR_SCOPE": sector_scope,
            "SECTOR_STATE": sector_state,
            "FINANCIAL_STRUCTURE": financial_structure,
            "COMPETITION": competition,
            "VALUATION": valuation,
            "PERFORMANCE": performance,
            "INVESTMENT_FRAMEWORK": investment_framework,
            "RISK_SECTION": risk_section,
            "APPENDIX": appendix,
        },
    )

    output_path = Path(args.output).expanduser() if args.output else data_dir / f"sector_overview_{args.industry}_{args.report_date}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
