#!/usr/bin/env python3
"""Template-driven morning note report generator."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 晨会纪要",
    "## 执行摘要",
    "## 隔夜动态",
    "## 昨日市场回顾",
    "## 今日重点关注",
    "## 交易观察",
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
    "macro_context",
    "policy_context",
    "industry_context",
    "global_market_context",
    "commodity_context",
}

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")
TITLE_FIELDS = ("title", "announcement_title", "info_name", "name", "summary")
ANNOUNCEMENT_DATE_FIELDS = ("announcement_date", "ann_date", "pub_date", "info_date", "datetime", "date")
DIVIDEND_DATE_FIELDS = ("announcement_date", "ex_dividend_date", "book_closure_date", "payable_date", "date")


@dataclass
class EarningsItem:
    ticker: str
    company: str
    quarter: str
    report_date: date
    revenue: Optional[float]
    net_profit: Optional[float]


@dataclass
class AnnouncementItem:
    ticker: str
    company: str
    title: str
    event_date: date
    category: str
    announcement_link: Optional[str]
    info_type: str
    media: str


@dataclass
class DividendItem:
    ticker: str
    company: str
    event_label: str
    event_date: date
    amount_note: str


@dataclass
class PriceMove:
    ticker: str
    company: str
    last_close: float
    change_pct: float
    turnover: Optional[float]


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
    parser = argparse.ArgumentParser(description="生成模板驱动的晨会纪要报告")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", default=date.today().isoformat(), help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--lookback-start", help="隔夜观察起始日，默认报告日前 1 天")
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


def normalize_title(record: Dict[str, Any]) -> str:
    value = pick_first(record, TITLE_FIELDS)
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


def build_company_lookup(*collections: List[Any]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for collection in collections:
        for item in collection:
            if isinstance(item, str):
                lookup.setdefault(item, item)
                continue
            if not isinstance(item, dict):
                continue
            ticker = normalize_ticker(item)
            if not ticker:
                continue
            lookup[ticker] = normalize_name(item) or lookup.get(ticker) or ticker
    return lookup


def resolve_company_name(ticker: str, record: Dict[str, Any], lookup: Dict[str, str]) -> str:
    return lookup.get(ticker) or normalize_name(record) or ticker or "未知公司"


def in_window(value: Optional[date], start_date: date, end_date: date) -> bool:
    return value is not None and start_date <= value <= end_date


def choose_first_date(record: Dict[str, Any], fields: Sequence[str]) -> Optional[date]:
    for field in fields:
        parsed = parse_iso_date(record.get(field))
        if parsed:
            return parsed
    return None


def classify_announcement(title: str) -> Optional[str]:
    low_signal_keywords = (
        "独立董事述职报告",
        "投资者关系活动记录表",
        "大宗交易",
        "H股公告",
        "证券变动月报表",
        "可持续发展报告摘要",
        "内部控制审计报告",
        "内部控制评价报告",
        "履职情况报告",
    )
    if any(keyword in title for keyword in low_signal_keywords):
        return None

    rules = [
        ("财报披露", ("业绩快报", "业绩预告", "年度报告", "半年度报告", "季报", "一季度报告", "三季度报告", "年报", "中报")),
        ("分红回报", ("利润分配", "分红", "派息", "权益分派")),
        ("治理事项", ("股东大会", "董事会", "监事会")),
        ("资本运作", ("回购", "增持", "减持", "定增", "发行股份")),
        ("投资者交流", ("业绩说明会", "说明会", "电话会", "路演")),
        ("经营更新", ("签署", "中标", "合同", "进展", "合作", "项目", "产销快报", "销量", "月报", "经营数据")),
    ]
    for label, keywords in rules:
        if any(keyword in title for keyword in keywords):
            return label
    return None


def format_amount_yi(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def format_pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "无数据"
    return f"{value:+.{digits}f}%"


def format_turnover(value: Optional[float]) -> str:
    if value is None:
        return "无数据"
    return f"{value / 1e8:.2f}亿元"


def format_link_markdown(url: Optional[str], label: str = "原文") -> str:
    if not url:
        return ""
    return f"[{label}]({url})"


def summarize_coverage(tickers: Sequence[str], lookup: Dict[str, str]) -> str:
    if not tickers:
        return "未识别覆盖股票池"
    labels = [f"{lookup.get(ticker, ticker)}({ticker})" for ticker in tickers[:6]]
    suffix = " 等" if len(tickers) > 6 else ""
    return f"{len(tickers)}只股票：{'、'.join(labels)}{suffix}"


def load_raw_inputs(data_dir: Path) -> Tuple[Dict[str, List[Any]], List[str]]:
    file_map = {
        "stock_pool": "stock_pool.json",
        "instrument_meta": "instrument_meta.json",
        "latest_earnings": "latest_earnings.json",
        "price_recent": "price_recent.json",
        "hs300_recent": "hs300_recent.json",
        "dividend_news": "dividend_news.json",
        "announcement_raw": "announcement_raw.json",
    }
    loaded: Dict[str, List[Any]] = {}
    missing: List[str] = []
    for key, filename in file_map.items():
        records = extract_records(read_json_file(data_dir / filename))
        loaded[key] = records
        if not records:
            missing.append(filename)
    return loaded, missing


def collect_tickers(raw_inputs: Dict[str, List[Any]]) -> List[str]:
    tickers: List[str] = []
    seen = set()
    for key in ("stock_pool", "instrument_meta", "price_recent", "latest_earnings", "announcement_raw", "dividend_news"):
        for item in raw_inputs.get(key, []):
            ticker = ""
            if isinstance(item, str):
                ticker = item
            elif isinstance(item, dict):
                ticker = normalize_ticker(item)
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers


def extract_recent_earnings(
    records: List[Any],
    lookup: Dict[str, str],
    start_date: date,
    end_date: date,
) -> List[EarningsItem]:
    items: List[EarningsItem] = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(record)
        report_date = choose_first_date(record, ("report_date", "info_date", "announcement_date"))
        if not ticker or not in_window(report_date, start_date, end_date):
            continue
        quarter = str(record.get("quarter") or record.get("report_period") or "最近一期")
        unique_key = (ticker, quarter, report_date.isoformat())
        if unique_key in seen:
            continue
        seen.add(unique_key)
        items.append(
            EarningsItem(
                ticker=ticker,
                company=resolve_company_name(ticker, record, lookup),
                quarter=quarter,
                report_date=report_date,
                revenue=float_or_none(record.get("revenue")),
                net_profit=float_or_none(record.get("net_profit")),
            )
        )
    items.sort(key=lambda item: (item.report_date, item.ticker), reverse=True)
    return items


def extract_recent_announcements(
    records: List[Any],
    lookup: Dict[str, str],
    start_date: date,
    end_date: date,
) -> List[AnnouncementItem]:
    items: List[AnnouncementItem] = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(record)
        title = normalize_title(record)
        event_date = choose_first_date(record, ANNOUNCEMENT_DATE_FIELDS)
        category = classify_announcement(title)
        if not ticker or not title or not category or not in_window(event_date, start_date, end_date):
            continue
        unique_key = (ticker, title, event_date.isoformat())
        if unique_key in seen:
            continue
        seen.add(unique_key)
        items.append(
            AnnouncementItem(
                ticker=ticker,
                company=resolve_company_name(ticker, record, lookup),
                title=title,
                event_date=event_date,
                category=category,
                announcement_link=normalize_link(record.get("announcement_link")),
                info_type=str(record.get("info_type") or "未分类"),
                media=str(record.get("media") or "未知来源"),
            )
        )
    items.sort(key=lambda item: (item.event_date, item.ticker), reverse=True)
    return items


def build_as_of_time(report_date: date, raw_inputs: Dict[str, List[Any]]) -> str:
    latest_dt: Optional[datetime] = None
    for key in ("announcement_raw", "latest_earnings", "dividend_news", "price_recent", "hs300_recent", "web_search_findings"):
        for item in raw_inputs.get(key, []):
            if not isinstance(item, dict):
                continue
            for field in ("create_tm", "datetime", "info_date", "report_date", "announcement_date", "date"):
                current = parse_iso_datetime(item.get(field))
                if current and (latest_dt is None or current > latest_dt):
                    latest_dt = current
    if latest_dt:
        if latest_dt.time() == datetime.min.time():
            return latest_dt.strftime("%Y-%m-%d")
        return latest_dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{report_date.isoformat()} 07:00:00"


def compute_avg_move(price_moves: Sequence[PriceMove]) -> Optional[float]:
    if not price_moves:
        return None
    return sum(item.change_pct for item in price_moves) / len(price_moves)


def infer_opening_stance(
    earnings_items: List[EarningsItem],
    announcement_items: List[AnnouncementItem],
    external_findings: List[ExternalFinding],
    price_moves: List[PriceMove],
    benchmark_move: Optional[float],
) -> Tuple[str, str, str]:
    avg_move = compute_avg_move(price_moves)
    avg_excess = avg_move - benchmark_move if avg_move is not None and benchmark_move is not None else avg_move
    finance_count = len(earnings_items) + sum(1 for item in announcement_items if item.category == "财报披露")
    core_count = sum(1 for item in announcement_items if item.category not in {"财报披露", "分红回报"})
    external_count = len(external_findings)

    if finance_count:
        theme = f"今日晨会主线偏向财报/经营更新，隔夜共出现 {finance_count} 条财务披露，盘前需要优先确认是否触发预期修正。"
    elif core_count:
        theme = f"今日晨会主线偏向公告催化，隔夜共出现 {core_count} 条高相关度公告，重点判断事项是否足以驱动资金重新定价。"
    elif external_count:
        theme = f"今日晨会主线偏向网络搜索结果变化，隔夜补充到 {external_count} 条宏观/行业线索，盘前需要判断其是否会向覆盖股票池传导。"
    else:
        theme = "今日晨会缺少强事件催化，主线将更多依赖相对强弱和开盘后的量价确认。"

    if external_count and avg_excess is not None and avg_excess >= 0:
        stance = "偏积极，网络搜索结果没有削弱风险偏好时，优先跟踪有基本面或公告配合的强势线索。"
        position = "以结构性偏多为主，但只围绕真实催化和量价确认配置，不把网络搜索结果直接等同于交易结论。"
    elif external_count and avg_excess is not None and avg_excess < 0:
        stance = "中性偏谨慎，需先确认网络搜索结果能否对冲样本内的相对弱势。"
        position = "控制追价节奏，优先等待网络搜索结果与个股公告、价格表现形成共振后再扩大风险暴露。"
    elif avg_excess is not None and avg_excess >= 0.5:
        stance = "偏积极，优先跟踪强势股的延续性，同时确认是否有基本面或公告继续配合。"
        position = "结构性偏多，保留强势股跟踪仓位，但不宜在缺少新增催化时盲目追高。"
    elif avg_excess is not None and avg_excess <= -0.5:
        stance = "偏谨慎，盘前应先排查负面信息与预期落空，再决定是否参与弱势修复。"
        position = "以防守和确认信息为主，弱势股需要看到负面出清或量价改善后再考虑加仓。"
    else:
        stance = "中性，优先依赖公告增量与开盘后的市场反馈来决定仓位方向。"
        position = "维持中性仓位，围绕真实催化和相对强弱做结构性观察，不急于扩大风险暴露。"
    return theme, stance, position


def announcement_check_point(item: AnnouncementItem) -> str:
    if item.category == "财报披露":
        return "盘前核查管理层表述、分红方案和全年经营指引是否超出市场预期。"
    if item.category == "经营更新":
        return "盘前核查销量、订单、项目或经营数据是否足以带来当期预期修正。"
    if item.category == "资本运作":
        return "盘前核查回购、增减持或融资事项对流通预期和情绪面的影响。"
    if item.category == "治理事项":
        return "盘前核查议案内容是否会引出新的治理、分红或资本运作催化。"
    if item.category == "投资者交流":
        return "盘前核查说明会主题、管理层出席安排以及是否可能释放新的经营口径。"
    return "盘前核查公告是否会改变盈利预期、情绪定价或资金关注度。"


def describe_dividend_amount(record: Dict[str, Any]) -> str:
    candidates = (
        ("cash_dividend_per_share", "每股派现"),
        ("dividend_cash_before_tax", "税前现金分红"),
        ("cash_dividend", "现金分红"),
        ("dividend_per_share", "每股分红"),
    )
    for field, label in candidates:
        value = float_or_none(record.get(field))
        if value is not None:
            return f"{label}{value:.4f}"
    return "金额字段缺失"


def extract_recent_dividends(
    records: List[Any],
    lookup: Dict[str, str],
    start_date: date,
    end_date: date,
) -> List[DividendItem]:
    items: List[DividendItem] = []
    seen = set()
    tomorrow = end_date + timedelta(days=1)
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(record)
        if not ticker:
            continue

        announcement_date = choose_first_date(record, ("announcement_date", "ann_date", "date"))
        ex_dividend_date = choose_first_date(record, ("ex_dividend_date", "book_closure_date"))

        event_date: Optional[date] = None
        event_label = ""
        if in_window(announcement_date, start_date, end_date):
            event_date = announcement_date
            event_label = "新披露分红信息"
        elif ex_dividend_date and end_date <= ex_dividend_date <= tomorrow:
            event_date = ex_dividend_date
            event_label = "临近除权除息"

        if event_date is None:
            continue

        unique_key = (ticker, event_label, event_date.isoformat())
        if unique_key in seen:
            continue
        seen.add(unique_key)

        items.append(
            DividendItem(
                ticker=ticker,
                company=lookup.get(ticker, ticker),
                event_label=event_label,
                event_date=event_date,
                amount_note=describe_dividend_amount(record),
            )
        )
    items.sort(key=lambda item: (item.event_date, item.ticker), reverse=True)
    return items


def extract_price_moves(records: List[Any], lookup: Dict[str, str]) -> List[PriceMove]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(record)
        if ticker:
            grouped[ticker].append(record)

    items: List[PriceMove] = []
    for ticker, series in grouped.items():
        normalized: List[Tuple[date, float, Optional[float]]] = []
        for record in series:
            event_date = choose_first_date(record, ("datetime", "date"))
            close = float_or_none(record.get("close"))
            if event_date is None or close is None:
                continue
            normalized.append((event_date, close, float_or_none(record.get("total_turnover"))))
        normalized.sort(key=lambda item: item[0])
        deduped: Dict[date, Tuple[float, Optional[float]]] = {}
        for event_date, close, turnover in normalized:
            deduped[event_date] = (close, turnover)
        ordered = sorted(deduped.items(), key=lambda item: item[0])
        if len(ordered) < 2:
            continue
        prev_close = ordered[-2][1][0]
        last_close, turnover = ordered[-1][1]
        change_pct = (last_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
        items.append(
            PriceMove(
                ticker=ticker,
                company=lookup.get(ticker, ticker),
                last_close=last_close,
                change_pct=change_pct,
                turnover=turnover,
            )
        )
    items.sort(key=lambda item: item.change_pct, reverse=True)
    return items


def extract_benchmark_move(records: List[Any]) -> Optional[float]:
    normalized: List[Tuple[date, float]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        event_date = choose_first_date(record, ("datetime", "date"))
        close = float_or_none(record.get("close"))
        if event_date is None or close is None:
            continue
        normalized.append((event_date, close))
    normalized = sorted({event_date: close for event_date, close in normalized}.items(), key=lambda item: item[0])
    if len(normalized) < 2:
        return None
    prev_close = normalized[-2][1]
    last_close = normalized[-1][1]
    if not prev_close:
        return None
    return (last_close / prev_close - 1.0) * 100.0


def build_exec_summary(
    earnings_items: List[EarningsItem],
    announcement_items: List[AnnouncementItem],
    dividend_items: List[DividendItem],
    external_findings: List[ExternalFinding],
    price_moves: List[PriceMove],
    benchmark_move: Optional[float],
) -> str:
    finance_announcement_count = sum(1 for item in announcement_items if item.category == "财报披露")
    core_announcement_count = sum(1 for item in announcement_items if item.category not in {"财报披露", "分红回报"})
    dividend_announcement_count = sum(1 for item in announcement_items if item.category == "分红回报")
    theme, stance, position = infer_opening_stance(
        earnings_items,
        announcement_items,
        external_findings,
        price_moves,
        benchmark_move,
    )
    lines: List[str] = []
    lines.append(f"**核心观点**：{theme}")
    lines.append(
        f"隔夜窗口内共识别出 {len(earnings_items) + finance_announcement_count} 条财务披露、"
        f"{core_announcement_count} 条重点公告、"
        f"{len(dividend_items) + dividend_announcement_count} 条分红相关事项"
        + (f"、{len(external_findings)} 条宏观/行业网络搜索结果。" if external_findings else "。")
    )
    if price_moves:
        avg_move = compute_avg_move(price_moves)
        leader = price_moves[0]
        laggard = min(price_moves, key=lambda item: item.change_pct)
        benchmark_text = format_pct(benchmark_move) if benchmark_move is not None else "无基准数据"
        lines.append(
            f"覆盖股票池昨日平均涨跌幅为 {format_pct(avg_move)}，沪深300 为 {benchmark_text}；"
            f"相对强势个股为 {leader.company}({leader.ticker}) {format_pct(leader.change_pct)}，"
            f"相对偏弱个股为 {laggard.company}({laggard.ticker}) {format_pct(laggard.change_pct)}。"
        )
    else:
        lines.append("价格数据不足，昨日市场回顾仅保留事件层面的重点提示。")
    lines.append(f"**盘前定位**：{stance}")
    lines.append(f"**持仓建议**：{position}")
    lines.append("*数据来源：RQData，置信度5*")
    if external_findings:
        lines.append(
            f"*补充网络搜索结果：{external_findings[0].source_name}"
            + (
                f" 等 {len(external_findings)} 个来源，"
                f"置信度{min(item.confidence for item in external_findings)}-{max(item.confidence for item in external_findings)}*"
            )
        )
    return "\n\n".join(lines)


def build_external_context_section(external_findings: List[ExternalFinding]) -> List[str]:
    if not external_findings:
        return []
    lines = ["### 宏观与行业语境"]
    for item in external_findings[:5]:
        lines.append(
            f"- **{item.published_at.isoformat()} {item.source_name}**：{item.title}。{item.summary}。"
            f" 对盘前判断的意义：{item.why_relevant}。 *数据来源：{item.source_name}，置信度{item.confidence}*"
        )
    return lines


def build_overnight_section(
    earnings_items: List[EarningsItem],
    announcement_items: List[AnnouncementItem],
    dividend_items: List[DividendItem],
    external_findings: List[ExternalFinding],
) -> str:
    finance_announcements = [item for item in announcement_items if item.category == "财报披露"]
    core_announcements = [item for item in announcement_items if item.category not in {"财报披露", "分红回报"}]
    dividend_announcements = [item for item in announcement_items if item.category == "分红回报"]
    lines: List[str] = []
    if external_findings:
        lines.extend(build_external_context_section(external_findings))
        lines.append("")
    if earnings_items:
        lines.append("### 财务披露")
        for item in earnings_items[:5]:
            lines.append(
                f"- **{item.company}({item.ticker})**：{item.report_date.isoformat()} 披露 {item.quarter}，"
                f"营收 {format_amount_yi(item.revenue)}，净利润 {format_amount_yi(item.net_profit)}；"
                "盘前应优先确认利润兑现、现金流质量与管理层口径是否支持当前估值。"
            )
        if finance_announcements:
            for item in finance_announcements[:4]:
                link_text = format_link_markdown(item.announcement_link)
                lines.append(
                    f"- **{item.company}({item.ticker})**：{item.event_date.isoformat()} 披露 `{item.title}`，"
                    f"属于公告口径下的财报/业绩更新，来源 {item.media}；{announcement_check_point(item)}"
                    + (f" {link_text}" if link_text else "")
                )
        lines.append("*数据来源：RQData，置信度5*")
    elif finance_announcements:
        lines.append("### 财务披露")
        for item in finance_announcements[:6]:
            link_text = format_link_markdown(item.announcement_link)
            lines.append(
                f"- **{item.company}({item.ticker})**：{item.event_date.isoformat()} 披露 `{item.title}`，"
                f"属于公告口径下的财报/业绩更新，来源 {item.media}；{announcement_check_point(item)}"
                + (f" {link_text}" if link_text else "")
            )
        lines.append("*数据来源：RQData，置信度5*")
    else:
        lines.append("### 财务披露")
        lines.append("- 隔夜窗口内未识别到覆盖股票池新的财报披露记录。")
        lines.append("*数据来源：RQData，置信度5*")

    lines.append("")
    if core_announcements:
        lines.append("### 重点公告")
        for item in core_announcements[:6]:
            link_text = format_link_markdown(item.announcement_link)
            lines.append(
                f"- **{item.company}({item.ticker})**：{item.event_date.isoformat()} 披露 `{item.title}`，归类为{item.category}，"
                f"来源 {item.media}；{announcement_check_point(item)}"
                + (f" {link_text}" if link_text else "")
            )
        lines.append("*数据来源：RQData，置信度5*")
    else:
        lines.append("### 重点公告")
        lines.append("- 隔夜窗口内未识别到高相关度公告。")
        lines.append("*数据来源：RQData，置信度5*")

    lines.append("")
    if dividend_items or dividend_announcements:
        lines.append("### 分红事项")
        for item in dividend_items[:4]:
            lines.append(
                f"- **{item.company}({item.ticker})**：{item.event_label}，日期 {item.event_date.isoformat()}，{item.amount_note}。"
            )
        for item in dividend_announcements[:4]:
            link_text = format_link_markdown(item.announcement_link)
            lines.append(
                f"- **{item.company}({item.ticker})**：{item.event_date.isoformat()} 披露 `{item.title}`，属于分红/利润分配相关公告；"
                "盘前需确认方案是否兑现为股息率改善或情绪催化。"
                + (f" {link_text}" if link_text else "")
            )
        lines.append("*数据来源：RQData，置信度5*")
    else:
        lines.append("### 分红事项")
        lines.append("- 隔夜窗口内未识别到新增分红披露或临近除权除息事项。")
        lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_market_recap(price_moves: List[PriceMove], benchmark_move: Optional[float]) -> str:
    if not price_moves:
        return "价格数据不足，无法生成市场回顾表。\n\n*数据来源：RQData，置信度5*"

    up_count = sum(1 for item in price_moves if item.change_pct > 0)
    down_count = sum(1 for item in price_moves if item.change_pct < 0)
    flat_count = len(price_moves) - up_count - down_count
    avg_move = sum(item.change_pct for item in price_moves) / len(price_moves)
    benchmark_text = format_pct(benchmark_move) if benchmark_move is not None else "无基准数据"

    avg_turnover = [item.turnover for item in price_moves if item.turnover is not None]
    avg_turnover_text = format_turnover(sum(avg_turnover) / len(avg_turnover)) if avg_turnover else "无数据"

    lines = [
        f"覆盖股票池昨日平均涨跌幅 {format_pct(avg_move)}，上涨 {up_count} 家、下跌 {down_count} 家、平盘 {flat_count} 家；沪深300 为 {benchmark_text}，"
        f"样本单票平均成交额约为 {avg_turnover_text}。",
        "",
        "| 股票 | 收盘价 | 涨跌幅 | 成交额 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in price_moves:
        lines.append(
            f"| {item.company}({item.ticker}) | {item.last_close:.2f} | {format_pct(item.change_pct)} | {format_turnover(item.turnover)} |"
        )
    leader = price_moves[0]
    laggard = min(price_moves, key=lambda item: item.change_pct)
    lines.append("")
    lines.append(
        f"相对强势的 {leader.company} 录得 {format_pct(leader.change_pct)}，"
        f"相对偏弱的 {laggard.company} 为 {format_pct(laggard.change_pct)}，"
        "两者将优先进入今日盘前观察名单。"
    )
    if benchmark_move is not None:
        breadth_bias = "偏强" if avg_move >= benchmark_move else "偏弱"
        lines.append(
            f"从广度看，当前覆盖池整体相对基准{breadth_bias}；若开盘后强势股继续放量、弱势股未见新增利空，短线风格延续概率更高。"
        )
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_watchlist(
    earnings_items: List[EarningsItem],
    announcement_items: List[AnnouncementItem],
    dividend_items: List[DividendItem],
    external_findings: List[ExternalFinding],
    price_moves: List[PriceMove],
    benchmark_move: Optional[float],
) -> str:
    lines: List[str] = []
    used = set()

    for item in earnings_items[:2]:
        key = ("earnings", item.ticker)
        if key in used:
            continue
        used.add(key)
        lines.append(
            f"- **跟踪 {item.company}({item.ticker})**：隔夜披露 {item.quarter}，"
            f"重点确认营收 {format_amount_yi(item.revenue)} 与净利润 {format_amount_yi(item.net_profit)} 是否继续支撑股价表现，"
            "并结合管理层表述判断预期差方向。"
        )

    for item in announcement_items[:2]:
        key = ("announcement", item.ticker, item.title)
        if key in used:
            continue
        used.add(key)
        lines.append(
            f"- **关注 {item.company}({item.ticker})**：最新公告为 `{item.title}`，"
            f"属于{item.category}，盘前需要判断该事项是否会带来预期修正；{announcement_check_point(item)}"
        )

    for item in dividend_items[:1]:
        key = ("dividend", item.ticker)
        if key in used:
            continue
        used.add(key)
        lines.append(
            f"- **留意 {item.company}({item.ticker})**：{item.event_label}，日期为 {item.event_date.isoformat()}，"
            f"分红信息为 `{item.amount_note}`，需确认其对股息率和情绪面的边际影响。"
        )

    for item in external_findings[:2]:
        lines.append(
            f"- **网络搜索结果跟踪**：{item.source_name} 最新提到“{item.title}”，"
            f"盘前需要确认这条线索是否会传导到覆盖池中的相关行业或主题。"
        )

    if price_moves:
        leader = price_moves[0]
        laggard = min(price_moves, key=lambda item: item.change_pct)
        excess = leader.change_pct - benchmark_move if benchmark_move is not None else leader.change_pct
        lines.append(
            f"- **相对强势观察**：{leader.company}({leader.ticker}) 昨日涨跌幅 {format_pct(leader.change_pct)}，"
            f"相对沪深300 的超额收益约为 {format_pct(excess)}，盘前重点观察是否存在公告或基本面配合。"
        )
        lines.append(
            f"- **相对偏弱观察**：{laggard.company}({laggard.ticker}) 昨日涨跌幅 {format_pct(laggard.change_pct)}，"
            "若开盘仍弱于板块，需要确认是否存在新增负面信息或前一日交易拥挤导致的补跌。"
        )

    if not lines:
        lines.append("- 无新增高优先级事件，今日重点以价格相对强弱和公告增量信息为主。")

    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    if external_findings:
        lines.append(
            f"*补充网络搜索结果：{external_findings[0].source_name}"
            + (
                f" 等 {len(external_findings)} 个来源，"
                f"置信度{min(item.confidence for item in external_findings)}-{max(item.confidence for item in external_findings)}*"
            )
        )
    return "\n".join(lines)


def build_trade_observations(
    earnings_items: List[EarningsItem],
    announcement_items: List[AnnouncementItem],
    price_moves: List[PriceMove],
    benchmark_move: Optional[float],
) -> str:
    lines: List[str] = []
    if price_moves:
        leader = price_moves[0]
        excess = leader.change_pct - benchmark_move if benchmark_move is not None else leader.change_pct
        lines.append(
            f"- **强势延续观察：{leader.company}({leader.ticker})**。昨日录得 {format_pct(leader.change_pct)}，"
            f"相对基准超额收益约 {format_pct(excess)}；若盘前没有新的负面公告，开盘后的量价延续值得跟踪。"
        )
        lines.append(
            "- 强势延续失效条件：若开盘后迅速跌回前一日收盘下方且成交并未放大，说明强势更多来自短线波动而非新增信息。"
        )
        laggard = min(price_moves, key=lambda item: item.change_pct)
        lines.append(
            f"- **弱势修复观察：{laggard.company}({laggard.ticker})**。昨日表现为 {format_pct(laggard.change_pct)}，"
            "若隔夜无新增利空且低开后快速收窄跌幅，可能形成短线修复观察点。"
        )
        lines.append(
            "- 弱势修复失效条件：若弱势继续放量扩大且跑输板块，说明负面预期仍在发酵，应避免把技术性反弹误判为修复。"
        )
    if earnings_items:
        item = earnings_items[0]
        lines.append(
            f"- **事件跟踪观察：{item.company}({item.ticker})**。最新披露 {item.quarter}，"
            f"营收 {format_amount_yi(item.revenue)}、净利润 {format_amount_yi(item.net_profit)}，"
            "盘前需结合市场预期判断情绪发酵方向。"
        )
    elif announcement_items:
        item = announcement_items[0]
        link_text = format_link_markdown(item.announcement_link)
        lines.append(
            f"- **公告催化观察：{item.company}({item.ticker})**。最新事项为 `{item.title}`，"
            f"属于{item.category}，需判断其是否足以驱动开盘后的资金聚焦。"
            + (f" {link_text}" if link_text else "")
        )

    if not lines:
        lines.append("- 当前数据不足以支持高置信度交易观察，建议优先等待新增公告或开盘后量价确认。")

    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_risk_alerts(
    announcement_items: List[AnnouncementItem],
    external_findings: List[ExternalFinding],
    price_moves: List[PriceMove],
    benchmark_move: Optional[float],
    missing_files: List[str],
) -> str:
    lines: List[str] = []
    if "announcement_raw.json" in missing_files:
        lines.append("- **公告覆盖风险**：当前隔夜公告样本不足，盘前结论可能遗漏正式披露的新增信息。")
    if price_moves:
        avg_move = compute_avg_move(price_moves)
        if benchmark_move is not None and avg_move is not None and avg_move < benchmark_move - 0.5:
            lines.append("- **情绪偏弱风险**：覆盖股票池昨日整体明显跑输沪深300，开盘后弱势股可能继续承压。")
        spread = price_moves[0].change_pct - min(price_moves, key=lambda item: item.change_pct).change_pct
        if spread >= 3.0:
            lines.append(f"- **分化加剧风险**：样本内强弱股日收益差约为 {format_pct(spread)}，盘前不宜把个股走势简单外推为板块共振。")
    if announcement_items:
        finance_count = sum(1 for item in announcement_items if item.category == "财报披露")
        if finance_count:
            lines.append("- **信息超预期风险**：财报类公告可能包含管理层对经营、分红和风险的新增表述，盘前需要先核查原文再下结论。")
    if external_findings:
        low_conf = [item for item in external_findings if item.confidence <= 3]
        if low_conf:
            lines.append("- **网络搜索结果确认风险**：部分宏观/行业线索来自非一级来源，盘前只能作为关注线索，不能直接替代交易判断。")
    if not lines:
        lines.append("- 当前未识别到突出的新增风险，但仍需关注盘前公告增量、开盘量价和样本内强弱分化。")
    lines.append("")
    lines.append("*数据来源：RQData，置信度5*")
    return "\n".join(lines)


def build_appendix(
    report_date: date,
    lookback_start: date,
    loaded_counts: Dict[str, int],
    missing_files: List[str],
) -> str:
    display_names = {
        "stock_pool": "覆盖股票池",
        "instrument_meta": "公司元数据",
        "latest_earnings": "财务披露样本",
        "price_recent": "个股价格样本",
        "hs300_recent": "基准指数样本",
        "dividend_news": "分红事项样本",
        "announcement_raw": "隔夜公告样本",
        "web_search_findings": "网络搜索结果样本",
    }
    missing_labels = [display_names.get(item.replace('.json', ''), item) for item in missing_files]
    lines = [
        f"- 报告日期为 {report_date.isoformat()}，隔夜观察起始日为 {lookback_start.isoformat()}。",
        "- 事件优先级顺序：财报披露 -> 重点公告 -> 分红事项 -> 相对强弱。",
        "- 若高优先级公告存在原文链接，正文会保留追溯入口，便于后续核查正式披露内容。",
        "- 若输入数据缺失，报告会明确标记无数据或未验证，不会伪造内容。",
        "",
        "### 输入文件加载情况",
    ]
    for label, count in loaded_counts.items():
        lines.append(f"- {label}：{count} 条记录")
    if missing_labels:
        lines.append(f"- 当前样本不足的模块：{'、'.join(missing_labels)}")
    else:
        lines.append("- 所有约定文件均已加载")
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
    lookback_start = date.fromisoformat(args.lookback_start) if args.lookback_start else report_date - timedelta(days=1)

    raw_inputs, missing_files = load_raw_inputs(data_dir)
    lookup = build_company_lookup(raw_inputs["stock_pool"], raw_inputs["instrument_meta"])
    tickers = collect_tickers(raw_inputs)
    web_search_records = extract_records(read_json_file(data_dir / "web_search_findings.json"))
    validate_web_search_records(web_search_records)
    raw_inputs["web_search_findings"] = web_search_records

    earnings_items = extract_recent_earnings(raw_inputs["latest_earnings"], lookup, lookback_start, report_date)
    announcement_items = extract_recent_announcements(raw_inputs["announcement_raw"], lookup, lookback_start, report_date)
    dividend_items = extract_recent_dividends(raw_inputs["dividend_news"], lookup, lookback_start, report_date)
    external_findings = extract_external_findings(web_search_records)
    price_moves = extract_price_moves(raw_inputs["price_recent"], lookup)
    benchmark_move = extract_benchmark_move(raw_inputs["hs300_recent"])

    coverage_scope = summarize_coverage(tickers, lookup)
    loaded_counts = {
        "覆盖股票池": len(raw_inputs["stock_pool"]),
        "公司元数据": len(raw_inputs["instrument_meta"]),
        "财务披露样本": len(raw_inputs["latest_earnings"]),
        "个股价格样本": len(raw_inputs["price_recent"]),
        "基准指数样本": len(raw_inputs["hs300_recent"]),
        "分红事项样本": len(raw_inputs["dividend_news"]),
        "隔夜公告样本": len(raw_inputs["announcement_raw"]),
        "网络搜索结果样本": len(web_search_records),
    }

    template_path = Path(args.template).expanduser()
    template_text = template_path.read_text(encoding="utf-8")
    report_text = render_template(
        template_text,
        {
            "REPORT_DATE": report_date.isoformat(),
            "AS_OF_TIME": build_as_of_time(report_date, raw_inputs),
            "LOOKBACK_START": lookback_start.isoformat(),
            "COVERAGE_SCOPE": coverage_scope,
            "EXEC_SUMMARY": build_exec_summary(
                earnings_items,
                announcement_items,
                dividend_items,
                external_findings,
                price_moves,
                benchmark_move,
            ),
            "OVERNIGHT_DEVELOPMENTS": build_overnight_section(
                earnings_items,
                announcement_items,
                dividend_items,
                external_findings,
            ),
            "MARKET_RECAP": build_market_recap(price_moves, benchmark_move),
            "WATCHLIST": build_watchlist(
                earnings_items,
                announcement_items,
                dividend_items,
                external_findings,
                price_moves,
                benchmark_move,
            ),
            "TRADE_OBSERVATIONS": build_trade_observations(
                earnings_items,
                announcement_items,
                price_moves,
                benchmark_move,
            ),
            "RISK_ALERTS": build_risk_alerts(
                announcement_items,
                external_findings,
                price_moves,
                benchmark_move,
                missing_files,
            ),
            "APPENDIX": build_appendix(report_date, lookback_start, loaded_counts, missing_files),
        },
    )

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = data_dir / f"morning_note_{report_date.isoformat()}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
