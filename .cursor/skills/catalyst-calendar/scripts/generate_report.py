#!/usr/bin/env python3
"""Template-driven catalyst calendar report generator."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 催化剂日历报告",
    "## 执行摘要",
    "## 完整日历视图",
    "## 按事件类型分类",
    "## 高影响事件详解",
    "## 近期已披露催化剂",
    "## 跟踪建议",
    "## 日期不确定事件",
    "## 附录：口径说明",
]

TICKER_FIELDS = ("order_book_id", "ticker", "stock_code", "symbol", "code")
NAME_FIELDS = ("display_name", "name", "stock_name", "company_name", "symbol_name", "symbol")
WEB_SOURCE_TYPE_LABELS = {
    "official": "上市公司公告 / 官方网站 / 交易所披露",
    "government": "政府 / 监管 / 行业协会 / 官方机构",
    "association": "政府 / 监管 / 行业协会 / 官方机构",
    "authoritative_media": "权威财经媒体",
    "general_news": "一般新闻源",
    "inference": "分析推断 / 未验证信息",
}
WEB_SOURCE_TYPE_MAX_CONFIDENCE = {
    "official": 5,
    "government": 4,
    "association": 4,
    "authoritative_media": 4,
    "general_news": 3,
    "inference": 1,
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACW_POS_LIST = [
    0x0F, 0x23, 0x1D, 0x18, 0x21, 0x10, 0x01, 0x26, 0x0A, 0x09,
    0x13, 0x1F, 0x28, 0x1B, 0x16, 0x17, 0x19, 0x0D, 0x06, 0x0B,
    0x27, 0x12, 0x14, 0x08, 0x0E, 0x15, 0x20, 0x1A, 0x02, 0x1E,
    0x07, 0x04, 0x11, 0x05, 0x03, 0x1C, 0x22, 0x25, 0x0C, 0x24,
]
ACW_MASK = "3000176000856006061501533003690027800375"
OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", re.S)
STREAM_RE = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
PAGE_RE = re.compile(rb"/Type\s*/Page\b")
TEXT_OP_RE = re.compile(
    r"/([A-Za-z0-9]+)\s+[0-9.]+\s+Tf|"
    r"<([0-9A-Fa-f\s]+)>\s*Tj|"
    r"\[(.*?)\]\s*TJ|"
    r"\(((?:\\.|[^\\)])*)\)\s*Tj|"
    r"(-?[0-9.]+)\s+(-?[0-9.]+)\s+T[Dd]|"
    r"T\*|BT|ET",
    re.S,
)


@dataclass
class Event:
    event_id: str
    ticker: str
    company: str
    event_type: str
    event_title: str
    event_date: Optional[date]
    date_type: str
    window_start: Optional[date]
    window_end: Optional[date]
    impact_level: str
    source: str
    confidence: int
    evidence: str
    notes: str
    origin: str
    disclosure_date: Optional[date] = None
    source_link: str = ""

    def display_date(self) -> str:
        if self.date_type == "exact" and self.event_date:
            return self.event_date.isoformat()
        if self.window_start and self.window_end:
            return f"预计 {self.window_start.isoformat()} 至 {self.window_end.isoformat()}"
        return "待确认"

    def date_type_label(self) -> str:
        return "精确日期" if self.date_type == "exact" else "预计窗口"

    def sort_key(self) -> Tuple[date, int, str, str]:
        anchor = self.event_date or self.window_start or date.max
        estimated_rank = 0 if self.date_type == "exact" else 1
        return (anchor, estimated_rank, self.event_type, self.ticker)


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成模板驱动的催化剂日历报告")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--start-date", required=True, help="观察开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="观察结束日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出 Markdown 文件路径")
    parser.add_argument(
        "--template",
        default=str(skill_dir / "assets" / "template.md"),
        help="Markdown 模板路径",
    )
    parser.add_argument(
        "--report-date",
        default=date.today().isoformat(),
        help="报告日期，默认当天",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="不尝试渲染 HTML",
    )
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


def markdown_link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else label


def calc_sse_acw_cookie(arg1: str) -> str:
    out = [""] * len(ACW_POS_LIST)
    for idx, char in enumerate(arg1):
        for out_idx, pos in enumerate(ACW_POS_LIST):
            if pos == idx + 1:
                out[out_idx] = char
                break
    arg2 = "".join(out)
    pieces = []
    for idx in range(0, min(len(arg2), len(ACW_MASK)), 2):
        pieces.append(f"{int(arg2[idx:idx + 2], 16) ^ int(ACW_MASK[idx:idx + 2], 16):02x}")
    return "".join(pieces)


def fetch_pdf_bytes(url: str, timeout: float = 20.0) -> Tuple[Optional[bytes], str]:
    referer = "https://disc.static.szse.cn/" if "szse.cn" in url else ("http://www.sse.com.cn/" if "sse.com.cn" in url else url)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html,*/*", "Referer": referer}
    session = requests.Session()

    try:
        response = session.get(url, timeout=timeout, headers=headers, allow_redirects=True)
    except requests.RequestException as exc:
        return None, f"network_error:{type(exc).__name__}"

    content_type = (response.headers.get("content-type") or "").lower()
    if response.ok and (content_type.startswith("application/pdf") or response.content.startswith(b"%PDF-")):
        return response.content, "ok"

    if "static.sse.com.cn" in response.url and "text/html" in content_type:
        match = re.search(r"arg1='([^']+)'", response.text)
        if not match:
            return None, "source_blocked:sse_html_without_arg1"
        session.cookies.set("acw_sc__v2", calc_sse_acw_cookie(match.group(1)), domain="static.sse.com.cn", path="/")
        try:
            retry = session.get(
                url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*", "Referer": "http://www.sse.com.cn/"},
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            return None, f"network_error:{type(exc).__name__}"
        retry_type = (retry.headers.get("content-type") or "").lower()
        if retry.ok and (retry_type.startswith("application/pdf") or retry.content.startswith(b"%PDF-")):
            return retry.content, "ok"
        return None, f"source_blocked:sse_retry_{retry.status_code}"

    if not response.ok:
        return None, f"http_{response.status_code}"
    return None, f"unsupported_content_type:{content_type or 'unknown'}"


def parse_pdf_objects(pdf_bytes: bytes) -> Dict[int, bytes]:
    return {int(match.group(1)): match.group(3) for match in OBJ_RE.finditer(pdf_bytes)}


def parse_stream(raw_object: bytes) -> Tuple[Optional[bytes], Optional[bytes]]:
    match = STREAM_RE.search(raw_object)
    if not match:
        return None, None
    stream_dict = match.group(1)
    stream_data = match.group(2)
    if b"/FlateDecode" in stream_dict:
        stream_data = zlib.decompress(stream_data)
    return stream_dict, stream_data


def decode_utf16be_hex(value: str) -> str:
    return bytes.fromhex(value).decode("utf-16-be", "ignore")


def build_cmap(stream_text: str) -> Dict[str, str]:
    cmap: Dict[str, str] = {}
    for block in re.findall(r"beginbfchar\s*(.*?)\s*endbfchar", stream_text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            cmap[src.upper()] = decode_utf16be_hex(dst)
    for block in re.findall(r"beginbfrange\s*(.*?)\s*endbfrange", stream_text, re.S):
        for start, end, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            start_int = int(start, 16)
            end_int = int(end, 16)
            dst_int = int(dst, 16)
            width = len(start)
            out_len = len(dst) // 2
            for idx, code in enumerate(range(start_int, end_int + 1)):
                cmap[f"{code:0{width}X}"] = (dst_int + idx).to_bytes(out_len, "big").decode("utf-16-be", "ignore")
        for start, _end, arr in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block, re.S):
            start_int = int(start, 16)
            width = len(start)
            for idx, dst in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", arr)):
                cmap[f"{start_int + idx:0{width}X}"] = decode_utf16be_hex(dst)
    return cmap


def decode_pdf_hex(hex_text: str, cmap: Dict[str, str]) -> str:
    hex_text = re.sub(r"\s+", "", hex_text)
    if not hex_text:
        return ""
    key_lengths = sorted({len(key) for key in cmap}, reverse=True) if cmap else [2]
    cursor = 0
    output: List[str] = []
    while cursor < len(hex_text):
        matched = False
        for width in key_lengths:
            key = hex_text[cursor:cursor + width].upper()
            if len(key) == width and key in cmap:
                output.append(cmap[key])
                cursor += width
                matched = True
                break
        if matched:
            continue
        chunk = hex_text[cursor:cursor + 2]
        if len(chunk) == 2:
            try:
                output.append(bytes.fromhex(chunk).decode("latin1"))
            except ValueError:
                pass
        cursor += 2
    return "".join(output)


def decode_pdf_literal(text: str) -> str:
    return (
        text.replace(r"\(", "(")
        .replace(r"\)", ")")
        .replace(r"\n", "\n")
        .replace(r"\r", "")
        .replace(r"\t", "\t")
        .replace(r"\\", "\\")
    )


def extract_pdf_text(pdf_bytes: bytes) -> str:
    objects = parse_pdf_objects(pdf_bytes)
    font_cmaps: Dict[int, Dict[str, str]] = {}
    for obj_num, raw_object in objects.items():
        match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", raw_object)
        if not match:
            continue
        stream_ref = int(match.group(1))
        if stream_ref not in objects:
            continue
        _stream_dict, stream_data = parse_stream(objects[stream_ref])
        if not stream_data:
            continue
        font_cmaps[obj_num] = build_cmap(stream_data.decode("latin1", "ignore"))

    pages: List[Tuple[int, List[int], Dict[str, int]]] = []
    for obj_num, raw_object in objects.items():
        if not PAGE_RE.search(raw_object):
            continue
        font_refs = {name.decode("latin1"): int(ref) for name, ref in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", raw_object)}
        contents = [int(ref) for ref in re.findall(rb"/Contents\s+(\d+)\s+0\s+R", raw_object)]
        if not contents:
            array_match = re.search(rb"/Contents\s*\[(.*?)\]", raw_object, re.S)
            if array_match:
                contents = [int(ref) for ref in re.findall(rb"(\d+)\s+0\s+R", array_match.group(1))]
        if contents:
            pages.append((obj_num, contents, font_refs))
    pages.sort(key=lambda item: item[0])

    output_lines: List[str] = []
    for _page_num, content_refs, font_refs in pages:
        current_font = None
        current_line: List[str] = []
        page_lines: List[str] = []

        def flush_line() -> None:
            nonlocal current_line
            text = "".join(current_line).strip()
            if text:
                page_lines.append(text)
            current_line = []

        for ref in content_refs:
            raw_object = objects.get(ref)
            if raw_object is None:
                continue
            _stream_dict, stream_data = parse_stream(raw_object)
            if not stream_data:
                continue
            stream_text = stream_data.decode("latin1", "ignore")
            for match in TEXT_OP_RE.finditer(stream_text):
                if match.group(1):
                    font_name = match.group(1)
                    current_font = font_refs.get(font_name, current_font)
                elif match.group(2):
                    cmap = font_cmaps.get(current_font, {})
                    current_line.append(decode_pdf_hex(match.group(2), cmap))
                elif match.group(3):
                    cmap = font_cmaps.get(current_font, {})
                    for hex_group, literal in re.findall(r"<([0-9A-Fa-f\s]+)>|\(((?:\\.|[^\\)])*)\)", match.group(3), re.S):
                        if hex_group:
                            current_line.append(decode_pdf_hex(hex_group, cmap))
                        elif literal:
                            current_line.append(decode_pdf_literal(literal))
                elif match.group(4):
                    current_line.append(decode_pdf_literal(match.group(4)))
                else:
                    flush_line()
            flush_line()
        if page_lines:
            output_lines.extend(page_lines)
    return "\n".join(output_lines)


def normalize_pdf_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = text.replace("\x00", "")
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff0-9])", "", text)
    text = re.sub(r"(?<=[0-9])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s*([:：\-－])\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text


def extract_scheduled_date_from_text(text: str, reference_date: date) -> Optional[date]:
    normalized = normalize_pdf_text(text)
    patterns = [
        r"(?:会议召开时间|召开时间|现场会议时间|现场会议召开时间|召开日期时间|会议时间|举行时间|活动时间)[:：]?\s*(20\d{2}年\d{1,2}月\d{1,2}日)",
        r"(?:会议召开时间|召开时间|现场会议时间|现场会议召开时间|召开日期时间|会议时间|举行时间|活动时间)[:：]?\s*(\d{1,2}月\d{1,2}日)",
        r"将?于\s*(20\d{2}年\d{1,2}月\d{1,2}日).*?(?:召开|举行)",
        r"将?于\s*(\d{1,2}月\d{1,2}日).*?(?:召开|举行)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        token = match.group(1)
        if "年" in token:
            parsed = datetime.strptime(token, "%Y年%m月%d日").date()
        else:
            parsed = datetime.strptime(f"{reference_date.year}年{token}", "%Y年%m月%d日").date()
            if parsed < reference_date - timedelta(days=7):
                parsed = datetime.strptime(f"{reference_date.year + 1}年{token}", "%Y年%m月%d日").date()
        return parsed
    return None


def parse_chinese_date_token(token: str, reference_date: date) -> Optional[date]:
    token = token.strip()
    if not token:
        return None
    try:
        if "年" in token:
            return datetime.strptime(token, "%Y年%m月%d日").date()
        parsed = datetime.strptime(f"{reference_date.year}年{token}", "%Y年%m月%d日").date()
        if parsed < reference_date - timedelta(days=7):
            return datetime.strptime(f"{reference_date.year + 1}年{token}", "%Y年%m月%d日").date()
        return parsed
    except ValueError:
        return None


def extract_scheduled_window_from_text(text: str, reference_date: date) -> Tuple[Optional[date], Optional[date]]:
    normalized = normalize_pdf_text(text)
    patterns = [
        r"(?:实施期间|减持期间|增持期间|计划期间|实施期限|减持计划实施期间|增持计划实施期间)[:：]?\s*(20\d{2}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)\s*(?:至|-|－|—)\s*(20\d{2}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)",
        r"(?:期限届满日|截止日期|截止时间)[:：]?\s*(20\d{2}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日)",
    ]
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, normalized)
        if not match:
            continue
        if idx == 0:
            start_token, end_token = match.group(1), match.group(2)
            return parse_chinese_date_token(start_token, reference_date), parse_chinese_date_token(end_token, reference_date)
        end_date = parse_chinese_date_token(match.group(1), reference_date)
        return reference_date, end_date
    return None, None


def build_company_lookup(stock_pool: List[Any], instrument_meta: List[Any]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for collection in (stock_pool, instrument_meta):
        for item in collection:
            if isinstance(item, str):
                lookup.setdefault(item, item)
                continue
            if not isinstance(item, dict):
                continue
            ticker = normalize_ticker(item)
            if not ticker:
                continue
            company = normalize_name(item) or ticker
            lookup[ticker] = company
    return lookup


def resolve_company_name(ticker: str, record: Dict[str, Any], lookup: Dict[str, str]) -> str:
    return lookup.get(ticker) or normalize_name(record) or ticker or "未知公司"


def parse_quarter(value: Any) -> Optional[Tuple[int, int]]:
    if value in (None, ""):
        return None

    text = str(value).strip()
    match = re.match(r"^(\d{4})[qQ]([1-4])$", text)
    if match:
        return int(match.group(1)), int(match.group(2))

    as_date = parse_iso_date(text)
    if not as_date:
        return None

    month_to_quarter = {3: 1, 6: 2, 9: 3, 12: 4}
    quarter = month_to_quarter.get(as_date.month)
    if quarter is None:
        return None
    return as_date.year, quarter


def make_event_id(*parts: str) -> str:
    cleaned = [re.sub(r"[^A-Za-z0-9_.-]+", "_", part or "na") for part in parts]
    return "__".join(cleaned)


def build_event(
    *,
    ticker: str,
    company: str,
    event_type: str,
    event_title: str,
    event_date: Optional[date] = None,
    date_type: str,
    window_start: Optional[date] = None,
    window_end: Optional[date] = None,
    impact_level: str,
    source: str,
    confidence: int,
    evidence: str,
    notes: str,
    origin: str,
    disclosure_date: Optional[date] = None,
    source_link: str = "",
) -> Event:
    anchor = event_date.isoformat() if event_date else (window_start.isoformat() if window_start else "unknown")
    return Event(
        event_id=make_event_id(ticker, event_type, event_title, anchor),
        ticker=ticker,
        company=company,
        event_type=event_type,
        event_title=event_title,
        event_date=event_date,
        date_type=date_type,
        window_start=window_start,
        window_end=window_end,
        impact_level=impact_level,
        source=source,
        confidence=confidence,
        evidence=evidence,
        notes=notes,
        origin=origin,
        disclosure_date=disclosure_date,
        source_link=source_link,
    )


def load_raw_inputs(data_dir: Path) -> Dict[str, List[Any]]:
    file_map = {
        "stock_pool": "stock_pool.json",
        "earnings_express_raw": "earnings_express_raw.json",
        "latest_financial_raw": "latest_financial_raw.json",
        "dividend_raw": "dividend_raw.json",
        "instrument_meta": "instrument_meta.json",
        "announcement_raw": "announcement_raw.json",
        "web_search_events": "web_search_events.json",
    }
    loaded: Dict[str, List[Any]] = {}
    for key, filename in file_map.items():
        payload = read_json_file(data_dir / filename)
        loaded[key] = extract_records(payload)
    return loaded


def quarter_sort_key(value: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    if not value:
        return (-1, -1)
    return value


def quarter_label(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}" if quarter != 4 else f"{year}年报"


def statutory_deadline_for_quarter(year: int, quarter: int) -> date:
    if quarter == 1:
        return date(year, 4, 30)
    if quarter == 2:
        return date(year, 8, 31)
    if quarter == 3:
        return date(year, 10, 31)
    return date(year + 1, 4, 30)


def build_latest_financial_lookup(records: List[Any]) -> Dict[str, Tuple[int, int]]:
    latest: Dict[str, Tuple[int, int]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        ticker = normalize_ticker(item)
        if not ticker:
            continue
        quarter_info = parse_quarter(pick_first(item, ("quarter", "report_period", "report_quarter")))
        if not quarter_info:
            continue
        current = latest.get(ticker)
        if current is None or quarter_sort_key(quarter_info) > quarter_sort_key(current):
            latest[ticker] = quarter_info
    return latest


def normalize_earnings_events(
    express_records: List[Any],
    latest_financial_records: List[Any],
    lookup: Dict[str, str],
    start_dt: date,
) -> List[Event]:
    events: List[Event] = []
    latest_financial_lookup = build_latest_financial_lookup(latest_financial_records)
    lookback_start = start_dt - timedelta(days=30)

    for item in express_records:
        if not isinstance(item, dict):
            continue
        ticker = normalize_ticker(item)
        if not ticker:
            continue
        company = resolve_company_name(ticker, item, lookup)
        info_date = parse_iso_date(item.get("info_date"))
        if not info_date or not (lookback_start <= info_date <= start_dt):
            continue

        quarter_info = parse_quarter(item.get("end_date"))
        if not quarter_info:
            continue

        latest_quarter = latest_financial_lookup.get(ticker)
        if latest_quarter is not None and quarter_sort_key(latest_quarter) >= quarter_sort_key(quarter_info):
            continue

        year, quarter = quarter_info
        express_label = quarter_label(year, quarter)
        revenue = item.get("operating_revenue")
        yoy = item.get("operating_revenue_yoy")
        evidence_bits = [f"业绩快报公告日 {info_date.isoformat()}", f"对应报告期 {express_label}"]
        if revenue not in (None, ""):
            evidence_bits.append(f"营业收入 {revenue}")
        if yoy not in (None, ""):
            evidence_bits.append(f"营收同比 {yoy}%")
        if latest_quarter:
            evidence_bits.append(f"当前正式财报最新期为 {quarter_label(*latest_quarter)}")
        else:
            evidence_bits.append("当前未识别到正式财报期")

        events.append(
            build_event(
                ticker=ticker,
                company=company,
                event_type="财报",
                event_title=f"{express_label}业绩快报后续正式财报待披露",
                date_type="estimated_window",
                window_start=info_date + timedelta(days=1),
                window_end=statutory_deadline_for_quarter(year, quarter),
                impact_level="H",
                source="RQData",
                confidence=4,
                evidence="；".join(evidence_bits),
                notes="近1个月已披露业绩快报，但正式财报尚未覆盖同一报告期，因此仅保留法定披露窗口供继续跟踪",
                origin="financial_express",
                disclosure_date=info_date,
            )
        )

    return events


def dividend_amount_text(record: Dict[str, Any]) -> str:
    fields = (
        "dividend_cash_before_tax",
        "cash_dividend_per_share",
        "dividend_per_share",
        "cash_before_tax",
    )
    value = pick_first(record, fields)
    if value in (None, ""):
        return "金额待确认"
    try:
        return f"每股现金分红 {float(value):.4f} 元"
    except (TypeError, ValueError):
        return f"分红金额 {value}"


def normalize_dividend_events(records: List[Any], lookup: Dict[str, str]) -> List[Event]:
    events: List[Event] = []

    for item in records:
        if not isinstance(item, dict):
            continue
        ticker = normalize_ticker(item)
        if not ticker:
            continue
        company = resolve_company_name(ticker, item, lookup)
        amount_text = dividend_amount_text(item)
        ex_dividend_date = parse_iso_date(item.get("ex_dividend_date"))
        if not ex_dividend_date:
            continue

        declaration_date = parse_iso_date(
            pick_first(item, ("declaration_announcement_date", "announcement_date"))
        )
        record_date = parse_iso_date(
            pick_first(item, ("book_closure_date", "record_date"))
        )
        payable_date = parse_iso_date(
            pick_first(item, ("payable_date", "payment_date"))
        )

        context_bits = [amount_text]
        quarter = item.get("quarter")
        if quarter:
            context_bits.append(f"对应报告期 {quarter}")
        if declaration_date:
            context_bits.append(f"分红公告日 {declaration_date.isoformat()}")
        if record_date:
            context_bits.append(f"登记相关日期 {record_date.isoformat()}")
        if payable_date:
            context_bits.append(f"派息日 {payable_date.isoformat()}")

        evidence = "；".join(context_bits)
        events.append(
            build_event(
                ticker=ticker,
                company=company,
                event_type="分红",
                event_title="除权除息日",
                event_date=ex_dividend_date,
                date_type="exact",
                impact_level="M",
                source="RQData",
                confidence=5,
                evidence=evidence,
                notes="分红数据按历史回溯查询，但只有除权除息日落入观察窗口时才计入日历",
                origin="dividend",
                disclosure_date=declaration_date,
            )
        )

    return events


def announcement_event_spec(title: str, info_type: str) -> Optional[Tuple[str, str, str, str]]:
    normalized = (title or "").strip()
    if not normalized:
        return None

    if "年度报告" in normalized or "年年度报告" in normalized:
        return ("财报", "年度报告公告", "H", "关注年报核心财务表现及分红方案")
    if "第一季度报告" in normalized or "一季度报告" in normalized:
        return ("财报", "一季度报告公告", "H", "关注一季报增速与全年指引")
    if "半年度报告" in normalized or "中期报告" in normalized:
        return ("财报", "半年度报告公告", "H", "关注中报表现与经营趋势")
    if "第三季度报告" in normalized or "三季度报告" in normalized:
        return ("财报", "三季度报告公告", "H", "关注三季报边际变化")
    if "业绩预告" in normalized:
        return ("财报", "业绩预告公告", "H", "关注预告区间与市场预期差")
    if "业绩快报" in normalized:
        return ("财报", "业绩快报公告", "H", "关注快报口径与正式财报差异")
    if "利润分配" in normalized or "权益分派" in normalized:
        return ("分红", "利润分配相关公告", "M", "关注分红方案、股权登记日和实施安排")
    if "除权除息" in normalized:
        return ("分红", "除权除息公告", "M", "关注除权除息和市场交易影响")
    if "股权登记日" in normalized:
        return ("分红", "股权登记相关公告", "M", "关注登记日和持有人资格")
    if "业绩说明会" in normalized:
        return ("投资者交流", "业绩说明会公告", "M", "关注交流时点、管理层表述与市场预期差")
    if "股东大会" in normalized and "表决结果" not in normalized and "决议" not in normalized:
        return ("股东大会", "股东大会相关公告", "M", "关注召开通知、审议议案及表决结果")
    if "股东大会" in normalized and ("表决结果" in normalized or "决议" in normalized):
        return ("股东大会", "股东大会结果公告", "M", "关注会议结果及其对后续治理事项的影响")
    if "董事会" in normalized and "决议" in normalized:
        return ("公司治理", "董事会决议公告", "M", "关注董事会决议是否引出后续催化剂")
    if "监事会" in normalized and "决议" in normalized:
        return ("公司治理", "监事会决议公告", "L", "关注监事会决议与治理层表态")
    if "回购" in normalized:
        return ("资本运作", "股份回购相关公告", "M", "关注回购节奏、规模与资本配置意图")
    if "增持" in normalized or "减持" in normalized:
        return ("资本运作", "股东增减持相关公告", "M", "关注股东行为及其对市场预期的影响")
    if "重大资产购买" in normalized or "重大资产重组" in normalized or "收购" in normalized:
        return ("资本运作", "并购重组相关公告", "H", "关注交易推进、审批和估值影响")
    if info_type == "定期报告":
        return ("财报", "定期报告公告", "H", "关注定期报告核心披露内容")
    return None


def announcement_lookback_start(start_dt: date) -> date:
    return start_dt - timedelta(days=120)


def recent_disclosure_start(start_dt: date) -> date:
    return start_dt - timedelta(days=45)


def needs_future_date_extraction(title: str, event_type: str) -> bool:
    normalized = (title or "").strip()
    if event_type == "投资者交流" and ("召开" in normalized or "举行" in normalized) and "业绩说明会" in normalized:
        return True
    if event_type == "股东大会" and any(token in normalized for token in ("召开", "通知", "会议材料")):
        return True
    if event_type == "资本运作" and any(token in normalized for token in ("增持", "减持")) and any(
        token in normalized for token in ("计划", "进展", "期间", "期限", "届满", "完成", "结果")
    ):
        return True
    return False


def extract_announced_event_date(item: Dict[str, Any], event_type: str, cache: Dict[str, Optional[date]]) -> Optional[date]:
    link = str(item.get("announcement_link") or "")
    if not link:
        return None
    if link in cache:
        return cache[link]

    if str(item.get("file_type") or "").upper() != "PDF":
        cache[link] = None
        return None

    reference_date = parse_iso_date(pick_first(item, ("info_date", "create_tm")))
    if not reference_date:
        cache[link] = None
        return None

    pdf_bytes, fetch_status = fetch_pdf_bytes(link)
    if not pdf_bytes or fetch_status != "ok":
        cache[link] = None
        return None

    try:
        text = extract_pdf_text(pdf_bytes)
    except Exception:
        cache[link] = None
        return None

    event_date = extract_scheduled_date_from_text(text, reference_date)
    cache[link] = event_date
    return event_date


def normalize_announcement_events(records: List[Any], lookup: Dict[str, str]) -> List[Event]:
    events: List[Event] = []
    extracted_date_cache: Dict[str, Optional[date]] = {}

    for item in records:
        if not isinstance(item, dict):
            continue
        ticker = normalize_ticker(item)
        if not ticker:
            continue
        company = resolve_company_name(ticker, item, lookup)
        title = str(item.get("title") or "").strip()
        info_type = str(item.get("info_type") or "").strip()
        spec = announcement_event_spec(title, info_type)
        if not spec:
            continue

        event_type, event_title, impact_level, notes = spec
        disclosure_date = parse_iso_date(pick_first(item, ("info_date", "create_tm")))
        if not disclosure_date:
            continue
        event_date = disclosure_date
        date_type = "exact"
        window_start: Optional[date] = None
        window_end: Optional[date] = None
        if needs_future_date_extraction(title, event_type):
            extracted_date = extract_announced_event_date(item, event_type, extracted_date_cache)
            if extracted_date:
                event_date = extracted_date
            elif event_type == "资本运作":
                link = str(item.get("announcement_link") or "")
                pdf_bytes, fetch_status = fetch_pdf_bytes(link)
                if pdf_bytes and fetch_status == "ok":
                    try:
                        text = extract_pdf_text(pdf_bytes)
                    except Exception:
                        text = ""
                    if text:
                        window_start, window_end = extract_scheduled_window_from_text(text, disclosure_date)
                        if window_end:
                            date_type = "estimated_window"
                            event_date = None
                            window_start = window_start or disclosure_date
                            confidence = 4
                        else:
                            confidence = 5
                    else:
                        confidence = 5
                else:
                    confidence = 5
            else:
                confidence = 5
        else:
            confidence = 5

        media = str(item.get("media") or "未知媒体")
        evidence = f"公告标题：{title}；公告类型：{info_type or '未分类'}；来源媒体：{media}"
        events.append(
            build_event(
                ticker=ticker,
                company=company,
                event_type=event_type,
                event_title=event_title,
                event_date=event_date,
                date_type=date_type,
                window_start=window_start,
                window_end=window_end,
                impact_level=impact_level,
                source="RQData",
                confidence=confidence,
                evidence=evidence,
                notes=notes,
                origin="announcement",
                disclosure_date=disclosure_date,
                source_link=str(item.get("announcement_link") or ""),
            )
        )

    return events


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
        confidence = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"置信度必须是 1-5 的整数: {value}") from exc
    if not 1 <= confidence <= 5:
        raise ValueError(f"置信度必须在 1-5 之间: {value}")
    return confidence


def normalize_web_search_events(records: List[Any], lookup: Dict[str, str]) -> List[Event]:
    events: List[Event] = []

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
            "event_scope",
            "scope_name",
            "event_title",
            "date_type",
            "impact_level",
        ]
        missing = [field for field in required_fields if item.get(field) in (None, "")]
        if missing:
            raise ValueError(f"web_search_events.json 第 {index + 1} 条缺少字段: {', '.join(missing)}")

        source_type = normalize_web_source_type(item.get("source_type"))
        source_name = str(item.get("source_name") or "").strip()
        query = str(item.get("query") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        summary = str(item.get("summary") or "").strip()
        why_relevant = str(item.get("why_relevant") or "").strip()
        scope = str(item.get("event_scope") or "").strip().lower()
        scope_name = str(item.get("scope_name") or "").strip()
        event_title = str(item.get("event_title") or title).strip()
        event_type = str(item.get("event_type") or ("宏观" if scope == "macro" else "行业")).strip()
        date_type = str(item.get("date_type") or "").strip().lower()
        impact_level = str(item.get("impact_level") or "").strip().upper()
        confidence = parse_confidence(item.get("confidence"))

        if scope not in {"macro", "industry"}:
            raise ValueError(f"web_search event_scope 仅支持 macro / industry: {scope or '空'}")
        if impact_level not in {"H", "M", "L"}:
            raise ValueError(f"web_search impact_level 必须为 H/M/L: {impact_level or '空'}")

        published_at = parse_iso_date(item.get("published_at"))
        retrieved_at = parse_iso_date(item.get("retrieved_at"))
        if not published_at or not retrieved_at:
            raise ValueError(f"web_search_events.json 第 {index + 1} 条的 published_at / retrieved_at 非法")
        if published_at > retrieved_at:
            raise ValueError(f"web_search_events.json 第 {index + 1} 条的 published_at 晚于 retrieved_at")

        max_confidence = WEB_SOURCE_TYPE_MAX_CONFIDENCE[source_type]
        event_date: Optional[date] = None
        window_start: Optional[date] = None
        window_end: Optional[date] = None
        if date_type == "exact":
            event_date = parse_iso_date(item.get("event_date"))
            if not event_date:
                raise ValueError(f"web_search_events.json 第 {index + 1} 条缺少合法的 event_date")
        elif date_type == "estimated_window":
            window_start = parse_iso_date(item.get("window_start"))
            window_end = parse_iso_date(item.get("window_end"))
            if not window_start or not window_end:
                raise ValueError(f"web_search_events.json 第 {index + 1} 条缺少合法的 window_start/window_end")
            if window_end < window_start:
                raise ValueError(f"web_search_events.json 第 {index + 1} 条的 window_end 早于 window_start")
            max_confidence = min(max_confidence, 3)
        else:
            raise ValueError(f"web_search date_type 仅支持 exact / estimated_window: {date_type or '空'}")
        confidence = min(confidence, max_confidence)

        ticker = normalize_ticker(item)
        company = resolve_company_name(ticker, item, lookup) if ticker else scope_name
        evidence = "；".join(
            [
                f"检索词：{query}",
                f"来源标题：{title}",
                f"来源类型：{WEB_SOURCE_TYPE_LABELS[source_type]}",
                f"发布时间：{published_at.isoformat()}",
                f"检索时间：{retrieved_at.isoformat()}",
                f"摘要：{summary}",
            ]
        )
        notes = why_relevant
        events.append(
            build_event(
                ticker=ticker,
                company=company,
                event_type=event_type,
                event_title=event_title,
                event_date=event_date,
                date_type=date_type,
                window_start=window_start,
                window_end=window_end,
                impact_level=impact_level,
                source=source_name,
                confidence=confidence,
                evidence=evidence,
                notes=notes,
                origin="web_search",
                disclosure_date=published_at,
                source_link=url,
            )
        )

    return events


def deduplicate_events(events: Iterable[Event]) -> List[Event]:
    deduped: Dict[Tuple[str, str, str, str], Event] = {}
    for event in events:
        anchor = event.display_date()
        key = (event.ticker, event.event_type, event.event_title, anchor)
        existing = deduped.get(key)
        if existing is None or event.confidence > existing.confidence:
            deduped[key] = event
    return sorted(deduped.values(), key=lambda item: item.sort_key())


def is_future_calendar_event(event: Event, start_dt: date, end_dt: date) -> bool:
    if event.date_type == "exact" and event.event_date:
        return start_dt <= event.event_date <= end_dt
    if event.window_start and event.window_end:
        return not (event.window_end < start_dt or event.window_start > end_dt)
    return False


def is_recent_disclosed_catalyst(event: Event, start_dt: date) -> bool:
    disclosure_date = event.disclosure_date or event.event_date
    if disclosure_date is None:
        return False
    if not (recent_disclosure_start(start_dt) <= disclosure_date < start_dt):
        return False
    if event.origin == "financial_express":
        return False
    if event.origin != "announcement":
        return False
    if event.date_type == "exact" and event.event_date and event.event_date >= start_dt:
        return False
    return event.impact_level in {"H", "M"}


def filter_future_calendar_events(events: Sequence[Event], start_dt: date, end_dt: date) -> List[Event]:
    return [event for event in events if is_future_calendar_event(event, start_dt, end_dt)]


def filter_recent_disclosed_events(events: Sequence[Event], start_dt: date) -> List[Event]:
    filtered = [event for event in events if is_recent_disclosed_catalyst(event, start_dt)]
    return sorted(
        filtered,
        key=lambda item: (score_recent_disclosed_event(item, start_dt), (item.disclosure_date or item.event_date or date.min), item.company),
        reverse=True,
    )[:40]


def score_recent_disclosed_event(event: Event, start_dt: date) -> int:
    score = 0
    disclosure_date = event.disclosure_date or event.event_date
    if disclosure_date:
        score += max(0, 40 - (start_dt - disclosure_date).days)
    if event.impact_level == "H":
        score += 60
    elif event.impact_level == "M":
        score += 25
    if event.origin == "financial_express":
        score += 30
    if event.event_type == "财报":
        score += 35
    elif event.event_type == "分红":
        score += 20
    elif event.event_type == "投资者交流":
        score += 18
    elif event.event_type == "资本运作" and "增减持" in event.event_title:
        score += 15
    elif event.event_type == "资本运作" and "并购" in event.event_title:
        score += 22
    elif event.event_type == "公司治理":
        score -= 15
    return score


def window_display_in_range(event: Event, start_dt: date, end_dt: Optional[date] = None) -> str:
    if event.date_type == "exact":
        return event.display_date()
    if not event.window_end:
        return event.display_date()
    clipped_start = max(start_dt, event.window_start or start_dt)
    clipped_end = min(end_dt, event.window_end) if end_dt else event.window_end
    return f"预计 {clipped_start.isoformat()} 至 {clipped_end.isoformat()}"


def source_confidence_label(event: Event) -> str:
    return f"{event.source} / {event.confidence}"


def summarize_source_mix(events: Sequence[Event], *, limit: int = 6) -> str:
    labels: List[str] = []
    for event in events:
        label = f"{event.source}(置信度{event.confidence})"
        if label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    if not labels:
        return "暂无可展示来源"
    return "；".join(labels)


def summarize_counter(values: Iterable[str], *, limit: int = 4) -> str:
    counter = Counter(value for value in values if value)
    if not counter:
        return "暂无"
    return "、".join(f"{name}{count}个" for name, count in counter.most_common(limit))


def markdown_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "暂无数据。"
    header_line = "| " + " | ".join(markdown_escape(cell) for cell in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(markdown_escape(cell) for cell in row) + " |"
        for row in rows
    ]
    return "\n".join([header_line, separator] + body)


def build_calendar_table(events: Sequence[Event]) -> str:
    exact_events = [event for event in events if event.date_type == "exact" and event.event_date]
    if not exact_events:
        return "未来 30 天内暂无已确认日期的事件。"

    grouped: Dict[str, List[Event]] = defaultdict(list)
    for event in exact_events:
        grouped[event.event_date.isoformat()].append(event)

    rows = []
    for day in sorted(grouped)[:20]:
        bucket = sorted(grouped[day], key=lambda item: (item.impact_level != "H", item.event_type, item.company))
        type_counter = Counter(event.event_type for event in bucket)
        focus = []
        for event in bucket:
            label = f"{event.company} {event.event_title}"
            if event.source_link:
                label = markdown_link(label, event.source_link)
            focus.append(label)
            if len(focus) >= 3:
                break
        rows.append(
            [
                day,
                len(bucket),
                sum(1 for event in bucket if event.impact_level == "H"),
                "、".join(f"{name}{count}" for name, count in type_counter.most_common(3)),
                "；".join(focus),
                summarize_source_mix(bucket, limit=3),
            ]
        )
    headers = ["日期", "事件数", "高影响", "主要类型", "重点公司/事项", "来源/置信度"]
    return render_markdown_table(headers, rows)


def build_summary(
    future_events: Sequence[Event],
    recent_events: Sequence[Event],
    start_dt: date,
    end_dt: date,
    coverage_scope: str,
) -> str:
    total = len(future_events)
    if total == 0 and not recent_events:
        return "\n".join(
            [
                f"- 观察区间内未识别到事件，覆盖范围为 {coverage_scope}。",
                "- 当前没有可展示的未来事件，也未识别到仍值得跟踪的近期已披露催化剂。",
                "- 建议优先检查原始 JSON 是否存在、字段名是否匹配输入契约。",
            ]
        )

    high_impact = [event for event in future_events if event.impact_level == "H"]
    exact_dates = Counter(
        event.event_date.isoformat()
        for event in future_events
        if event.event_date and event.date_type == "exact"
    )
    most_crowded = "无精确日期事件"
    if exact_dates:
        event_date, count = exact_dates.most_common(1)[0]
        most_crowded = f"{event_date}（{count} 个事件）"

    tracked_subjects = len({event.ticker or event.company for event in future_events})
    estimated = len([event for event in future_events if event.date_type == "estimated_window"])
    future_from_prior_disclosures = sum(
        1
        for event in future_events
        if event.origin == "announcement" and event.disclosure_date and event.event_date and event.disclosure_date < start_dt <= event.event_date
    )
    external_events = len([event for event in future_events if event.origin == "web_search"])
    recent_type_mix = summarize_counter(event.event_type for event in recent_events)
    recent_company_mix = summarize_counter(event.company for event in recent_events)

    return "\n".join(
        [
            f"- 未来 {(end_dt - start_dt).days + 1} 天共识别 {total} 个事件，覆盖 {tracked_subjects} 个跟踪主体。",
            f"- 其中高影响事件 {len(high_impact)} 个，主要集中在财报和关键信息披露时点。",
            f"- 事件最密集日期为 {most_crowded}。",
            f"- 其中 {future_from_prior_disclosures} 个确定日期事件来自更早公告的日程安排，已按实际事件日而非公告披露日纳入日历。",
            f"- 另补充纳入 {external_events} 个宏观 / 行业外部催化事件，用于补足 `RQData CLI` 之外的实时信息。",
            f"- 仍有 {estimated} 个事件缺少精确日期，仅能以预计窗口形式跟踪。",
            f"- 另有 {len(recent_events)} 个近期已披露催化剂保留在回顾区，供判断未来 30 天潜在影响。",
            f"- 近期已披露催化剂主要集中在 {recent_type_mix}，涉及主体以 {recent_company_mix} 为主。",
        ]
    )


def build_event_type_sections(
    future_events: Sequence[Event],
    recent_events: Sequence[Event],
    start_dt: date,
    end_dt: date,
) -> str:
    if not future_events and not recent_events:
        return "暂无事件类型明细。\n\n*数据来源：暂无可展示来源*"

    buckets: Dict[str, List[Event]] = defaultdict(list)
    active_events = list(future_events)
    using_recent_fallback = False
    for event in future_events:
        buckets[event.event_type].append(event)
    if not future_events and recent_events:
        using_recent_fallback = True
        for event in recent_events:
            buckets[event.event_type].append(event)

    sections: List[str] = []
    if using_recent_fallback:
        sections.extend(
            [
                "当前观察区间内缺少已确认的未来事件，以下按类型回顾近期已披露、但仍可能影响未来 30 天预期演化的催化剂。",
                "",
            ]
        )
        active_events = list(recent_events)
    ordered_types = ["财报", "分红", "股东大会", "投资者交流", "公司治理", "资本运作", "宏观", "行业"]
    for event_type in ordered_types + sorted(set(buckets) - set(ordered_types)):
        bucket = buckets.get(event_type)
        if not bucket:
            continue
        bucket = sorted(bucket, key=lambda item: item.sort_key())[:8]
        sections.append(f"### {event_type}")
        if using_recent_fallback:
            sections.append(
                render_markdown_table(
                    ["披露日", "公司", "事项", "仍需跟踪", "来源/置信度"],
                    [
                        [
                            (event.disclosure_date or event.event_date).isoformat() if (event.disclosure_date or event.event_date) else "待确认",
                            event.company,
                            event.event_title,
                            event.notes,
                            source_confidence_label(event),
                        ]
                        for event in bucket
                    ],
                )
            )
            sections.append("")
            sections.append(f"- 类型说明：近期该类事项共保留 {len(bucket)} 个，主要用于评估未来 30 天的预期延续、股东回报兑现和二次交易催化。")
            sections.append("")
            sections.append(f"*数据来源：{summarize_source_mix(bucket)}*")
            sections.append("")
            continue
        sections.append(
            render_markdown_table(
                ["日期", "公司", "事件", "影响", "跟踪要点", "来源/置信度"],
                [
                    [
                        window_display_in_range(event, start_dt, end_dt),
                        event.company,
                        event.event_title,
                        event.impact_level,
                        event.notes,
                        source_confidence_label(event),
                    ]
                    for event in bucket
                ],
            )
        )
        sections.append("")
        sections.append(f"*数据来源：{summarize_source_mix(bucket)}*")
        sections.append("")
    if using_recent_fallback:
        sections.append(f"整体来源汇总：{summarize_source_mix(active_events)}。")
    return "\n".join(sections).strip()


def build_high_impact_sections(
    future_events: Sequence[Event],
    recent_events: Sequence[Event],
    start_dt: date,
    end_dt: date,
) -> str:
    high_impact = [event for event in future_events if event.impact_level == "H"]
    using_recent_fallback = False
    if not high_impact:
        high_impact = [event for event in recent_events if event.impact_level == "H"]
        using_recent_fallback = bool(high_impact)
    if not high_impact:
        return "当前观察区间内无高影响事件。"

    sections: List[str] = []
    if using_recent_fallback:
        sections.extend(
            [
                "未来窗口内缺少已确认的高影响未来事件，以下展开近期已披露、但仍可能持续影响未来 30 天预期和交易节奏的高影响催化剂。",
                "",
            ]
        )
    for event in high_impact[:12]:
        sections.append(f"### {event.company} - {event.event_title}")
        if using_recent_fallback:
            sections.append(
                f"- 披露日：{(event.disclosure_date or event.event_date).isoformat() if (event.disclosure_date or event.event_date) else '待确认'}"
            )
        else:
            sections.append(f"- 日期：{window_display_in_range(event, start_dt, end_dt)}")
        sections.append(f"- 类型：{event.event_type}")
        sections.append(f"- 关注点：{event.notes}")
        sections.append(f"- 依据：{event.evidence}")
        if using_recent_fallback:
            sections.append("- 持续影响：虽然事项已披露，但年报、分红方案、治理安排和资本运作仍可能在未来 30 天内继续影响预期修正和交易行为。")
        if event.disclosure_date and event.disclosure_date != event.event_date:
            sections.append(f"- 披露日：{event.disclosure_date.isoformat()}")
        if event.source_link:
            sections.append(f"- 原文：{markdown_link('公告链接', event.source_link)}")
        sections.append(f"- 数据来源：{event.source}，置信度{event.confidence}")
        sections.append("")
    return "\n".join(sections).strip()


def build_recent_disclosed_sections(events: Sequence[Event]) -> str:
    if not events:
        return "近期未识别到需要单独回顾的已披露催化剂。"

    type_mix = summarize_counter((event.event_type for event in events))
    company_mix = summarize_counter((event.company for event in events), limit=6)
    rows = []
    for event in events[:16]:
        rows.append(
            [
                (event.disclosure_date or event.event_date).isoformat() if (event.disclosure_date or event.event_date) else "待确认",
                event.company,
                event.event_title,
                event.notes,
                source_confidence_label(event),
                markdown_link("原文", event.source_link),
            ]
        )
    return "\n".join(
        [
            "- 以下事项已在观察窗口前披露，但仍可能影响未来 30 天的交易与预期演化。",
            f"- 类型分布：{type_mix}。",
            f"- 重点公司分布：{company_mix}。",
            "",
            render_markdown_table(["披露日", "公司", "事项", "仍需跟踪", "来源/置信度", "链接"], rows),
            "",
            f"*数据来源：{summarize_source_mix(events)}*",
        ]
    )


def build_action_section(future_events: Sequence[Event], recent_events: Sequence[Event], start_dt: date) -> str:
    if not future_events and not recent_events:
        return "当前无可执行的重点跟踪建议。"

    lines: List[str] = []
    next_week = [event for event in future_events if event.event_date and event.event_date <= start_dt + timedelta(days=7)]
    if next_week:
        by_company = Counter(event.company for event in next_week if event.impact_level == "H")
        if by_company:
            focus_names = "、".join(name for name, _count in by_company.most_common(5))
            lines.append(f"- **优先盯住未来 7 天高影响事项**：{focus_names} 在短窗口内有关键披露或会议安排，适合放入盘前重点跟踪清单。")

    combo_names = []
    future_tickers = {event.ticker for event in future_events if event.event_date and event.event_date <= start_dt + timedelta(days=14)}
    recent_tickers = {event.ticker for event in recent_events}
    for event in future_events:
        if event.ticker in future_tickers & recent_tickers and event.company not in combo_names:
            combo_names.append(event.company)
        if len(combo_names) >= 5:
            break
    if combo_names:
        lines.append(f"- **重视“近期已披露 + 即将落地”组合**：{('、'.join(combo_names))} 已有前序公告铺垫，后续事件更容易形成连续催化。")

    dividend_names = [event.company for event in future_events if event.event_type == "分红"]
    if dividend_names:
        deduped = []
        for name in dividend_names:
            if name not in deduped:
                deduped.append(name)
            if len(deduped) >= 5:
                break
        lines.append(f"- **分红与股东回报线索**：{('、'.join(deduped))} 在窗口内有除权除息或相关安排，可结合持仓收益兑现和事件后交易节奏评估。")

    if recent_events:
        recent_focus = []
        for event in recent_events:
            if event.company not in recent_focus:
                recent_focus.append(event.company)
            if len(recent_focus) >= 5:
                break
        lines.append(f"- **把近期披露板作为前瞻跟踪补位**：{('、'.join(recent_focus))} 近 45 天内已有高关注公告，即使未来日历暂时缺少精确日期，也应持续跟踪市场对业绩、分红和治理事项的二次反应。")

        recent_type_mix = summarize_counter((event.event_type for event in recent_events), limit=3)
        lines.append(f"- **优先围绕高频类型做盘前更新**：当前近期披露事项以 {recent_type_mix} 为主，适合在后续催化确认前持续维护重点名单和观察字段。")

    if not lines:
        lines.append("- 当前更适合维持事件跟踪而非提前做重仓博弈，等待更明确的高影响日程或经营披露进一步落地。")

    lines.extend(["", f"*数据来源：{summarize_source_mix([*future_events, *recent_events])}*"])
    return "\n".join(lines)


def build_estimated_event_sections(events: Sequence[Event], start_dt: date, end_dt: date) -> str:
    estimated = [event for event in events if event.date_type == "estimated_window"]
    if not estimated:
        return "当前观察区间内无日期不确定事件。"

    lines = [
        "以下事件缺少精确日期，仅展示预计窗口，不代表正式公告日。",
        "",
        render_markdown_table(
            ["预计窗口", "公司", "代码", "事件", "影响", "说明", "来源/置信度"],
            [
                [
                    window_display_in_range(event, start_dt, end_dt),
                    event.company,
                    event.ticker,
                    event.event_title,
                    event.impact_level,
                    event.evidence,
                    source_confidence_label(event),
                ]
                for event in estimated[:12]
            ],
        ),
        "",
        f"*数据来源：{summarize_source_mix(estimated)}*",
    ]
    return "\n".join(lines)


def build_appendix(
    report_date: str,
    future_events: Sequence[Event],
    recent_events: Sequence[Event],
) -> str:
    all_events = [*future_events, *recent_events]
    rqdata_count = sum(1 for event in all_events if event.origin != "web_search")
    web_count = sum(1 for event in all_events if event.origin == "web_search")
    return "\n".join(
        [
            f"- 报告日期：{report_date}",
            f"- 未来日历事件数：{len(future_events)}；近期已披露催化剂数：{len(recent_events)}。",
            "- 公告口径：若过去公告已明确未来会议或活动日期，报告按实际事件日纳入未来日历，并保留原文链接。",
            "- 预计窗口口径：仅在缺少精确日期时展示法定或规则化窗口，不将预计窗口伪装成确定日期。",
            f"- 数据来源构成：RQData 事件 {rqdata_count} 个；web_search 补充事件 {web_count} 个。",
            f"- 主要来源汇总：{summarize_source_mix(all_events)}。",
            f"- 近期披露类型分布：{summarize_counter((event.event_type for event in recent_events))}。",
        ]
    )


def load_template(template_path: Path) -> str:
    with template_path.open("r", encoding="utf-8") as fh:
        return fh.read()


def replace_tokens(template_text: str, tokens: Dict[str, str]) -> str:
    rendered = template_text
    for key, value in tokens.items():
        rendered = rendered.replace(f"[[{key}]]", value)

    leftovers = sorted(set(TOKEN_RE.findall(rendered)))
    if leftovers:
        raise ValueError(f"模板仍有未替换占位符: {', '.join(leftovers)}")
    return rendered


def validate_rendered_report(report_text: str) -> None:
    for heading in REQUIRED_HEADINGS:
        if heading not in report_text:
            raise ValueError(f"报告缺少必需章节: {heading}")
    if "数据来源：" not in report_text:
        raise ValueError("报告缺少数据来源标注")


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def save_markdown(output_path: Path, content: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        fh.write(content)
    return output_path


def locate_renderer_script() -> Optional[Path]:
    env_path = os.environ.get("RQ_REPORT_RENDERER")
    candidates = [Path(env_path).expanduser()] if env_path else []
    repo_renderer = Path(__file__).resolve().parents[2] / "report-renderer" / "scripts" / "render_report.py"
    candidates.append(repo_renderer)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def render_html_if_needed(markdown_path: Path, no_render: bool) -> Optional[Path]:
    if no_render:
        return None

    renderer = locate_renderer_script()
    if not renderer:
        print("警告: 未找到 rq-report-renderer，跳过 HTML 渲染。", file=sys.stderr)
        return None

    html_path = markdown_path.with_suffix(".html")
    try:
        subprocess.run(
            ["python3", str(renderer), str(markdown_path), str(html_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return html_path
    except subprocess.CalledProcessError as exc:
        print("警告: HTML 渲染失败，保留 Markdown 输出。", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return None


def infer_coverage_scope(stock_pool: List[Any], events: Sequence[Event]) -> str:
    tickers = {event.ticker for event in events if event.ticker}
    external_scopes = {event.company for event in events if event.origin == "web_search" and event.company}
    if stock_pool:
        pool_size = 0
        for item in stock_pool:
            if isinstance(item, str):
                pool_size += 1
            elif isinstance(item, dict) and normalize_ticker(item):
                pool_size += 1
        if pool_size:
            parts = [f"股票池 {pool_size} 只证券，窗口内识别到 {len(tickers)} 只证券存在事件"]
            if external_scopes:
                parts.append(f"并补充 {len(external_scopes)} 个宏观 / 行业主题催化")
            return "，".join(parts)
    if tickers:
        return f"窗口内共 {len(tickers)} 只证券存在事件"
    if external_scopes:
        return f"窗口内共 {len(external_scopes)} 个宏观 / 行业主题存在事件"
    return "未识别到有效覆盖证券"


def default_output_path(data_dir: Path, start_dt: date, end_dt: date) -> Path:
    filename = f"催化剂日历_{start_dt.isoformat()}_{end_dt.isoformat()}.md"
    return data_dir / filename


def main() -> None:
    args = parse_args()
    start_dt = parse_iso_date(args.start_date)
    end_dt = parse_iso_date(args.end_date)
    if not start_dt or not end_dt:
        raise SystemExit("错误: --start-date 和 --end-date 必须是 YYYY-MM-DD")
    if end_dt < start_dt:
        raise SystemExit("错误: end-date 不能早于 start-date")

    data_dir = Path(args.data_dir).expanduser().resolve()
    template_path = Path(args.template).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else default_output_path(data_dir, start_dt, end_dt)
    )

    raw_inputs = load_raw_inputs(data_dir)
    company_lookup = build_company_lookup(raw_inputs["stock_pool"], raw_inputs["instrument_meta"])

    events = deduplicate_events(
        [
            *normalize_earnings_events(
                raw_inputs["earnings_express_raw"],
                raw_inputs["latest_financial_raw"],
                company_lookup,
                start_dt,
            ),
            *normalize_dividend_events(raw_inputs["dividend_raw"], company_lookup),
            *normalize_announcement_events(raw_inputs["announcement_raw"], company_lookup),
            *normalize_web_search_events(raw_inputs["web_search_events"], company_lookup),
        ]
    )
    future_events = filter_future_calendar_events(events, start_dt, end_dt)
    recent_events = filter_recent_disclosed_events(events, start_dt)

    coverage_scope = infer_coverage_scope(raw_inputs["stock_pool"], future_events or recent_events)
    tokens = {
        "REPORT_DATE": args.report_date,
        "START_DATE": start_dt.isoformat(),
        "END_DATE": end_dt.isoformat(),
        "COVERAGE_SCOPE": coverage_scope,
        "EXEC_SUMMARY": build_summary(future_events, recent_events, start_dt, end_dt, coverage_scope),
        "CALENDAR_TABLE": build_calendar_table(future_events),
        "EVENT_TYPE_SECTIONS": build_event_type_sections(future_events, recent_events, start_dt, end_dt),
        "HIGH_IMPACT_SECTIONS": build_high_impact_sections(future_events, recent_events, start_dt, end_dt),
        "RECENT_DISCLOSED_SECTIONS": build_recent_disclosed_sections(recent_events),
        "ACTION_SECTION": build_action_section(future_events, recent_events, start_dt),
        "ESTIMATED_EVENT_SECTIONS": build_estimated_event_sections(future_events, start_dt, end_dt),
        "APPENDIX": build_appendix(args.report_date, future_events, recent_events),
    }

    report_text = replace_tokens(load_template(template_path), tokens)
    validate_rendered_report(report_text)

    markdown_path = save_markdown(output_path, report_text)
    html_path = render_html_if_needed(markdown_path, args.no_render)

    print(f"Markdown 报告: {markdown_path}")
    if html_path:
        print(f"HTML 报告: {html_path}")

    char_count = chinese_char_count(report_text)
    if char_count < 1200:
        print(
            f"警告: 当前报告中文字符数约 {char_count}，低于目标长度，通常意味着未来事件样本不足或输入数据不足。",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
