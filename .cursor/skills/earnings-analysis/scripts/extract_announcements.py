#!/usr/bin/env python3
"""Extract structured announcement snippets for earnings-analysis."""

from __future__ import annotations

import argparse
import json
import re
import zlib
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from generate_report import (
    build_snapshot,
    dedupe_financial_records,
    extract_records,
    parse_iso_date,
    read_json_file,
    select_relevant_announcements,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACW_POS_LIST = [
    0x0F,
    0x23,
    0x1D,
    0x18,
    0x21,
    0x10,
    0x01,
    0x26,
    0x0A,
    0x09,
    0x13,
    0x1F,
    0x28,
    0x1B,
    0x16,
    0x17,
    0x19,
    0x0D,
    0x06,
    0x0B,
    0x27,
    0x12,
    0x14,
    0x08,
    0x0E,
    0x15,
    0x20,
    0x1A,
    0x02,
    0x1E,
    0x07,
    0x04,
    0x11,
    0x05,
    0x03,
    0x1C,
    0x22,
    0x25,
    0x0C,
    0x24,
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
TEXT_SECTION_STOP_MARKERS = [
    "重要内容提示",
    "一、主要财务数据",
    "二、股东信息",
    "三、其他提醒事项",
    "四、季度财务报表",
    "五、重要事项",
    "六、其他事项",
    "风险提示",
    "重大风险提示",
    "经营情况讨论与分析",
    "管理层讨论与分析",
    "投资者关系活动主要内容介绍",
    "未来展望",
    "经营计划",
    "发展战略",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="提取公告 PDF 正文片段并生成 announcement_extracts.json")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--data-dir", required=True, help="原始 JSON 数据目录")
    parser.add_argument("--report-date", required=True, help="报告日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出 JSON 路径，默认写到 data-dir/announcement_extracts.json")
    parser.add_argument("--timeout", type=float, default=20.0, help="公告抓取超时时间，默认 20 秒")
    return parser.parse_args()


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


def fetch_pdf_bytes(url: str, timeout: float) -> Tuple[Optional[bytes], str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html,*/*", "Referer": url}
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
        cookie = calc_sse_acw_cookie(match.group(1))
        session.cookies.set("acw_sc__v2", cookie, domain="static.sse.com.cn", path="/")
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
        content_refs = [int(value) for value in re.findall(rb"/Contents\s+(\d+)\s+0\s+R", raw_object)]
        if not content_refs:
            array_match = re.search(rb"/Contents\s*\[(.*?)\]", raw_object, re.S)
            if array_match:
                content_refs = [int(value) for value in re.findall(rb"(\d+)\s+0\s+R", array_match.group(1))]
        font_map: Dict[str, int] = {}
        font_block = re.search(rb"/Font\s*<<(.+?)>>", raw_object, re.S)
        if font_block:
            for font_name, font_ref in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", font_block.group(1)):
                font_map[font_name.decode("ascii", "ignore")] = int(font_ref)
        pages.append((obj_num, content_refs, font_map))
    pages.sort(key=lambda item: item[0])

    lines: List[str] = []
    current_font: Optional[str] = None
    for _page_num, content_refs, font_map in pages:
        for content_ref in content_refs:
            if content_ref not in objects:
                continue
            _stream_dict, stream_data = parse_stream(objects[content_ref])
            if not stream_data:
                continue
            content_text = stream_data.decode("latin1", "ignore")
            current_line: List[str] = []
            for match in TEXT_OP_RE.finditer(content_text):
                token = match.group(0)
                if " Tf" in token:
                    current_font = match.group(1)
                    continue
                if token == "BT":
                    current_line = []
                    continue
                if token == "ET":
                    line = "".join(current_line).strip()
                    if line:
                        lines.append(line)
                    current_line = []
                    continue
                if token == "T*" or token.endswith("TD") or token.endswith("Td"):
                    if match.group(6) and abs(float(match.group(6))) > 1e-6:
                        line = "".join(current_line).strip()
                        if line:
                            lines.append(line)
                        current_line = []
                    continue
                if token.endswith("Tj") and token.startswith("<"):
                    font_ref = font_map.get(current_font or "")
                    cmap = font_cmaps.get(font_ref, {})
                    current_line.append(decode_pdf_hex(match.group(2), cmap))
                    continue
                if token.endswith("TJ"):
                    font_ref = font_map.get(current_font or "")
                    cmap = font_cmaps.get(font_ref, {})
                    segment = match.group(3) or ""
                    for hex_group in re.findall(r"<([0-9A-Fa-f\s]+)>", segment):
                        current_line.append(decode_pdf_hex(hex_group, cmap))
                    for literal in re.findall(r"\(((?:\\.|[^\\)])*)\)", segment):
                        current_line.append(decode_pdf_literal(literal))
                    continue
                current_line.append(decode_pdf_literal(match.group(4)))

    text = "\n".join(line for line in lines if line.strip())
    text = text.replace("\r", "\n").replace("\u3000", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def squash_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def clip_text(text: str, limit: int = 260) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，、；： ") + "…"


def normalize_section_text(text: str, limit: Optional[int] = None) -> str:
    text = str(text or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"\s+", "", text)
    if not text:
        return ""
    meaningful_chars = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。！？；：、“”‘’（）()\-%./]", text)
    if len(meaningful_chars) < max(20, int(len(text) * 0.6)):
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return ""
    if limit is None:
        return text
    return clip_text(text, limit)


def is_annual_or_interim_report(title: str, info_type: str) -> bool:
    title = str(title or "")
    info_type = str(info_type or "")
    if not re.search(r"(年度报告|年报|半年度报告|半年报|中报)", title):
        return False
    if re.search(r"(摘要|英文版|公告|业绩说明会|主要经营数据|信息披露公告)", title):
        return False
    return "定期报告" in info_type or bool(re.search(r"(年度报告|年报|半年度报告|半年报|中报)", title))


def find_marker_window(
    text: str,
    markers: Sequence[str],
    stop_markers: Sequence[str],
    max_chars: int,
    forbidden_patterns: Sequence[str] = (),
) -> str:
    candidates: List[Tuple[int, str]] = []
    for marker in markers:
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            candidates.append((idx, marker))
            start = idx + len(marker)
    if not candidates:
        return ""

    candidates.sort(key=lambda item: item[0])
    for best_start, matched_marker in candidates:
        local_context = text[max(0, best_start - 80): min(len(text), best_start + 120)]
        if re.search(r"[.．。…]{12,}", local_context):
            continue
        search_start = best_start + len(matched_marker)
        end_positions = [
            text.find(stop_marker, search_start)
            for stop_marker in stop_markers
            if stop_marker not in markers and text.find(stop_marker, search_start) >= 0
        ]
        end = min(end_positions) if end_positions else min(len(text), best_start + max_chars)
        end = min(end, best_start + max_chars)
        snippet = normalize_section_text(text[best_start:end], max_chars)
        if snippet and forbidden_patterns and any(pattern in snippet for pattern in forbidden_patterns):
            continue
        if snippet:
            return snippet
    return ""


def find_sentence_by_keywords(text: str, keywords: Sequence[str], max_chars: int) -> str:
    sentences = re.split(r"(?<=[。！？；])", text)
    for sentence in sentences:
        sentence = sentence.strip()
        if sentence and any(keyword in sentence for keyword in keywords):
            return normalize_section_text(sentence, max_chars)
    collapsed = text
    for keyword in keywords:
        idx = collapsed.find(keyword)
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(collapsed), idx + max_chars)
            return normalize_section_text(collapsed[start:end], max_chars)
    return ""


def extract_sections(title: str, info_type: str, raw_text: str) -> Dict[str, str]:
    squashed = squash_text(raw_text)
    stop_markers = TEXT_SECTION_STOP_MARKERS
    long_form_report = is_annual_or_interim_report(title, info_type)

    company_intro = ""
    management_discussion = ""
    outlook = ""
    if long_form_report:
        intro_end = len(squashed)
        for marker in ("重要内容提示", "一、主要财务数据"):
            idx = squashed.find(marker)
            if idx >= 0:
                intro_end = min(intro_end, idx)
        company_intro = normalize_section_text(squashed[:intro_end] or squashed[:220], 220)
        company_intro_marked = find_marker_window(
            squashed,
            ["公司简介", "公司基本情况", "发行人基本情况"],
            stop_markers,
            220,
        )
        if company_intro_marked:
            company_intro = company_intro_marked

        management_discussion = find_marker_window(
            squashed,
            [
                "管理层讨论与分析",
                "经营情况讨论与分析",
                "董事会报告",
                "经营回顾",
            ],
            stop_markers,
            280,
        )
        if not management_discussion:
            management_discussion = find_sentence_by_keywords(
                squashed,
                ["经营", "销量", "需求", "增长", "盈利能力", "毛利率", "渠道", "产能"],
                240,
            )

        outlook = find_marker_window(
            squashed,
            ["未来展望", "经营计划", "发展战略", "未来规划", "下半年展望", "后续规划"],
            stop_markers,
            220,
            forbidden_patterns=("前瞻性陈述", "注意投资风险"),
        )
        if not outlook:
            outlook = find_sentence_by_keywords(
                squashed,
                ["未来", "展望", "预计", "计划", "规划", "目标", "将继续", "后续"],
                220,
            )

    risk_warning = find_marker_window(
        squashed,
        ["风险提示", "重大风险提示", "风险因素", "重大风险"],
        stop_markers,
        220,
    )

    return {
        "company_intro": company_intro,
        "management_discussion": management_discussion,
        "risk_warning": risk_warning,
        "outlook": outlook,
    }


def build_raw_sections(title: str, info_type: str, raw_text: str) -> Dict[str, str]:
    squashed = squash_text(raw_text)
    stop_markers = TEXT_SECTION_STOP_MARKERS
    long_form_report = is_annual_or_interim_report(title, info_type)

    company_intro = ""
    management_discussion = ""
    outlook = ""
    if long_form_report:
        intro_end = len(squashed)
        for marker in ("重要内容提示", "一、主要财务数据"):
            idx = squashed.find(marker)
            if idx >= 0:
                intro_end = min(intro_end, idx)
        company_intro = normalize_section_text(squashed[:intro_end] or squashed[:1200], 1200)
        company_intro_marked = find_marker_window(
            squashed,
            ["公司简介", "公司基本情况", "发行人基本情况"],
            stop_markers,
            1400,
        )
        if company_intro_marked:
            company_intro = company_intro_marked

        management_discussion = find_marker_window(
            squashed,
            ["管理层讨论与分析", "经营情况讨论与分析", "董事会报告", "经营回顾"],
            stop_markers,
            2600,
        )
        if not management_discussion:
            management_discussion = find_sentence_by_keywords(
                squashed,
                ["经营", "销量", "需求", "增长", "盈利能力", "毛利率", "渠道", "产能"],
                1600,
            )

        outlook = find_marker_window(
            squashed,
            ["未来展望", "经营计划", "发展战略", "未来规划", "下半年展望", "后续规划"],
            stop_markers,
            1800,
            forbidden_patterns=("前瞻性陈述", "注意投资风险"),
        )
        if not outlook:
            outlook = find_sentence_by_keywords(
                squashed,
                ["未来", "展望", "预计", "计划", "规划", "目标", "将继续", "后续"],
                1200,
            )

    risk_warning = find_marker_window(
        squashed,
        ["风险提示", "重大风险提示", "风险因素", "重大风险"],
        stop_markers,
        1400,
    )

    return {
        "company_intro": company_intro,
        "management_discussion": management_discussion,
        "risk_warning": risk_warning,
        "outlook": outlook,
    }


def choose_extract_status(sections: Dict[str, str], title: str, info_type: str) -> str:
    populated = sum(1 for value in sections.values() if value)
    if not is_annual_or_interim_report(title, info_type) and populated == 0:
        return "skipped_non_annual_interim"
    if populated >= 4:
        return "ok"
    if populated >= 1:
        return "partial"
    return "no_sections"


def main() -> None:
    args = parse_args()
    report_date = date.fromisoformat(args.report_date)
    data_dir = Path(args.data_dir).expanduser()
    output_path = Path(args.output).expanduser() if args.output else data_dir / "announcement_extracts.json"

    financial_records = extract_records(read_json_file(data_dir / "historical_financials.json"))
    announcement_records = extract_records(read_json_file(data_dir / "announcement_raw.json"))
    if not financial_records:
        raise ValueError("缺少 historical_financials.json，无法定位财报事件日")

    deduped_financials = dedupe_financial_records(financial_records, args.stock, report_date)
    latest_snapshot = build_snapshot(deduped_financials[-1]) if deduped_financials else None
    if latest_snapshot is None:
        raise ValueError("未识别到 report-date 之前的最新财报季度")

    selected_announcements = select_relevant_announcements(
        announcement_records,
        args.stock,
        latest_snapshot.info_date,
        report_date,
    )

    records: List[Dict[str, Any]] = []
    for item in selected_announcements:
        title = str(item.get("title") or "")
        link = str(item.get("announcement_link") or "")
        info_date = parse_iso_date(item.get("info_date") or item.get("date") or item.get("create_tm"))
        empty_sections = {
            "company_intro": "",
            "management_discussion": "",
            "risk_warning": "",
            "outlook": "",
        }
        record: Dict[str, Any] = {
            "title": title,
            "info_date": info_date.isoformat() if info_date else str(item.get("info_date") or ""),
            "announcement_link": link,
            "media": item.get("media"),
            "info_type": item.get("info_type"),
            "is_annual_or_interim_report": is_annual_or_interim_report(title, str(item.get("info_type") or "")),
            "fetch_status": "skipped",
            "extract_status": "not_started",
            "raw_sections": dict(empty_sections),
            "summaries": dict(empty_sections),
            "sections": dict(empty_sections),
        }

        if str(item.get("file_type") or "").upper() != "PDF":
            record["fetch_status"] = "unsupported_file_type"
            record["extract_status"] = "unsupported"
            records.append(record)
            continue

        pdf_bytes, fetch_status = fetch_pdf_bytes(link, args.timeout)
        record["fetch_status"] = fetch_status
        if not pdf_bytes:
            record["extract_status"] = "fetch_failed"
            records.append(record)
            continue

        try:
            extracted_text = extract_pdf_text(pdf_bytes)
        except Exception as exc:  # pragma: no cover - defensive branch for malformed PDFs
            record["extract_status"] = f"pdf_parse_failed:{type(exc).__name__}"
            records.append(record)
            continue

        raw_sections = build_raw_sections(title, str(item.get("info_type") or ""), extracted_text)
        sections = extract_sections(title, str(item.get("info_type") or ""), extracted_text)
        record["raw_sections"] = raw_sections
        record["summaries"] = dict(empty_sections)
        record["sections"] = raw_sections
        record["extract_status"] = choose_extract_status(raw_sections, title, str(item.get("info_type") or ""))
        records.append(record)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stock": args.stock,
        "report_date": args.report_date,
        "event_date": latest_snapshot.info_date.isoformat(),
        "record_count": len(records),
        "records": records,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 公告提取结果已写入：{output_path}")
    print(f"相关公告样本：{len(records)} 条")


if __name__ == "__main__":
    main()
