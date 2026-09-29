#!/usr/bin/env python3
"""Render Markdown research reports into standalone HTML."""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


HEADING_RE = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
ORDERED_ITEM_RE = re.compile(r"^\d+\.\s+(.*\S)\s*$")
UNORDERED_ITEM_RE = re.compile(r"^[-*]\s+(.*\S)\s*$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
REPORT_DATE_RE = re.compile(r"报告日期[:：]\s*([^\n]+)")
META_LINE_RE = re.compile(r"^[-*]\s*([^：:]{1,24})[:：]\s*(.+)$")


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 Markdown 研究报告渲染为单文件 HTML")
    parser.add_argument("input", help="输入 Markdown 文件路径")
    parser.add_argument("output", nargs="?", help="输出 HTML 文件路径，默认与输入同名")
    parser.add_argument("--title", help="覆盖封面标题")
    parser.add_argument("--open", action="store_true", help="渲染完成后尝试在默认浏览器打开")
    return parser.parse_args()


def load_css(script_path: Path) -> str:
    css_path = script_path.resolve().parent.parent / "assets" / "report.css"
    return css_path.read_text(encoding="utf-8")


def split_table_row(line: str) -> List[str]:
    inner = line.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


def is_table_divider(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        stripped = cell.replace(":", "").replace("-", "").strip()
        if stripped:
            return False
    return True


def cell_alignment(cell: str) -> str:
    stripped = cell.strip()
    if stripped.startswith(":") and stripped.endswith(":"):
        return "align-center"
    if stripped.endswith(":"):
        return "align-right"
    return "align-left"


def preserve_soft_breaks(text: str) -> str:
    return text.replace("&lt;br&gt;", "<br>")


def render_inline(text: str) -> str:
    escaped = preserve_soft_breaks(html.escape(text, quote=False))

    def replace_code(match: re.Match[str]) -> str:
        return f"<code>{match.group(1)}</code>"

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = html.escape(match.group(2), quote=True)
        return f'<a href="{url}" target="_blank" rel="noreferrer">{label}</a>'

    escaped = re.sub(r"`([^`]+)`", replace_code, escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def slugify(text: str, index: int) -> str:
    compact = re.sub(r"\s+", "-", text.strip().lower())
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff-]", "", compact)
    compact = compact.strip("-")
    return compact or f"section-{index}"


def parse_metadata(markdown_text: str, markdown_path: Path) -> dict[str, str]:
    lines = markdown_text.splitlines()
    title = markdown_path.stem
    for line in lines:
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) == 1:
            title = match.group(2).strip()
            break

    report_date_match = REPORT_DATE_RE.search(markdown_text)
    report_date = report_date_match.group(1).strip() if report_date_match else "未提供"

    meta: dict[str, str] = {"title": title, "report_date": report_date}
    for line in lines[:20]:
        meta_match = META_LINE_RE.match(line.strip())
        if meta_match:
            key = meta_match.group(1).strip()
            value = meta_match.group(2).strip()
            if key and value:
                meta[key] = value
    return meta


def render_table(table_lines: Sequence[str]) -> str:
    rows = [split_table_row(line) for line in table_lines if line.strip()]
    if not rows:
        return ""

    header = rows[0]
    divider = rows[1] if len(rows) > 1 and is_table_divider(rows[1]) else None
    alignments = [cell_alignment(cell) for cell in divider] if divider else ["align-left"] * len(header)
    body_rows = rows[2:] if divider else rows[1:]

    head_html = "".join(f'<th class="{alignments[idx] if idx < len(alignments) else "align-left"}">{render_inline(cell)}</th>' for idx, cell in enumerate(header))
    parts = ["<table>", f"<thead><tr>{head_html}</tr></thead>", "<tbody>"]
    for row in body_rows:
        cells = []
        for idx, cell in enumerate(row):
            align = alignments[idx] if idx < len(alignments) else "align-left"
            cells.append(f'<td class="{align}">{render_inline(cell)}</td>')
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_list(list_lines: Sequence[str], ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    items = []
    for line in list_lines:
        match = ORDERED_ITEM_RE.match(line) if ordered else UNORDERED_ITEM_RE.match(line)
        if match:
            items.append(f"<li>{render_inline(match.group(1).strip())}</li>")
    return f"<{tag}>" + "".join(items) + f"</{tag}>"


def render_code_block(code_lines: Sequence[str], language: str) -> str:
    payload = html.escape("\n".join(code_lines), quote=False)
    class_attr = f' data-language="{html.escape(language, quote=True)}"' if language else ""
    return f'<pre class="code-block"{class_attr}><code>{payload}</code></pre>'


def paragraph_class(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text).strip()
    if plain.startswith("数据来源：") or plain.startswith("*数据来源："):
        return " class=\"source-note\""
    return ""


def render_markdown(markdown_text: str) -> tuple[str, List[Heading]]:
    lines = markdown_text.splitlines()
    parts: List[str] = []
    headings: List[Heading] = []
    paragraph: List[str] = []
    unordered_items: List[str] = []
    ordered_items: List[str] = []
    table_lines: List[str] = []
    quote_lines: List[str] = []
    code_lines: List[str] = []
    in_code_block = False
    code_language = ""
    heading_index = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        text = render_inline(" ".join(item.strip() for item in paragraph))
        parts.append(f"<p{paragraph_class(text)}>{text}</p>")
        paragraph = []

    def flush_unordered() -> None:
        nonlocal unordered_items
        if unordered_items:
            parts.append(render_list(unordered_items, ordered=False))
            unordered_items = []

    def flush_ordered() -> None:
        nonlocal ordered_items
        if ordered_items:
            parts.append(render_list(ordered_items, ordered=True))
            ordered_items = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            parts.append(render_table(table_lines))
            table_lines = []

    def flush_quote() -> None:
        nonlocal quote_lines
        if quote_lines:
            text = render_inline(" ".join(quote_lines))
            parts.append(f"<blockquote><p>{text}</p></blockquote>")
            quote_lines = []

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        if in_code_block:
            if line.strip().startswith("```"):
                parts.append(render_code_block(code_lines, code_language))
                code_lines = []
                in_code_block = False
                code_language = ""
            else:
                code_lines.append(line)
            continue

        if line.strip().startswith("```"):
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_table()
            flush_quote()
            in_code_block = True
            code_language = line.strip()[3:].strip()
            code_lines = []
            continue

        if not line.strip():
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_table()
            flush_quote()
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_table()
            flush_quote()
            heading_index += 1
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            anchor = slugify(text, heading_index)
            headings.append(Heading(level=level, text=text, anchor=anchor))
            parts.append(f'<h{level} id="{anchor}">{render_inline(text)}</h{level}>')
            continue

        if line.strip() == "---":
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_table()
            flush_quote()
            parts.append("<hr>")
            continue

        if line.lstrip().startswith("|"):
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_quote()
            table_lines.append(line)
            continue

        quote_match = BLOCKQUOTE_RE.match(line.strip())
        if quote_match:
            flush_paragraph()
            flush_unordered()
            flush_ordered()
            flush_table()
            quote_lines.append(quote_match.group(1).strip())
            continue

        if UNORDERED_ITEM_RE.match(line.strip()):
            flush_paragraph()
            flush_ordered()
            flush_table()
            flush_quote()
            unordered_items.append(line.strip())
            continue

        if ORDERED_ITEM_RE.match(line.strip()):
            flush_paragraph()
            flush_unordered()
            flush_table()
            flush_quote()
            ordered_items.append(line.strip())
            continue

        flush_unordered()
        flush_ordered()
        flush_table()
        flush_quote()
        paragraph.append(line.strip())

    if in_code_block:
        parts.append(render_code_block(code_lines, code_language))
    flush_paragraph()
    flush_unordered()
    flush_ordered()
    flush_table()
    flush_quote()
    return "\n".join(parts), headings


def build_toc(headings: Iterable[Heading]) -> str:
    items = [heading for heading in headings if heading.level in (2, 3, 4)]
    if not items:
        return ""
    lines = ['<nav class="toc">', '<p class="toc-title">Contents</p>', "<ul>"]
    for heading in items:
        lines.append(
            f'<li class="level-{heading.level}"><a href="#{heading.anchor}">{html.escape(heading.text)}</a></li>'
        )
    lines.extend(["</ul>", "</nav>"])
    return "\n".join(lines)


def render_meta_list(metadata: dict[str, str]) -> str:
    preferred = ["报告日期", "行业", "公司", "覆盖范围"]
    items = []
    for key in preferred:
        if key in metadata:
            items.append((key, metadata[key]))
    for key, value in metadata.items():
        if key in {"title", "report_date"} or key in preferred:
            continue
        items.append((key, value))
    if not items:
        items = [("报告日期", metadata.get("report_date", "未提供"))]
    return "<ul class=\"meta-list\">" + "".join(
        f"<li><strong>{html.escape(key)}</strong> {render_inline(value)}</li>" for key, value in items
    ) + "</ul>"


def build_html_document(title: str, subtitle: str, report_date: str, metadata: dict[str, str], toc_html: str, body_html: str, css: str) -> str:
    meta_chips = "".join(
        f'<span class="chip">{html.escape(label)} {render_inline(value)}</span>'
        for label, value in [("Report Date", report_date), ("Format", "Standalone HTML"), ("Source", "RQ Research Markdown")]
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
{css}
  </style>
</head>
<body>
  <div class="page">
    <aside class="sidebar">
      <p class="eyebrow">RQ Report Renderer</p>
      <h1 class="sidebar-title">{html.escape(title)}</h1>
      {render_meta_list(metadata)}
      {toc_html}
    </aside>

    <main class="report">
      <section class="cover">
        <p class="cover-kicker">Research Output</p>
        <h1 class="cover-title">{html.escape(title)}</h1>
        <p class="cover-subtitle">{render_inline(subtitle)}</p>
        <div class="cover-meta">{meta_chips}</div>
      </section>

      <section class="content">
{body_html}
        <div class="footer">Generated by report-renderer/scripts/render_report.py</div>
      </section>
    </main>
  </div>
</body>
</html>
"""


def resolve_output_path(args: argparse.Namespace, input_path: Path) -> Path:
    return Path(args.output).expanduser() if args.output else input_path.with_suffix(".html")


def infer_subtitle(metadata: dict[str, str]) -> str:
    for key in ("行业", "公司", "覆盖范围"):
        if key in metadata:
            return f"{key}：{metadata[key]}"
    return "由 Markdown 研究报告自动渲染的单文件 HTML。"


def try_open(path: Path) -> None:
    commands = (["xdg-open", str(path)], ["open", str(path)])
    for command in commands:
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    print("警告：未找到可用的浏览器打开命令，已保留 HTML 文件。")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(f"输入 Markdown 不存在：{input_path}")

    markdown_text = input_path.read_text(encoding="utf-8")
    metadata = parse_metadata(markdown_text, input_path)
    title = args.title or metadata.get("title") or input_path.stem
    report_date = metadata.get("report_date", "未提供")
    subtitle = infer_subtitle(metadata)
    body_html, headings = render_markdown(markdown_text)
    toc_html = build_toc(headings)
    css = load_css(Path(__file__))
    output_html = build_html_document(title, subtitle, report_date, metadata, toc_html, body_html, css)

    output_path = resolve_output_path(args, input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_html, encoding="utf-8")
    print(f"✅ HTML 报告已生成：{output_path}")

    if args.open:
        try_open(output_path)


if __name__ == "__main__":
    main()
