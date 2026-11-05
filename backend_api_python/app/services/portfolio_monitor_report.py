"""Shared rendering helpers for scheduled professional analysis reports."""
from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, List


SUPPORTED_REPORT_VERSIONS = {"professional_report_v1", "1.0"}


def get_professional_report(analysis: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Return the validated report-shaped payload attached to an analysis."""
    if not isinstance(analysis, dict):
        return None
    report = analysis.get("professional_report")
    if not isinstance(report, dict):
        return None
    if report.get("schema_version") not in SUPPORTED_REPORT_VERSIONS:
        return None
    return report


def collect_professional_reports(analyses: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collect report payloads while preserving analysis order."""
    reports: List[Dict[str, Any]] = []
    for analysis in analyses:
        report = get_professional_report(analysis)
        if report:
            reports.append(report)
    return reports


def _number(value: Any, digits: int = 0, suffix: str = "") -> str:
    try:
        return f"{float(value):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "--"


def _text(value: Any) -> str:
    return escape(str(value or "").strip())


def render_professional_html(report: Dict[str, Any], labels: Dict[str, str]) -> str:
    """Render the current professional contract as an email-safe HTML fragment."""
    decision = report.get("decision_profile") or {}
    quality = report.get("data_quality") or {}
    dimensions = report.get("dimensions") or []
    scenarios = report.get("scenarios") or []
    risk = report.get("risk_plan") or {}
    candidate = risk.get("candidate_setup") or {}
    display_risk = candidate or risk
    instrument = report.get("instrument") or {}
    decision_name = str(decision.get("decision") or "HOLD").upper()
    decision_label = labels.get(decision_name.lower(), decision_name)
    bias = str(decision.get("market_bias") or "NEUTRAL").upper()
    summary = report.get("executive_summary") or decision.get("rationale") or ""

    dimension_html = "".join(
        "<div class=\"qd-pro-card\">"
        f"<div class=\"qd-pro-card-head\"><strong>{_text(item.get('key'))}</strong>"
        f"<span>{_number(item.get('score'))}</span></div>"
        f"<p>{_text(item.get('narrative'))}</p></div>"
        for item in dimensions
        if isinstance(item, dict)
    )
    scenario_html = "".join(
        "<div class=\"qd-pro-card\">"
        f"<div class=\"qd-pro-card-head\"><strong>{_text(item.get('case'))}</strong>"
        f"<span>{_money(item.get('target_price'))}</span></div>"
        f"<p>{_text(item.get('thesis') or item.get('trigger'))}</p></div>"
        for item in scenarios
        if isinstance(item, dict)
    )
    risk_html = ""
    if display_risk:
        risk_rows = (
            (labels.get("entry_price", "Entry"), _money(display_risk.get("entry_price"))),
            (labels.get("stop_loss", "Stop loss"), _money(display_risk.get("stop_loss"))),
            (labels.get("take_profit", "Take profit"), _money(display_risk.get("take_profit"))),
            (labels.get("risk_reward", "Net R/R"), _number(display_risk.get("net_risk_reward"), 2)),
            (labels.get("recommended_position", "Position"), _number(risk.get("recommended_position_pct"), 1, "%")),
            (labels.get("estimated_cost", "Cost"), _number(risk.get("estimated_roundtrip_cost_bps"), 1, " bps")),
        )
        risk_html = "".join(
            f"<div><span>{_text(label)}</span><strong>{_text(value)}</strong></div>"
            for label, value in risk_rows
        )

    return f"""
    <style>
      .qd-professional {{ margin: 12px 0 0; padding: 16px; border-top: 1px solid #e5e7eb; background: #ffffff; color: #171717; }}
      .qd-pro-header {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(180px,30%); gap: 16px; padding: 16px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fafafa; }}
      .qd-pro-kicker {{ color: #dc2626; font-size: 11px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }}
      .qd-pro-header h3 {{ margin: 5px 0; font-size: 20px; }}
      .qd-pro-header p, .qd-pro-card p {{ margin: 6px 0 0; color: #525252; font-size: 13px; line-height: 1.65; }}
      .qd-pro-decision {{ display: grid; align-content: center; gap: 4px; padding: 12px; border: 1px solid #e5e7eb; border-radius: 9px; background: #ffffff; text-align: right; }}
      .qd-pro-decision span, .qd-pro-decision small, .qd-pro-quality span, .qd-pro-risk span {{ color: #737373; font-size: 11px; }}
      .qd-pro-decision strong {{ color: #d97706; font-size: 20px; }}
      .qd-pro-quality, .qd-pro-risk {{ display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 8px; margin-top: 10px; }}
      .qd-pro-quality div, .qd-pro-risk div {{ display: grid; gap: 4px; padding: 10px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fafafa; }}
      .qd-professional section {{ margin-top: 14px; }}
      .qd-professional h4 {{ margin: 0 0 8px; font-size: 14px; }}
      .qd-pro-grid {{ display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }}
      .qd-pro-grid-3 {{ grid-template-columns: repeat(3,minmax(0,1fr)); }}
      .qd-pro-card {{ padding: 11px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fafafa; }}
      .qd-pro-card-head {{ display: flex; justify-content: space-between; gap: 8px; }}
      .qd-pro-card-head span {{ color: #d97706; font-weight: 800; }}
      .qd-professional footer {{ margin-top: 14px; color: #737373; font-size: 10px; }}
      @media (prefers-color-scheme: dark) {{
        .qd-professional {{ border-color: #333333; background: #171717; color: #f5f5f5; }}
        .qd-pro-header, .qd-pro-card, .qd-pro-quality div, .qd-pro-risk div {{ border-color: #353535; background: #202020; }}
        .qd-pro-decision {{ border-color: #3b3b3b; background: #262626; }}
        .qd-pro-header p, .qd-pro-card p {{ color: #d4d4d4; }}
        .qd-pro-decision span, .qd-pro-decision small, .qd-pro-quality span, .qd-pro-risk span, .qd-professional footer {{ color: #a3a3a3; }}
      }}
      @media (max-width: 600px) {{
        .qd-pro-header {{ grid-template-columns: 1fr; }}
        .qd-pro-quality, .qd-pro-risk, .qd-pro-grid, .qd-pro-grid-3 {{ grid-template-columns: repeat(2,minmax(0,1fr)); }}
        .qd-pro-decision {{ text-align: left; }}
      }}
    </style>
    <div class="qd-professional" data-report-version="{_text(report.get('schema_version'))}">
      <div class="qd-pro-header">
        <div>
          <span class="qd-pro-kicker">{_text(labels.get('professional_report', labels.get('title')))}</span>
          <h3>{_text(instrument.get('market'))}:{_text(instrument.get('canonical_symbol') or instrument.get('symbol'))}</h3>
          <p>{_text(summary)}</p>
        </div>
        <div class="qd-pro-decision">
          <span>{_text(labels.get('ai_decision'))}</span>
          <strong>{_text(decision_label)}</strong>
          <small>{_text(labels.get('market_bias', 'Market bias'))}: {_text(bias)} · {_text(labels.get('confidence'))}: {_number(decision.get('confidence'))}%</small>
        </div>
      </div>
      <div class="qd-pro-quality">
        <div><span>{_text(labels.get('data_quality', 'Data quality'))}</span><strong>{_number(float(quality.get('quality_score') or 0) * 100)}%</strong></div>
        <div><span>{_text(labels.get('data_coverage', 'Coverage'))}</span><strong>{_number(float(quality.get('coverage_ratio') or 0) * 100)}%</strong></div>
        <div><span>{_text(labels.get('data_freshness', 'Freshness'))}</span><strong>{_number(float(quality.get('freshness_ratio') or 0) * 100)}%</strong></div>
        <div><span>{_text(labels.get('conclusion_strength', 'Conclusion'))}</span><strong>{_text(quality.get('max_conclusion_strength'))}</strong></div>
      </div>
      {f'<section><h4>{_text(labels.get("analysis_dimensions", "Analysis dimensions"))}</h4><div class="qd-pro-grid">{dimension_html}</div></section>' if dimension_html else ''}
      {f'<section><h4>{_text(labels.get("scenario_analysis", "Scenario analysis"))}</h4><div class="qd-pro-grid qd-pro-grid-3">{scenario_html}</div></section>' if scenario_html else ''}
      {f'<section><h4>{_text(labels.get("risk_plan", "Risk plan"))}</h4><div class="qd-pro-risk">{risk_html}</div></section>' if risk_html else ''}
      <footer>{_text(report.get('schema_version'))} · {_text(report.get('report_id'))}</footer>
    </div>
    """


def render_professional_telegram(report: Dict[str, Any], labels: Dict[str, str]) -> List[str]:
    """Render a compact Telegram section from the same professional contract."""
    instrument = report.get("instrument") or {}
    decision = report.get("decision_profile") or {}
    quality = report.get("data_quality") or {}
    risk = report.get("risk_plan") or {}
    candidate = risk.get("candidate_setup") or {}
    display_risk = candidate or risk
    action = str(decision.get("decision") or "HOLD").upper()
    action_label = labels.get(action.lower(), action)
    symbol = instrument.get("canonical_symbol") or instrument.get("symbol") or "-"
    lines = [
        f"<b>{escape(str(instrument.get('market') or ''))}:{escape(str(symbol))}</b>",
        f"{escape(labels.get('ai_decision', 'AI'))}: <b>{escape(action_label)}</b> · {escape(labels.get('confidence', 'Confidence'))} {_number(decision.get('confidence'))}%",
        f"{escape(labels.get('data_quality', 'Data quality'))}: {_number(float(quality.get('quality_score') or 0) * 100)}%",
    ]
    summary = report.get("executive_summary") or decision.get("rationale")
    if summary:
        lines.append(escape(str(summary))[:900])
    if display_risk:
        lines.append(
            f"{escape(labels.get('entry_price', 'Entry'))} {_money(display_risk.get('entry_price'))} · "
            f"{escape(labels.get('stop_loss', 'Stop'))} {_money(display_risk.get('stop_loss'))} · "
            f"{escape(labels.get('take_profit', 'Target'))} {_money(display_risk.get('take_profit'))}"
        )
    return lines
