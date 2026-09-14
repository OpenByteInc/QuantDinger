#!/usr/bin/env python3
"""Render idea-generation report from structured snapshot + LLM summaries."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


TOKEN_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
REQUIRED_HEADINGS = [
    "# 投资创意生成报告",
    "## 执行摘要",
    "## 股票池与筛选框架",
    "## 指标看板",
    "## 价值策略候选",
    "## 成长策略候选",
    "## 质量策略候选",
    "## 候选组合与交叉验证",
    "## 风险与跟踪重点",
    "## 附录：口径说明",
]
SUMMARY_KEYS = [
    "exec_summary",
    "universe_overview",
    "metric_scoreboard",
    "value_section",
    "growth_section",
    "quality_section",
    "overlap_section",
    "risk_section",
    "appendix",
]


def parse_args() -> argparse.Namespace:
    skill_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="根据结构化快照与 LLM 摘要生成 idea-generation 报告")
    parser.add_argument("--data-dir", help="原始数据目录；默认从其中读取 idea_screening_snapshot.json")
    parser.add_argument("--snapshot", help="结构化快照 JSON 路径")
    parser.add_argument("--strategy", help="兼容旧入口参数；当前忽略")
    parser.add_argument("--report-date", help="兼容旧入口参数；当前忽略")
    parser.add_argument("--top-n", help="兼容旧入口参数；当前忽略")
    parser.add_argument("--output", required=True, help="输出 Markdown 文件路径")
    parser.add_argument("--template", default=str(skill_dir / "assets" / "template.md"), help="Markdown 模板路径")
    parser.add_argument("--no-render", action="store_true", help="不尝试渲染 HTML")
    return parser.parse_args()


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def number_text(value: Optional[float], digits: int = 1) -> str:
    if value in (None, "", "null"):
        return "无数据"
    return f"{float(value):.{digits}f}"


def percent_text(value: Optional[float], digits: int = 1) -> str:
    if value in (None, "", "null"):
        return "无数据"
    return f"{float(value):+.{digits}f}%"


def unsigned_percent_text(value: Optional[float], digits: int = 1) -> str:
    if value in (None, "", "null"):
        return "无数据"
    return f"{float(value):.{digits}f}%"


def billion_yuan_text(value: Optional[float]) -> str:
    if value in (None, "", "null"):
        return "无数据"
    return f"{float(value) / 1e8:.2f}亿元"


def x_text(value: Optional[float], digits: int = 2) -> str:
    if value in (None, "", "null"):
        return "无数据"
    return f"{float(value):.{digits}f}x"


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_counter_table(title_field: str, counters: Sequence[Dict[str, Any]]) -> str:
    rows = [[item.get("name", "无数据"), str(item.get("count", 0))] for item in counters]
    if not rows:
        rows = [["无数据", "-"]]
    return format_table([title_field, "数量"], rows)


def thresholds_text(name: str, thresholds: Dict[str, Any]) -> str:
    if name == "value":
        return (
            f"PE < {x_text(thresholds.get('pe_lt'), 1)}"
            f"（全池 PE 中位数倍数 {number_text(thresholds.get('pe_median_multiplier'), 2)}）"
            f"；PB < {number_text(thresholds.get('pb_lt'), 2)}；ROE > {number_text(thresholds.get('roe_gt'), 1)}%"
        )
    if name == "growth":
        return f"营收同比 > {number_text(thresholds.get('revenue_yoy_gt'), 1)}%；净利润同比 > {number_text(thresholds.get('profit_yoy_gt'), 1)}%；ROE > {number_text(thresholds.get('roe_gt'), 1)}%"
    return f"ROE > {number_text(thresholds.get('roe_gt'), 1)}%；资产负债率 < {number_text(thresholds.get('debt_ratio_lt'), 1)}%"


def sector_count_text(counters: Sequence[Dict[str, Any]], limit: int = 3) -> str:
    chosen = counters[:limit]
    if not chosen:
        return "无数据"
    return "、".join(f"{item.get('name', '未分类')}（{item.get('count', 0)}只）" for item in chosen)


def render_strategy_facts(name: str, payload: Dict[str, Any]) -> str:
    metrics = payload.get("median_metrics", {})
    rows: List[List[str]] = [
        ["策略状态", "启用" if payload.get("enabled") else "未启用"],
        ["通过数量", str(payload.get("candidate_count", 0))],
        ["展示数量", str(payload.get("display_count", 0))],
        ["筛选阈值", thresholds_text(name, payload.get("thresholds", {}))],
        ["行业集中", sector_count_text(payload.get("sector_counts", []), 3)],
    ]
    if name == "value":
        rows.extend(
            [
                ["候选PE中位数", x_text(metrics.get("pe"), 1)],
                ["候选PB中位数", x_text(metrics.get("pb"))],
                ["候选ROE中位数", unsigned_percent_text(metrics.get("roe"))],
            ]
        )
    elif name == "growth":
        rows.extend(
            [
                ["候选营收同比中位数", percent_text(metrics.get("revenue_yoy"))],
                ["候选净利润同比中位数", percent_text(metrics.get("profit_yoy"))],
                ["候选ROE中位数", unsigned_percent_text(metrics.get("roe"))],
            ]
        )
    else:
        rows.extend(
            [
                ["候选ROE中位数", unsigned_percent_text(metrics.get("roe"))],
                ["候选资产负债率中位数", unsigned_percent_text(metrics.get("debt_ratio"))],
                ["候选净利润中位数", billion_yuan_text(metrics.get("net_profit"))],
            ]
        )
    return format_table(["字段", "数值"], rows)


def render_strategy_candidates(name: str, candidates: Sequence[Dict[str, Any]]) -> str:
    if name == "value":
        headers = ["股票", "行业", "最新季度", "PE", "PB", "ROE", "市值"]
        rows = [
            [
                f"{item.get('name', item.get('order_book_id', '-'))}<br>`{item.get('order_book_id', '-')}`",
                item.get("sector", "未分类"),
                item.get("latest_quarter", "无数据"),
                x_text(item.get("pe"), 1),
                x_text(item.get("pb")),
                unsigned_percent_text(item.get("roe")),
                billion_yuan_text(item.get("market_cap")),
            ]
            for item in candidates
        ]
    elif name == "growth":
        headers = ["股票", "行业", "最新季度", "营收同比", "净利润同比", "ROE", "PE"]
        rows = [
            [
                f"{item.get('name', item.get('order_book_id', '-'))}<br>`{item.get('order_book_id', '-')}`",
                item.get("sector", "未分类"),
                item.get("latest_quarter", "无数据"),
                percent_text(item.get("revenue_yoy")),
                percent_text(item.get("profit_yoy")),
                unsigned_percent_text(item.get("roe")),
                x_text(item.get("pe"), 1),
            ]
            for item in candidates
        ]
    else:
        headers = ["股票", "行业", "最新季度", "ROE", "资产负债率", "净利润", "PB"]
        rows = [
            [
                f"{item.get('name', item.get('order_book_id', '-'))}<br>`{item.get('order_book_id', '-')}`",
                item.get("sector", "未分类"),
                item.get("latest_quarter", "无数据"),
                unsigned_percent_text(item.get("roe")),
                unsigned_percent_text(item.get("debt_ratio")),
                billion_yuan_text(item.get("net_profit")),
                x_text(item.get("pb")),
            ]
            for item in candidates
        ]
    if not rows:
        rows = [["无候选", "-", "-", "-", "-", "-", "-"]]
    return format_table(headers, rows)


def render_metric_table(snapshot: Dict[str, Any]) -> str:
    metrics = snapshot.get("universe", {}).get("median_metrics", {})
    strategy_payloads = snapshot.get("strategies", {})
    rows = [
        ["PE 中位数", x_text(metrics.get("pe"), 1), str(strategy_payloads.get("value", {}).get("candidate_count", 0))],
        ["PB 中位数", x_text(metrics.get("pb")), "-"],
        ["ROE 中位数", unsigned_percent_text(metrics.get("roe")), str(strategy_payloads.get("quality", {}).get("candidate_count", 0))],
        ["营收同比中位数", percent_text(metrics.get("revenue_yoy")), str(strategy_payloads.get("growth", {}).get("candidate_count", 0))],
        ["净利润同比中位数", percent_text(metrics.get("profit_yoy")), "-"],
        ["总市值中位数", billion_yuan_text(metrics.get("market_cap")), "-"],
    ]
    return format_table(["指标", "全池参考", "关联候选数量"], rows)


def render_universe_facts(snapshot: Dict[str, Any]) -> str:
    universe = snapshot.get("universe", {})
    rows = [
        ["股票池规模", str(universe.get("stock_pool_size", 0))],
        ["财务快照覆盖", str(universe.get("valid_financials", 0))],
        ["ROE 覆盖", str(universe.get("valid_roe", 0))],
        ["PE 覆盖", str(universe.get("valid_pe", 0))],
        ["PB 覆盖", str(universe.get("valid_pb", 0))],
    ]
    return format_table(["字段", "数值"], rows)


def render_overlap_facts(snapshot: Dict[str, Any]) -> str:
    overlap = snapshot.get("overlap", {})
    sectors = sector_count_text(overlap.get("sector_counts", []), 3)
    rows = [
        ["交叉命中数量", str(overlap.get("candidate_count", 0))],
        ["行业集中", sectors],
    ]
    return format_table(["字段", "数值"], rows)


def render_overlap_table(snapshot: Dict[str, Any]) -> str:
    rows = [
        [
            f"{item.get('name', item.get('order_book_id', '-'))}<br>`{item.get('order_book_id', '-')}`",
            item.get("sector", "未分类"),
            " / ".join(item.get("strategies", [])),
            unsigned_percent_text(item.get("roe")),
            x_text(item.get("pe"), 1),
            x_text(item.get("pb")),
            percent_text(item.get("revenue_yoy")),
            percent_text(item.get("profit_yoy")),
        ]
        for item in snapshot.get("overlap", {}).get("display_candidates", [])
    ]
    if not rows:
        rows = [["无候选", "-", "-", "-", "-", "-", "-", "-"]]
    return format_table(["股票", "行业", "命中策略", "ROE", "PE", "PB", "营收同比", "净利润同比"], rows)


def render_risk_facts(snapshot: Dict[str, Any]) -> str:
    risk = snapshot.get("risk_flags", {})
    top_sector = risk.get("top_selected_sector") or {}
    rows = [
        ["缺失财务样本数", str(risk.get("missing_financial_count", 0))],
        ["当前候选去重数量", str(risk.get("selected_count", 0))],
        [
            "候选最集中行业",
            f"{top_sector.get('name', '无数据')}（{top_sector.get('count', 0)}次）" if top_sector else "无数据",
        ],
        ["成长候选PE中位数", x_text(risk.get("growth_pe_median"), 1)],
        ["价值/质量交集", str(risk.get("value_quality_overlap_count", 0))],
        ["价值/成长交集", str(risk.get("value_growth_overlap_count", 0))],
    ]
    return format_table(["字段", "数值"], rows)


def render_appendix_facts(snapshot: Dict[str, Any]) -> str:
    inputs = snapshot.get("inputs", {})
    rows = [[name, str(value)] for name, value in inputs.items()]
    if not rows:
        rows = [["无数据", "-"]]
    return format_table(["输入文件", "记录数"], rows)


def require_summaries(snapshot: Dict[str, Any]) -> Dict[str, str]:
    summaries = snapshot.get("summaries") or {}
    missing = [key for key in SUMMARY_KEYS if not str(summaries.get(key, "")).strip()]
    if missing:
        raise ValueError(
            "结构化快照中的 summaries 缺失，需先由 LLM 回写以下字段："
            + ", ".join(missing)
        )
    return {key: str(summaries[key]).strip() for key in SUMMARY_KEYS}


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
    snapshot_path = Path(args.snapshot).expanduser() if args.snapshot else None
    if snapshot_path is None:
        if not args.data_dir:
            raise ValueError("必须提供 --snapshot 或 --data-dir。")
        snapshot_path = Path(args.data_dir).expanduser() / "idea_screening_snapshot.json"
    snapshot = read_json_file(snapshot_path)
    summaries = require_summaries(snapshot)

    strategy_payloads = snapshot.get("strategies", {})
    template_text = Path(args.template).read_text(encoding="utf-8")
    report_text = render_template(
        template_text,
        {
            "REPORT_DATE": str(snapshot.get("report_date", date.today().isoformat())),
            "STRATEGY": str(snapshot.get("strategy", "all")),
            "EXEC_SUMMARY": summaries["exec_summary"],
            "UNIVERSE_OVERVIEW": summaries["universe_overview"],
            "UNIVERSE_FACTS": render_universe_facts(snapshot),
            "QUARTER_TABLE": render_counter_table("最新财报季度", snapshot.get("universe", {}).get("latest_quarters", [])),
            "SECTOR_TABLE": render_counter_table("行业", snapshot.get("universe", {}).get("top_sectors", [])),
            "METRIC_SCOREBOARD": summaries["metric_scoreboard"],
            "METRIC_TABLE": render_metric_table(snapshot),
            "VALUE_SUMMARY": summaries["value_section"],
            "VALUE_FACTS": render_strategy_facts("value", strategy_payloads.get("value", {})),
            "VALUE_TABLE": render_strategy_candidates("value", strategy_payloads.get("value", {}).get("display_candidates", [])),
            "GROWTH_SUMMARY": summaries["growth_section"],
            "GROWTH_FACTS": render_strategy_facts("growth", strategy_payloads.get("growth", {})),
            "GROWTH_TABLE": render_strategy_candidates("growth", strategy_payloads.get("growth", {}).get("display_candidates", [])),
            "QUALITY_SUMMARY": summaries["quality_section"],
            "QUALITY_FACTS": render_strategy_facts("quality", strategy_payloads.get("quality", {})),
            "QUALITY_TABLE": render_strategy_candidates("quality", strategy_payloads.get("quality", {}).get("display_candidates", [])),
            "OVERLAP_SUMMARY": summaries["overlap_section"],
            "OVERLAP_FACTS": render_overlap_facts(snapshot),
            "OVERLAP_TABLE": render_overlap_table(snapshot),
            "RISK_SUMMARY": summaries["risk_section"],
            "RISK_FACTS": render_risk_facts(snapshot),
            "APPENDIX_SUMMARY": summaries["appendix"],
            "APPENDIX_FACTS": render_appendix_facts(snapshot),
        },
    )

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"✅ Markdown 报告已生成：{output_path}")

    if not args.no_render:
        try_render_html(output_path)


if __name__ == "__main__":
    main()
